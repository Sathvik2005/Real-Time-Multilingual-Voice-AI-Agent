"""
Stream manager — coordinates the STT → Agent → TTS pipeline per session.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Coroutine, Dict, Optional

import structlog
from langchain_core.messages import HumanMessage
from backend.agent.graph import build_agent_graph
from backend.agent.state import AgentState
from backend.config import settings
from backend.database import crud
from backend.database.connection import get_session_factory
from backend.memory.long_term import LongTermMemory
from backend.memory.session import RedisSessionManager
from backend.voice_pipeline.tts import elevenlabs_tts

logger = structlog.get_logger(__name__)

AudioChunkCallback = Callable[[bytes], Coroutine[Any, Any, None]]
TextCallback = Callable[[str, bool], Coroutine[Any, Any, None]]
AudioEndCallback = Callable[[], Coroutine[Any, Any, None]]
LatencyCallback = Callable[[float, float, float], Coroutine[Any, Any, None]]
ToolEventCallback = Callable[[list], Coroutine[Any, Any, None]]


class StreamManager:
    """One StreamManager instance lives for the duration of a WebSocket session."""

    def __init__(
        self,
        session_id: str,
        session_store: RedisSessionManager,
        on_audio_chunk: AudioChunkCallback,
        on_text_event: TextCallback,
        on_audio_end: Optional[AudioEndCallback] = None,
        on_latency_event: Optional[LatencyCallback] = None,
        on_tool_event: Optional[ToolEventCallback] = None,
    ) -> None:
        self.session_id = session_id
        self._store = session_store
        self._on_audio_chunk = on_audio_chunk
        self._on_text_event = on_text_event
        self._on_audio_end = on_audio_end
        self._on_latency_event = on_latency_event
        self._on_tool_event = on_tool_event

        self._tts_task: Optional[asyncio.Task] = None
        self._agent_lock = asyncio.Lock()

    # ── Initialisation ────────────────────────────────────────────────────

    async def initialise(
        self,
        patient_name: Optional[str] = None,
        patient_phone: Optional[str] = None,
        preferred_language: Optional[str] = None,
    ) -> tuple[Optional[int], str]:
        """
        Create or resume a session.

        Returns:
          (patient_id, active_language)
        """
        normalized_lang = (preferred_language or "").strip().lower() or None

        if not await self._store.session_exists(self.session_id):
            await self._store.create_session(
                self.session_id,
                language=normalized_lang or "en",
            )
            logger.info("New session initialised", session_id=self.session_id)

        active_language = normalized_lang or await self._store.get_language(self.session_id)
        patient_id: Optional[int] = await self._store.get_patient_id(self.session_id)

        if patient_name:
            # Use a dedicated short-lived session for this write
            factory = get_session_factory()
            async with factory() as db:
                patient = await crud.get_or_create_patient(
                    db,
                    name=patient_name,
                    phone=patient_phone,
                    preferred_language=active_language,
                )

                # Explicit user preference should override previously stored language.
                if normalized_lang and patient.preferred_language != normalized_lang:
                    patient.preferred_language = normalized_lang

                await db.commit()

                await self._store.set_patient(
                    self.session_id,
                    patient.patient_id,
                    patient.name,
                )
                patient_id = patient.patient_id

                # Carry language preference across sessions for returning patients.
                active_language = normalized_lang or patient.preferred_language or active_language

        await self._store.set_language(self.session_id, active_language)
        return patient_id, active_language

    # ── Barge-in ──────────────────────────────────────────────────────────

    def interrupt_tts(self) -> None:
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
            logger.debug("TTS interrupted by barge-in", session_id=self.session_id)

    # ── Main entry point ─────────────────────────────────────────────────

    async def process_text(self, text: str, asr_ms: float = 0.0) -> None:
        async with self._agent_lock:
            await self._run_pipeline(text, asr_ms=asr_ms)

    async def _local_rule_based_response(
        self,
        db,
        user_text: str,
        patient_id: Optional[int],
    ) -> str:
        """Fallback conversational planner when external LLM keys are unavailable."""
        text = (user_text or "").strip()
        low = text.lower()

        # Appointment mutations by explicit IDs
        if patient_id and (m := re.search(r"\bbook\s+slot\s+(\d+)\b", low)):
            slot_id = int(m.group(1))
            slot = await crud.get_slot_by_id(db, slot_id)
            if not slot:
                return f"I could not find slot {slot_id}. Please ask me to list available slots first."
            try:
                appt = await crud.create_appointment(
                    db,
                    patient_id=patient_id,
                    doctor_id=slot.doctor_id,
                    slot_id=slot_id,
                    reason="Booked via fallback assistant",
                )
                doctor = await crud.get_doctor_by_id(db, slot.doctor_id)
                await db.commit()
                return (
                    f"Appointment booked successfully. Appointment ID {appt.appointment_id} with "
                    f"Dr. {doctor.name if doctor else 'the doctor'} at "
                    f"{slot.start_time.strftime('%A, %d %B at %I:%M %p')}."
                )
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                return f"I could not book slot {slot_id}: {str(exc)}"

        if patient_id and (m := re.search(r"\bcancel\s+appointment\s+(\d+)\b", low)):
            appt_id = int(m.group(1))
            try:
                await crud.cancel_appointment(db, appt_id, patient_id)
                await db.commit()
                return f"Appointment {appt_id} has been cancelled."
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                return f"I could not cancel appointment {appt_id}: {str(exc)}"

        if patient_id and (m := re.search(r"\breschedule\s+appointment\s+(\d+)\s+to\s+slot\s+(\d+)\b", low)):
            appt_id = int(m.group(1))
            slot_id = int(m.group(2))
            try:
                appt = await crud.reschedule_appointment(db, appt_id, patient_id, slot_id)
                slot = await crud.get_slot_by_id(db, slot_id)
                await db.commit()
                return (
                    f"Appointment {appt.appointment_id} has been rescheduled to "
                    f"{slot.start_time.strftime('%A, %d %B at %I:%M %p') if slot else f'slot {slot_id}'}."
                )
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                return f"I could not reschedule appointment {appt_id}: {str(exc)}"

        # Read-only intents
        if "specialization" in low or "specialisation" in low:
            doctors = await crud.list_all_doctors(db)
            specs = sorted({d.specialization for d in doctors})
            return "Available specializations: " + ", ".join(specs[:15])

        if "doctor" in low or "doctors" in low:
            doctors = await crud.list_all_doctors(db)
            if not doctors:
                return "No doctors are currently available in the system."
            lines = [
                f"{d.doctor_id}. Dr. {d.name} - {d.specialization}"
                for d in doctors[:12]
            ]
            return "Available doctors:\n" + "\n".join(lines)

        if patient_id and ("my appointments" in low or "list appointments" in low):
            appts = await crud.get_patient_appointments(db, patient_id)
            if not appts:
                return "You have no upcoming appointments."
            lines = [
                f"{a.appointment_id}: Dr. {a.doctor.name if a.doctor else 'Unknown'} on "
                f"{a.slot.start_time.strftime('%A, %d %B at %I:%M %p') if a.slot else 'N/A'} ({a.status.value})"
                for a in appts[:10]
            ]
            return "Your upcoming appointments:\n" + "\n".join(lines)

        if "book" in low or "appointment" in low or "slot" in low:
            spec_keywords = {
                "cardio": "Cardiology",
                "heart": "Cardiology",
                "derma": "Dermatology",
                "skin": "Dermatology",
                "neuro": "Neurology",
                "brain": "Neurology",
                "pediatric": "Pediatrics",
                "child": "Pediatrics",
                "ortho": "Orthopedics",
                "bone": "Orthopedics",
                "ent": "ENT",
            }
            specialization = ""
            for k, v in spec_keywords.items():
                if k in low:
                    specialization = v
                    break

            doctors = (
                await crud.search_doctors(db, specialization=specialization)
                if specialization
                else await crud.list_all_doctors(db)
            )
            if not doctors:
                return "I could not find matching doctors. Ask me to list available doctors first."

            doctor = doctors[0]
            slots = await crud.get_available_slots(db, doctor.doctor_id, limit=5)
            if not slots:
                return f"Dr. {doctor.name} currently has no available slots. Try another specialization."

            slot_lines = [
                f"slot {s.slot_id}: {s.start_time.strftime('%A, %d %B at %I:%M %p')}"
                for s in slots
            ]
            return (
                f"I found availability with Dr. {doctor.name} ({doctor.specialization}).\n"
                + "\n".join(slot_lines)
                + "\nTo confirm, say: book slot <slot_id>."
            )

        return (
            "I'm running in local fallback mode because LLM credentials are unavailable. "
            "You can ask me to list doctors, list specializations, book slot <id>, "
            "cancel appointment <id>, or reschedule appointment <id> to slot <id>."
        )

    # ── Pipeline ──────────────────────────────────────────────────────────

    async def _run_pipeline(self, user_text: str, asr_ms: float = 0.0) -> None:
        turn = await self._store.increment_turn(self.session_id)
        language = await self._store.get_language(self.session_id)
        patient_id = await self._store.get_patient_id(self.session_id)

        logger.info(
            "Pipeline start",
            session_id=self.session_id,
            turn=turn,
            language=language,
            text_preview=user_text[:60],
        )

        # Each pipeline turn gets its own short-lived DB session to avoid
        # SQLite write-lock contention with the WebSocket's session.
        factory = get_session_factory()
        async with factory() as db:
            # ── Step 1: Long-term patient context ──────────────────────
            patient_ctx: Dict[str, Any] = {}
            if patient_id:
                patient_ctx = await LongTermMemory.get_patient_context(db, patient_id)

            # ── Step 2: LangGraph agent ────────────────────────────────
            t_llm_start = time.perf_counter()

            graph = build_agent_graph(
                db=db,
                patient_context=patient_ctx or None,
                session_id=self.session_id,
            )

            initial_state: AgentState = {
                "messages": [HumanMessage(content=user_text)],
                "session_id": self.session_id,
                "patient_id": patient_id,
                "patient_name": patient_ctx.get("name"),
                "detected_language": language,
                "english_query": None,
                "current_intent": None,
                "pending_confirmation": None,
                "selected_doctor": None,
                "selected_slot": None,
                "english_response": None,
                "final_response": None,
                "error": None,
                "tool_calls_trace": None,
            }

            try:
                result_state: AgentState = await graph.ainvoke(initial_state)
                await db.commit()
            except Exception as exc:
                logger.error("Agent graph failed", error=str(exc), exc_info=True)
                await db.rollback()
                error_str = str(exc).lower()
                if "insufficient_quota" in error_str or "429" in error_str:
                    user_msg = (
                        "I'm sorry, the AI service has exceeded its quota. "
                        "Please check the OpenAI billing and add credits, "
                        "or configure a Groq API key in the .env file."
                    )
                elif "timeout" in error_str or "timed out" in error_str:
                    user_msg = "I'm sorry, the request timed out. Please try again."
                elif "authentication" in error_str or "401" in error_str or "api key" in error_str:
                    user_msg = await self._local_rule_based_response(db, user_text, patient_id)
                else:
                    user_msg = "I'm sorry, I encountered an error. Please try again."
                await self._on_text_event(user_msg, True)
                if "authentication" in error_str or "401" in error_str or "api key" in error_str:
                    # Continue to TTS so voice UX remains intact in fallback mode.
                    self._tts_task = asyncio.create_task(
                        self._stream_tts(user_msg, language, llm_ms=0.0, asr_ms=asr_ms)
                    )
                    try:
                        await self._tts_task
                    except Exception as tts_exc:  # noqa: BLE001
                        logger.error("Fallback TTS failed", error=str(tts_exc), session_id=self.session_id)
                        if self._on_audio_end:
                            await self._on_audio_end()
                    return

                # Send audio_end so the frontend can accept new input (not stuck in 'speaking')
                if self._on_audio_end:
                    await self._on_audio_end()
                return

            llm_ms = (time.perf_counter() - t_llm_start) * 1000

            detected_lang = result_state.get("detected_language", language)
            if detected_lang != language:
                await self._store.set_language(self.session_id, detected_lang)
                language = detected_lang

            final_response = result_state.get("final_response") or ""

            # Emit reasoning trace (tool calls) to the client for visibility
            tool_trace = result_state.get("tool_calls_trace") or []
            if tool_trace and self._on_tool_event:
                await self._on_tool_event(tool_trace)

            if not final_response:
                logger.warning("Empty agent response", session_id=self.session_id)
                return

            # Emit agent text event
            await self._on_text_event(final_response, True)

            logger.info("LLM_latency", ms=round(llm_ms, 1), session_id=self.session_id)

            # ── Step 3: Stream TTS audio ───────────────────────────────
            self._tts_task = asyncio.create_task(
                self._stream_tts(final_response, language, llm_ms, asr_ms)
            )
            try:
                await self._tts_task
            except asyncio.CancelledError:
                logger.info("TTS task cancelled", session_id=self.session_id)
            except Exception as tts_exc:
                logger.error("TTS failed", error=str(tts_exc), session_id=self.session_id)
                # Ensure frontend receives audio_end so it's not stuck waiting
                if self._on_audio_end:
                    await self._on_audio_end()

            # ── Step 4: Update long-term memory ───────────────────────
            if patient_id:
                try:
                    await LongTermMemory.record_interaction(db, patient_id)
                    if detected_lang != "en":
                        await LongTermMemory.update_language(db, patient_id, detected_lang)
                    await db.commit()
                except Exception as exc:
                    logger.warning("Failed to update long-term memory", error=str(exc))
                    await db.rollback()

    async def _stream_tts(self, text: str, language: str, llm_ms: float, asr_ms: float = 0.0) -> None:
        t_tts_start = time.perf_counter()
        first_chunk = True

        async for chunk in elevenlabs_tts.synthesize_stream(text, language=language):
            await self._on_audio_chunk(chunk)
            if first_chunk:
                tts_first_ms = (time.perf_counter() - t_tts_start) * 1000
                total_ms = asr_ms + llm_ms + tts_first_ms
                logger.info(
                    "Latency_breakdown",
                    ASR_ms=round(asr_ms, 1),
                    LLM_ms=round(llm_ms, 1),
                    TTS_first_chunk_ms=round(tts_first_ms, 1),
                    Total_to_first_audio_ms=round(total_ms, 1),
                )
                # Emit latency metrics to client
                if self._on_latency_event:
                    await self._on_latency_event(asr_ms, llm_ms, tts_first_ms)
                first_chunk = False

        tts_total_ms = (time.perf_counter() - t_tts_start) * 1000
        logger.info("TTS_latency", ms=round(tts_total_ms, 1), session_id=self.session_id)

        # Signal end of audio stream to the client
        if self._on_audio_end:
            await self._on_audio_end()

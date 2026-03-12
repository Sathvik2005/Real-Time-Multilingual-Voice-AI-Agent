"""
LangGraph agent nodes.

Each node is an async function that receives AgentState and returns a
partial state update (a dict with only the keys being modified).

Node execution order (defined in graph.py):
  translate_input  →  agent  →  [tools  →  agent]*  →  translate_output
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from backend.agent.prompts import build_system_prompt
from backend.agent.state import AgentState
from backend.config import settings
from backend.services.language_detection import language_detector
from backend.services.translation import translation_service

logger = structlog.get_logger(__name__)


def _build_llm(tools: List[Any]):
    # Prefer Groq (free tier) when a Groq API key is configured.
    # Use ChatGroq (not ChatOpenAI+base_url) — it handles Llama tool calling correctly.
    if settings.GROQ_API_KEY:
        llm = ChatGroq(
            model=settings.GROQ_MODEL,
            groq_api_key=settings.GROQ_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            max_retries=0,
        )
    else:
        llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            max_retries=0,  # fail fast — retrying quota/auth errors just wastes time
        )
    return llm.bind_tools(tools)


# ─────────────────────────────────────────────────────────────────────────────
# Node: translate_input
# ─────────────────────────────────────────────────────────────────────────────


async def translate_input_node(state: AgentState) -> Dict[str, Any]:
    """
    Detect language of the latest user message and translate to English
    if needed.  Updates detected_language and english_query in state.
    """
    messages = state["messages"]
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
    )

    if last_human is None:
        return {}

    raw_text: str = last_human.content if isinstance(last_human.content, str) else ""

    detected_lang = language_detector.detect(raw_text)
    logger.debug("Language detected", language=detected_lang, session_id=state["session_id"])

    if detected_lang == "en":
        english_text = raw_text
    else:
        english_text = await translation_service.to_english(raw_text, source=detected_lang)
        logger.debug(
            "Input translated to English",
            original=raw_text[:50],
            translated=english_text[:50],
            source=detected_lang,
        )

    return {
        "detected_language": detected_lang,
        "english_query": english_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: agent  (LLM reasoning + tool selection)
# ─────────────────────────────────────────────────────────────────────────────


def make_agent_node(tools: List[Any], patient_context: Optional[Dict] = None):
    """
    Factory that returns an agent node function bound to a set of tools and
    optional patient context.  Called once per request to inject fresh
    session-specific data.
    """
    llm_with_tools = _build_llm(tools)

    async def agent_node(state: AgentState) -> Dict[str, Any]:
        messages = state["messages"]

        # Replace the last HumanMessage content with the English translation
        # so the LLM always reasons in English.
        english_query = state.get("english_query")
        if english_query:
            updated_messages: List = []
            replaced = False
            for msg in reversed(messages):
                if not replaced and isinstance(msg, HumanMessage):
                    updated_messages.insert(0, HumanMessage(content=english_query))
                    replaced = True
                else:
                    updated_messages.insert(0, msg)
            messages = updated_messages

        # Prepend system prompt with patient context
        now_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y at %H:%M UTC")
        system_prompt = build_system_prompt(
            current_datetime=now_str,
            patient_context=patient_context,
        )

        full_messages = [SystemMessage(content=system_prompt)] + list(messages)

        response: AIMessage = await llm_with_tools.ainvoke(full_messages)

        # Build tool call trace for reasoning visibility
        tool_trace: List[Dict[str, Any]] = []
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_trace.append({
                    "tool": tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""),
                    "args": tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {}),
                    "id": tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", ""),
                })

        logger.debug(
            "LLM response",
            session_id=state["session_id"],
            has_tool_calls=bool(response.tool_calls),
            tool_names=[t["tool"] for t in tool_trace],
        )

        # Accumulate trace entries across multi-step tool loops
        existing_trace: List[Dict[str, Any]] = state.get("tool_calls_trace") or []
        return {
            "messages": [response],
            "tool_calls_trace": existing_trace + tool_trace,
        }

    return agent_node


# ─────────────────────────────────────────────────────────────────────────────
# Node: translate_output
# ─────────────────────────────────────────────────────────────────────────────


async def translate_output_node(state: AgentState) -> Dict[str, Any]:
    """
    Take the final English AI response and translate it back to the
    user's detected language.
    """
    messages = state["messages"]
    detected_lang = state.get("detected_language", "en")

    last_ai = next(
        (m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )

    if last_ai is None:
        return {"final_response": "", "english_response": ""}

    english_text: str = last_ai.content if isinstance(last_ai.content, str) else ""

    if detected_lang == "en":
        final_text = english_text
    else:
        final_text = await translation_service.from_english(english_text, target=detected_lang)
        logger.debug(
            "Response translated",
            target=detected_lang,
            original=english_text[:60],
            translated=final_text[:60],
        )

    return {
        "english_response": english_text,
        "final_response": final_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Edge condition: should_continue
# ─────────────────────────────────────────────────────────────────────────────


def should_continue(state: AgentState) -> str:
    """
    Route to 'tools' if the last AI message contains tool calls,
    otherwise route to 'translate_output'.
    """
    messages = state["messages"]
    last_msg = messages[-1] if messages else None
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return "translate_output"

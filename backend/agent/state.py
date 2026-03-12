"""
LangGraph agent state definition.

AgentState is the single mutable object that flows through every node
in the graph.  All fields are typed; optional fields default to None.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):

    # ── Conversation messages (append-only via add_messages reducer) ──────
    messages: Annotated[List[BaseMessage], add_messages]

    # ── Session context ───────────────────────────────────────────────────
    session_id: str
    patient_id: Optional[int]
    patient_name: Optional[str]

    # ── Language pipeline ─────────────────────────────────────────────────
    detected_language: str          # BCP-47 code of the user's language
    english_query: Optional[str]    # User query translated to English

    # ── Agent working memory ──────────────────────────────────────────────
    current_intent: Optional[str]   # e.g. "book", "cancel", "check_availability"
    pending_confirmation: Optional[Dict[str, Any]]  # awaiting user yes/no
    selected_doctor: Optional[Dict[str, Any]]
    selected_slot: Optional[Dict[str, Any]]

    # ── Response pipeline ─────────────────────────────────────────────────
    english_response: Optional[str]   # Agent reply in English
    final_response: Optional[str]     # Reply in user's detected language

    # ── Error state ───────────────────────────────────────────────────────
    error: Optional[str]

    # ── Reasoning trace (tool calls made during this turn) ───────────────
    tool_calls_trace: Optional[List[Dict[str, Any]]]

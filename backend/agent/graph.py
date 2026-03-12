"""
LangGraph agent graph definition.

Graph topology
--------------

  START
    |
  translate_input      (detect language, translate to English)
    |
  agent                (LLM reasoning with bound tools)
    |
  [tools_condition]----> tools  (execute tool calls)
    |                      |
    |                   (loop back to agent)
    v
  translate_output     (translate English reply → user language)
    |
   END

Usage
-----
  from backend.agent.graph import build_agent_graph
  from backend.tools.appointment_tools import get_appointment_tools
  from backend.tools.doctor_tools import get_doctor_tools

  graph = build_agent_graph(db=session, patient_context=ctx)
  result = await graph.ainvoke({"messages": [HumanMessage(content=query)], ...})
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.nodes import (
    make_agent_node,
    should_continue,
    translate_input_node,
    translate_output_node,
)
from backend.agent.state import AgentState
from backend.tools.appointment_tools import get_appointment_tools
from backend.tools.doctor_tools import get_doctor_tools


def build_agent_graph(
    db: AsyncSession,
    patient_context: Optional[Dict[str, Any]] = None,
    session_id: str = "",
):
    """
    Construct and compile a LangGraph agent graph for a single request.

    A new graph is compiled per-request so that:
      * Tools are bound to a fresh, request-scoped DB session.
      * Patient context is baked into the system prompt.

    The compiled graph object is cheap to create (no I/O).
    """
    # Assemble all tools
    tools: List[Any] = get_appointment_tools(db) + get_doctor_tools(db)

    # Build the agent node with tools + context
    agent_node_fn = make_agent_node(tools=tools, patient_context=patient_context)

    # Build the tool executor node
    tool_node = ToolNode(tools)

    # ── Graph construction ────────────────────────────────────────────────
    graph_builder = StateGraph(AgentState)

    graph_builder.add_node("translate_input", translate_input_node)
    graph_builder.add_node("agent", agent_node_fn)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("translate_output", translate_output_node)

    # Edges
    graph_builder.add_edge(START, "translate_input")
    graph_builder.add_edge("translate_input", "agent")

    graph_builder.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "translate_output": "translate_output",
        },
    )

    graph_builder.add_edge("tools", "agent")
    graph_builder.add_edge("translate_output", END)

    return graph_builder.compile()

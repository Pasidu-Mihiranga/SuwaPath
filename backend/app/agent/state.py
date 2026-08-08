"""Shared state for the SuwaPath agent graph.

The one piece of real machinery here is ``agent_outputs``. It carries an
``operator.add`` reducer, which is what makes parallel fan-out work: when the
supervisor dispatches to several agent nodes at once, each node returns a
one-element list and LangGraph concatenates them automatically on fan-in.
Without the reducer, concurrent writes to the same key would conflict and the
last writer would win, silently discarding the other agents' work.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage


# Routes the supervisor can dispatch to. Each maps to one agent node, and to
# a field allowlist in app/privacy/boundary.py — adding a route without adding
# its allowlist means it gets the most restrictive ("direct") set by default.
VALID_ROUTES = ("clinical", "admin", "records", "knowledge", "direct")

# Cap on parallel branches. Compound questions are real ("check my appointment
# AND explain my report") but beyond three the merge step degrades and latency
# stops being worth it.
MAX_ROUTES = 3


class RouteDecision(TypedDict, total=False):
    route: str
    confidence: float
    reasoning: str
    action: Optional[str]
    params: dict[str, Any]


class AgentState(TypedDict, total=False):
    # --- conversation ---
    messages: Annotated[list[AnyMessage], add_messages]
    user_text: str
    language: str

    # --- identity and scope ---
    user_id: str
    role: str
    session_id: str
    # Consent scopes / RBAC facts the tools enforce. Never sent to a model.
    scope: dict[str, Any]

    # --- privacy ---
    # Minimised, pseudonymised context — the only patient data agents may read.
    safe_context: dict[str, Any]

    # --- routing ---
    routes: list[RouteDecision]
    routing_source: str

    # --- fan-in collector ---
    # Each parallel agent appends exactly one dict; operator.add merges them.
    agent_outputs: Annotated[list[dict], operator.add]

    # --- clinical authority (deterministic, never model-written) ---
    red_flags: dict[str, Any]
    recommendation: dict[str, Any]

    # --- output ---
    answer: str
    citations: list[dict]
    guard: dict[str, Any]
    trace: Annotated[list[dict], operator.add]

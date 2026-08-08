"""SuwaPath agent graph — supervisor with parallel fan-out.

    guard_input ─(blocked)────────────────────────────────► END
         │
         ▼
       route ──► supervisor ─┬─► clinical_agent ─┐
                             ├─► admin_agent    ─┤
                             ├─► records_agent  ─┼─► merge ─► judge ─► END
                             ├─► knowledge_agent─┤
                             └─► direct_agent   ─┘

The supervisor dispatches to one *or more* agent nodes in the same superstep.
A compound question ("is my appointment still on, and what did my blood test
mean?") runs the admin and records agents concurrently rather than serially;
their outputs are collected by the ``operator.add`` reducer on
``agent_outputs`` and combined by ``merge``.

Determinism is preserved where it matters: ``guard_input`` and ``judge`` are
rule-based, and clinical urgency is never produced by this graph — it is read
from the red-flag engine's own output.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agent.guardrails import (
    GuardVerdict,
    check_input,
    judge_output,
    judge_summary,
)
from app.agent.state import MAX_ROUTES, VALID_ROUTES, AgentState
from app.agent.tools import ROUTE_TOOLS, TOOLS, ToolDenied
from app.privacy.boundary import PHIBoundary, EgressBlocked
from app.services import ai_orchestrator as ai

logger = logging.getLogger(__name__)

# Set by the API layer for the duration of a request. The graph needs a DB
# session but LangGraph state must stay JSON-serialisable, so the session is
# passed out-of-band rather than through the state dict.
_SESSION_HOLDER: dict[str, Any] = {}


def set_session(db) -> None:
    _SESSION_HOLDER["db"] = db


def _db():
    return _SESSION_HOLDER.get("db")


ROUTER_PROMPT = """You classify a patient's message for a healthcare assistant.

Available routes:
- clinical:  symptoms, how they feel, whether something is serious
- admin:     appointments, finding or booking a doctor or hospital
- records:   their uploaded reports, test results, scans, medications
- knowledge: general health questions not about their own data
- direct:    greetings, thanks, chit-chat, anything else

A message may contain SEVERAL independent intents. Return one entry per
intent, at most {max_routes}. Most messages have exactly one.

Message: "{message}"

Return JSON only:
{{"routes": [{{"route": "...", "confidence": 0.0-1.0, "reasoning": "one short line"}}]}}"""


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------
def guard_input_node(state: AgentState) -> dict:
    result = check_input(state.get("user_text", ""))
    update: dict[str, Any] = {
        "guard": {"input": str(result.verdict), "rules": result.matched_rules},
        "trace": [{
            "node": "guard_input",
            "verdict": str(result.verdict),
            "rules": result.matched_rules,
            "source": "deterministic",
        }],
    }
    if result.blocked:
        update["answer"] = result.replacement or ""
        update["routes"] = []
    return update


def route_node(state: AgentState) -> dict:
    """Classify intent, possibly into several parallel routes."""
    message = state.get("user_text", "")
    prompt = ROUTER_PROMPT.format(message=message, max_routes=MAX_ROUTES)
    data = ai._parse_json(ai._generate(prompt, temperature=0.0, as_json=True))

    routes: list[dict] = []
    source = "gemini"
    for entry in (data or {}).get("routes", [])[:MAX_ROUTES]:
        name = str(entry.get("route", "")).strip()
        if name in VALID_ROUTES:
            routes.append({
                "route": name,
                "confidence": float(entry.get("confidence", 0.5)),
                "reasoning": str(entry.get("reasoning", ""))[:200],
            })

    if not routes:
        # Deterministic fallback. This is not a token gesture: parallel
        # fan-out is the headline capability, so it must not depend on an
        # external quota. The fallback detects multiple intents too, which
        # keeps compound questions working with no API key at all.
        source = "keyword_fallback"
        routes = _fallback_routes(message)

    return {
        "routes": routes,
        "routing_source": source,
        "trace": [{
            "node": "route",
            "source": source,
            "routes": [r["route"] for r in routes],
            "multi": len(routes) > 1,
        }],
    }


_FALLBACK_KEYWORDS = [
    ("admin", ("appointment", "book", "booking", "doctor", "hospital",
               "reschedule", "cancel", "clinic", "specialist")),
    ("records", ("report", "result", "scan", "x-ray", "xray", "medication",
                 "blood test", "lab", "prescription", "screening")),
    ("clinical", ("pain", "fever", "symptom", "hurt", "feel", "sick", "cough",
                  "dizzy", "headache", "bleeding", "rash", "breath")),
    ("knowledge", ("what is", "what does", "why does", "how does", "explain",
                   "is it normal", "tell me about")),
]

# Clauses joined by these are treated as separate intents, which is what makes
# "check my appointment AND explain my report" fan out to two agents.
_CLAUSE_SPLIT = re.compile(r"\band\b|\balso\b|[;?]|,\s*(?=and\b)", re.I)


def _fallback_routes(message: str) -> list[dict]:
    """Keyword multi-intent detection, used when no LLM router is available."""
    lowered = (message or "").lower()
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(lowered) if c.strip()]
    if not clauses:
        clauses = [lowered]

    ordered: list[str] = []
    for clause in clauses:
        for route, keywords in _FALLBACK_KEYWORDS:
            if route in ordered:
                continue
            if any(keyword in clause for keyword in keywords):
                ordered.append(route)
                break

    if not ordered:
        ordered = ["direct"]

    return [
        {
            "route": route,
            "confidence": 0.45,
            "reasoning": "Matched a keyword pattern (no LLM router available).",
        }
        for route in ordered[:MAX_ROUTES]
    ]


def dispatch(state: AgentState) -> list[Send]:
    """Fan out to every selected agent node in one superstep.

    ``Send`` is what makes this genuinely parallel rather than a loop — each
    returned Send becomes an independent branch executed in the same step.
    """
    routes = state.get("routes") or []
    if not routes:
        return [Send("direct_agent", {**state, "_route": {"route": "direct"}})]
    return [
        Send(f"{decision['route']}_agent", {**state, "_route": decision})
        for decision in routes
    ]


def _run_agent(state: AgentState, route: str) -> dict:
    """Shared body for every agent node: tools, then a grounded reply."""
    started = time.perf_counter()
    db = _db()
    scope = state.get("scope") or {}
    decision = state.get("_route") or {"route": route}

    tool_texts: list[str] = []
    structured: dict[str, Any] = {}
    tool_events: list[dict] = []

    for tool_name in ROUTE_TOOLS.get(route, ()):
        tool = TOOLS.get(tool_name)
        if tool is None or db is None:
            continue
        tool_started = time.perf_counter()
        try:
            text, payload = tool(db, scope, question=state.get("user_text", ""))
            tool_texts.append(text)
            structured.update(payload)
            tool_events.append({
                "tool": tool_name,
                "status": "ok",
                "ms": int((time.perf_counter() - tool_started) * 1000),
            })
        except ToolDenied as exc:
            # A consent refusal is a normal outcome, not an error. It is shown
            # to the user as a boundary, and the reason never reaches the model.
            tool_texts.append(f"[Access to {tool_name} is not permitted for this user.]")
            tool_events.append({"tool": tool_name, "status": "denied", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tool %s failed: %s", tool_name, exc)
            tool_events.append({"tool": tool_name, "status": "error"})

    answer = _synthesise(state, route, tool_texts)

    return {
        "agent_outputs": [{
            "route": route,
            "answer": answer,
            "structured": structured,
            "confidence": decision.get("confidence", 0.5),
        }],
        "trace": [{
            "node": f"{route}_agent",
            "tools": tool_events,
            "ms": int((time.perf_counter() - started) * 1000),
        }],
    }


AGENT_BRIEF = {
    "clinical": "You explain symptoms and what kind of care they warrant. You never diagnose.",
    "admin": "You help with appointments and finding the right doctor or facility.",
    "records": "You explain the patient's own reports and screenings in plain language.",
    "knowledge": "You answer general health questions using only the reference material.",
    "direct": "You reply briefly and warmly, and steer back to how SuwaPath can help.",
}


def _synthesise(state: AgentState, route: str, tool_texts: list[str]) -> str:
    """Build the reply for one agent, behind the PHI boundary."""
    boundary = PHIBoundary(
        session_salt=state.get("session_id", "session"), route=route
    )
    safe_context = boundary.minimise(state.get("safe_context") or {})
    language = {"en": "English", "si": "Sinhala", "ta": "Tamil"}.get(
        state.get("language", "en"), "English"
    )

    prompt = f"""{ai.SAFETY_PREAMBLE}

{AGENT_BRIEF.get(route, "")}

Patient context (de-identified): {safe_context}

Information retrieved for this question:
{chr(10).join(tool_texts) if tool_texts else "None."}

Patient's message: "{state.get('user_text', '')}"

Answer in {language}, in 2-4 short sentences. Use only the information above.
Do not invent names, results, prices or availability. If the information is
missing, say so and suggest the next step."""

    try:
        boundary.guard(prompt)
    except EgressBlocked as exc:
        logger.error("Egress blocked for route %s: %s", route, exc)
        return (
            "I could not prepare that answer safely. Please contact SuwaPath "
            "Care on 0112 123 456."
        )

    generated = ai._generate(prompt, temperature=0.3)
    if not generated:
        # Deterministic fallback: return the retrieved facts directly rather
        # than nothing, so the product still works without an API key.
        return "\n".join(tool_texts) if tool_texts else (
            "I can help with your symptoms, appointments, reports and general "
            "health questions. What would you like to do?"
        )
    return boundary.rehydrate(generated)


def clinical_agent(state: AgentState) -> dict:
    return _run_agent(state, "clinical")


def admin_agent(state: AgentState) -> dict:
    return _run_agent(state, "admin")


def records_agent(state: AgentState) -> dict:
    return _run_agent(state, "records")


def knowledge_agent(state: AgentState) -> dict:
    return _run_agent(state, "knowledge")


def direct_agent(state: AgentState) -> dict:
    return _run_agent(state, "direct")


def merge_node(state: AgentState) -> dict:
    """Fan-in. One branch passes through; several are combined."""
    outputs = state.get("agent_outputs") or []

    if not outputs:
        return {"answer": state.get("answer") or "", "trace": [{"node": "merge", "count": 0}]}

    if len(outputs) == 1:
        # Single route is the common case — no extra model call.
        return {
            "answer": outputs[0]["answer"],
            "citations": (outputs[0].get("structured") or {}).get("citations", []),
            "trace": [{"node": "merge", "count": 1, "source": "passthrough"}],
        }

    sections = "\n\n".join(
        f"[{o['route']}] {o['answer']}" for o in outputs
    )
    prompt = f"""{ai.SAFETY_PREAMBLE}

The patient asked one message containing several questions. Separate
assistants answered each part:

{sections}

Combine these into ONE reply that answers every part. Keep it under six
sentences, do not repeat yourself, and do not add new facts."""

    merged = ai._generate(prompt, temperature=0.2)
    citations: list[dict] = []
    for output in outputs:
        citations.extend((output.get("structured") or {}).get("citations", []))

    return {
        "answer": merged or "\n\n".join(o["answer"] for o in outputs),
        "citations": citations,
        "trace": [{
            "node": "merge",
            "count": len(outputs),
            "source": "gemini" if merged else "concatenated",
        }],
    }


def judge_node(state: AgentState) -> dict:
    """Output safety check. May soften or block; never escalates urgency."""
    red_flags = state.get("red_flags") or {}
    result = judge_output(
        state.get("answer", ""),
        engine_urgency=red_flags.get("urgency"),
    )

    answer = state.get("answer", "")
    if result.verdict == GuardVerdict.BLOCK:
        answer = result.replacement or red_flags.get("escalation_message") or (
            "Please speak to a clinician about this. You can find one under "
            "Doctors & Hospitals."
        )
    elif result.verdict == GuardVerdict.SOFTEN and result.replacement:
        answer = result.replacement

    guard = dict(state.get("guard") or {})
    guard.update(judge_summary(check_input(""), result))

    return {
        "answer": answer,
        "guard": guard,
        "trace": [{
            "node": "judge",
            "verdict": str(result.verdict),
            "rules": result.matched_rules,
            "source": "deterministic",
        }],
    }


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------
def _after_guard(state: AgentState) -> str:
    return END if state.get("answer") and not state.get("routes") else "route"


def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("guard_input", guard_input_node)
    graph.add_node("route", route_node)
    graph.add_node("clinical_agent", clinical_agent)
    graph.add_node("admin_agent", admin_agent)
    graph.add_node("records_agent", records_agent)
    graph.add_node("knowledge_agent", knowledge_agent)
    graph.add_node("direct_agent", direct_agent)
    graph.add_node("merge", merge_node)
    graph.add_node("judge", judge_node)

    graph.add_edge(START, "guard_input")
    graph.add_conditional_edges("guard_input", _after_guard, {"route": "route", END: END})

    # Fan-out: `dispatch` returns one Send per selected route, all executed
    # in the same superstep.
    graph.add_conditional_edges("route", dispatch, [
        "clinical_agent", "admin_agent", "records_agent",
        "knowledge_agent", "direct_agent",
    ])

    for node in ("clinical_agent", "admin_agent", "records_agent",
                 "knowledge_agent", "direct_agent"):
        graph.add_edge(node, "merge")

    graph.add_edge("merge", "judge")
    graph.add_edge("judge", END)

    return graph.compile()


agent_graph = build_agent_graph()


def topology() -> dict:
    return {
        "nodes": [
            "guard_input", "route", "clinical_agent", "admin_agent",
            "records_agent", "knowledge_agent", "direct_agent", "merge", "judge",
        ],
        "parallel_agents": list(VALID_ROUTES),
        "max_parallel_routes": MAX_ROUTES,
        "deterministic_nodes": ["guard_input", "judge"],
        "llm_nodes": ["route", "*_agent", "merge"],
        "urgency_authority": "deterministic_red_flag_engine",
    }

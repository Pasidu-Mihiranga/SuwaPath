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
from contextlib import contextmanager
from typing import Any, Iterator

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agent import cag, consult
from app.agent.guardrails import (
    GuardVerdict,
    check_input,
    judge_output,
    judge_summary,
)
from app.agent.state import ROUTE_ALIASES, MAX_ROUTES, VALID_ROUTES, AgentState
from app.agent.tools import ROUTE_TOOLS, TOOLS, ToolDenied
from app.privacy.boundary import PHIBoundary, EgressBlocked
from app.services import ai_orchestrator as ai
from app.services import llm

logger = logging.getLogger(__name__)

# Agent nodes need database access, but LangGraph state must stay
# JSON-serialisable, so a Session cannot live in the state dict.
#
# Crucially it also cannot be a single shared Session: LangGraph executes
# fan-out branches on separate worker threads, and a SQLAlchemy Session is not
# thread-safe — two agents querying concurrently raises "this session is
# provisioning a new connection; concurrent operations are not permitted" and
# the branch dies. Each node therefore opens its own short-lived Session from
# the shared engine pool.
@contextmanager
def agent_session() -> Iterator[Any]:
    """A private Session for one agent node, always closed."""
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def set_session(db) -> None:  # noqa: ARG001 - kept for call-site compatibility
    """No-op. Sessions are per-node now; see ``agent_session``."""
    return None


ROUTER_PROMPT = """You classify a patient's message for a healthcare assistant.

Available routes:
- consult:   they are describing how they feel — a symptom, pain, illness,
             or answering a follow-up question about one
- admin:     appointments, and finding or booking a doctor, hospital or
             laboratory — including where a test can be done and what it costs
- records:   results they ALREADY have — a report they uploaded, a past scan's
             findings, their medication list. Not where to obtain a new test.
- knowledge: general health questions not about their own data
- web:       something current that a fixed reference cannot know — an
             outbreak, a recall, this year's guidance, recent news
- direct:    greetings, thanks, chit-chat, off-topic, anything else

A message may contain SEVERAL independent intents. Return one entry per
intent, at most {max_routes}. Most messages have exactly one.

{context}Message: "{message}"

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


def cag_node(state: AgentState) -> dict:
    """Answer greetings and product FAQs from reviewed text, before any model.

    A hit here skips the whole graph. That is the point: these answers should
    be identical every time, and no clinical question is eligible.
    """
    started = time.perf_counter()
    hit = cag.lookup(
        state.get("user_text", ""), confidential=bool(state.get("confidential"))
    )

    if hit is None:
        return {
            "cag": {"hit": False},
            "trace": [{
                "node": "cache_lookup",
                "hit": False,
                "ms": int((time.perf_counter() - started) * 1000),
                "source": "deterministic",
            }],
        }

    return {
        "answer": hit.answer,
        "routes": [],
        "suggestions": hit.suggestions,
        "cag": {"hit": True, "key": hit.key, "kind": hit.kind, "score": hit.score},
        "trace": [{
            "node": "cache_lookup",
            "hit": True,
            "key": hit.key,
            "score": hit.score,
            "ms": int((time.perf_counter() - started) * 1000),
            "source": "cache",
        }],
    }


def route_node(state: AgentState) -> dict:
    """Classify intent, possibly into several parallel routes."""
    message = state.get("user_text", "")
    history = state.get("history") or []

    # Mid-consultation, a bare "3 days" or "it's throbbing" is meaningless in
    # isolation. The last assistant question is what makes it classifiable.
    context = ""
    if history:
        last = history[-1]
        if last.get("role") == "assistant":
            context = f'You previously asked: "{last.get("content", "")[:200]}"\n'

    prompt = ROUTER_PROMPT.format(
        message=message, max_routes=MAX_ROUTES, context=context
    )
    data, completion = llm.complete_json(prompt, temperature=0.0, fast=True)

    routes: list[dict] = []
    source = completion.provider if completion else "none"
    for entry in (data or {}).get("routes", [])[:MAX_ROUTES]:
        name = str(entry.get("route", "")).strip().lower()
        name = ROUTE_ALIASES.get(name, name)
        if name in VALID_ROUTES and not any(r["route"] == name for r in routes):
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

    mid_consultation = bool(history) and _awaiting_answer(history)

    # A reply to our own follow-up question always continues the consultation,
    # whatever the classifier made of the fragment on its own.
    if mid_consultation and not any(r["route"] == "consult" for r in routes):
        routes.insert(0, {
            "route": "consult",
            "confidence": 0.9,
            "reasoning": "Continues an open consultation.",
        })

    routes = _prune_routes(
        routes,
        mid_consultation=mid_consultation,
        seeking_service=bool(_SEEKING_SERVICE.search(message or "")),
    )

    return {
        "routes": routes,
        "routing_source": source,
        "trace": [{
            "node": "route",
            "source": source,
            "routes": [r["route"] for r in routes],
            "multi": len(routes) > 1,
            "ms": completion.latency_ms if completion else 0,
        }],
    }


def _awaiting_answer(history: list[dict]) -> bool:
    """Did we just ask the patient a question?"""
    for message in reversed(history):
        if message.get("role") == "assistant":
            return message.get("content", "").rstrip().endswith("?")
    return False


def _prune_routes(
    routes: list[dict], *, mid_consultation: bool, seeking_service: bool = False
) -> list[dict]:
    """Drop routes that only add noise.

    Classifiers are generous — they will happily return `direct` alongside
    `consult`, or `records` because the patient's answer mentioned "two days".
    Every extra route triggers a merge, and merging an empty or irrelevant
    answer into a good one is how a reply ends up contradicting itself
    ("your appointment is at 11:30… another source says the date is not
    available"). Fewer, better-chosen routes beat more of them.
    """
    if not routes:
        return routes

    # "Where can I get an MRI and what does it cost?" shares every keyword
    # with "what did my MRI show?". Classifiers routinely pick `records` for
    # both, and the patient then gets their old blood count read back at them
    # instead of a price. The question form disambiguates it; the classifier
    # does not get a vote.
    if seeking_service and any(r["route"] == "admin" for r in routes):
        routes = [r for r in routes if r["route"] != "records"]

    # `direct` is the catch-all. If anything substantive was also selected,
    # the catch-all has nothing to contribute.
    if len(routes) > 1:
        routes = [r for r in routes if r["route"] != "direct"] or routes[:1]

    # Mid-consultation the patient is answering our question, not opening a
    # new topic. Pulling their whole record in because they said "two days"
    # buries the consultation under unrelated lab values.
    if mid_consultation:
        consult_only = [r for r in routes if r["route"] == "consult"]
        if consult_only:
            return consult_only[:1]

    return routes[:MAX_ROUTES]


_FALLBACK_KEYWORDS = [
    ("admin", ("appointment", "book", "booking", "doctor", "hospital",
               "reschedule", "cancel", "clinic", "specialist", "channel")),
    ("records", ("report", "result", "scan", "x-ray", "xray", "medication",
                 "blood test", "lab", "prescription", "screening")),
    ("web", ("outbreak", "latest", "current", "news", "recall", "this year",
             "right now", "recently", "2025", "2026")),
    ("consult", ("pain", "fever", "symptom", "hurt", "feel", "sick", "cough",
                 "dizzy", "headache", "bleeding", "rash", "breath", "ache",
                 "vomit", "nausea", "swollen", "itch", "tired")),
    ("knowledge", ("what is", "what does", "why does", "how does", "explain",
                   "is it normal", "tell me about")),
]

# Clauses joined by these are treated as separate intents, which is what makes
# "check my appointment AND explain my report" fan out to two agents.
_CLAUSE_SPLIT = re.compile(r"\band\b|\balso\b|[;?]|,\s*(?=and\b)", re.I)


# "Where can I get an MRI" and "what did my MRI show" share every keyword. The
# difference is whether the patient is looking for a service or for their own
# result, so this is checked before the keyword table and wins outright.
_SEEKING_SERVICE = re.compile(
    r"\b(where|which (place|hospital|lab|clinic)|how much|what does it cost|"
    r"cheapest|nearest|book|find|get)\b.{0,40}"
    r"\b(test|scan|mri|ct|x-?ray|ultrasound|blood work|lab|biopsy|screening)\b"
    r"|\b(test|scan|mri|ct|x-?ray|ultrasound|biopsy)\b.{0,30}"
    r"\b(cost|price|near me|available|where)\b",
    re.I,
)


def _fallback_routes(message: str) -> list[dict]:
    """Keyword multi-intent detection, used when no LLM router is available."""
    lowered = (message or "").lower()
    if _SEEKING_SERVICE.search(lowered):
        return [{
            "route": "admin",
            "confidence": 0.7,
            "reasoning": "Looking for where to obtain a test, not for a past result.",
        }]
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
    scope = state.get("scope") or {}
    decision = state.get("_route") or {"route": route}

    tool_texts: list[str] = []
    structured: dict[str, Any] = {}
    tool_events: list[dict] = []

    with agent_session() as db:
        for tool_name in ROUTE_TOOLS.get(route, ()):
            tool = TOOLS.get(tool_name)
            if tool is None:
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
                # A consent refusal is a normal outcome, not an error. It is
                # shown to the user as a boundary, and the reason never
                # reaches the model.
                tool_texts.append(
                    f"[Access to {tool_name} is not permitted for this user.]"
                )
                tool_events.append({
                    "tool": tool_name, "status": "denied", "detail": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tool %s failed: %s", tool_name, exc)
                tool_events.append({"tool": tool_name, "status": "error"})

    answer, source = _synthesise(state, route, tool_texts)

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
            "source": source,
            "ms": int((time.perf_counter() - started) * 1000),
        }],
    }


AGENT_BRIEF = {
    "admin": (
        "You help with appointments and finding the right doctor or facility. "
        "Lead with the concrete option — who, where, when — not with an "
        "explanation of what you are about to do."
    ),
    "records": (
        "You explain the patient's own reports and screenings in plain "
        "language: what the value means, whether it is outside range, and "
        "what it does and does not imply. Never speculate beyond the numbers."
    ),
    "knowledge": (
        "You answer general health questions using only the reference "
        "material supplied. If it does not cover the question, say so."
    ),
    "web": (
        "You report current public information from the supplied sources "
        "only. Attribute each claim to its source. Current news never changes "
        "how urgent this patient's own situation is."
    ),
    "direct": (
        "You reply briefly and warmly. If the message is not about health, "
        "say so pleasantly in one line and offer what you can actually do — "
        "do not pretend to answer it."
    ),
}

# Applied to every non-consult agent. The old prompt asked for "2-4 short
# sentences", which is why answers arrived as undifferentiated grey text.
_FORMAT_RULES = """
Formatting — this matters, the reply is rendered as markdown:
- Open with the answer itself. No "Certainly!", no restating the question.
- **Bold** the specific things that matter: values, names, dates, the verdict.
- Use a short bulleted list when there is more than one item. Never a wall of text.
- Use a `**Heading**` line only when the reply genuinely has two or more parts.
- Two to three short paragraphs at most.
- Never paste reference material verbatim; explain it in your own words.
- Never invent names, results, prices, dates or availability.
- If the information needed is missing, say plainly what is missing and what to do next."""


def _synthesise(state: AgentState, route: str, tool_texts: list[str]) -> tuple[str, str]:
    """Build the reply for one agent, behind the PHI boundary.

    Returns ``(answer, source)`` so the trace can show which provider — or
    which deterministic composer — actually produced the words.
    """
    boundary = PHIBoundary(
        session_salt=state.get("session_id", "session"), route=route
    )
    safe_context = boundary.minimise(state.get("safe_context") or {})
    language = {"en": "English", "si": "Sinhala", "ta": "Tamil"}.get(
        state.get("language", "en"), "English"
    )

    system = f"""{ai.SAFETY_PREAMBLE}

{AGENT_BRIEF.get(route, "")}
{_FORMAT_RULES}

Answer in {language}."""

    prompt = f"""Patient context (de-identified): {safe_context}

Information retrieved for this question:
{chr(10).join(tool_texts) if tool_texts else "None."}

Patient's message: "{state.get('user_text', '')}\""""

    try:
        boundary.guard(prompt)
    except EgressBlocked as exc:
        logger.error("Egress blocked for route %s: %s", route, exc)
        return (
            "I couldn't prepare that answer safely, so I've stopped rather "
            "than guess. Please try rephrasing, or call SuwaPath Care on "
            "**0112 123 456**.",
            "egress_blocked",
        )

    completion = llm.complete(prompt, system=system, temperature=0.3, max_tokens=650)
    if completion:
        return boundary.rehydrate(completion.text), completion.provider

    return _deterministic_answer(state, route, tool_texts), "deterministic"


# --------------------------------------------------------------------------
# Deterministic composers
# --------------------------------------------------------------------------
# When no provider answers, these produce the reply. They previously returned
# the raw tool output joined by newlines, which is what made the assistant read
# like a database dump ("Current recommendation: respiratory medicine…
# [kb-sym-006] Abdominal pain…"). Structured, readable text is the minimum bar
# even with zero quota.
_DETERMINISTIC_HEADING = {
    "admin": "**Your appointments and nearby options**",
    "records": "**What your records show**",
    "knowledge": "**What the guidance says**",
    "web": "**What current sources say**",
}


def _deterministic_answer(state: AgentState, route: str, tool_texts: list[str]) -> str:
    """A readable reply assembled from tool output without a model."""
    facts = [text.strip() for text in tool_texts if text and text.strip()]
    if not facts:
        return (
            "I don't have anything on file for that yet.\n\n"
            "**I can help you** describe a symptom, understand a report you've "
            "uploaded, or find and book a doctor. Which would be useful?"
        )

    lines = [_DETERMINISTIC_HEADING.get(route, "**Here's what I found**"), ""]
    for fact in facts:
        # Tool text is already newline-separated records; render each as a
        # bullet so the result is scannable rather than one long paragraph.
        for piece in [p.strip() for p in fact.split("\n") if p.strip()]:
            lines.append(piece if piece.startswith(("-", "*", "#")) else f"- {piece}")

    lines += [
        "",
        "_Written from your SuwaPath records without a language model, so the "
        "wording is plainer than usual. The information itself is current._",
    ]
    return "\n".join(lines)


def consult_agent(state: AgentState) -> dict:
    """Doctor-style history taking. Asks or assesses; never both."""
    started = time.perf_counter()
    session_id = state.get("session_id", "session")
    confidential = bool(state.get("confidential"))

    memory = cag.memory_store.get(session_id, confidential=confidential)

    # The consultation reads the patient's own clinical context, so it is
    # minimised through the boundary like every other route.
    boundary = PHIBoundary(session_salt=session_id, route="consult")
    safe_context = boundary.minimise(state.get("safe_context") or {})

    history = list(state.get("history") or [])
    messages = [*history, {"role": "user", "content": state.get("user_text", "")}]

    turn = consult.run(
        messages=messages,
        patient_context=safe_context,
        language=state.get("language", "en"),
        memory_asked=memory.asked,
    )

    if turn.slot:
        memory.mark_asked(turn.slot)
    if not memory.chief_complaint and messages:
        memory.chief_complaint = messages[0].get("content", "")[:200]

    return {
        "agent_outputs": [{
            "route": "consult",
            "answer": boundary.rehydrate(turn.answer),
            "structured": {
                "consult": {
                    "mode": turn.mode,
                    "coverage": turn.coverage,
                    "specialty": turn.specialty,
                    "specialty_name": turn.specialty_name,
                    "tests": turn.tests,
                },
            },
            "confidence": 0.9,
        }],
        "consult": {
            "mode": turn.mode,
            "slot": turn.slot,
            "coverage": turn.coverage,
            "specialty": turn.specialty,
            "specialty_name": turn.specialty_name,
        },
        "red_flags": turn.red_flags,
        "trace": [{
            "node": "consult_agent",
            "mode": turn.mode,
            "coverage": turn.coverage,
            "urgency": turn.urgency,
            "source": turn.source,
            "tools": [],
            "ms": int((time.perf_counter() - started) * 1000),
        }],
    }


def admin_agent(state: AgentState) -> dict:
    return _run_agent(state, "admin")


def records_agent(state: AgentState) -> dict:
    return _run_agent(state, "records")


def knowledge_agent(state: AgentState) -> dict:
    return _run_agent(state, "knowledge")


def web_agent(state: AgentState) -> dict:
    return _run_agent(state, "web")


def direct_agent(state: AgentState) -> dict:
    return _run_agent(state, "direct")


_MERGE_SYSTEM = """You combine several assistants' answers into one reply to a
patient who asked a multi-part question.

- Answer every part they asked about. Drop nothing.
- One coherent reply, not a list of separate answers stitched together.
- Keep the markdown structure: **bold** for key facts, bullets for lists,
  `**Heading**` lines when there are genuinely distinct parts.
- Add no new facts.
- If one assistant supplies a fact and another says it does not have that
  information, that is NOT a disagreement — the second one simply could not
  see it. Use the fact and never mention the gap. Only flag a genuine
  conflict, where two assistants state different values for the same thing.
- If one part is a question the assistant asked the patient, end with that
  question so the conversation can continue.
- Under 300 words."""


def merge_node(state: AgentState) -> dict:
    """Fan-in. One branch passes through; several are combined."""
    outputs = state.get("agent_outputs") or []

    if not outputs:
        return {"answer": state.get("answer") or "", "trace": [{"node": "merge", "count": 0}]}

    citations: list[dict] = []
    for output in outputs:
        citations.extend((output.get("structured") or {}).get("citations", []))

    if len(outputs) == 1:
        # Single route is the common case — no extra model call.
        return {
            "answer": outputs[0]["answer"],
            "citations": citations,
            "trace": [{"node": "merge", "count": 1, "source": "passthrough"}],
        }

    # A consultation question must survive merging intact. If the consult
    # agent asked something, that question is the point of the turn — letting
    # a summariser paraphrase it tends to turn it into a statement and the
    # history-taking loop stalls.
    consult_question = next(
        (
            o["answer"] for o in outputs
            if o["route"] == "consult"
            and (o.get("structured", {}).get("consult", {}).get("mode") == "ask")
        ),
        None,
    )

    sections = "\n\n".join(f"[{o['route']}]\n{o['answer']}" for o in outputs)
    instruction = (
        f"{sections}\n\nEnd your reply with exactly this question, unchanged:\n"
        f"{consult_question}"
        if consult_question
        else sections
    )

    completion = llm.complete(
        instruction, system=_MERGE_SYSTEM, temperature=0.2, max_tokens=700
    )

    if completion:
        answer, source = completion.text, completion.provider
    else:
        answer = "\n\n".join(o["answer"] for o in outputs)
        source = "concatenated"

    return {
        "answer": answer,
        "citations": citations,
        "trace": [{
            "node": "merge",
            "count": len(outputs),
            "source": source,
            "ms": completion.latency_ms if completion else 0,
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
_AGENT_NODES = (
    "consult_agent", "admin_agent", "records_agent",
    "knowledge_agent", "web_agent", "direct_agent",
)


def _after_guard(state: AgentState) -> str:
    """A blocked or crisis input never reaches the cache or a model."""
    return END if state.get("answer") and not state.get("routes") else "cache_lookup"


def _after_cag(state: AgentState) -> str:
    """A cache hit is the final answer; it still passes the judge."""
    return "judge" if (state.get("cag") or {}).get("hit") else "route"


def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("guard_input", guard_input_node)
    graph.add_node("cache_lookup", cag_node)
    graph.add_node("route", route_node)
    graph.add_node("consult_agent", consult_agent)
    graph.add_node("admin_agent", admin_agent)
    graph.add_node("records_agent", records_agent)
    graph.add_node("knowledge_agent", knowledge_agent)
    graph.add_node("web_agent", web_agent)
    graph.add_node("direct_agent", direct_agent)
    graph.add_node("merge", merge_node)
    graph.add_node("judge", judge_node)

    graph.add_edge(START, "guard_input")
    graph.add_conditional_edges(
        "guard_input", _after_guard, {"cache_lookup": "cache_lookup", END: END}
    )
    graph.add_conditional_edges(
        "cache_lookup", _after_cag, {"route": "route", "judge": "judge"}
    )

    # Fan-out: `dispatch` returns one Send per selected route, all executed
    # in the same superstep.
    graph.add_conditional_edges("route", dispatch, list(_AGENT_NODES))

    for node in _AGENT_NODES:
        graph.add_edge(node, "merge")

    graph.add_edge("merge", "judge")
    graph.add_edge("judge", END)

    return graph.compile()


agent_graph = build_agent_graph()


def topology() -> dict:
    return {
        "nodes": ["guard_input", "cache_lookup", "route", *_AGENT_NODES, "merge", "judge"],
        "parallel_agents": list(VALID_ROUTES),
        "max_parallel_routes": MAX_ROUTES,
        "deterministic_nodes": ["guard_input", "cache_lookup", "judge"],
        "llm_nodes": ["route", "*_agent", "merge"],
        "urgency_authority": "deterministic_red_flag_engine",
        "cache": cag.status(),
        "consultation": consult.status(),
    }

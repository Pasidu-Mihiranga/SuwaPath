"""LangGraph orchestration for the SuwaPath care journey.

The graph makes the flow in spec §23 explicit and inspectable:

    route ─┬─ symptom_intake ─► (more questions?) ─► END
           │                  └─ extract ─► red_flag ─► navigate ─► match ─► END
           ├─ knowledge ─► END
           ├─ web_search ─► END
           ├─ provider/facility matching ─► match ─► END
           └─ document / image ─► handoff ─► END

Two design decisions worth stating:

1. **The red-flag node contains no LLM call.** Gemini extracts structure; the
   deterministic engine decides urgency and its verdict is written into state
   after extraction, so no downstream node can raise or lower it (internal
   rules 1 and 2). Using a graph does not loosen that — it makes the ordering
   visible as an edge.

2. **No LangGraph checkpointer.** Conversation state already lives in
   PostgreSQL (`SymptomSession` / `SymptomMessage`), which is the source of
   truth and is what the doctor's pre-consultation summary reads from. Adding a
   second persistence layer would mean two places to reconcile, so each
   invocation is hydrated from the database and the graph itself stays
   stateless.

Every node appends to `trace`, so the API can show which capabilities ran and
why — the orchestration is explainable rather than a black box.
"""

from __future__ import annotations

import logging
import warnings
from typing import Annotated, Any, TypedDict

# langgraph's checkpoint module emits a pending-deprecation warning about a
# serialiser default we never touch (no checkpointer is configured).
warnings.filterwarnings(
    "ignore", message=".*allowed_objects.*", category=Warning, module="langgraph.*"
)

from langgraph.graph import END, START, StateGraph  # noqa: E402

from app.models.enums import Language, UrgencyLevel
from app.services import ai_orchestrator as ai
from app.services.ai_orchestrator import Capability
from app.services.navigation import navigate as run_navigation
from app.services.red_flag_engine import assess_concepts, build_context

logger = logging.getLogger(__name__)


def _append(left: list | None, right: list | None) -> list:
    """Reducer so parallel/sequential nodes accumulate trace entries."""
    return (left or []) + (right or [])


class CareState(TypedDict, total=False):
    """State flowing through the graph."""

    # --- Inputs ---
    user_text: str
    language: str
    conversation: list[dict]
    patient_context: dict
    attachment_kind: str | None
    max_turns: int

    # --- Routing ---
    capability: str
    routing_rationale: str
    routing_source: str

    # --- Conversation ---
    assistant_message: str
    asked_about: list[str]
    is_complete: bool

    # --- Clinical pipeline ---
    intake: dict
    red_flags: dict
    recommendation: dict

    # --- Outputs ---
    answer: str
    citations: list[dict]
    match_request: dict
    trace: Annotated[list[dict], _append]


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------
def route_node(state: CareState) -> dict:
    """Gemini (or the keyword fallback) picks the capability for this turn."""
    decision = ai.route(
        state.get("user_text", ""),
        has_attachment=state.get("attachment_kind"),
    )
    return {
        "capability": str(decision.capability),
        "routing_rationale": decision.rationale,
        "routing_source": decision.source,
        "trace": [
            {
                "node": "route",
                "capability": str(decision.capability),
                "rationale": decision.rationale,
                "source": decision.source,
            }
        ],
    }


def intake_turn_node(state: CareState) -> dict:
    """Ask the next clinically useful follow-up question."""
    language = Language(state.get("language", "en"))
    turn = ai.next_assistant_turn(
        conversation=state.get("conversation", []),
        language=language,
        patient_context=state.get("patient_context", {}),
        max_turns=state.get("max_turns", 6),
    )
    return {
        "assistant_message": turn.message,
        "asked_about": turn.asked_about,
        "is_complete": turn.is_complete,
        "trace": [
            {
                "node": "symptom_intake",
                "source": turn.source,
                "asked_about": turn.asked_about,
                "is_complete": turn.is_complete,
            }
        ],
    }


def extract_node(state: CareState) -> dict:
    """Convert the raw conversation into structured intake fields."""
    intake = ai.extract_structured_intake(
        conversation=state.get("conversation", []),
        patient_context=state.get("patient_context", {}),
    )
    return {
        "intake": intake,
        "trace": [
            {
                "node": "extract_intake",
                "source": intake.get("extraction_source"),
                "confidence": intake.get("extraction_confidence"),
                "symptom_count": len(intake.get("symptoms", [])),
            }
        ],
    }


def red_flag_node(state: CareState) -> dict:
    """Deterministic urgency decision.

    Deliberately contains no model call. It re-derives concepts from what the
    patient actually said rather than trusting the LLM's symptom list, so a
    hallucinated or omitted symptom cannot change the care level.
    """
    from app.clinical.lexicon import extract_concepts

    conversation = state.get("conversation", [])
    patient_text = " ".join(
        m["content"] for m in conversation if m.get("role") == "patient"
    )
    concepts, negated = extract_concepts(patient_text)

    context = state.get("patient_context", {})
    assessment = assess_concepts(
        concepts,
        build_context(
            age=context.get("age"),
            sex=context.get("sex"),
            is_pregnant=bool(context.get("is_pregnant")),
            is_postpartum=bool(context.get("is_postpartum")),
            pregnancy_week=context.get("pregnancy_week"),
            chronic_conditions=context.get("chronic_conditions"),
        ),
    )
    assessment.negated_concepts = negated

    return {
        "red_flags": {
            "urgency": str(assessment.urgency),
            "triggered_rules": assessment.rules_as_dicts(),
            "escalation_message": assessment.escalation_message,
            "requires_emergency_facility": assessment.requires_emergency_facility,
            "required_capabilities": assessment.required_capabilities,
            "specialty_hints": assessment.specialty_hints,
            "concepts": sorted(concepts),
            "engine_version": assessment.engine_version,
        },
        "trace": [
            {
                "node": "red_flag_assessment",
                "source": "deterministic_rule_engine",
                "urgency": str(assessment.urgency),
                "rules_fired": [r.rule_id for r in assessment.triggered_rules],
            }
        ],
    }


def navigate_node(state: CareState) -> dict:
    """Produce the explainable care recommendation."""
    from app.services.red_flag_engine import RedFlagResult

    flags = state.get("red_flags", {})
    # Rebuild the engine result from state rather than re-running the rules.
    assessment = RedFlagResult(
        urgency=UrgencyLevel(flags.get("urgency", "routine")),
        escalation_message=flags.get("escalation_message", ""),
        requires_emergency_facility=flags.get("requires_emergency_facility", False),
        required_capabilities=flags.get("required_capabilities", []),
        specialty_hints=flags.get("specialty_hints", []),
        concepts=set(flags.get("concepts", [])),
    )
    # Preserve the fired rules so the reason text can quote them.
    from app.services.red_flag_engine import TriggeredRule

    assessment.triggered_rules = [
        TriggeredRule(
            rule_id=r["rule_id"],
            label=r["label"],
            category=r.get("category", "general"),
            urgency=UrgencyLevel(r["urgency"]),
            matched_concepts=r.get("matched_concepts", []),
            rationale=r.get("rationale", ""),
        )
        for r in flags.get("triggered_rules", [])
    ]

    intake = state.get("intake", {})
    result = run_navigation(
        red_flags=assessment,
        chief_complaint=intake.get("chief_complaint", ""),
        source="symptom",
    )

    return {
        "recommendation": {
            "care_category": result.care_category,
            "specialty_code": result.specialty_code,
            "specialty_name": result.specialty_name,
            "secondary_specialty_codes": result.secondary_specialty_codes,
            "urgency": str(result.urgency),
            "reason": result.reason,
            "suggested_next_action": result.suggested_next_action,
            "confidence": result.confidence,
            "required_capabilities": result.required_capabilities,
            "recommended_tests": result.recommended_tests,
            "patient_guidance": result.patient_guidance,
        },
        "trace": [
            {
                "node": "care_navigation",
                "source": "deterministic",
                "specialty": result.specialty_code,
                "confidence": result.confidence,
            }
        ],
    }


def match_node(state: CareState) -> dict:
    """Hand the matching engine everything it needs.

    Ranking itself runs in the API layer, which holds the database session; the
    graph stays free of persistence concerns.
    """
    recommendation = state.get("recommendation", {})
    context = state.get("patient_context", {})

    request = {
        "specialty_code": recommendation.get("specialty_code", "general_medicine"),
        "secondary_specialty_codes": recommendation.get("secondary_specialty_codes", []),
        "required_capabilities": recommendation.get("required_capabilities", []),
        "urgency": recommendation.get("urgency", "routine"),
        "patient_lat": context.get("latitude"),
        "patient_lon": context.get("longitude"),
        "patient_language": state.get("language", "en"),
    }
    return {
        "match_request": request,
        "trace": [
            {
                "node": "provider_matching",
                "source": "capability_aware_matcher",
                "specialty": request["specialty_code"],
                "required_capabilities": request["required_capabilities"],
            }
        ],
    }


def knowledge_node(state: CareState) -> dict:
    """Answer a general health question, grounded in retrieved knowledge."""
    answer, citations = ai.explain(
        state.get("user_text", ""),
        language=Language(state.get("language", "en")),
    )
    return {
        "answer": answer,
        "assistant_message": answer,
        "citations": citations,
        "is_complete": False,
        "trace": [
            {
                "node": "knowledge_retrieval",
                "source": "qdrant+minilm",
                "citations": [c["id"] for c in citations],
            }
        ],
    }


def web_search_node(state: CareState) -> dict:
    """Current public information only — never a clinical decision (§24)."""
    results = ai.web_search(state.get("user_text", ""))
    if results:
        lines = [f"- {r['title']}: {r['snippet'][:160]}" for r in results[:3]]
        answer = (
            "Here is current public information I found. This is background "
            "only and is not a clinical assessment:\n" + "\n".join(lines)
        )
    else:
        answer = (
            "I could not retrieve current external information for that. For "
            "anything affecting your care, please speak to a clinician."
        )
    return {
        "answer": answer,
        "assistant_message": answer,
        "citations": [
            {"id": r.get("url", ""), "title": r.get("title", ""), "source": "tavily"}
            for r in results
        ],
        "trace": [
            {"node": "web_search", "source": "tavily", "result_count": len(results)}
        ],
    }


def handoff_node(state: CareState) -> dict:
    """Documents and images are processed by their own upload endpoints."""
    capability = state.get("capability", "")
    message = (
        "I can read your uploaded report and explain it in plain language. "
        "Upload it under Medical Reports and I will summarise the results."
        if capability == str(Capability.DOCUMENT_UNDERSTANDING)
        else "I can screen a supported medical image. Upload it under Image "
        "Screening and I will run the analysis."
    )
    return {
        "assistant_message": message,
        "is_complete": False,
        "trace": [{"node": "handoff", "capability": capability}],
    }


# --------------------------------------------------------------------------
# Conditional edges
# --------------------------------------------------------------------------
def _after_route(state: CareState) -> str:
    capability = state.get("capability", str(Capability.SYMPTOM_INTAKE))
    return {
        str(Capability.SYMPTOM_INTAKE): "symptom_intake",
        str(Capability.KNOWLEDGE_RETRIEVAL): "knowledge",
        str(Capability.WEB_SEARCH): "web_search",
        str(Capability.PROVIDER_MATCHING): "match",
        str(Capability.FACILITY_MATCHING): "match",
        str(Capability.DOCUMENT_UNDERSTANDING): "handoff",
        str(Capability.IMAGE_SCREENING): "handoff",
    }.get(capability, "symptom_intake")


def _after_intake(state: CareState) -> str:
    """Keep asking until enough history is gathered, then run the pipeline."""
    return "extract" if state.get("is_complete") else END


# --------------------------------------------------------------------------
# Graph construction
# --------------------------------------------------------------------------
def build_care_graph():
    graph = StateGraph(CareState)

    graph.add_node("route", route_node)
    graph.add_node("symptom_intake", intake_turn_node)
    graph.add_node("extract", extract_node)
    graph.add_node("red_flag", red_flag_node)
    graph.add_node("navigate", navigate_node)
    graph.add_node("match", match_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("handoff", handoff_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _after_route,
        {
            "symptom_intake": "symptom_intake",
            "knowledge": "knowledge",
            "web_search": "web_search",
            "match": "match",
            "handoff": "handoff",
        },
    )
    graph.add_conditional_edges(
        "symptom_intake", _after_intake, {"extract": "extract", END: END}
    )

    # The clinical spine. This ordering is the safety property: extraction can
    # never skip the red-flag node, and navigation always consumes its verdict.
    graph.add_edge("extract", "red_flag")
    graph.add_edge("red_flag", "navigate")
    graph.add_edge("navigate", "match")

    graph.add_edge("match", END)
    graph.add_edge("knowledge", END)
    graph.add_edge("web_search", END)
    graph.add_edge("handoff", END)

    return graph.compile()


# Compiled once; the graph is stateless so it is safe to share.
care_graph = build_care_graph()


def run_turn(
    *,
    user_text: str,
    conversation: list[dict],
    language: str = "en",
    patient_context: dict | None = None,
    attachment_kind: str | None = None,
    max_turns: int = 6,
) -> dict:
    """Execute one turn of the care graph and return the resulting state."""
    initial: CareState = {
        "user_text": user_text,
        "conversation": conversation,
        "language": language,
        "patient_context": patient_context or {},
        "attachment_kind": attachment_kind,
        "max_turns": max_turns,
        "trace": [],
    }
    try:
        return dict(care_graph.invoke(initial))
    except Exception as exc:  # noqa: BLE001 - never fail a patient-facing turn
        logger.exception("Care graph failed: %s", exc)
        return {
            "capability": str(Capability.SYMPTOM_INTAKE),
            "assistant_message": (
                "Sorry — something went wrong while processing that. Could you "
                "tell me again what you are experiencing?"
            ),
            "is_complete": False,
            "trace": [{"node": "error", "detail": str(exc)}],
        }


def graph_topology() -> dict:
    """Describe the compiled graph, for the system-admin console."""
    return {
        "nodes": [
            "route", "symptom_intake", "extract", "red_flag",
            "navigate", "match", "knowledge", "web_search", "handoff",
        ],
        "clinical_spine": ["extract", "red_flag", "navigate", "match"],
        "llm_nodes": ["route", "symptom_intake", "extract", "knowledge"],
        "deterministic_nodes": ["red_flag", "navigate", "match"],
        "checkpointer": "none (PostgreSQL is the source of truth)",
    }

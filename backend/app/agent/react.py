"""A bounded reason-act-observe loop.

What this replaces
------------------
`fulfil_node` was a single hop: after the parallel agents merged, if the
consultation had landed on a specialty, two specific tools were called with
that specialty as input. It was real inter-agent communication and it was
honest about its limits — "one hop only, no loops, no negotiation" — but it
could only ever do the one thing its author anticipated.

The loop keeps the property that made the hop worth having (one agent's
conclusion becomes another's input) and removes the ceiling: the planner picks
the next tool from what it has already learned, and stops when it has enough.

What the planner is allowed to see
----------------------------------
**Metadata about observations, never their content.**

    {"step": 2, "tool": "find_care", "status": "ok", "n_results": 3}

Planning needs to know whether the last call returned anything, not what it
returned. That distinction is doing real work here: the PHI boundary
(`app/privacy/boundary.py`) minimises fields *per route*, and `web` — the one
route whose payload leaves the infrastructure permanently — has the tightest
allowlist in the file. A scratchpad accumulating record values across steps
would be a route-crossing leak waiting for the planner prompt to include it.

Because content never enters the planning prompt, that leak is structurally
impossible rather than merely forbidden. Content is read exactly once, by the
synthesis step, under a single route's allowlist.

The bounds, and why each one exists
-----------------------------------
- **Four steps.** Enough for consult -> find_care -> directory with one spare.
- **A twelve-second deadline**, checked between steps. Free-tier providers are
  the real latency risk: Groq answers in under a second, an OpenRouter free
  model routinely takes ten, and three sequential calls on a slow provider
  will blow any conversational budget. The deadline is what makes the worst
  case survivable.
- **A repeat-call detector.** Calling the same tool with the same arguments
  twice is the classic loop failure. It is refused with an observation saying
  so, which tells the planner something, rather than silently re-running.
- **Six tool calls total**, independent of steps, as a backstop.

Zero-key behaviour
------------------
`llm.py` has no native tool calling, so the protocol is JSON. When no provider
answers — which is a supported way to run this product — `DeterministicPlanner`
reproduces the previous behaviour exactly: the same route-to-tool table, the
same handoffs `fulfil_node` performed. **With no API keys the graph degrades to
what it did before**, so the loop cannot regress the offline path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent.tools import ROUTE_TOOLS, TOOLS, ToolDenied
from app.services import llm

logger = logging.getLogger(__name__)

MAX_STEPS = 4
MAX_TOOL_CALLS = 6
DEADLINE_MS = 12_000

# Tools the planner may choose. Deliberately not `TOOLS` itself: write actions
# live in `app/agent/actions.py` and the loop must never be able to reach them.
# Asserted in tests as `set(TOOLS) & set(ACTIONS) == set()`.
PLANNABLE = (
    "appointments",
    "find_care",
    "records",
    "medications",
    "knowledge",
    "recommendation",
    "directory",
    "web_search",
)


@dataclass
class Observation:
    """What a step produced — the metadata half, which the planner may see."""

    step: int
    tool: str
    status: str  # ok | denied | error | refused
    n_results: int = 0
    detail: str | None = None
    origin_route: str = "loop"

    def for_planner(self) -> dict:
        """Strictly metadata. No field of any record ever appears here."""
        out = {
            "step": self.step,
            "tool": self.tool,
            "status": self.status,
            "n_results": self.n_results,
        }
        if self.detail:
            out["detail"] = self.detail[:120]
        return out


@dataclass
class Scratchpad:
    observations: list[Observation] = field(default_factory=list)
    # Structured payloads, kept out of the planner's sight and merged into the
    # answer at the end.
    payloads: dict[str, Any] = field(default_factory=dict)
    called: set[str] = field(default_factory=set)
    tool_calls: int = 0

    def planner_view(self) -> list[dict]:
        return [o.for_planner() for o in self.observations]


def _signature(tool: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(f"{tool}:{canonical}".encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Planners
# --------------------------------------------------------------------------
# One line each. Without these the planner picks by name alone and reaches for
# whatever sounds vaguely relevant — which in practice meant calling the
# knowledge base repeatedly with slightly different phrasings.
TOOL_PURPOSE = {
    "find_care": "find doctors who treat a given specialty, with availability and fees",
    "directory": "find facilities that can perform a named test or procedure",
    "appointments": "read this patient's upcoming and past appointments",
    "records": "read this patient's uploaded reports and their extracted values",
    "medications": "read this patient's current medications and adherence",
    "recommendation": "read the current care recommendation already produced",
    "knowledge": "search curated clinical guidance — background, not patient data",
    "web_search": "search reputable public sources for current information",
}

PLANNER_SYSTEM = """You are planning which internal lookup to run next for a \
healthcare navigation assistant.

You will see the patient's question, what the assistant has concluded so far, \
and a log of lookups already performed with their outcomes. You do NOT see \
the contents of those lookups — only whether they returned anything. That is \
deliberate; you do not need the contents to decide what to do next.

Reply with JSON only:
  {"thought": "<one short sentence>", "action": "call_tool"|"finish",
   "tool": "<tool name>", "args": {...}}

Choose "finish" as soon as the available lookups would add nothing. Prefer \
finishing early over calling another tool speculatively. Repeating a lookup \
with slightly different wording is never useful — if a tool already returned \
results, use a different tool or finish."""


class DeterministicPlanner:
    """The previous behaviour, expressed as a plan.

    Not a stub. This is the planner whenever no model provider answers, and it
    reproduces `fulfil_node`'s two handoffs exactly — the specialty a
    consultation landed on becomes a provider search, and any suggested tests
    become a directory lookup.
    """

    name = "deterministic"

    def plan(self, context: dict, pad: Scratchpad) -> dict:
        consult = context.get("consult") or {}
        specialty = consult.get("specialty")
        assessed = consult.get("mode") in ("assess", "escalate")
        done = {o.tool for o in pad.observations}

        if assessed and specialty and "find_care" not in done:
            return {
                "thought": f"The assessment recommended {specialty}; find who provides it.",
                "action": "call_tool",
                "tool": "find_care",
                "args": {
                    "specialty_code": specialty,
                    "capabilities": (context.get("red_flags") or {}).get(
                        "required_capabilities"
                    )
                    or [],
                },
            }

        tests = context.get("suggested_tests") or []
        if assessed and tests and "directory" not in done:
            names = ", ".join(str(t.get("name", "")) for t in tests[:3])
            return {
                "thought": f"The assessment suggested {names}; find where to have them done.",
                "action": "call_tool",
                "tool": "directory",
                "args": {"question": names},
            }

        return {"thought": "Nothing further to look up.", "action": "finish"}


class LlmPlanner:
    """Chooses the next tool, seeing outcomes but never contents."""

    name = "llm"

    def __init__(self, allowed: list[str]) -> None:
        self.allowed = allowed

    def plan(self, context: dict, pad: Scratchpad) -> dict | None:
        prompt = {
            "question": context.get("user_text", "")[:400],
            "assistant_conclusion": (context.get("consult") or {}).get("summary", "")[:300],
            "specialty_identified": (context.get("consult") or {}).get("specialty"),
            "lookups_so_far": pad.planner_view(),
            "tools_available": {
                name: TOOL_PURPOSE.get(name, "") for name in self.allowed
            },
        }
        data, completion = llm.complete_json(
            json.dumps(prompt),
            system=PLANNER_SYSTEM,
            temperature=0.0,
            max_tokens=200,
            fast=True,
        )
        if not data or not completion.ok:
            return None

        action = str(data.get("action", "finish")).strip()
        if action not in ("call_tool", "finish"):
            return None
        if action == "finish":
            return {"thought": str(data.get("thought", ""))[:200], "action": "finish"}

        tool = str(data.get("tool", "")).strip()
        # The model may only name a tool it was offered. Scope filtering
        # happens before the manifest is built, so a tool the caller has no
        # right to use was never visible and cannot be named here.
        if tool not in self.allowed:
            return None

        args = data.get("args")
        return {
            "thought": str(data.get("thought", ""))[:200],
            "action": "call_tool",
            "tool": tool,
            "args": args if isinstance(args, dict) else {},
        }


def allowed_tools(scope: dict) -> list[str]:
    """Tools this caller may use, filtered before the planner sees them.

    Visibility, not just enforcement: a guardian without the medications scope
    never learns a medications tool exists, so no prompt can talk the planner
    into naming one.
    """
    permissions = set(scope.get("permissions") or [])
    role = str(scope.get("role", ""))

    allowed = []
    for tool in PLANNABLE:
        if tool in ("records", "medications") and role == "guardian":
            needed = "medical_records" if tool == "records" else "medications"
            if needed not in permissions and "full_medical" not in permissions:
                continue
        allowed.append(tool)
    return allowed


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------
def run_loop(
    *,
    context: dict,
    scope: dict,
    db,
    planner=None,
) -> tuple[Scratchpad, list[dict]]:
    """Run bounded plan-act-observe. Returns the scratchpad and trace events."""
    pad = Scratchpad()
    trace: list[dict] = []
    started = time.monotonic()

    allowed = allowed_tools(scope)
    llm_planner = LlmPlanner(allowed)
    fallback = DeterministicPlanner()

    for step in range(1, MAX_STEPS + 1):
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms > DEADLINE_MS:
            trace.append({"node": "react", "step": step, "stop": "deadline"})
            break
        if pad.tool_calls >= MAX_TOOL_CALLS:
            trace.append({"node": "react", "step": step, "stop": "tool_budget"})
            break

        decision = None
        if planner is not None:
            decision = planner.plan(context, pad)
        else:
            decision = llm_planner.plan(context, pad)
            if decision is None:
                # No provider, or an unusable reply. Fall back rather than
                # abandoning the turn — this is the zero-key path.
                decision = fallback.plan(context, pad)

        if not decision or decision.get("action") == "finish":
            trace.append({
                "node": "react",
                "step": step,
                "stop": "finished",
                "thought": (decision or {}).get("thought", ""),
            })
            break

        tool = decision.get("tool", "")
        args = decision.get("args") or {}

        if tool not in allowed or tool not in TOOLS:
            pad.observations.append(
                Observation(step, tool or "?", "refused", detail="tool not available")
            )
            continue

        signature = _signature(tool, args)
        if signature in pad.called:
            # Telling the planner it repeated itself is more useful than
            # silently running the same query again.
            pad.observations.append(
                Observation(step, tool, "refused", detail="already called with these arguments")
            )
            trace.append({"node": "react", "step": step, "tool": tool, "stop": "repeat"})
            continue
        pad.called.add(signature)

        step_started = time.perf_counter()
        try:
            _, payload = TOOLS[tool](db, scope, **args)
            pad.tool_calls += 1
            count = _result_count(payload)
            pad.payloads.setdefault(tool, {}).update(payload or {})
            pad.observations.append(
                Observation(step, tool, "ok", n_results=count, origin_route=tool)
            )
            status = "ok"
        except ToolDenied as exc:
            pad.observations.append(Observation(step, tool, "denied", detail=str(exc)))
            status = "denied"
        except TypeError as exc:
            # The planner produced arguments this tool does not take.
            pad.observations.append(
                Observation(step, tool, "error", detail=f"bad arguments: {exc}")
            )
            status = "error"
        except Exception as exc:  # noqa: BLE001 - one tool must not end the turn
            logger.warning("Loop tool %s failed: %s", tool, exc)
            pad.observations.append(Observation(step, tool, "error", detail=str(exc)))
            status = "error"

        trace.append({
            "node": "react",
            "step": step,
            "tool": tool,
            "status": status,
            "thought": decision.get("thought", ""),
            "ms": int((time.perf_counter() - step_started) * 1000),
        })

    return pad, trace


def _result_count(payload: Any) -> int:
    """How many things came back — the only quantity the planner is told."""
    if not isinstance(payload, dict):
        return 0
    for key in ("providers", "results", "appointments", "records", "medications", "passages"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 1 if payload else 0

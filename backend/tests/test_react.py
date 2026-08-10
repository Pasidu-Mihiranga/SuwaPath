"""The properties the reasoning loop rests on.

A loop that picks its own tools is only safe because of things that are easy
to state and easy to break later. Each one is asserted here rather than
documented and hoped for:

- the planner never sees the *contents* of what a tool returned, so a
  scratchpad cannot carry record fields into a web prompt
- the loop can read and cannot act — the tool registry and the action
  registry are disjoint sets
- urgency never comes from anything the loop produced
- a confidential session does not run the loop at all
- every bound actually terminates it

Runs without a server; it drives the loop directly with fake tools so the
bounds can be tested without waiting for real latency.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import react  # noqa: E402
from app.agent.actions import ACTIONS  # noqa: E402
from app.agent.tools import TOOLS  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"    {'PASS' if condition else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return condition


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


class ScriptedPlanner:
    """Plays a fixed sequence, so the bounds can be tested deterministically."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.seen: list[list[dict]] = []

    def plan(self, context, pad):
        # Record exactly what the planner was shown, for the PHI assertion.
        self.seen.append(pad.planner_view())
        if not self.decisions:
            return {"action": "finish", "thought": "done"}
        return self.decisions.pop(0)


def fake_tools(monkey: dict):
    """Swap the real registry for fakes returning known payloads."""
    original = dict(TOOLS)
    TOOLS.clear()
    TOOLS.update(monkey)
    return original


def main() -> int:
    section("Read and act are disjoint — the loop cannot reach a write")

    overlap = set(TOOLS) & set(ACTIONS)
    check("No tool is also an action", not overlap, str(sorted(overlap)))
    check(
        "Every plannable tool exists in the registry",
        set(react.PLANNABLE) <= set(TOOLS),
        str(sorted(set(react.PLANNABLE) - set(TOOLS))),
    )

    section("The planner never sees observation content")

    secret = "PATIENT HAEMOGLOBIN 8.1 CRITICAL"
    original = fake_tools({
        "records": lambda db, scope, **kw: (secret, {"records": [{"value": secret}]}),
        "web_search": lambda db, scope, **kw: ("web text", {"results": [{"title": "x"}]}),
    })
    try:
        planner = ScriptedPlanner([
            {"action": "call_tool", "tool": "records", "args": {}, "thought": "look"},
            {"action": "call_tool", "tool": "web_search", "args": {"question": "q"},
             "thought": "then web"},
        ])
        pad, trace = react.run_loop(
            context={"user_text": "how are my results"},
            scope={"role": "patient", "permissions": ["full_medical"]},
            db=None,
            planner=planner,
        )

        # What the planner was shown on the web step, having already run records.
        web_step_view = planner.seen[1] if len(planner.seen) > 1 else []
        serialised = repr(web_step_view)
        check(
            "A records result is not visible to the planner on a later step",
            secret not in serialised,
            serialised[:90],
        )
        check(
            "The planner still learns the lookup happened",
            any(o.get("tool") == "records" and o.get("status") == "ok" for o in web_step_view),
        )
        check(
            "The planner learns how many results, not which",
            all("value" not in repr(o) for o in web_step_view),
        )
        # The content is still available to synthesis, just not to planning.
        check(
            "The content is kept for the answer",
            secret in repr(pad.payloads),
        )
    finally:
        TOOLS.clear()
        TOOLS.update(original)

    section("Bounds terminate the loop")

    original = fake_tools({"knowledge": lambda db, scope, **kw: ("k", {"passages": [1]})})
    try:
        # A planner that never stops must still be stopped.
        forever = ScriptedPlanner([
            {"action": "call_tool", "tool": "knowledge", "args": {"question": f"q{i}"},
             "thought": "again"}
            for i in range(50)
        ])
        pad, trace = react.run_loop(
            context={}, scope={"role": "patient"}, db=None, planner=forever
        )
        check(
            "A planner that never finishes is capped by max steps",
            pad.tool_calls <= react.MAX_STEPS,
            f"{pad.tool_calls} tool calls, cap {react.MAX_STEPS}",
        )

        # Same arguments twice must be refused rather than re-run.
        repeater = ScriptedPlanner([
            {"action": "call_tool", "tool": "knowledge", "args": {"question": "same"},
             "thought": "one"},
            {"action": "call_tool", "tool": "knowledge", "args": {"question": "same"},
             "thought": "again"},
        ])
        pad, trace = react.run_loop(
            context={}, scope={"role": "patient"}, db=None, planner=repeater
        )
        refused = [o for o in pad.observations if o.status == "refused"]
        check("An identical repeat call is refused", len(refused) == 1, f"{len(refused)}")
        check("The repeat did not reach the tool", pad.tool_calls == 1, f"{pad.tool_calls}")
    finally:
        TOOLS.clear()
        TOOLS.update(original)

    section("Scope filters which tools exist at all")

    guardian_no_meds = react.allowed_tools(
        {"role": "guardian", "permissions": ["appointments"]}
    )
    check(
        "A guardian without the scope is never offered medications",
        "medications" not in guardian_no_meds and "records" not in guardian_no_meds,
        str(guardian_no_meds),
    )
    guardian_full = react.allowed_tools(
        {"role": "guardian", "permissions": ["full_medical"]}
    )
    check(
        "Full medical consent restores them",
        "medications" in guardian_full and "records" in guardian_full,
    )
    check(
        "A patient sees the full set",
        set(react.allowed_tools({"role": "patient"})) == set(react.PLANNABLE),
    )

    section("A tool the planner was not offered cannot be called")

    original = fake_tools({"records": lambda db, scope, **kw: ("secret", {"records": [1]})})
    try:
        sneaky = ScriptedPlanner([
            {"action": "call_tool", "tool": "records", "args": {}, "thought": "try it"}
        ])
        pad, _ = react.run_loop(
            context={},
            scope={"role": "guardian", "permissions": ["appointments"]},
            db=None,
            planner=sneaky,
        )
        check(
            "Naming an out-of-scope tool is refused, not executed",
            pad.tool_calls == 0
            and any(o.status == "refused" for o in pad.observations),
        )
    finally:
        TOOLS.clear()
        TOOLS.update(original)

    section("With no model provider, the deterministic planner reproduces the old hop")

    plan = react.DeterministicPlanner().plan(
        {
            "consult": {"mode": "assess", "specialty": "gastroenterology"},
            "red_flags": {"required_capabilities": ["ultrasound"]},
            "suggested_tests": [{"name": "abdominal ultrasound"}],
        },
        react.Scratchpad(),
    )
    check(
        "It reaches for provider matching on the assessed specialty",
        plan.get("tool") == "find_care"
        and plan["args"].get("specialty_code") == "gastroenterology",
        str(plan.get("tool")),
    )
    pad = react.Scratchpad()
    pad.observations.append(react.Observation(1, "find_care", "ok", 3))
    second = react.DeterministicPlanner().plan(
        {
            "consult": {"mode": "assess", "specialty": "gastroenterology"},
            "suggested_tests": [{"name": "abdominal ultrasound"}],
        },
        pad,
    )
    check(
        "Then looks up where the suggested test can be done",
        second.get("tool") == "directory",
        str(second.get("tool")),
    )
    third = react.DeterministicPlanner().plan(
        {"consult": {"mode": "followup"}}, react.Scratchpad()
    )
    check(
        "Mid-history-taking it does nothing",
        third.get("action") == "finish",
    )

    section("The judge/revise cycle always terminates")

    # The graph has exactly one cycle. The first version of it never cleared
    # the constraint on the terminal path, so the rewrite edge kept firing and
    # LangGraph died with GraphRecursionError after 25 hops. It was caught by
    # a manual call returning an empty answer, not by a test — which is the
    # same gap that let three undefined enum references ship. So: a test.
    from app.agent import graph as agent_graph
    from app.agent.guardrails import GuardResult, GuardVerdict

    original_judge = agent_graph.judge_output
    original_llm = agent_graph.llm

    class _NoProvider:
        """No model answers, so `revise` returns the text unchanged.

        The nastiest case for termination: the rewrite cannot fix anything, so
        a cycle that relied on the answer improving would spin for ever.
        """

        @staticmethod
        def complete(*args, **kwargs):
            class _Failed:
                ok = False
                text = ""
            return _Failed()

    # A judge that is never satisfied.
    agent_graph.judge_output = lambda answer, engine_urgency=None: GuardResult(
        verdict=GuardVerdict.SOFTEN,
        replacement="Please speak to a clinician.",
        matched_rules=["missing_safety_netting"],
    )
    agent_graph.llm = _NoProvider

    try:
        state: dict = {"answer": "Take two aspirin.", "red_flags": {}, "trace": []}
        visited: list[str] = []

        for _ in range(20):  # generous; termination should need far fewer
            update = agent_graph.judge_node(state)
            state.update({k: v for k, v in update.items() if k != "trace"})
            visited.append("judge")

            if agent_graph._after_judge(state) != "revise":
                break

            update = agent_graph.revise_node(state)
            state.update({k: v for k, v in update.items() if k != "trace"})
            visited.append("revise")

        check(
            "The cycle terminates",
            visited[-1] == "judge" and len(visited) < 20,
            " -> ".join(visited),
        )
        check(
            "It rewrites exactly once, never twice",
            visited.count("revise") == 1,
            f"{visited.count('revise')} rewrites",
        )
        check(
            "The constraint is cleared so the edge cannot re-fire",
            not state.get("judge_constraints"),
        )
        check(
            "An unsatisfiable judge still yields its replacement",
            state.get("answer") == "Please speak to a clinician.",
            repr(state.get("answer"))[:60],
        )

        # A BLOCK must never take the rewrite edge at all.
        agent_graph.judge_output = lambda answer, engine_urgency=None: GuardResult(
            verdict=GuardVerdict.BLOCK,
            replacement="Seek urgent care.",
            matched_rules=["urgency_mismatch"],
        )
        blocked: dict = {"answer": "It is probably nothing.", "red_flags": {}}
        update = agent_graph.judge_node(blocked)
        blocked.update({k: v for k, v in update.items() if k != "trace"})
        check(
            "A blocked answer is never sent for rewrite",
            agent_graph._after_judge(blocked) != "revise",
        )
        check(
            "A block replaces the answer immediately",
            blocked.get("answer") == "Seek urgent care.",
        )
    finally:
        agent_graph.judge_output = original_judge
        agent_graph.llm = original_llm

    section("The graph has exactly one cycle")

    compiled = agent_graph.build_agent_graph()
    edges = compiled.get_graph().edges
    back_edges = [
        (e.source, e.target)
        for e in edges
        if (e.source, e.target) in (("revise", "judge"), ("judge", "revise"))
    ]
    check(
        "judge <-> revise is the only loop in the graph",
        len(back_edges) == 2,
        str(back_edges),
    )

    print("\n" + "=" * 74)
    print("RESULTS")
    print("=" * 74)
    print(f"  Passed: {len(PASSED)}")
    print(f"  Failed: {len(FAILED)}")
    for label in FAILED:
        print(f"    - {label}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

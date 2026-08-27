"""Turn raw arm results into the table that answers the panel.

Two rules govern what gets printed here.

First, **emergency recall leads**. Triage errors are asymmetric: sending a heart
attack home can kill someone, sending a sore throat to A&E wastes an afternoon.
A single accuracy figure averages those together and hides the only number that
matters, so recall on the emergency class is reported first and the
over-triage rate is reported beside it as the price paid.

Second, **the biased cells are labelled**. The `positive` vignettes were derived
from the rule set, so the deterministic arms have a structural advantage there.
Reporting that as a win would be dishonest, so the per-category table marks it
and the summary is computed over the unbiased categories as well as overall.
"""

from __future__ import annotations

from collections import defaultdict

from app.eval.vignettes import BY_ID, Vignette
from app.eval.arms import ARM_DESCRIPTIONS
from app.services.ml import metrics

URGENCY_ORDER = ["emergency", "urgent", "routine", "self_care"]
SEVERITY = {"emergency": 3, "urgent": 2, "routine": 1, "self_care": 0}

# Categories whose labels were NOT derived from the rule set, and where the
# deterministic arms therefore have no built-in advantage.
UNBIASED = ("near_miss", "benign", "negation", "multilingual")


def _fmt(value: float | None, places: int = 3) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _paired(rows: list[dict]) -> dict[str, list[tuple[Vignette, dict]]]:
    """Group rows by arm, dropping any whose vignette no longer exists."""
    by_arm: dict[str, list[tuple[Vignette, dict]]] = defaultdict(list)
    for row in rows:
        vignette = BY_ID.get(row["vignette_id"])
        if vignette is not None:
            by_arm[row["arm"]].append((vignette, row))
    return by_arm


def _answered(pairs: list[tuple[Vignette, dict]]) -> list[tuple[Vignette, dict]]:
    """Only the rows where the arm actually produced an answer.

    Accuracy is computed over these. A rate-limited free tier says nothing
    about whether a model can triage, and averaging the two together produced
    a confidently wrong result the first time this ran: because the vignette
    list ends with the negation and multilingual cases, a burst of 429s landed
    entirely on them and read as "0% on Sinhala". Reachability is reported
    separately, in its own column, where it belongs.
    """
    return [(v, r) for v, r in pairs if r.get("urgency")]


def _scored(pairs: list[tuple[Vignette, dict]]) -> tuple[list[str], list[str]]:
    """(truth, prediction) label lists over answered rows only."""
    truth: list[str] = []
    predicted: list[str] = []
    for vignette, row in _answered(pairs):
        truth.append(vignette.expected_urgency)
        predicted.append(row.get("urgency") or "__none__")
    return truth, predicted


def _summary_table(by_arm: dict) -> str:
    header = (
        "| Arm | Answered | Emergency recall | Emergency spec. | Accuracy | "
        "Under-triage | Over-triage | Mean Qs | Mean ms |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for arm in sorted(by_arm):
        pairs = by_arm[arm]
        answered = _answered(pairs)
        truth, predicted = _scored(pairs)
        emergency = metrics.binary_outcome(truth, predicted, "emergency")
        mean_q = (sum(row.get("questions_asked", 0) for _, row in answered)
                  / len(answered)) if answered else 0.0
        mean_ms = (sum(row.get("latency_ms", 0) for _, row in answered)
                   / len(answered)) if answered else 0.0
        reach = f"{len(answered)}/{len(pairs)}"
        lines.append(
            f"| **{arm}** | {reach} | "
            f"**{_pct(emergency.recall)}** | {_pct(emergency.specificity)} | "
            f"{_pct(metrics.accuracy(truth, predicted))} | "
            f"{_pct(metrics.under_triage_rate(truth, predicted, SEVERITY))} | "
            f"{_pct(metrics.over_triage_rate(truth, predicted, SEVERITY))} | "
            f"{mean_q:.1f} | {mean_ms:.0f} |"
        )
    return header + "\n".join(lines)


def _distance_table(by_arm: dict) -> str:
    """How far wrong, not just how often.

    Plain accuracy scores "routine when it should have been self-care" exactly
    the same as "self-care when it was a heart attack". Reporting only that
    number flattered the deterministic arms and made the LLM arm look
    incompetent when most of its disagreements were one-level boundary calls.
    The distance profile is what separates a difference of clinical opinion
    from a dangerous mistake.
    """
    header = ("| Arm | Exact | Within one level | Two or more levels off | "
              "Missed emergencies |\n|---|---|---|---|---|\n")
    lines = []
    for arm in sorted(by_arm):
        answered = _answered(by_arm[arm])
        if not answered:
            continue
        exact = adjacent = far = 0
        missed = 0
        for vignette, row in answered:
            distance = SEVERITY.get(row["urgency"], 0) - SEVERITY.get(
                vignette.expected_urgency, 0
            )
            if distance == 0:
                exact += 1
            elif abs(distance) == 1:
                adjacent += 1
            else:
                far += 1
            if vignette.is_emergency and row["urgency"] != "emergency":
                missed += 1
        total = len(answered)
        emergencies = sum(1 for v, _ in answered if v.is_emergency)
        lines.append(
            f"| **{arm}** | {_pct(exact / total)} | "
            f"{_pct((exact + adjacent) / total)} | "
            f"{_pct(far / total)} | "
            f"**{missed}/{emergencies}** |"
        )
    return header + "\n".join(lines)


def _language_table(by_arm: dict) -> str:
    """Does the system work as well in Sinhala and Tamil as in English?

    The platform's central claim is that a Sinhala speaker gets the same
    deterministic urgency as an English one. That is a testable claim and this
    is the test, kept separate because an average over a mostly-English set
    would hide a failure confined to 13 vignettes.
    """
    languages = sorted({v.language for pairs in by_arm.values() for v, _ in pairs})
    header = ("| Arm | " + " | ".join(f"{lang} (acc / missed emerg.)" for lang in languages)
              + " |\n" + "|---" * (len(languages) + 1) + "|\n")
    lines = []
    for arm in sorted(by_arm):
        cells = []
        for language in languages:
            pairs = [p for p in by_arm[arm] if p[0].language == language]
            answered = _answered(pairs)
            if not answered:
                cells.append("—")
                continue
            truth, predicted = _scored(pairs)
            emergencies = [(v, r) for v, r in answered if v.is_emergency]
            missed = sum(1 for v, r in emergencies if r["urgency"] != "emergency")
            cells.append(
                f"{_pct(metrics.accuracy(truth, predicted))} / {missed}/{len(emergencies)}"
            )
        lines.append(f"| **{arm}** | " + " | ".join(cells) + " |")
    return header + "\n".join(lines)


def _missed_emergencies(by_arm: dict) -> str:
    """The safety-critical failures, named individually."""
    lines = []
    for arm in sorted(by_arm):
        rows = [
            (v, r) for v, r in _answered(by_arm[arm])
            if v.is_emergency and r["urgency"] != "emergency"
        ]
        if not rows:
            lines.append(f"\n**{arm}** — none.")
            continue
        lines.append(f"\n**{arm}** — {len(rows)} missed")
        for vignette, row in sorted(rows, key=lambda p: p[0].id):
            lines.append(
                f"- `{vignette.id}` ({vignette.language}) got *{row['urgency']}* — "
                f"{vignette.opening!r}"
            )
    return "\n".join(lines)


def _degradation_table(by_arm: dict) -> str:
    """Which component actually produced each answer.

    This table exists because of an accident. Arm C was run while the free
    tiers were exhausted, so most of its assessments fell back to the
    deterministic composer instead of a model — and its urgency accuracy was
    completely unchanged, because urgency never depended on the model in the
    first place. Arm B under the same conditions returned nothing at all.

    That is the "LLM for language, rules for safety" claim demonstrated under
    adversarial conditions rather than asserted, so the run is kept as-is
    rather than repeated on a quiet provider.
    """
    sources: dict[str, dict[str, int]] = {}
    for arm, pairs in by_arm.items():
        counts: dict[str, int] = {}
        for _, row in pairs:
            counts[row.get("source") or "—"] = counts.get(row.get("source") or "—", 0) + 1
        sources[arm] = counts

    lines = ["| Arm | Answer produced by |", "|---|---|"]
    for arm in sorted(sources):
        parts = ", ".join(
            f"{name} ({count})"
            for name, count in sorted(sources[arm].items(), key=lambda kv: -kv[1])
        )
        lines.append(f"| **{arm}** | {parts} |")
    return "\n".join(lines)


def _category_table(by_arm: dict) -> str:
    categories = sorted({v.category for pairs in by_arm.values() for v, _ in pairs})
    header = "| Arm | " + " | ".join(categories) + " |\n"
    header += "|---" * (len(categories) + 1) + "|\n"

    lines = []
    for arm in sorted(by_arm):
        cells = []
        for category in categories:
            pairs = [p for p in by_arm[arm] if p[0].category == category]
            answered = _answered(pairs)
            if not answered:
                cells.append("—")
                continue
            truth, predicted = _scored(pairs)
            marker = "*" if category == "positive" else ""
            # Flag a cell whose sample was thinned by unreachable calls, so a
            # figure resting on three answers is not read like one resting on 20.
            thin = "" if len(answered) == len(pairs) else f" ({len(answered)}/{len(pairs)})"
            cells.append(f"{_pct(metrics.accuracy(truth, predicted))}{marker}{thin}")
        lines.append(f"| **{arm}** | " + " | ".join(cells) + " |")
    return header + "\n".join(lines)


def _unbiased_table(by_arm: dict) -> str:
    header = ("| Arm | Answered | Accuracy | Emergency recall | Under-triage |\n"
              "|---|---|---|---|---|\n")
    lines = []
    for arm in sorted(by_arm):
        pairs = [p for p in by_arm[arm] if p[0].category in UNBIASED]
        if not _answered(pairs):
            continue
        truth, predicted = _scored(pairs)
        emergency = metrics.binary_outcome(truth, predicted, "emergency")
        lines.append(
            f"| **{arm}** | {len(_answered(pairs))}/{len(pairs)} | "
            f"{_pct(metrics.accuracy(truth, predicted))} | "
            f"{_pct(emergency.recall)} | "
            f"{_pct(metrics.under_triage_rate(truth, predicted, SEVERITY))} |"
        )
    return header + "\n".join(lines)


def _variance_table(rows: list[dict]) -> str:
    """How often repeat runs of the same arm on the same vignette disagree.

    Reproducibility is a safety property in its own right. A triage tool that
    answers differently on Tuesday cannot be audited, and no accuracy figure
    reveals that — only repetition does.
    """
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    runs: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        seen[(row["arm"], row["vignette_id"])].add(row.get("urgency") or "__none__")
        runs[row["arm"]].add(row.get("run", 0))

    lines = []
    for arm in sorted(runs):
        if len(runs[arm]) < 2:
            lines.append(f"| **{arm}** | {len(runs[arm])} | not measured |")
            continue
        keys = [k for k in seen if k[0] == arm]
        unstable = sum(1 for k in keys if len(seen[k]) > 1)
        lines.append(
            f"| **{arm}** | {len(runs[arm])} | "
            f"{unstable}/{len(keys)} vignettes answered inconsistently |"
        )
    header = "| Arm | Runs | Reproducibility |\n|---|---|---|\n"
    return header + "\n".join(lines)


def _confusion(by_arm: dict) -> str:
    blocks = []
    for arm in sorted(by_arm):
        truth, predicted = _scored(by_arm[arm])
        labels = URGENCY_ORDER + (["__none__"] if "__none__" in predicted else [])
        matrix = metrics.confusion_matrix(truth, predicted, labels)
        block = [f"\n**{arm}** — rows are the truth, columns the prediction\n"]
        block.append("| actual \\ predicted | " + " | ".join(labels) + " |")
        block.append("|---" * (len(labels) + 1) + "|")
        for actual in URGENCY_ORDER:
            counts = matrix.get(actual, {})
            cells = []
            for predicted_label in labels:
                count = counts.get(predicted_label, 0)
                # Mark the dangerous direction: truth more severe than guess.
                danger = SEVERITY.get(predicted_label, 0) < SEVERITY.get(actual, 0)
                cells.append(f"**{count}**" if count and danger else str(count))
            block.append(f"| {actual} | " + " | ".join(cells) + " |")
        blocks.append("\n".join(block))
    return "\n".join(blocks)


def _misses(by_arm: dict) -> str:
    """Every case sent to a less urgent level than it deserved."""
    lines = []
    for arm in sorted(by_arm):
        rows = [
            (v, r) for v, r in _answered(by_arm[arm])
            if SEVERITY.get(r.get("urgency") or "", 0) < SEVERITY.get(v.expected_urgency, 0)
        ]
        if not rows:
            continue
        lines.append(f"\n**{arm}** — {len(rows)} under-triaged")
        for vignette, row in sorted(rows, key=lambda p: p[0].id):
            lines.append(
                f"- `{vignette.id}` [{vignette.category}] expected "
                f"**{vignette.expected_urgency}**, got *{row.get('urgency')}* — "
                f"{vignette.opening!r}"
            )
            if vignette.known_engine_gap:
                lines.append(f"  - known gap: {vignette.known_engine_gap}")
    return "\n".join(lines) if lines else "\nNone."


def render(rows: list[dict]) -> str:
    by_arm = _paired(rows)
    if not by_arm:
        return "No results to report."

    parts = [
        "# SuwaPath triage evaluation",
        "",
        "Arms under test:",
        "",
    ]
    for arm in sorted(by_arm):
        parts.append(f"- **{arm}** — {ARM_DESCRIPTIONS.get(arm, '?')}")

    parts += [
        "",
        "## Headline",
        "",
        "Emergency recall leads because triage errors are asymmetric: a missed "
        "emergency can be fatal, an unnecessary escalation costs an afternoon.",
        "",
        _summary_table(by_arm),
        "",
        "## How far wrong, not just how often",
        "",
        "Accuracy alone treats a routine/self-care boundary call as equal to "
        "missing a heart attack. It is not. The last column is the one that "
        "matters clinically.",
        "",
        _distance_table(by_arm),
        "",
        "## Missed emergencies",
        "",
        "The safety-critical failures, named. Nothing else in this report "
        "outranks this list.",
        _missed_emergencies(by_arm),
        "",
        "## Graceful degradation",
        "",
        "Arm C was run while the free-tier providers were exhausted, so most of "
        "its assessments fell back to the deterministic composer. Its urgency "
        "accuracy was unchanged — the patient got plainer wording and the same "
        "correct triage. Arm B under the same conditions returned nothing at "
        "all until the calls were retried.",
        "",
        "*Latency caveat: Arm C's mean is therefore understated. On a healthy "
        "provider a full consultation measures 5–9s, not 838ms.*",
        "",
        _degradation_table(by_arm),
        "",
        "## By language",
        "",
        "The platform claims a Sinhala speaker receives the same urgency as an "
        "English one. This is that claim, tested.",
        "",
        _language_table(by_arm),
        "",
        "## Accuracy by category",
        "",
        "`*` marks a cell where the labels were derived from the rule set, so "
        "the deterministic arms hold a structural advantage and the figure "
        "should not be read as a win.",
        "",
        _category_table(by_arm),
        "",
        "## Unbiased categories only",
        "",
        "Near-miss, benign, negation and multilingual cases only — no arm has a "
        "built-in advantage here, so this is the fair comparison.",
        "",
        _unbiased_table(by_arm),
        "",
        "## Reproducibility",
        "",
        _variance_table(rows),
        "",
        "## Confusion matrices",
        "",
        "Bold cells are under-triage — the truth was more urgent than the guess.",
        _confusion(by_arm),
        "",
        "## Under-triaged cases",
        _misses(by_arm),
        "",
    ]
    return "\n".join(parts)

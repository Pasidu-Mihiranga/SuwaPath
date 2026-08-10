"""Measuring whether a probabilistic model is any good.

Nothing in this codebase measured that. The no-show model has been fitted,
scored, banded, persisted and rendered on a dashboard since it was written,
and no line of code has ever asked whether its probabilities are right. A
number on a screen with no accuracy behind it is the "decorative ML" criticism
in its purest form, and it is answered by measurement rather than by adding a
fourth model.

Pure numpy, no scikit-learn. Not stubbornness: sklearn drags scipy — roughly
125 MB onto an image already near 800 MB — and would loosen a dependency set
that fastembed already constrains tightly, with Pillow and onnxruntime
deliberately left as ranges so pip can find one working combination. Every
function here is between five and twenty lines, and the bottleneck was never
the optimiser.

Three of these measure different things and are all worth having:

- **AUC** — ranking. Does the model put the people who missed above the people
  who came? Insensitive to calibration, so a model can score well here and
  still be badly wrong about absolute risk.
- **Brier / ECE** — calibration. When it says 30%, do 30% of them actually
  miss? This is what matters for a threshold, and it is the one AUC hides.
- **PR-AUC** — performance on the minority class, which for no-shows at a 20%
  base rate is the class anyone cares about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Evaluation:
    n: int = 0
    positives: int = 0
    base_rate: float = 0.0
    auc: float | None = None
    pr_auc: float | None = None
    brier: float | None = None
    ece: float | None = None
    calibration: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "positives": self.positives,
            "base_rate": round(self.base_rate, 4),
            "auc": round(self.auc, 4) if self.auc is not None else None,
            "pr_auc": round(self.pr_auc, 4) if self.pr_auc is not None else None,
            "brier": round(self.brier, 4) if self.brier is not None else None,
            "ece": round(self.ece, 4) if self.ece is not None else None,
            "calibration": self.calibration,
        }


def _clean(y_true, y_score) -> tuple[np.ndarray, np.ndarray]:
    truth = np.asarray(y_true, dtype=np.float64).ravel()
    score = np.asarray(y_score, dtype=np.float64).ravel()
    keep = np.isfinite(truth) & np.isfinite(score)
    return truth[keep], score[keep]


def roc_auc(y_true, y_score) -> float | None:
    """AUC via the rank statistic — no scipy, and ties handled correctly.

    AUC is the probability that a randomly chosen positive outranks a randomly
    chosen negative, which is exactly the normalised Mann-Whitney U. Averaging
    ranks across ties is what makes a model that outputs the same probability
    for everyone score 0.5 rather than something arbitrary.
    """
    truth, score = _clean(y_true, y_score)
    positives = truth.sum()
    negatives = len(truth) - positives
    if positives == 0 or negatives == 0:
        return None  # undefined with only one class present

    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1, dtype=np.float64)

    # Tie correction: every tied group takes the mean of its ranks.
    sorted_scores = score[order]
    start = 0
    for index in range(1, len(sorted_scores) + 1):
        if index == len(sorted_scores) or sorted_scores[index] != sorted_scores[start]:
            if index - start > 1:
                ranks[order[start:index]] = ranks[order[start:index]].mean()
            start = index

    rank_sum = ranks[truth == 1].sum()
    return float((rank_sum - positives * (positives + 1) / 2) / (positives * negatives))


def pr_auc(y_true, y_score) -> float | None:
    """Average precision — the area under precision/recall by step summation."""
    truth, score = _clean(y_true, y_score)
    if truth.sum() == 0:
        return None

    order = np.argsort(-score, kind="mergesort")
    truth = truth[order]
    true_positives = np.cumsum(truth)
    precision = true_positives / np.arange(1, len(truth) + 1)
    recall = true_positives / truth.sum()

    # Sum precision at each point where recall increases: the standard
    # average-precision estimator, which does not interpolate optimistically.
    gains = np.diff(np.concatenate([[0.0], recall]))
    return float((precision * gains).sum())


def brier(y_true, y_score) -> float | None:
    """Mean squared error of the probabilities. Lower is better; 0.25 is a coin."""
    truth, score = _clean(y_true, y_score)
    if len(truth) == 0:
        return None
    return float(np.mean((score - truth) ** 2))


def calibration_curve(y_true, y_score, bins: int = 10) -> list[dict]:
    """Predicted versus observed frequency, in equal-width probability bins."""
    truth, score = _clean(y_true, y_score)
    if len(truth) == 0:
        return []

    edges = np.linspace(0.0, 1.0, bins + 1)
    out = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        # Closed on the right for the final bin so p == 1.0 is counted.
        mask = (score >= lower) & (score < upper)
        if upper == 1.0:
            mask |= score == 1.0
        count = int(mask.sum())
        if count == 0:
            continue
        out.append({
            "lower": round(float(lower), 2),
            "upper": round(float(upper), 2),
            "n": count,
            "predicted": round(float(score[mask].mean()), 4),
            "observed": round(float(truth[mask].mean()), 4),
        })
    return out


def expected_calibration_error(y_true, y_score, bins: int = 10) -> float | None:
    """Weighted mean gap between predicted and observed frequency.

    The number to quote alongside AUC. A model can rank perfectly and still
    claim 30% for people who miss 60% of the time, and only this notices.
    """
    curve = calibration_curve(y_true, y_score, bins=bins)
    if not curve:
        return None
    total = sum(b["n"] for b in curve)
    return float(
        sum(b["n"] * abs(b["predicted"] - b["observed"]) for b in curve) / total
    )


def evaluate(y_true, y_score, *, bins: int = 10) -> Evaluation:
    """Everything at once, for a dashboard card or a report table."""
    truth, score = _clean(y_true, y_score)
    if len(truth) == 0:
        return Evaluation()
    return Evaluation(
        n=len(truth),
        positives=int(truth.sum()),
        base_rate=float(truth.mean()),
        auc=roc_auc(truth, score),
        pr_auc=pr_auc(truth, score),
        brier=brier(truth, score),
        ece=expected_calibration_error(truth, score, bins=bins),
        calibration=calibration_curve(truth, score, bins=bins),
    )


def time_split(rows: list, *, key, holdout: float = 0.25) -> tuple[list, list]:
    """Split chronologically, never randomly.

    A random split on appointment data leaks: the same patient appears on both
    sides, and their prior-no-show-rate feature then carries information from
    the future. Sorting by time and cutting once is the only honest split for
    a model whose features are built from history.
    """
    ordered = sorted(rows, key=key)
    if len(ordered) < 4:
        return ordered, []
    cut = int(len(ordered) * (1 - holdout))
    return ordered[:cut], ordered[cut:]

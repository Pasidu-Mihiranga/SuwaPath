"""Choose a screening model's operating point from a labelled test set.

    python scripts/calibrate_pneumonia.py \
        --variant general --data /path/to/test_set --min-recall 0.95

    test_set/
      normal/     *.png|jpg      # label 0
      pneumonia/  *.png|jpg      # label 1

Why a threshold is not a detail
-------------------------------
A classifier outputs a probability; a *decision* needs a cut-off, and where
that cut-off sits is the clinical safety choice. It is not a property of the
architecture and it does not transfer between datasets — the same ResNet50
tuned for 95% sensitivity lands near 0.17 on one test set and 0.37 on another.
Shipping 0.5 because it is the midpoint is choosing an operating point by
accident.

For a *screening* tool the errors are not symmetric. Missing a pneumonia sends
someone home; a false positive sends them for a chest X-ray they did not need.
So the threshold is chosen to meet a **minimum sensitivity**, and the
specificity that results is reported as its price — the same asymmetry the
triage evaluation harness applies to urgency.

What regulators and reporting standards expect, and what this writes
--------------------------------------------------------------------
FDA/MDR submissions and the STARD and CLAIM reporting checklists all converge
on the same disclosures for a diagnostic aid, and each maps to a field here:

  * the intended use and the operating point         -> operating_point
  * sensitivity and specificity **with confidence
    intervals**, not point estimates                 -> test_metrics
  * the prevalence of the test set, because PPV and
    NPV are meaningless without it                   -> test_metrics.prevalence
  * a described, held-out dataset                    -> training/test_set
  * an indeterminate band where the model declines
    to call it                                       -> uncertainty_band

Wilson intervals are used rather than the normal approximation: at the
sensitivities that matter here the counts are small and skewed, and the normal
interval misbehaves badly near 1.0 — it can produce an upper bound above 100%.

This does not make the model approved for clinical use. It makes its
behaviour *stated* instead of assumed, which is the precondition for anyone
being able to review it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

MODELS = REPO_ROOT / "models" / "pneumonia"
SUFFIXES = {".png", ".jpg", ".jpeg"}


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — well behaved for small n and proportions near 1."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def load_labels(root: Path) -> list[tuple[Path, int]]:
    rows: list[tuple[Path, int]] = []
    for label, folder in ((0, "normal"), (1, "pneumonia")):
        directory = root / folder
        if not directory.is_dir():
            raise SystemExit(f"Expected {directory} to exist.")
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in SUFFIXES:
                rows.append((path, label))
    if not rows:
        raise SystemExit(f"No images found under {root}.")
    return rows


def score_all(variant: str, rows: list[tuple[Path, int]]) -> tuple[list[float], list[int]]:
    """Probability of pneumonia for each image, using the shipped adapter.

    Scored through `OnnxPneumoniaAdapter`, not a separate inference path, so
    the calibration measures the preprocessing the API actually applies. A
    threshold measured against different resizing or normalisation is a
    threshold for a model that is not deployed.
    """
    from app.services.vision.base import load_grayscale
    from app.services.vision.pneumonia import OnnxPneumoniaAdapter

    adapter = OnnxPneumoniaAdapter(variant)
    if not adapter.is_available():
        raise SystemExit(f"No model for variant {variant!r} in {MODELS}.")

    scores: list[float] = []
    labels: list[int] = []
    for index, (path, label) in enumerate(rows, start=1):
        result = adapter.predict(load_grayscale(path), heatmap_path=None)
        probabilities = result.class_probabilities or {}
        scores.append(float(probabilities.get("pneumonia", 0.0)))
        labels.append(label)
        if index % 25 == 0 or index == len(rows):
            print(f"  scored {index}/{len(rows)}", flush=True)
    return scores, labels


def at_threshold(scores: list[float], labels: list[int], threshold: float) -> dict:
    tp = sum(1 for s, y in zip(scores, labels) if y == 1 and s >= threshold)
    fn = sum(1 for s, y in zip(scores, labels) if y == 1 and s < threshold)
    tn = sum(1 for s, y in zip(scores, labels) if y == 0 and s < threshold)
    fp = sum(1 for s, y in zip(scores, labels) if y == 0 and s >= threshold)

    positives, negatives = tp + fn, tn + fp
    sensitivity = tp / positives if positives else 0.0
    specificity = tn / negatives if negatives else 0.0
    return {
        "threshold": round(threshold, 4),
        "sensitivity": round(sensitivity, 4),
        "sensitivity_ci95": [round(v, 4) for v in wilson(tp, positives)],
        "specificity": round(specificity, 4),
        "specificity_ci95": [round(v, 4) for v in wilson(tn, negatives)],
        "ppv": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "npv": round(tn / (tn + fn), 4) if (tn + fn) else None,
        "counts": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument(
        "--min-recall", type=float, default=0.95,
        help="Minimum sensitivity the operating point must achieve. The "
             "threshold chosen is the highest one still meeting it, which is "
             "the one with the best specificity among acceptable options.",
    )
    parser.add_argument("--dataset-name", default="")
    parser.add_argument("--write", action="store_true", help="update the sidecar")
    args = parser.parse_args()

    rows = load_labels(args.data)
    positives = sum(1 for _, y in rows if y == 1)
    print(f"{len(rows)} images — {positives} pneumonia, {len(rows) - positives} normal")

    scores, labels = score_all(args.variant, rows)

    from app.services.ml.metrics import roc_auc, pr_auc, brier

    # Sweep every distinct score: the optimum always sits at an observed
    # value, so a fixed grid can only miss it.
    candidates = sorted({round(s, 4) for s in scores})
    acceptable = [
        point for point in (at_threshold(scores, labels, t) for t in candidates)
        if point["sensitivity"] >= args.min_recall
    ]
    if not acceptable:
        best = max(
            (at_threshold(scores, labels, t) for t in candidates),
            key=lambda p: p["sensitivity"],
        )
        print(
            f"\nNo threshold reaches {args.min_recall:.0%} sensitivity on this "
            f"set. The best available is {best['sensitivity']:.1%} at "
            f"{best['threshold']}. That is a finding about the model, not a "
            f"reason to lower the target quietly."
        )
        return 1

    chosen = max(acceptable, key=lambda p: p["specificity"])
    prevalence = positives / len(rows)

    print(f"\nOperating point at >= {args.min_recall:.0%} sensitivity")
    print(f"  threshold   : {chosen['threshold']}")
    print(f"  sensitivity : {chosen['sensitivity']:.1%}  95% CI {chosen['sensitivity_ci95']}")
    print(f"  specificity : {chosen['specificity']:.1%}  95% CI {chosen['specificity_ci95']}")
    print(f"  PPV / NPV   : {chosen['ppv']} / {chosen['npv']}  (prevalence {prevalence:.1%})")
    print(f"  AUC         : {roc_auc(labels, scores):.4f}")

    if not args.write:
        print("\nRe-run with --write to update the sidecar.")
        return 0

    sidecar = MODELS / f"{args.variant}.json"
    meta = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
    meta["operating_point"] = {
        "threshold": chosen["threshold"],
        "policy": f"highest threshold meeting >= {args.min_recall:.0%} sensitivity",
        "min_recall_target": args.min_recall,
        "tta": False,
    }
    # An indeterminate band around the cut-off. Scores landing inside it are
    # reported as uncertain rather than called either way, because a decision
    # taken one point either side of the threshold is not a decision the data
    # supports.
    meta["uncertainty_band"] = {
        "lower": round(max(0.0, chosen["threshold"] - 0.10), 4),
        "upper": round(min(1.0, chosen["threshold"] + 0.10), 4),
    }
    meta["test_metrics"] = {
        **{k: v for k, v in chosen.items() if k != "threshold"},
        "prevalence": round(prevalence, 4),
        "n": len(rows),
        "auc": round(roc_auc(labels, scores), 4),
        "pr_auc": round(pr_auc(labels, scores), 4),
        "brier": round(brier(labels, scores), 4),
    }
    meta.setdefault("training", {})["test_set"] = args.dataset_name or str(args.data)
    meta["calibrated_at"] = datetime.now(timezone.utc).isoformat()
    sidecar.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"\nWrote {sidecar.name}. Restart the API to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""What the chest X-ray screener will and will not agree to look at.

Why this suite exists
---------------------
A patient uploaded a full-colour marketing illustration — two people and some
UI icons — to the X-ray screener. It was accepted, classified, and returned as
"No acute abnormality detected, 78% confidence", with a saliency heatmap
highlighting the doctor's hands. Everything downstream behaved correctly; the
gate in front of it simply let the image through.

Two things had gone wrong. `_validate_chest_xray` ran only on the grayscale
array, so **colour — the single strongest evidence that something is not a
radiograph — was discarded by `convert("L")` before any check could see it**.
And the remaining tonal checks were tuned loosely enough that the illustration
cleared them by narrow margins (mean 0.891 against a 0.93 cutoff, extremes
0.61 against 0.75).

There was no test here at all, which is why neither was noticed. The one
negative sample that did exist, `not_an_xray.png`, is a grayscale blank page —
it passes trivially and gave false confidence in the gate.

The case that must not regress
------------------------------
Rejecting colour is only safe if it does not also reject a *photograph of a
film*, which is a legitimate and common upload in Sri Lanka where patients are
handed physical films. Such a photo carries a colour cast from tungsten or
fluorescent lighting, and measured on real files that cast reaches the same
saturation level as a genuinely colourful illustration. So the tests below
include simulated casts across the plausible range, and they must all pass.
The check that distinguishes them is not how colourful the image is but
whether the colour is uniform (a cast) or localised (real content).

Needs no server, no database and no model weights — the validator is a pure
function over pixels.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.vision.base import load_grayscale, load_rgb  # noqa: E402
from app.services.vision.pneumonia import (  # noqa: E402
    MAX_NEAR_WHITE_FRACTION,
    MAX_RESIDUAL_SATURATION,
    _residual_saturation,
    _validate_chest_xray,
)

SAMPLES = ROOT / "storage" / "samples"
XRAY = SAMPLES / "chest_xray_pneumonia.png"

# Channel gains standing in for a photograph of a film under real lighting.
# The last is deliberately worse than anything a phone would produce.
LIGHTING_CASTS = {
    "no cast (digital export)": (1.00, 1.00, 1.00),
    "mild warm, indoor": (1.06, 1.00, 0.94),
    "fluorescent green": (0.92, 1.10, 0.94),
    "tungsten": (1.16, 1.00, 0.86),
    "extreme, badly white-balanced": (1.28, 1.00, 0.78),
}


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as handle:
        return np.asarray(handle.convert("RGB"), dtype=np.float32) / 255.0


def _cast(rgb: np.ndarray, gains: tuple[float, float, float]) -> np.ndarray:
    return np.clip(rgb * np.array(gains, dtype=np.float32), 0.0, 1.0)


def real_xrays_still_pass() -> list[str]:
    """Nothing here may be rejected. A false rejection blocks real care."""
    failures: list[str] = []

    for name in ("chest_xray_normal.png", "chest_xray_pneumonia.png"):
        path = SAMPLES / name
        if not path.is_file():
            continue
        result = _validate_chest_xray(load_grayscale(path), load_rgb(path))
        if not result.passed:
            failures.append(f"{name} was rejected: {result.notes}")

    base = _load_rgb(XRAY)
    for label, gains in LIGHTING_CASTS.items():
        rgb = _cast(base, gains)
        result = _validate_chest_xray(rgb.mean(axis=-1), rgb)
        if not result.passed:
            failures.append(
                f"a photographed film under {label} was rejected: {result.notes}"
            )

    return failures


def non_xrays_are_rejected() -> list[str]:
    """Each of these must be refused, and for the stated reason."""
    failures: list[str] = []
    base = _load_rgb(XRAY)

    # A real radiograph screenshotted out of a viewer, coloured UI chrome and
    # all. Mid-toned, correctly proportioned, and normal in every tonal
    # statistic — only the colour check can catch this one.
    screenshot = base.copy()
    screenshot[:, :60] = np.array([0.05, 0.55, 0.62], dtype=np.float32)
    screenshot[:28, :] = np.array([0.85, 0.25, 0.20], dtype=np.float32)
    result = _validate_chest_xray(screenshot.mean(axis=-1), screenshot)
    if result.passed:
        failures.append("a radiograph screenshot with coloured UI chrome was accepted")

    # A page of white with a little dark content: a document or a slide.
    document = np.full((512, 512, 3), 0.97, dtype=np.float32)
    document[200:260, 40:470] = 0.02
    result = _validate_chest_xray(document.mean(axis=-1), document)
    if result.passed:
        failures.append("a document-like image was accepted")

    # The bundled negative sample, whatever it happens to be.
    blank = SAMPLES / "not_an_xray.png"
    if blank.is_file():
        result = _validate_chest_xray(load_grayscale(blank), load_rgb(blank))
        if result.passed:
            failures.append("not_an_xray.png was accepted")

    return failures


def thresholds_keep_their_margin() -> list[str]:
    """The two cutoffs must stay clear of both sides, not merely be ordered.

    A threshold that only just separates the cases is one dataset away from
    separating nothing, so this asserts headroom rather than correctness of a
    single verdict.
    """
    failures: list[str] = []
    base = _load_rgb(XRAY)

    worst_legitimate = max(
        _residual_saturation(_cast(base, gains)) for gains in LIGHTING_CASTS.values()
    )
    if worst_legitimate > MAX_RESIDUAL_SATURATION * 0.75:
        failures.append(
            f"the worst legitimate colour cast scores {worst_legitimate:.3f}, "
            f"too close to the {MAX_RESIDUAL_SATURATION} cutoff to be safe"
        )

    grey = load_grayscale(XRAY)
    near_white = float((grey > 0.90).mean())
    if near_white > MAX_NEAR_WHITE_FRACTION * 0.5:
        failures.append(
            f"a real radiograph is {near_white:.3f} near-white, too close to "
            f"the {MAX_NEAR_WHITE_FRACTION} cutoff"
        )

    return failures


def grayscale_only_callers_still_work() -> list[str]:
    """`rgb` is optional, and omitting it must not raise."""
    failures: list[str] = []
    try:
        result = _validate_chest_xray(load_grayscale(XRAY), None)
    except Exception as exc:  # noqa: BLE001
        return [f"validating without a colour view raised {exc!r}"]
    if not result.passed:
        failures.append("a real radiograph was rejected when passed grayscale-only")
    return failures


CHECKS = (
    ("Real radiographs and photographed films pass", real_xrays_still_pass),
    ("Non-radiographs are rejected", non_xrays_are_rejected),
    ("Thresholds keep headroom on both sides", thresholds_keep_their_margin),
    ("Grayscale-only callers still work", grayscale_only_callers_still_work),
)


def main() -> int:
    if not XRAY.is_file():
        print(f"Sample radiograph missing at {XRAY} — run the seeder first.")
        return 1

    print("Chest X-ray upload validation\n")
    failures: list[str] = []
    for title, check in CHECKS:
        problems = check()
        mark = "ok" if not problems else "FAIL"
        print(f"  [{mark}] {title}")
        for problem in problems:
            print(f"         {problem}")
        failures.extend(problems)

    print(f"\n  Failed: {len(failures)}")
    return 1 if failures else 0


def test_real_xrays_still_pass() -> None:
    assert real_xrays_still_pass() == []


def test_non_xrays_are_rejected() -> None:
    assert non_xrays_are_rejected() == []


def test_thresholds_keep_their_margin() -> None:
    assert thresholds_keep_their_margin() == []


def test_grayscale_only_callers_still_work() -> None:
    assert grayscale_only_callers_still_work() == []


if __name__ == "__main__":
    raise SystemExit(main())

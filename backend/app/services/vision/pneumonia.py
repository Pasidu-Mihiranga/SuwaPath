"""Chest X-ray pneumonia screening.

Two adapters share one interface:

`OnnxPneumoniaAdapter`
    The real path. Drop a trained model at ``models/pneumonia/model.onnx`` (any
    filename ending in ``.onnx`` in that directory works) and it is picked up
    automatically on the next request, taking priority over the baseline. Input
    shape, channel count and layout are read from the graph, so models exported
    from Keras (NHWC) and PyTorch (NCHW) both load without code changes.

`BaselinePneumoniaAdapter`
    A transparent, untrained fallback so the end-to-end flow is demonstrable
    before weights are supplied. It computes genuine radiographic features from
    the pixels — lower-zone opacity relative to upper zones, left/right
    asymmetry and local texture heterogeneity — rather than returning a random
    number. It reports `is_trained_model = False`, and the API and UI label it
    as a baseline that must not be used clinically.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.models.enums import ImageModality
from app.services.vision.base import (
    InferenceResult,
    ModelAdapter,
    ValidationResult,
    load_grayscale,
    occlusion_saliency,
    write_heatmap,
)

logger = logging.getLogger(__name__)

PNEUMONIA_SPECIALTY = "respiratory_medicine"
PNEUMONIA_CAPABILITIES = ["chest_xray", "respiratory", "laboratory"]

# Below this margin between classes the result is reported as uncertain.
#
# Only meaningful when the decision threshold is 0.5, which is the untrained
# baseline's operating point. A trained screening model is tuned for
# sensitivity and sits far lower — the BioFusion runs land around 0.165 — and
# a fixed +/- 0.20 window around 0.5 is then nonsense: it would call every
# probability between 0.165 and 0.40 "normal" while flagging almost everything
# else "uncertain". Adapters carrying a tuned threshold supply their own band
# in log-odds instead. See `uncertainty_band`.
UNCERTAINTY_MARGIN = 0.20

# Width of the uncertainty band, in log-odds distance from the operating
# point. 0.7 is roughly an odds ratio of two either side — near enough to the
# boundary that a clinician should look rather than trust the label.
#
# Log-odds rather than a probability margin because probability space is
# compressed near the extremes: +/- 0.2 around 0.5 spans a sensible range,
# while +/- 0.2 around 0.165 reaches below zero on one side and covers a
# doubling of risk on the other.
LOGIT_BAND = 0.7


def uncertainty_band(threshold: float, width: float = LOGIT_BAND) -> tuple[float, float]:
    """Probability bounds `width` log-odds either side of `threshold`."""
    threshold = min(max(threshold, 1e-6), 1 - 1e-6)
    centre = math.log(threshold / (1 - threshold))
    lo, hi = centre - width, centre + width
    return (1 / (1 + math.exp(-lo)), 1 / (1 + math.exp(-hi)))


def boundary_confidence(probability: float, threshold: float) -> float:
    """How far from the decision boundary, scaled to 0.5-1.0.

    `max(p, 1-p)` was reporting 0.8 for a firm positive and 0.9 for a marginal
    one once the threshold moved off 0.5, which is worse than no number at
    all. Distance from the boundary is what a reader actually wants.
    """
    if probability >= threshold:
        span = max(1.0 - threshold, 1e-6)
        return 0.5 + 0.5 * min((probability - threshold) / span, 1.0)
    span = max(threshold, 1e-6)
    return 0.5 + 0.5 * min((threshold - probability) / span, 1.0)


def _validate_chest_xray(image: np.ndarray) -> ValidationResult:
    """Reject images that are clearly not chest radiographs.

    Deliberately permissive — the goal is catching obvious mistakes (a selfie, a
    screenshot, a document scan), not performing quality control.
    """
    height, width = image.shape
    if min(height, width) < 100:
        return ValidationResult(False, "Image resolution is too low to screen reliably.")

    aspect = width / height
    if not 0.5 <= aspect <= 2.0:
        return ValidationResult(
            False,
            "Image proportions do not match a standard chest radiograph.",
        )

    mean = float(image.mean())
    std = float(image.std())
    if std < 0.04:
        return ValidationResult(
            False, "Image has almost no tonal variation and may be blank or corrupted."
        )
    if mean > 0.93 or mean < 0.04:
        return ValidationResult(
            False, "Image is almost entirely white or black."
        )

    # Radiographs concentrate tone in the mid range; documents are bimodal at
    # the extremes (white paper, black text).
    extremes = float(((image < 0.06) | (image > 0.94)).mean())
    if extremes > 0.75:
        return ValidationResult(
            False,
            "Image looks like a document or screenshot rather than a radiograph.",
        )

    return ValidationResult(True, None)


def _next_step(label: str) -> str:
    if label == "pneumonia":
        return (
            "Consult a respiratory physician or general physician. Bring this "
            "image and any recent blood tests to the consultation."
        )
    if label == "uncertain":
        return (
            "This screening result is not clear enough to be informative. A "
            "general physician should review the original image."
        )
    return (
        "No pneumonia-related pattern was detected by this screening tool. If "
        "you have ongoing symptoms such as cough, fever or breathlessness, "
        "still consult a general physician — screening cannot rule out disease."
    )


def _describe(label: str, probability: float, trained: bool) -> str:
    prefix = "" if trained else "Baseline screening (not a trained model): "
    if label == "pneumonia":
        return (
            f"{prefix}A pneumonia-related opacity pattern was detected. This is "
            f"screening support only and is not a diagnosis — a radiologist or "
            f"physician must review the original image."
        )
    if label == "uncertain":
        return (
            f"{prefix}The result is uncertain: the image does not fall clearly "
            f"into either category. Clinical review of the original image is "
            f"needed."
        )
    return (
        f"{prefix}No acute pneumonia-related pattern was detected. Screening "
        f"cannot rule out disease, so persistent symptoms still need review."
    )


def _build_result(
    *,
    prob_pneumonia: float,
    adapter: ModelAdapter,
    image: np.ndarray,
    heatmap_path: Path | None,
    score_fn,
    started: float,
    score_batch_fn=None,
    measurements: list[dict] | None = None,
) -> InferenceResult:
    prob_normal = 1.0 - prob_pneumonia

    # The operating point comes from the adapter, which reads it from the
    # sidecar shipped beside the weights. Without one it stays at 0.5 with the
    # old symmetric margin, so the baseline behaves exactly as before.
    threshold = getattr(adapter, "decision_threshold", 0.5)
    band = getattr(adapter, "uncertainty_bounds", None)
    if band is None:
        band = (0.5 - UNCERTAINTY_MARGIN / 2, 0.5 + UNCERTAINTY_MARGIN / 2)

    lower, upper = band
    uncertain = lower <= prob_pneumonia <= upper

    if uncertain:
        label = "uncertain"
    elif prob_pneumonia >= threshold:
        label = "pneumonia"
    else:
        label = "normal"
    confidence = boundary_confidence(prob_pneumonia, threshold)

    written_heatmap: str | None = None
    if heatmap_path is not None and adapter.supports_heatmap:
        try:
            saliency = occlusion_saliency(
                image, score_fn, grid=8, score_batch_fn=score_batch_fn
            )
            written_heatmap = write_heatmap(image, saliency, heatmap_path)
        except Exception as exc:  # noqa: BLE001 - a heatmap is never critical
            logger.warning("Heatmap generation failed: %s", exc)

    finding_label = {
        "pneumonia": "Pneumonia-related pattern detected",
        "normal": "No acute abnormality detected",
        "uncertain": "Result uncertain",
    }[label]

    return InferenceResult(
        finding_label=finding_label,
        finding_description=_describe(label, confidence, adapter.is_trained_model),
        confidence=round(float(confidence), 4),
        class_probabilities={
            "normal": round(float(prob_normal), 4),
            "pneumonia": round(float(prob_pneumonia), 4),
        },
        is_uncertain=uncertain,
        uncertainty_note=(
            "The two classes scored too closely for a reliable screening "
            "signal. Treat this as inconclusive."
            if uncertain
            else None
        ),
        heatmap_path=written_heatmap,
        suggested_specialty_code=PNEUMONIA_SPECIALTY,
        suggested_next_step=_next_step(label),
        required_capabilities=list(PNEUMONIA_CAPABILITIES),
        inference_ms=int((time.perf_counter() - started) * 1000),
        adapter_name=adapter.name,
        model_name=adapter.model_name,
        model_version=adapter.model_version,
        is_trained_model=adapter.is_trained_model,
        measurements=measurements or [],
        decision_threshold=round(float(threshold), 4),
    )


# --------------------------------------------------------------------------
# ONNX adapter — the real path
# --------------------------------------------------------------------------
class OnnxPneumoniaAdapter(ModelAdapter):
    name = "pneumonia_onnx"
    modality = ImageModality.CHEST_XRAY
    model_name = "Chest X-ray pneumonia classifier (ONNX)"
    model_version = "1.0.0"
    class_labels = ("normal", "pneumonia")
    supports_heatmap = True
    is_trained_model = True

    def __init__(self, variant: str = "") -> None:
        # A named variant loads `<variant>.onnx`; the empty default keeps the
        # original behaviour of taking whatever single .onnx is present, so a
        # deployment with one unnamed model still works untouched.
        self.variant = variant
        if variant:
            self.name = f"pneumonia_onnx_{variant}"
            self.model_name = f"Chest X-ray pneumonia classifier ({variant})"
        self._session = None
        self._input_name: str | None = None
        self._input_size = 224
        self._channels = 1
        self._layout = "NCHW"
        self._loaded = False
        self._load_failed = False

    def _model_path(self) -> Path | None:
        directory = settings.cv_model_dir / "pneumonia"
        if not directory.is_dir():
            return None
        if self.variant:
            named = directory / f"{self.variant}.onnx"
            return named if named.is_file() else None
        candidates = sorted(directory.glob("*.onnx"))
        return candidates[0] if candidates else None

    def _load_sidecar(self, model_path: Path) -> None:
        """Read the operating point shipped beside the weights.

        A threshold is a property of a *trained model*, not of this code: the
        same architecture tuned for 97% sensitivity lands near 0.165 on one
        dataset and 0.37 on another. Hardcoding any of them would be wrong for
        every model but one, so it travels with the weights.

        Missing or malformed sidecar leaves the neutral 0.5 default in place,
        which is what `models/pneumonia/README.md` already promises for a bare
        `.onnx`, and `describe()` reports `sidecar_present: false` so the gap
        is visible rather than silent.
        """
        sidecar = model_path.with_suffix(".json")
        if not sidecar.is_file():
            logger.warning(
                "No sidecar beside %s — scoring at the default 0.5 threshold.",
                model_path.name,
            )
            return

        try:
            meta = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            logger.exception("Could not read %s; using default threshold.", sidecar.name)
            return

        point = meta.get("operating_point") or {}
        threshold = point.get("threshold")
        if isinstance(threshold, (int, float)) and 0.0 < float(threshold) < 1.0:
            self.decision_threshold = float(threshold)
        else:
            logger.warning("Sidecar %s has no usable threshold.", sidecar.name)

        band = meta.get("uncertainty_band") or {}
        lower, upper = band.get("lower"), band.get("upper")
        if isinstance(lower, (int, float)) and isinstance(upper, (int, float)):
            self.uncertainty_bounds = (float(lower), float(upper))
        else:
            self.uncertainty_bounds = uncertainty_band(self.decision_threshold)

        if meta.get("model_version"):
            self.model_version = str(meta["model_version"])

        self.operating_point = {
            "threshold": self.decision_threshold,
            "policy": point.get("policy"),
            "min_recall_target": point.get("min_recall_target"),
            "tta": point.get("tta"),
            "uncertainty_band": list(self.uncertainty_bounds),
            "trained_on": (meta.get("training") or {}).get("dataset"),
            "trained_at": (meta.get("training") or {}).get("trained_at"),
            "test_metrics": meta.get("test_metrics"),
        }
        logger.info(
            "Loaded operating point from %s: threshold %.3f, band %.3f-%.3f",
            sidecar.name,
            self.decision_threshold,
            *self.uncertainty_bounds,
        )

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        if self._load_failed:
            return False

        path = self._model_path()
        if path is None:
            self._load_failed = True
            return False

        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(path), providers=["CPUExecutionProvider"]
            )
            self._load_sidecar(path)
            spec = self._session.get_inputs()[0]
            self._input_name = spec.name
            shape = spec.shape

            # Infer layout and spatial size from the graph, tolerating dynamic
            # (string or None) dimensions.
            def dim(value) -> int | None:
                return value if isinstance(value, int) and value > 0 else None

            if len(shape) == 4:
                if dim(shape[1]) in (1, 3):
                    self._layout = "NCHW"
                    self._channels = dim(shape[1]) or 1
                    self._input_size = dim(shape[2]) or dim(shape[3]) or 224
                elif dim(shape[3]) in (1, 3):
                    self._layout = "NHWC"
                    self._channels = dim(shape[3]) or 1
                    self._input_size = dim(shape[1]) or dim(shape[2]) or 224
                else:
                    self._input_size = dim(shape[2]) or 224

            self.model_name = f"Chest X-ray pneumonia classifier ({path.name})"
            self._loaded = True
            logger.info(
                "Loaded pneumonia ONNX model from %s (layout=%s, size=%d, channels=%d)",
                path, self._layout, self._input_size, self._channels,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load pneumonia ONNX model at %s: %s", path, exc)
            self._load_failed = True
            return False

    def is_available(self) -> bool:
        return self._ensure_loaded()

    def validate(self, image: np.ndarray) -> ValidationResult:
        return _validate_chest_xray(image)

    def _to_tensor(self, image: np.ndarray) -> np.ndarray:
        """One grayscale image to the layout this graph declared."""
        from PIL import Image as PILImage

        resized = np.asarray(
            PILImage.fromarray((image * 255).astype(np.uint8)).resize(
                (self._input_size, self._input_size), PILImage.BILINEAR
            ),
            dtype=np.float32,
        ) / 255.0

        if self._channels == 3:
            tensor = np.stack([resized] * 3, axis=0)  # CHW
        else:
            tensor = resized[None, ...]

        if self._layout == "NHWC":
            tensor = np.transpose(tensor, (1, 2, 0))
        return tensor.astype(np.float32)

    def _score(self, image: np.ndarray) -> float:
        """Positive-class (pneumonia) probability for a grayscale image."""
        return self._score_batch([image])[0]

    def _score_batch(self, images: list[np.ndarray]) -> list[float]:
        """Score many images in one call.

        Occlusion saliency needs 65 scores for a single heatmap. One call
        instead of 65 is the difference between a heatmap taking a couple of
        seconds and taking a few tens of milliseconds, which is why the export
        keeps a dynamic batch axis.
        """
        batch = np.stack([self._to_tensor(image) for image in images], axis=0)
        outputs = np.asarray(self._session.run(None, {self._input_name: batch})[0])
        if outputs.ndim == 1:
            outputs = outputs.reshape(len(images), -1)
        return [_to_positive_probability(row.reshape(-1)) for row in outputs]

    def predict(
        self, image: np.ndarray, *, heatmap_path: Path | None = None
    ) -> InferenceResult:
        started = time.perf_counter()
        self._ensure_loaded()
        probability = self._score(image)
        return _build_result(
            prob_pneumonia=probability,
            adapter=self,
            image=image,
            heatmap_path=heatmap_path,
            score_fn=self._score,
            score_batch_fn=getattr(self, "_score_batch", None),
            started=started,
        )


def _to_positive_probability(raw: np.ndarray) -> float:
    """Normalise varied model outputs into P(pneumonia).

    Handles single-logit sigmoid heads, 2-class softmax heads and already
    normalised probability vectors.
    """
    if raw.size == 1:
        value = float(raw[0])
        if 0.0 <= value <= 1.0:
            return value
        return float(1.0 / (1.0 + np.exp(-value)))  # sigmoid over a logit

    values = raw[:2].astype(np.float64)
    total = values.sum()
    if np.all(values >= 0) and abs(total - 1.0) < 1e-3:
        return float(values[1])  # already a probability distribution

    shifted = values - values.max()
    exponentials = np.exp(shifted)
    return float(exponentials[1] / exponentials.sum())


# --------------------------------------------------------------------------
# Baseline adapter — transparent fallback, explicitly not a trained model
# --------------------------------------------------------------------------
class BaselinePneumoniaAdapter(ModelAdapter):
    name = "pneumonia_baseline"
    modality = ImageModality.CHEST_XRAY
    model_name = "Radiographic opacity baseline (untrained heuristic)"
    model_version = "0.1.0-baseline"
    class_labels = ("normal", "pneumonia")
    supports_heatmap = True
    is_trained_model = False

    def is_available(self) -> bool:
        return True

    def validate(self, image: np.ndarray) -> ValidationResult:
        return _validate_chest_xray(image)

    def _score(self, image: np.ndarray) -> float:
        """Score consolidation-like opacity from real image statistics.

        Consolidation on a chest film shows as increased density in the lower
        lung zones, often asymmetric, with locally heterogeneous texture. These
        three signals are computed from the pixels and combined. This is a
        transparent heuristic, not a learned classifier.
        """
        height, width = image.shape

        # Restrict to the lung fields. The lower bound stops at 72% of height
        # deliberately: the diaphragm domes below that are dense in every
        # normal film and would otherwise swamp the lower-zone opacity signal.
        top, bottom = int(height * 0.18), int(height * 0.72)
        left, right = int(width * 0.10), int(width * 0.90)
        field = image[top:bottom, left:right]
        if field.size == 0:
            return 0.5

        field_h, field_w = field.shape
        upper = field[: field_h // 2]
        lower = field[field_h // 2 :]

        mid = field_w // 2
        gap = max(1, int(field_w * 0.08))  # skip the mediastinum
        left_lung = lower[:, : max(1, mid - gap)]
        right_lung = lower[:, min(field_w, mid + gap) :]

        # 1. Lower-zone opacity relative to upper zones.
        opacity = float(lower.mean() - upper.mean())

        # 2. Left/right asymmetry in the lower zones.
        asymmetry = (
            abs(float(left_lung.mean()) - float(right_lung.mean()))
            if left_lung.size and right_lung.size
            else 0.0
        )

        # 3. Local texture heterogeneity via block standard deviation.
        blocks = 6
        block_means: list[float] = []
        for row in range(blocks):
            for col in range(blocks):
                y0, y1 = row * field_h // blocks, (row + 1) * field_h // blocks
                x0, x1 = col * field_w // blocks, (col + 1) * field_w // blocks
                patch = field[y0:y1, x0:x1]
                if patch.size:
                    block_means.append(float(patch.mean()))
        heterogeneity = float(np.std(block_means)) if block_means else 0.0

        # Weighted combination mapped through a logistic to [0, 1].
        # Asymmetry carries the most weight: unilateral consolidation is the
        # strongest of the three signals, while a symmetric brightness shift is
        # usually exposure rather than disease. TEXTURE_BASELINE is the block
        # variation a structurally normal film already shows.
        TEXTURE_BASELINE = 0.15
        contributions = {
            "opacity": 10.0 * opacity,
            "asymmetry": 12.0 * asymmetry,
            "heterogeneity": 6.0 * (heterogeneity - TEXTURE_BASELINE),
        }
        raw = sum(contributions.values()) - 1.4

        # Kept for the next `predict` to read. The occlusion sweep calls
        # `_score` sixty-five times, so this is overwritten constantly — only
        # the value from the full-image call is ever published, and `predict`
        # reads it immediately after that call and before the sweep starts.
        self._last_measurements = [
            {
                "code": "lower_zone_opacity",
                "label": "Lower-zone opacity",
                "detail": "Mean density of the lower lung fields against the upper zones.",
                "value": round(opacity, 4),
                "contribution": round(contributions["opacity"], 3),
            },
            {
                "code": "lateral_asymmetry",
                "label": "Left/right asymmetry",
                "detail": "Density difference between lower lung fields, skipping the mediastinum.",
                "value": round(asymmetry, 4),
                "contribution": round(contributions["asymmetry"], 3),
            },
            {
                "code": "texture_heterogeneity",
                "label": "Texture heterogeneity",
                "detail": "Block-wise variation across a 6×6 grid, above the variation a normal film shows.",
                "value": round(heterogeneity, 4),
                "contribution": round(contributions["heterogeneity"], 3),
            },
        ]
        return float(1.0 / (1.0 + np.exp(-raw)))

    def predict(
        self, image: np.ndarray, *, heatmap_path: Path | None = None
    ) -> InferenceResult:
        started = time.perf_counter()
        probability = self._score(image)
        # Read before `_build_result`, because the occlusion sweep inside it
        # calls `_score` on 65 masked copies and overwrites this each time.
        measurements = list(getattr(self, "_last_measurements", []))
        return _build_result(
            prob_pneumonia=probability,
            adapter=self,
            image=image,
            heatmap_path=heatmap_path,
            score_fn=self._score,
            score_batch_fn=getattr(self, "_score_batch", None),
            started=started,
            measurements=measurements,
        )

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

import logging
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
UNCERTAINTY_MARGIN = 0.20


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
) -> InferenceResult:
    prob_normal = 1.0 - prob_pneumonia
    margin = abs(prob_pneumonia - prob_normal)
    uncertain = margin < UNCERTAINTY_MARGIN

    if uncertain:
        label = "uncertain"
        confidence = max(prob_pneumonia, prob_normal)
    elif prob_pneumonia > prob_normal:
        label = "pneumonia"
        confidence = prob_pneumonia
    else:
        label = "normal"
        confidence = prob_normal

    written_heatmap: str | None = None
    if heatmap_path is not None and adapter.supports_heatmap:
        try:
            saliency = occlusion_saliency(image, score_fn, grid=8)
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

    def __init__(self) -> None:
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
        candidates = sorted(directory.glob("*.onnx"))
        return candidates[0] if candidates else None

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

    def _score(self, image: np.ndarray) -> float:
        """Positive-class (pneumonia) probability for a grayscale image."""
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
        batch = tensor[None, ...].astype(np.float32)

        outputs = self._session.run(None, {self._input_name: batch})
        return _to_positive_probability(np.asarray(outputs[0]).reshape(-1))

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
        raw = (
            10.0 * opacity
            + 12.0 * asymmetry
            + 6.0 * (heterogeneity - TEXTURE_BASELINE)
            - 1.4
        )
        return float(1.0 / (1.0 + np.exp(-raw)))

    def predict(
        self, image: np.ndarray, *, heatmap_path: Path | None = None
    ) -> InferenceResult:
        started = time.perf_counter()
        probability = self._score(image)
        return _build_result(
            prob_pneumonia=probability,
            adapter=self,
            image=image,
            heatmap_path=heatmap_path,
            score_fn=self._score,
            started=started,
        )

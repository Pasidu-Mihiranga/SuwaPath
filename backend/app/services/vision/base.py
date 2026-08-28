"""Computer-vision model adapter interface.

Adding a new validated model (dermatology, fundus, another radiology model)
means implementing `ModelAdapter` and registering it — no changes to the API
layer, the care-navigation engine or the UI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.models.enums import ImageModality


@dataclass
class ValidationResult:
    passed: bool
    notes: str | None = None


@dataclass
class InferenceResult:
    """Screening-support output. Never presented as a diagnosis."""

    finding_label: str
    finding_description: str
    confidence: float
    class_probabilities: dict[str, float]
    is_uncertain: bool = False
    uncertainty_note: str | None = None
    heatmap_path: str | None = None
    suggested_specialty_code: str | None = None
    suggested_next_step: str | None = None
    # Capabilities a facility needs for the follow-up this finding implies.
    required_capabilities: list[str] = field(default_factory=list)
    inference_ms: int = 0
    adapter_name: str = ""
    model_name: str = ""
    model_version: str = "1.0.0"
    is_trained_model: bool = True
    # What the model actually measured, when it can say. A probability alone
    # is unreadable and unfalsifiable; a reader who can see the underlying
    # quantities can disagree with the score for a stated reason. Adapters
    # that cannot decompose their output leave this empty.
    measurements: list[dict] = field(default_factory=list)
    # The decision boundary this result was judged against, so a screening
    # tuned for sensitivity is visibly tuned for sensitivity.
    decision_threshold: float | None = None


class ModelAdapter(ABC):
    """Base class for every medical-image model."""

    name: str = "adapter"
    modality: ImageModality = ImageModality.OTHER
    model_name: str = "unnamed"
    model_version: str = "1.0.0"
    class_labels: tuple[str, ...] = ()
    supports_heatmap: bool = False
    # False for the bundled baseline, so the UI can label it honestly.
    is_trained_model: bool = True

    # The operating point. 0.5 with a symmetric band is the neutral default;
    # a trained screening model tuned for sensitivity overrides both from the
    # sidecar shipped beside its weights. Surfaced in `describe()` because a
    # screening tool whose threshold is invisible cannot be audited — and
    # because a model silently scoring at 0.5 when it was tuned for 0.165 is
    # exactly the failure the sidecar exists to prevent.
    decision_threshold: float = 0.5
    uncertainty_bounds: tuple[float, float] | None = None
    operating_point: dict | None = None

    @abstractmethod
    def is_available(self) -> bool:
        """True when this adapter can actually run inference."""

    @abstractmethod
    def validate(
        self, image: np.ndarray, *, rgb: np.ndarray | None = None
    ) -> ValidationResult:
        """Check the image is plausibly the expected modality.

        `rgb` is the same image before grayscale conversion. Optional so that
        callers holding only a grayscale array (tests, offline tools) still
        work; when it is supplied the colour checks are applied as well.
        """

    @abstractmethod
    def predict(self, image: np.ndarray, *, heatmap_path: Path | None = None) -> InferenceResult:
        """Run inference and optionally write a visual explanation."""

    def describe(self) -> dict:
        return {
            "adapter": self.name,
            "modality": str(self.modality),
            "model_name": self.model_name,
            "model_version": self.model_version,
            "classes": list(self.class_labels),
            "supports_heatmap": self.supports_heatmap,
            "available": self.is_available(),
            "is_trained_model": self.is_trained_model,
            "decision_threshold": self.decision_threshold,
            "uncertainty_bounds": list(self.uncertainty_bounds)
            if self.uncertainty_bounds
            else None,
            "operating_point": self.operating_point,
            # A trained model with no sidecar is running at a default
            # threshold that nobody chose. Worth a warning in the console.
            "sidecar_present": bool(self.operating_point),
        }


# --------------------------------------------------------------------------
# Shared image utilities
# --------------------------------------------------------------------------
def load_grayscale(path: Path, size: int | None = None) -> np.ndarray:
    """Load an image as a float32 grayscale array scaled to [0, 1]."""
    from PIL import Image

    with Image.open(path) as handle:
        image = handle.convert("L")
        if size:
            image = image.resize((size, size), Image.BILINEAR)
        return np.asarray(image, dtype=np.float32) / 255.0


def load_rgb(path: Path) -> np.ndarray:
    """Load an image as a float32 HxWx3 array scaled to [0, 1].

    Inference runs on grayscale, but *validation* must not: a radiograph is
    monochrome and an illustration, screenshot or photo generally is not, so
    colour is the single strongest signal for "this is not a radiograph". It
    was previously discarded by `convert("L")` before any check could see it,
    which is how a full-colour marketing illustration reached the classifier
    and came back with a confident finding.
    """
    from PIL import Image

    with Image.open(path) as handle:
        return np.asarray(handle.convert("RGB"), dtype=np.float32) / 255.0


def write_heatmap(
    base_image: np.ndarray, saliency: np.ndarray, destination: Path
) -> str:
    """Blend a saliency map over the image as a jet-coloured overlay."""
    from PIL import Image

    saliency = saliency.astype(np.float32)
    span = saliency.max() - saliency.min()
    normalised = (
        (saliency - saliency.min()) / span if span > 1e-8 else np.zeros_like(saliency)
    )

    target_h, target_w = base_image.shape
    heat = np.asarray(
        Image.fromarray((normalised * 255).astype(np.uint8)).resize(
            (target_w, target_h), Image.BICUBIC
        ),
        dtype=np.float32,
    ) / 255.0

    coloured = _jet_colormap(heat)
    grey_rgb = np.stack([base_image] * 3, axis=-1)

    # Weight the overlay by intensity so cool regions stay close to the original.
    alpha = (0.55 * heat)[..., None]
    blended = np.clip(grey_rgb * (1 - alpha) + coloured * alpha, 0, 1)

    destination.parent.mkdir(parents=True, exist_ok=True)
    # compress_level=1 rather than the default 6. Encoding dominates the cost
    # of producing a heatmap — by an order of magnitude over the occlusion
    # sweep — and these are transient explanation images, not archives. The
    # file is somewhat larger and written several times faster.
    Image.fromarray((blended * 255).astype(np.uint8)).save(
        destination, format="PNG", compress_level=1
    )
    return str(destination)


def _jet_colormap(values: np.ndarray) -> np.ndarray:
    """Minimal jet colormap (blue -> cyan -> yellow -> red)."""
    v = np.clip(values, 0.0, 1.0)
    red = np.clip(1.5 - np.abs(4 * v - 3), 0, 1)
    green = np.clip(1.5 - np.abs(4 * v - 2), 0, 1)
    blue = np.clip(1.5 - np.abs(4 * v - 1), 0, 1)
    return np.stack([red, green, blue], axis=-1)


def occlusion_saliency(
    image: np.ndarray,
    score_fn,
    *,
    grid: int = 8,
    patch_scale: float = 1.5,
    score_batch_fn=None,
) -> np.ndarray:
    """Model-agnostic saliency by occlusion sensitivity.

    Slides a grey patch across the image and records how much the positive-class
    score drops. Works for any black-box adapter, including ONNX graphs whose
    internals are not exposed, so a dropped-in model still gets a visual
    explanation without needing named conv layers for Grad-CAM.

    An 8x8 grid means 65 forward passes, and that is not cheap. Measured on
    the untrained baseline adapter, a prediction takes 11 ms and the heatmap
    takes 2.1 seconds — the explanation costs two hundred times the answer.

    Two things bring it down:

    **The sweep runs on a downsampled copy.** The output is an 8x8 grid however
    large the input, and every adapter resizes to its own input size anyway, so
    scoring a 512x512 image 65 times is work thrown away. `write_heatmap` still
    renders against the full-resolution original.

    **`score_batch_fn` scores every occlusion in one call**, which is why the
    exported ONNX graph keeps a dynamic batch axis.
    """
    # Comfortably above the 224 that the models want, so downsampling cannot
    # be what loses detail, and far below the 1-2 MP a real radiograph carries.
    max_side = 256
    longest = max(image.shape)
    if longest > max_side:
        from PIL import Image as PILImage

        scale = max_side / longest
        target = (max(1, int(image.shape[1] * scale)), max(1, int(image.shape[0] * scale)))
        image = np.asarray(
            PILImage.fromarray((image * 255).astype(np.uint8)).resize(
                target, PILImage.BILINEAR
            ),
            dtype=np.float32,
        ) / 255.0

    height, width = image.shape
    saliency = np.zeros((grid, grid), dtype=np.float32)

    patch_h = max(1, int(height / grid * patch_scale))
    patch_w = max(1, int(width / grid * patch_scale))
    fill = float(image.mean())

    variants: list[np.ndarray] = []
    for row in range(grid):
        for col in range(grid):
            centre_y = int((row + 0.5) * height / grid)
            centre_x = int((col + 0.5) * width / grid)
            y0 = max(0, centre_y - patch_h // 2)
            y1 = min(height, y0 + patch_h)
            x0 = max(0, centre_x - patch_w // 2)
            x1 = min(width, x0 + patch_w)

            occluded = image.copy()
            occluded[y0:y1, x0:x1] = fill
            variants.append(occluded)

    if score_batch_fn is not None:
        # The original goes first so the baseline costs no extra call.
        scores = list(score_batch_fn([image, *variants]))
        baseline, occluded_scores = scores[0], scores[1:]
    else:
        baseline = score_fn(image)
        occluded_scores = [score_fn(variant) for variant in variants]

    for index, score in enumerate(occluded_scores):
        # A large drop means the region mattered to the prediction.
        saliency[index // grid, index % grid] = max(0.0, baseline - score)

    return saliency

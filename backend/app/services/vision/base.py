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

    @abstractmethod
    def is_available(self) -> bool:
        """True when this adapter can actually run inference."""

    @abstractmethod
    def validate(self, image: np.ndarray) -> ValidationResult:
        """Check the image is plausibly the expected modality."""

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
    Image.fromarray((blended * 255).astype(np.uint8)).save(destination)
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
) -> np.ndarray:
    """Model-agnostic saliency by occlusion sensitivity.

    Slides a grey patch across the image and records how much the positive-class
    score drops. Works for any black-box adapter, including ONNX graphs whose
    internals are not exposed, so a dropped-in model still gets a visual
    explanation without needing named conv layers for Grad-CAM.
    """
    height, width = image.shape
    baseline = score_fn(image)
    saliency = np.zeros((grid, grid), dtype=np.float32)

    patch_h = max(1, int(height / grid * patch_scale))
    patch_w = max(1, int(width / grid * patch_scale))
    fill = float(image.mean())

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
            # A large drop means the region mattered to the prediction.
            saliency[row, col] = max(0.0, baseline - score_fn(occluded))

    return saliency

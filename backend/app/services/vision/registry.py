"""Adapter registry and the screening entry point.

Adapters are ordered per modality by priority: a trained model always takes
precedence over the bundled baseline, so dropping weights into
``models/pneumonia/`` switches the pipeline over with no code change.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.models.enums import ImageModality
from app.services.vision.base import (
    InferenceResult,
    ModelAdapter,
    load_grayscale,
    load_rgb,
)
from app.services.vision.pneumonia import (
    BaselinePneumoniaAdapter,
    OnnxPneumoniaAdapter,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Which trained model answers which upload
# --------------------------------------------------------------------------
# Three checkpoints, same ResNet50 architecture, different training data.
# Converted by `scripts/convert_pneumonia_onnx.py`.
#
#   general     `pneumonia_resnet50_best`            - the default
#   clean       `pneumonia_resnet50_combined_noPhone` - phone photographs were
#               EXCLUDED from its training set (confirmed with the author).
#               It is therefore the wrong model for a photographed film: it
#               has never seen one. Not routed to automatically; kept because
#               a model trained on cleaner data is worth comparing against
#               once both have measured operating points.
#   paediatric  `pneumonia_resnet50_pediatric_only`  - children only
#
# There is deliberately no camera-specific model. The one that sounded like it
# was is the one that saw the fewest photographs.
PAEDIATRIC_MAX_AGE = 16

_VARIANTS: dict[str, ModelAdapter] = {
    "general": OnnxPneumoniaAdapter("general"),
    "clean": OnnxPneumoniaAdapter("clean"),
    "paediatric": OnnxPneumoniaAdapter("paediatric"),
}


def select_variant(*, age: int | None = None, from_camera: bool = False) -> str:
    """Which trained model should read this image.

    Age is the only signal that changes the answer. Whether the chest belongs
    to a child changes the anatomy being read; how the file was captured
    changes only its quality, and no model here was trained to handle that
    better than the others. `from_camera` is accepted so callers need not
    change if that ever stops being true.
    """
    if age is not None and age <= PAEDIATRIC_MAX_AGE:
        if _VARIANTS["paediatric"].is_available():
            return "paediatric"
    return "general"


# Highest priority first within each modality.
_REGISTRY: dict[ImageModality, list[ModelAdapter]] = {
    ImageModality.CHEST_XRAY: [
        _VARIANTS["general"],
        _VARIANTS["clean"],
        _VARIANTS["paediatric"],
        BaselinePneumoniaAdapter(),
    ],
    # Future modalities register here; nothing else needs to change:
    # ImageModality.SKIN_LESION: [OnnxDermatologyAdapter()],
    # ImageModality.FUNDUS: [OnnxFundusAdapter()],
}

SUPPORTED_MODALITIES = tuple(_REGISTRY.keys())


class UnsupportedModalityError(ValueError):
    pass


class ImageValidationError(ValueError):
    pass


def get_adapter(modality: ImageModality) -> ModelAdapter:
    """Return the highest-priority available adapter for a modality."""
    adapters = _REGISTRY.get(modality)
    if not adapters:
        raise UnsupportedModalityError(
            f"No screening model is registered for '{modality}'. "
            f"Supported: {', '.join(str(m) for m in SUPPORTED_MODALITIES)}."
        )
    for adapter in adapters:
        if adapter.is_available():
            return adapter
    raise UnsupportedModalityError(
        f"No adapter for '{modality}' is currently available."
    )


def list_adapters() -> list[dict]:
    """Describe every registered adapter, for the system-admin console."""
    out: list[dict] = []
    for modality, adapters in _REGISTRY.items():
        for index, adapter in enumerate(adapters):
            description = adapter.describe()
            description["modality"] = str(modality)
            description["priority"] = index
            description["active"] = adapter.is_available() and not any(
                a.is_available() for a in adapters[:index]
            )
            out.append(description)
    return out


def screen_image(
    image_path: Path,
    modality: ImageModality,
    *,
    heatmap_name: str | None = None,
    age: int | None = None,
    from_camera: bool = False,
) -> InferenceResult:
    """Validate then screen an image, writing a heatmap when supported.

    `age` and `from_camera` choose between trained variants for chest X-rays.
    Both are hints: when the matching variant is absent the general model
    answers, so a deployment carrying one model behaves exactly as before.
    """
    if modality == ImageModality.CHEST_XRAY:
        variant = select_variant(age=age, from_camera=from_camera)
        adapter = _VARIANTS[variant]
        if not adapter.is_available():
            adapter = get_adapter(modality)
        else:
            logger.info("Screening with the %s model.", variant)
    else:
        adapter = get_adapter(modality)
    image = load_grayscale(Path(image_path))
    # Loaded separately and only for validation: inference runs on grayscale,
    # but the check for "this is not a radiograph at all" needs the colour
    # that grayscale conversion throws away.
    try:
        rgb = load_rgb(Path(image_path))
    except Exception:  # noqa: BLE001 - a colour view is a bonus, never required
        rgb = None

    validation = adapter.validate(image, rgb=rgb)
    if not validation.passed:
        raise ImageValidationError(
            validation.notes or "The uploaded image failed modality validation."
        )

    heatmap_path = (
        settings.heatmap_dir / heatmap_name if heatmap_name else None
    )
    return adapter.predict(image, heatmap_path=heatmap_path)

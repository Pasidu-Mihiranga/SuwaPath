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
from app.services.vision.base import InferenceResult, ModelAdapter, load_grayscale
from app.services.vision.pneumonia import (
    BaselinePneumoniaAdapter,
    OnnxPneumoniaAdapter,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Which trained model answers which upload
# --------------------------------------------------------------------------
# Three checkpoints were trained on different data, all the same ResNet50
# architecture. Converted by `scripts/convert_pneumonia_onnx.py` into
# `general.onnx`, `photo.onnx` and `paediatric.onnx`.
#
# ⚠ `photo` comes from a checkpoint named `combined_noPhone`, which most
# naturally reads as "trained on combined data with phone photographs
# EXCLUDED". If that reading is right, routing camera uploads to it is exactly
# backwards — it would be the one model that never saw a photograph of a film.
# The checkpoints carry no metadata, so the filename is the only evidence and
# it cannot settle the question.
#
# Until someone who knows confirms it, camera uploads keep using `general`.
# Flip `PHOTO_MODEL_CONFIRMED` to True once verified; nothing else changes.
# Being wrong here degrades accuracy silently on exactly the lowest-quality
# images, which is where a screening model is already weakest.
PHOTO_MODEL_CONFIRMED = False

# Paediatric chest X-rays differ enough from adult ones that a model trained
# only on children is the right tool when we know the patient is a child.
PAEDIATRIC_MAX_AGE = 16

_VARIANTS: dict[str, ModelAdapter] = {
    "general": OnnxPneumoniaAdapter("general"),
    "photo": OnnxPneumoniaAdapter("photo"),
    "paediatric": OnnxPneumoniaAdapter("paediatric"),
}


def select_variant(*, age: int | None = None, from_camera: bool = False) -> str:
    """Which trained model should read this image.

    Age wins over capture method: whether the chest belongs to a child changes
    the anatomy the model is reading, while how the file was captured changes
    only its quality. A blurred paediatric film is still a paediatric film.
    """
    if age is not None and age <= PAEDIATRIC_MAX_AGE:
        if _VARIANTS["paediatric"].is_available():
            return "paediatric"
    if from_camera and PHOTO_MODEL_CONFIRMED and _VARIANTS["photo"].is_available():
        return "photo"
    return "general"


# Highest priority first within each modality.
_REGISTRY: dict[ImageModality, list[ModelAdapter]] = {
    ImageModality.CHEST_XRAY: [
        _VARIANTS["general"],
        _VARIANTS["photo"],
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

    validation = adapter.validate(image)
    if not validation.passed:
        raise ImageValidationError(
            validation.notes or "The uploaded image failed modality validation."
        )

    heatmap_path = (
        settings.heatmap_dir / heatmap_name if heatmap_name else None
    )
    return adapter.predict(image, heatmap_path=heatmap_path)

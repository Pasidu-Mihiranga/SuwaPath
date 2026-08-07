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

# Highest priority first within each modality.
_REGISTRY: dict[ImageModality, list[ModelAdapter]] = {
    ImageModality.CHEST_XRAY: [
        OnnxPneumoniaAdapter(),
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
) -> InferenceResult:
    """Validate then screen an image, writing a heatmap when supported."""
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

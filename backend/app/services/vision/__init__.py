"""Computer-vision screening with pluggable model adapters."""

from app.services.vision.base import (
    InferenceResult,
    ModelAdapter,
    ValidationResult,
    load_grayscale,
)
from app.services.vision.registry import (
    ImageValidationError,
    SUPPORTED_MODALITIES,
    UnsupportedModalityError,
    get_adapter,
    list_adapters,
    screen_image,
)

__all__ = [
    "InferenceResult",
    "ModelAdapter",
    "ValidationResult",
    "load_grayscale",
    "screen_image",
    "get_adapter",
    "list_adapters",
    "SUPPORTED_MODALITIES",
    "UnsupportedModalityError",
    "ImageValidationError",
]

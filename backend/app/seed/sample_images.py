"""Synthetic chest-radiograph phantoms for demonstrating the CV pipeline.

These are procedurally generated approximations of a PA chest film — thorax
silhouette, lung fields, mediastinum, ribs, diaphragm — not real patient
images. They exist so the upload -> validate -> screen -> navigate flow is
demonstrable without distributing clinical data.

Replace them with real de-identified radiographs when evaluating an actual
trained model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "storage" / "samples"
SIZE = 512


def _base_thorax(rng: np.random.Generator) -> np.ndarray:
    """Radiograph-like base: dark lung fields on a brighter body silhouette."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    ny = (yy - SIZE / 2) / (SIZE / 2)
    nx = (xx - SIZE / 2) / (SIZE / 2)

    # Soft-tissue body: bright ellipse, dark background.
    body = np.exp(-((nx / 0.88) ** 2 + (ny / 0.96) ** 2) ** 3)
    image = 0.06 + 0.62 * body

    # Two lung fields (radiolucent, therefore darker).
    for centre_x in (-0.40, 0.40):
        lung = np.exp(
            -(((nx - centre_x) / 0.30) ** 2 + ((ny + 0.08) / 0.52) ** 2) ** 1.6
        )
        image -= 0.42 * lung

    # Mediastinum and spine (dense, bright).
    image += 0.34 * np.exp(-((nx / 0.10) ** 2) - ((ny / 0.95) ** 2) ** 3)
    # Diaphragm domes.
    image += 0.30 * np.exp(-(((np.abs(nx) - 0.40) / 0.34) ** 2 + ((ny - 0.62) / 0.20) ** 2))

    # Ribs: periodic bright arcs across the lung fields.
    ribs = 0.05 * np.sin(ny * 26.0 + nx * 3.0) * body
    image += ribs

    image += rng.normal(0, 0.012, image.shape)  # detector noise
    return np.clip(image, 0, 1)


def _add_consolidation(
    image: np.ndarray, centre: tuple[float, float], strength: float, spread: float
) -> np.ndarray:
    """Add an airspace-opacity patch (brighter, texturally heterogeneous)."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    ny = (yy - SIZE / 2) / (SIZE / 2)
    nx = (xx - SIZE / 2) / (SIZE / 2)
    blob = np.exp(-(((nx - centre[0]) / spread) ** 2 + ((ny - centre[1]) / spread) ** 2))
    return np.clip(image + strength * blob, 0, 1)


def generate_normal(seed: int = 11) -> Path:
    rng = np.random.default_rng(seed)
    image = _base_thorax(rng)
    return _save(image, "chest_xray_normal.png")


def generate_pneumonia(seed: int = 23) -> Path:
    """Right lower-zone consolidation, the classic teaching appearance."""
    rng = np.random.default_rng(seed)
    image = _base_thorax(rng)
    image = _add_consolidation(image, centre=(-0.44, 0.34), strength=0.34, spread=0.26)
    image = _add_consolidation(image, centre=(-0.34, 0.46), strength=0.22, spread=0.18)
    # Patchy air bronchogram-like texture within the opacity.
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    patchy = 0.05 * np.sin(xx / 7.0) * np.sin(yy / 9.0)
    mask = np.exp(-(((xx / SIZE - 0.28) / 0.14) ** 2 + ((yy / SIZE - 0.67) / 0.14) ** 2))
    image = np.clip(image + patchy * mask, 0, 1)
    return _save(image, "chest_xray_pneumonia.png")


def generate_not_an_xray() -> Path:
    """A document-like image, used to demonstrate modality validation."""
    image = np.ones((SIZE, SIZE), dtype=np.float32)
    for row in range(60, SIZE - 60, 26):
        image[row : row + 7, 70 : SIZE - 70] = 0.05
    return _save(image, "not_an_xray.png")


def _save(image: np.ndarray, name: str) -> Path:
    from PIL import Image

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    path = SAMPLES_DIR / name
    Image.fromarray((image * 255).astype(np.uint8)).save(path)
    return path


def generate_all() -> list[Path]:
    return [generate_normal(), generate_pneumonia(), generate_not_an_xray()]


if __name__ == "__main__":
    for created in generate_all():
        print(f"  wrote {created}")

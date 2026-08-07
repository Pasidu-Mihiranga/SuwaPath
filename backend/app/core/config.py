"""Application configuration.

Every external dependency (Gemini, Tavily, Qdrant, the CV weights directory) is
optional at runtime.  When a dependency is absent the corresponding service
falls back to a deterministic implementation so the product never hard-fails
during a demo.  See `app/services/` for the individual fallbacks.
"""

from __future__ import annotations

import getpass
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "SuwaPath"
    app_tagline: str = "Your Health. Our Path."
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"

    # --- Database ---
    database_url: str = (
        f"postgresql+psycopg2://{getpass.getuser()}@127.0.0.1:5436/suwapath"
    )
    sql_echo: bool = False

    # --- Auth ---
    jwt_secret: str = "suwapath-dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    refresh_token_ttl_days: int = 14

    # --- AI orchestration ---
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    tavily_api_key: str | None = None

    # --- Knowledge retrieval ---
    qdrant_url: str | None = None  # when unset, a local on-disk Qdrant is used
    qdrant_path: Path = BACKEND_ROOT / "storage" / "qdrant"
    qdrant_collection: str = "suwapath_health_knowledge"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- Storage ---
    storage_dir: Path = BACKEND_ROOT / "storage"
    document_dir: Path = BACKEND_ROOT / "storage" / "documents"
    image_dir: Path = BACKEND_ROOT / "storage" / "images"
    heatmap_dir: Path = BACKEND_ROOT / "storage" / "heatmaps"

    # --- Computer vision ---
    # Drop a trained pneumonia model (.onnx) in here and the ONNX adapter takes
    # over automatically from the bundled baseline adapter.
    cv_model_dir: Path = REPO_ROOT / "models"

    # --- CORS ---
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key and self.tavily_api_key.strip())

    def ensure_directories(self) -> None:
        for path in (
            self.storage_dir,
            self.document_dir,
            self.image_dir,
            self.heatmap_dir,
            self.qdrant_path,
            self.cv_model_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


settings = get_settings()

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

from pydantic import model_validator
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
    # Three providers are supported and tried in order. None of them is
    # required: without any key the platform still runs end to end on its
    # deterministic composers. See app/services/llm.py for the routing policy.
    #
    # Groq is first because on free tiers it is by far the fastest (sub-second
    # for llama-3.1-8b-instant), which matters for a conversational product.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # An agentic "compound" model, chosen because it is the only small Groq
    # model that reliably honours JSON mode — gpt-oss-20b failed it on every
    # attempt, and qwen returns its reasoning inside the content. Two
    # consequences worth knowing before changing it: it rejects tool calling
    # outright (see llm._call_groq), and because it runs on llama-3.3-70b
    # underneath, its rate-limit errors name *that* model, which is otherwise
    # baffling since nothing here configures it.
    groq_fast_model: str = "groq/compound-mini"

    open_router_api_key: str | None = None
    open_router_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"

    # Let the ReAct planner pick tools through the provider's own
    # function-calling API instead of the hand-rolled JSON protocol. Off by
    # default until there is evidence a free-tier model does it reliably;
    # when it fails the planner chain falls back to the JSON protocol and
    # then to fixed handoffs, so enabling it cannot break the turn.
    native_tool_calling_enabled: bool = False

    tavily_api_key: str | None = None

    # When True (default), the system falls back to deterministic rule-based
    # composers if LLM providers fail or are unconfigured.
    # When False, LLM failures will raise explicit errors rather than silently
    # falling back to canned rule-based text.
    allow_rule_based_fallback: bool = True

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

    # --- Autonomy ---
    # The background layer that lets the system act without an inbound
    # request. Off under pytest so tests never race a background writer.
    agentic_enabled: bool = True
    scheduler_enabled: bool = True
    job_tick_seconds: int = 60
    task_max_attempts: int = 5
    # Where the patients are. Medication times, quiet hours and daily job
    # buckets are all local-calendar concepts, not UTC ones.
    local_timezone: str = "Asia/Colombo"
    # SMS delivery. Empty means the no-op provider: the whole delivery path
    # still runs and records attempts, nothing leaves the machine.
    sms_provider: str = ""
    sms_api_key: str = ""
    sms_sender_id: str = "SuwaPath"

    # --- Encryption ---
    # Base64 32-byte key for AES-256-GCM over stored conversation content.
    # Absent in development, which stores plaintext; required in production.
    suwapath_encryption_key: str = ""

    # --- CORS ---
    # The deployed frontend and API live on different origins (the SPA on a
    # CDN, the API in a container), so the browser preflights every call.
    # Auth travels as a Bearer header from localStorage rather than a cookie,
    # so this needs no credentialed-origin handling — just the exact origin.
    # Set CORS_ORIGINS as a JSON list to add the deployed frontend.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
    # Convenience for platforms that only offer single string variables:
    # a comma-separated list, merged with the above.
    extra_cors_origins: str = ""

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key and self.groq_api_key.strip())

    @property
    def open_router_enabled(self) -> bool:
        return bool(self.open_router_api_key and self.open_router_api_key.strip())

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def llm_enabled(self) -> bool:
        return self.groq_enabled or self.open_router_enabled or self.gemini_enabled

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key and self.tavily_api_key.strip())

    @property
    def allowed_origins(self) -> list[str]:
        extra = [o.strip() for o in self.extra_cors_origins.split(",") if o.strip()]
        return list(dict.fromkeys([*self.cors_origins, *extra]))

    @model_validator(mode="after")
    def _derive_storage_paths(self) -> "Settings":
        """Let one variable move all the runtime directories.

        Each path is its own field so it can be pinned individually, but a
        container only wants to say "put writable state here" once. Anything
        still sitting under the default root follows `storage_dir`; anything
        set explicitly is left alone.
        """
        default_root = BACKEND_ROOT / "storage"
        if self.storage_dir != default_root:
            for field, leaf in (
                ("document_dir", "documents"),
                ("image_dir", "images"),
                ("heatmap_dir", "heatmaps"),
                ("qdrant_path", "qdrant"),
            ):
                if getattr(self, field) == default_root / leaf:
                    object.__setattr__(self, field, self.storage_dir / leaf)
        return self

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

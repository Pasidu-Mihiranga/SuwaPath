"""SuwaPath API — Your Health. Our Path."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    actions,
    admin,
    agent,
    appointments,
    auth,
    care,
    clinical,
    confidential,
    guardian,
    hospital,
    media,
    patients,
    providers,
    symptoms,
)
from app.core.config import settings
from app.core.db import create_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("suwapath")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    settings.ensure_directories()

    from app.services.ai_orchestrator import orchestrator_status

    status = orchestrator_status()
    logger.info(
        "SuwaPath API ready | Gemini: %s | knowledge: %s",
        "live" if status["gemini_reachable"] else "deterministic fallback",
        status["knowledge_backend"],
    )
    if not status["gemini_reachable"]:
        logger.warning(
            "GEMINI_API_KEY is not configured. The AI orchestrator will use "
            "deterministic fallbacks — every feature still works, but "
            "conversation wording will be scripted rather than generated."
        )

    # The autonomy layer. Everything above this line only ever reacts to a
    # request; this is the one thing in the process that acts on its own.
    from app.services.jobs import scheduler as job_scheduler

    job_scheduler.start()
    try:
        yield
    finally:
        job_scheduler.shutdown()


app = FastAPI(
    title="SuwaPath API",
    description=(
        "AI patient navigation, clinical intake and hospital intelligence "
        "platform. Screening and navigation support only — not a diagnostic "
        "device."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak internals to a patient-facing client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "Something went wrong on our side. Please try again. If this "
                "keeps happening, contact SuwaPath support."
            )
        },
    )


prefix = settings.api_v1_prefix
for router in (
    auth.router,
    agent.router,
    actions.router,
    patients.router,
    symptoms.router,
    media.router,
    providers.router,
    appointments.router,
    clinical.router,
    care.router,
    guardian.router,
    hospital.router,
    admin.router,
    confidential.router,
):
    app.include_router(router, prefix=prefix)


@app.get("/health", tags=["system"])
def health() -> dict:
    from sqlalchemy import text

    from app.core.db import engine

    database_ok = True
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database_ok = False

    from app.services.ai_orchestrator import orchestrator_status

    return {
        "status": "ok" if database_ok else "degraded",
        "app": settings.app_name,
        "tagline": settings.app_tagline,
        "version": "1.0.0",
        "database": "connected" if database_ok else "unavailable",
        "ai": orchestrator_status(),
    }


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "tagline": settings.app_tagline,
        "docs": "/docs",
        "health": "/health",
        "api": settings.api_v1_prefix,
    }

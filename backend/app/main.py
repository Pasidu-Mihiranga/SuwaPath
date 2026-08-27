"""SuwaPath API — Your Health. Our Path."""

from __future__ import annotations

import logging
import threading
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
from app.core import crypto
from app.core.config import settings
from app.core.db import create_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("suwapath")



def _warm_retrieval_in_background() -> None:
    """Build the vector index off the request path.

    `ensure_ready()` loads every collection and upserts it, opening its own
    database session for the provider directory when none is passed. That
    makes it self-healing on a cold store, which is what matters where the
    container filesystem is ephemeral: an embedded vector index does not
    survive a rebuild, and without this the provider directory would silently
    be missing until someone remembered to run the ingest CLI.
    """

    def warm() -> None:
        try:
            from app.services.knowledge import knowledge_service

            knowledge_service.ensure_ready()
            logger.info("Retrieval ready: %s", knowledge_service.backend)
        except Exception:  # noqa: BLE001 - warming must never kill the process
            logger.exception("Retrieval warm-up failed; TF-IDF fallback stands.")

    threading.Thread(target=warm, name="retrieval-warm", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    settings.ensure_directories()

    # Fail closed on encryption, but only where it matters.
    #
    # Without a key, the encrypted column types write plaintext — which is the
    # right behaviour for a demo and the wrong behaviour for a deployment
    # holding real records. `crypto.is_enabled()` has existed since encryption
    # was added and had no callers; this is the check it was written for.
    #
    # Refusing to start is the correct response rather than a warning: a
    # warning in a startup log is exactly what nobody reads before the first
    # patient record is written in the clear.
    if settings.environment == "production" and not crypto.is_enabled():
        raise RuntimeError(
            "SUWAPATH_ENCRYPTION_KEY must be set when environment=production. "
            "Without it, patient records are written to the database in "
            "plaintext."
        )
    if not crypto.is_enabled():
        logger.warning(
            "No encryption key set — patient records will be stored in "
            "plaintext. Acceptable only for synthetic data."
        )

    # Retrieval warms on a background thread rather than here.
    #
    # Reading `orchestrator_status()` touches `knowledge_service.backend`,
    # which builds the embedding session and opens the vector store — on a
    # cold container that means downloading ~90 MB and re-embedding the corpus
    # before the first request is served. Blocking startup on it fails the
    # platform health check, and a restart policy then turns that into a loop.
    #
    # Nothing needs retrieval to answer a login or a dashboard, and the
    # knowledge tools already degrade to a TF-IDF index while it is warming.
    _warm_retrieval_in_background()

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
    allow_origins=settings.allowed_origins,
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

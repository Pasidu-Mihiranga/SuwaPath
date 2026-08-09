"""Database engine, session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
    future=True,
    # The agent graph fans out to six parallel nodes, each opening its own
    # short-lived session, and the scheduler adds worker threads on top. The
    # default 5+10 is exhausted by one busy conversation plus a job tick.
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, expire_on_commit=False, future=True
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Create every table.  Import models first so metadata is populated."""
    from app.models import Base  # noqa: WPS433 - deferred to avoid circular import

    import app.models  # noqa: F401,WPS433 - registers all mappers

    Base.metadata.create_all(bind=engine)

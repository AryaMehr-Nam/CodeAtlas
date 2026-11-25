"""Database engine, declarative ORM base, and session context helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base for SampleSaaS data models."""


def get_database_url() -> str:
    """Return configured database DSN with an in-memory SQLite fallback."""
    return os.getenv("SAMPLE_SAAS_DATABASE_URL", "sqlite:///./sample_saas.db")


engine = create_engine(get_database_url(), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db() -> Iterator[Session]:
    """Yield a managed SQLAlchemy session for FastAPI dependency injection."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional database session scope for service functions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_health(session: Session) -> bool:
    """Execute a lightweight connectivity probe against the primary store."""
    result = session.execute(text("SELECT 1")).scalar()
    return result == 1

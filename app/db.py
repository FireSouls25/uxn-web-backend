"""Postgres (production/docker) or any SQLAlchemy URL (tests)."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

log = logging.getLogger("uxn.db")


class Base(DeclarativeBase):
    pass


def normalize_url(url: str) -> str:
    """Force the psycopg 3 driver onto any Postgres URL.

    We depend on `psycopg` (v3) and not `psycopg2`, so the default
    dialect SQLAlchemy picks for a bare `postgresql://` — psycopg2 —
    would fail at import with ModuleNotFoundError. That is exactly
    what a managed platform injects: Render links a Postgres and sets
    DATABASE_URL to `postgresql://uxn:...@dpg-.../uxn`, with no
    driver in the scheme, so the app died on boot before serving
    /health.

    Leaving an explicit driver alone keeps hand-written
    `postgresql+psycopg://` (compose, tests) working, and leaving
    non-Postgres URLs (sqlite in tests) untouched.
    """
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 — hand back anything unparseable
        return url
    # `postgres://` is an alias SQLAlchemy accepts but does not fold
    # onto "postgresql" for get_backend_name(), so name both.
    if parsed.drivername in ("postgresql", "postgres"):
        return parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    return url


engine = create_engine(normalize_url(settings.database_url), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema(attempts: int = 5, delay: float = 1.0) -> bool:
    """Create the tables if they are not there, retrying a bounded
    number of times. There are no migrations (see ARCHITECTURE.md):
    a fresh database gets its schema this way.

    A managed database is routinely not listening yet when the web
    process starts, so the failure is a wait, not an error: it returns
    False and the caller decides whether to keep trying. Returns True
    once the schema is there.
    """
    for attempt in range(1, attempts + 1):
        try:
            Base.metadata.create_all(bind=engine)
            return True
        except Exception as e:  # noqa: BLE001 — any driver error means "not ready yet"
            if attempt == attempts:
                log.error("schema not ready after %d attempts: %s", attempts, e)
                return False
            log.warning("database not ready (%s), retry %d/%d", e, attempt, attempts)
            time.sleep(delay)
    return False

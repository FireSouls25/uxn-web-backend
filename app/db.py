"""Postgres (production/docker) or any SQLAlchemy URL (tests)."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

log = logging.getLogger("uxn.db")


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
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

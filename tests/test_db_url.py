"""DATABASE_URL normalization: a bare `postgresql://` must load the
psycopg 3 driver, because that is the spelling a managed platform
injects and psycopg2 is not installed.

This is the failure that took the first Render deploy down: Render
links a Postgres and sets DATABASE_URL to `postgresql://...@dpg-...`
with no driver in the scheme, SQLAlchemy reached for psycopg2, and
`create_engine` raised ModuleNotFoundError at import — before
lifespan, before /health, before anything could serve.
"""
from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.db import normalize_url


def driver_of(url: str) -> str:
    return make_url(normalize_url(url)).get_dialect().driver


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://uxn:pw@dpg-abc123/uxn",
        "postgres://uxn:pw@dpg-abc123/uxn",
        "postgresql://uxn:pw@localhost:5432/uxnweb",
    ],
)
def test_bare_postgres_urls_get_psycopg3(raw):
    assert driver_of(raw) == "psycopg"


def test_explicit_psycopg_url_is_untouched():
    url = "postgresql+psycopg://uxn:uxn@db:5432/uxnweb"
    assert normalize_url(url) == url


def test_explicit_other_driver_is_left_for_the_operator():
    # Not ours to rewrite: someone asking for psycopg2 (or any other
    # dialect) means it, and silently swapping the driver would hide a
    # missing dependency behind a confusing connect error.
    url = "postgresql+psycopg2://uxn:pw@db/uxnweb"
    assert normalize_url(url) == url


def test_non_postgres_urls_are_untouched():
    assert normalize_url("sqlite:///:memory:") == "sqlite:///:memory:"


def test_password_with_reserved_characters_survives():
    # Render generates passwords with symbols, percent-encoded in the
    # URL it injects. The round-trip must not mangle them:
    # render_as_string re-quotes what needs quoting.
    raw = "postgresql://uxn:p%40ss%2Fw%20rd@dpg-abc/uxn"
    out = normalize_url(raw)
    parsed = make_url(out)
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.password == "p@ss/w rd"
    # And it must still parse back to the same credentials.
    assert make_url(out).username == "uxn"
    assert make_url(out).host == "dpg-abc"


def test_unparseable_url_is_handed_back_untouched():
    # Better a clear SQLAlchemy error than a mangled string.
    assert normalize_url("not a url at all") == "not a url at all"

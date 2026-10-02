"""API tests. Fresh sqlite DB per run; the local compiler checkout
is discovered automatically."""
from __future__ import annotations

import os
import tempfile

_db = tempfile.NamedTemporaryFile(prefix="uxnweb-test-", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import limits  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

HELLO = 'main :: fn() {\n    print("hi");\n}\n'


@pytest.fixture(autouse=True)
def _clean():
    limits.reset_limits()
    yield
    limits.reset_limits()


def _compile(**kw):
    body = {"target": "linux", "mode": "tal", "entry": "main.ux", "files": {"main.ux": HELLO}}
    body.update(kw)
    return client.post("/compile", json=body)


def test_health():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["compiler"] == "ok"
    assert body["database"] == "ok"


def test_targets():
    body = client.get("/targets").json()
    assert {t["id"] for t in body["supported"]} == {"linux", "web"}
    assert any(t["id"] == "windows-x86_64" for t in body["coming_soon"])


def test_compile_tal():
    r = _compile()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["cached"] is False
    assert "@main" in body["artifacts"]["tal"] or "main" in body["artifacts"]["tal"]
    # Stored job round-trips the same bytes.
    job = client.get(f"/jobs/{body['job_id']}").json()
    assert job["artifacts"] == body["artifacts"]


def test_compile_cache_hit():
    first = _compile(mode="rom").json()
    second = _compile(mode="rom").json()
    assert first["status"] == "ok" and second["status"] == "ok"
    assert second["cached"] is True
    assert second["job_id"] == first["job_id"]
    assert second["artifacts"] == first["artifacts"]


def test_compile_rom_and_bundle():
    rom = _compile(mode="rom").json()
    assert rom["status"] == "ok" and rom["artifacts"]["rom_b64"]
    web = _compile(target="web", mode="bundle").json()
    assert web["status"] == "ok" and web["artifacts"]["html_b64"]
    native = _compile(target="linux", mode="bundle").json()
    assert native["status"] == "ok" and native["artifacts"]["bundle_b64"]


def test_compile_error_spanish():
    r = _compile(files={"main.ux": "main :: fn() {\n nosuchfn();\n}\n"}, lang="es")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["message"] == "La compilación falló."
    assert body["diagnostics"], body
    first = body["diagnostics"][0]
    assert first["line"] == 1  # undefined calls report the declaration site
    assert "nosuchfn" in first["msg"]


def test_rejections():
    assert _compile(target="windows").status_code == 400
    assert _compile(target="amiga").status_code == 400
    assert _compile(files={"../evil.ux": HELLO}, entry="../evil.ux").status_code == 400
    assert _compile(files={"main.ux": HELLO}, entry="other.ux").status_code == 400
    r = client.post(
        "/compile",
        json={"target": "linux", "entry": "main.ux", "files": {"main.ux": HELLO},
              "etal_version": "9.9.9"},
    )
    assert r.status_code == 409
    assert client.get("/jobs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    assert _compile().status_code == 401
    assert _compile().headers.get("content-type", "").startswith("application/json")
    assert client.post(
        "/compile",
        json={"target": "linux", "mode": "tal", "entry": "main.ux",
              "files": {"main.ux": HELLO}},
        headers={"X-API-Key": "wrong"},
    ).status_code == 401
    ok = client.post(
        "/compile",
        json={"target": "linux", "mode": "tal", "entry": "main.ux",
              "files": {"main.ux": HELLO}},
        headers={"X-API-Key": "secret-1"},
    )
    assert ok.status_code == 200


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    assert _compile().status_code == 200
    r = _compile()
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    es = _compile(lang="es")
    assert es.status_code == 429
    assert "Reintente" in es.json()["detail"]


def test_server_busy(monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_compiles", 0)
    other = 'main :: fn() {\n    print("busy");\n}\n'
    r = _compile(files={"main.ux": other})
    assert r.status_code == 503
    assert r.json()["detail"]


def test_app_logs_reach_a_handler():
    """Uvicorn leaves the root logger bare: without configure_logging
    the routing decisions the operations doc promises never arrive."""
    import logging

    from app.main import configure_logging

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        configure_logging()
        assert root.handlers, "no handler installed"
        assert root.level == logging.INFO
        # httpx narrates every upstream call; the relay says it better.
        assert logging.getLogger("httpx").level == logging.WARNING
        # Already configured: hands off, so pytest's caplog survives.
        before = list(root.handlers)
        configure_logging()
        assert root.handlers == before
    finally:
        root.handlers, root.level = saved_handlers, saved_level


def test_schema_creation_waits_for_a_cold_database(monkeypatch):
    """A managed database is routinely not listening when the web
    process starts. Failing fast turns a 20-second wait into a crash
    loop; giving up silently turns it into 500s nobody explains."""
    from app import db as dbmod

    calls: list[int] = []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("connection refused")
        return None

    monkeypatch.setattr(dbmod.Base.metadata, "create_all", flaky)
    assert dbmod.ensure_schema(attempts=5, delay=0) is True
    assert len(calls) == 3  # two refusals, then the schema is in

    calls.clear()

    def always_down(*a, **kw):
        calls.append(1)
        raise RuntimeError("connection refused")

    monkeypatch.setattr(dbmod.Base.metadata, "create_all", always_down)
    assert dbmod.ensure_schema(attempts=2, delay=0) is False
    assert len(calls) == 2  # it gives up loudly, and reports it

"""User auth flows. Shares the suite DB (unique emails per test)."""
from __future__ import annotations

import os
import tempfile
import uuid

_db = tempfile.NamedTemporaryFile(prefix="uxnweb-auth-", suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db.name}")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import limits  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    limits.reset_limits()
    yield
    limits.reset_limits()


def _email() -> str:
    return f"u-{uuid.uuid4().hex[:12]}@example.com"


def _register(email: str | None = None, password: str = "correct-horse-8", name: str = "Pilot"):
    email = email or _email()
    r = client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    return email, password, r.json()


def test_register_login_me():
    email, password, pair = _register()
    assert pair["token_type"] == "bearer" and pair["expires_in"] > 0
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_login_wrong_password():
    email, password, _ = _register()
    r = client.post("/auth/login", json={"email": email, "password": "nope-nope-nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Wrong email or password."
    # Unknown email gives the same message (no enumeration).
    r2 = client.post("/auth/login", json={"email": "ghost@example.com", "password": password})
    assert r2.status_code == 401
    assert r2.json()["detail"] == r.json()["detail"]


def test_register_duplicate_and_validation():
    email, _, _ = _register()
    dup = client.post(
        "/auth/register", json={"name": "Other", "email": email, "password": "another-pass-1"}
    )
    assert dup.status_code == 409
    bad_mail = client.post(
        "/auth/register", json={"name": "Pilot", "email": "not-an-email", "password": "valid-pass-1"}
    )
    assert bad_mail.status_code == 400
    weak = client.post(
        "/auth/register", json={"name": "Pilot", "email": _email(), "password": "short"}
    )
    assert weak.status_code == 400
    es = client.post(
        "/auth/login", json={"email": "x@y.zz", "password": "whatever-1", "lang": "es"}
    )
    assert es.json()["detail"] == "Correo o contraseña incorrectos."


def test_refresh_rotation_and_logout():
    _, _, pair = _register()
    r1 = client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r1.status_code == 200
    rotated = r1.json()
    assert rotated["refresh_token"] != pair["refresh_token"]
    # Old refresh token died with the rotation.
    stale = client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert stale.status_code == 401
    # Logout kills the live one too.
    out = client.post("/auth/logout", json={"refresh_token": rotated["refresh_token"]})
    assert out.status_code == 200
    gone = client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert gone.status_code == 401


def test_compile_with_user_bearer(monkeypatch):
    """User tokens authorize compiles even when service keys are locked down."""
    monkeypatch.setenv("API_KEYS", "service-secret")
    _, _, pair = _register()
    denied = client.post(
        "/compile",
        json={"target": "linux", "mode": "tal", "entry": "m.ux",
              "files": {"m.ux": 'main :: fn() {\n    print("hi");\n}\n'}},
    )
    assert denied.status_code == 401
    ok = client.post(
        "/compile",
        json={"target": "linux", "mode": "tal", "entry": "m.ux",
              "files": {"m.ux": 'main :: fn() {\n    print("hi");\n}\n'}},
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ok"


def test_auth_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_per_minute", 2)
    for _ in range(2):
        client.post("/auth/login", json={"email": "nobody@example.com", "password": "x" * 12})
    r = client.post("/auth/login", json={"email": "nobody@example.com", "password": "x" * 12})
    assert r.status_code == 429

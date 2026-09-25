"""API tests. Bring your own DB via DATABASE_URL (sqlite works);
the local compiler checkout is discovered automatically."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/uxnweb-test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

HELLO = 'main :: fn() {\n    print("hi");\n}\n'


def _compile(**kw):
    body = {"target": "linux", "mode": "tal", "entry": "main.ux", "files": {"main.ux": HELLO}}
    body.update(kw)
    return client.post("/compile", json=body)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_targets():
    body = client.get("/targets").json()
    assert {t["id"] for t in body["supported"]} == {"linux", "web"}
    assert any(t["id"] == "windows-x86_64" for t in body["coming_soon"])


def test_compile_tal():
    r = _compile()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "@main" in body["artifacts"]["tal"] or "main" in body["artifacts"]["tal"]
    # Stored job round-trips the same bytes.
    job = client.get(f"/jobs/{body['job_id']}").json()
    assert job["artifacts"] == body["artifacts"]


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

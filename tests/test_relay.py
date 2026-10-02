"""Relay: translation, eligibility re-check, usage/breaker wiring.
httpx is stubbed — no network."""
from __future__ import annotations

import os
import tempfile

_db = tempfile.NamedTemporaryFile(prefix="uxnweb-relay-", suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db.name}")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.llm_relay as relay  # noqa: E402
from app import llm  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
SVC = {"X-API-Key": "operator-secret"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from app import limits

    limits.reset_limits()
    monkeypatch.setenv("API_KEYS", "operator-secret")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Resolved from the environment on every routing decision: clear
    # the escape hatches so a developer's shell cannot add a candidate.
    for var in ("LLM_DEV_BASE_URL", "LLM_DEV_KEY_ENV", "LLM_DEV_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(var, raising=False)

    def no_network(*a, **kw):  # tests must never reach a real provider
        raise AssertionError("unstubbed network call in tests")

    monkeypatch.setattr(relay.httpx, "post", no_network)
    db = SessionLocal()
    try:
        from app.models import LLMProviderState, LLMUsage

        db.query(LLMUsage).delete()
        db.query(LLMProviderState).delete()
        db.commit()
    finally:
        db.close()
    yield


class StubResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def ok_payload():
    return {
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "message": {
                    "content": "hi",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "describe", "arguments": "{}"}}
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_translate_roles():
    out = relay.translate_messages(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": "x", "arguments": '{"a":1}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
        ]
    )
    assert out[0] == {"role": "user", "content": "hi"}
    assert out[1]["tool_calls"][0] == {
        "id": "1",
        "type": "function",
        "function": {"name": "x", "arguments": '{"a":1}'},
    }
    assert out[2]["role"] == "tool"
    with pytest.raises(relay.RelayError):
        relay.translate_messages([{"role": "smoke", "content": "x"}])


def test_translate_accepts_flat_tool_calls():
    """The SDK's own shape shows up in transcripts; both must work."""
    out = relay.translate_messages(
        [{"role": "assistant", "tool_calls": [{"id": "9", "name": "x", "arguments": {"a": 1}}]}]
    )
    assert out[0]["tool_calls"][0]["function"] == {"name": "x", "arguments": '{"a": 1}'}
    with pytest.raises(relay.RelayError):
        relay.translate_messages([{"role": "assistant", "tool_calls": [{"id": "9"}]}])


def test_relay_happy_path(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["auth"] = (headers or {}).get("Authorization")
        seen["tools"] = json.get("tools")
        return StubResp(payload=ok_payload())

    monkeypatch.setattr(relay.httpx, "post", fake_post)
    auth = {"X-API-Key": "operator-secret"}
    r = client.post(
        "/agent/llm",
        headers=auth,
        json={
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "describe", "description": "d", "parameters": {}}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "hi"
    assert body["tool_calls"][0]["name"] == "describe"
    assert body["usage"] == {"in": 10, "out": 5}
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk-test"
    assert seen["tools"][0]["function"]["name"] == "describe"
    db = SessionLocal()
    try:
        assert llm.spent_today(db, "groq") == 15
        assert llm.get_state(db, "groq").fails == 0
    finally:
        db.close()


def test_relay_refuses_ineligible():
    auth = {"X-API-Key": "operator-secret"}
    r = client.post(
        "/agent/llm",
        headers=auth,
        json={"provider": "anthropic", "model": "claude-sonnet-4-5", "messages": []},
    )
    assert r.status_code == 400  # no key set
    assert "ineligible" in r.json()["detail"]


def test_relay_refuses_native_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    auth = {"X-API-Key": "operator-secret"}
    r = client.post(
        "/agent/llm",
        headers=auth,
        json={"provider": "anthropic", "model": "claude-sonnet-4-5", "messages": []},
    )
    assert r.status_code == 400
    assert "OpenAI-compatible" in r.json()["detail"]


def test_relay_provider_down_feeds_breaker(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return StubResp(status=429, text="slow down")

    monkeypatch.setattr(relay.httpx, "post", fake_post)
    auth = {"X-API-Key": "operator-secret"}
    r = client.post(
        "/agent/llm",
        headers=auth,
        json={"provider": "groq", "model": "llama-3.3-70b-versatile", "messages": []},
    )
    assert r.status_code == 502
    db = SessionLocal()
    try:
        state = llm.get_state(db, "groq")
        assert state.fails == 1 and state.cooldown_until is not None
        ranked = llm.rank_models(db)
        assert "groq" not in [c["provider"] for c in ranked["candidates"]]
    finally:
        db.close()


def test_relay_needs_auth():
    r = client.post("/agent/llm", json={"provider": "groq", "model": "x", "messages": []})
    assert r.status_code in (401, 503)


# --- /agent/turn: the door the browser actually uses --------------


def test_turn_picks_a_route_without_being_told(monkeypatch):
    calls: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return StubResp(payload=ok_payload())

    monkeypatch.setattr(relay.httpx, "post", fake_post)
    r = client.post(
        "/agent/turn",
        json={"messages": [{"role": "user", "content": "build a maze"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool_calls"][0]["name"] == "describe"
    # Free-first ranking picked groq; the client never named it.
    assert calls == ["https://api.groq.com/openai/v1/chat/completions"]


def test_turn_schema_refuses_provider_fields():
    r = client.post(
        "/agent/turn",
        json={"provider": "groq", "model": "x", "messages": []},
    )
    assert r.status_code == 422  # no such field: the client cannot choose


def test_turn_falls_through_to_the_next_candidate(monkeypatch):
    seen: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(url)
        if "groq" in url:
            return StubResp(status=503, text="down")
        return StubResp(payload=ok_payload())

    monkeypatch.setenv("CEREBRAS_API_KEY", "cbe-test")
    monkeypatch.setattr(relay.httpx, "post", fake_post)
    r = client.post("/agent/turn", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "hi"
    assert len(seen) == 2 and "cerebras" in seen[1]
    db = SessionLocal()
    try:
        assert llm.get_state(db, "groq").cooldown_until is not None
    finally:
        db.close()


def test_turn_503_when_nothing_eligible(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r = client.post("/agent/turn", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code in (502, 503)
    # The user never learns which providers exist or why they failed.
    assert "groq" not in r.json()["detail"]


def test_turn_is_rate_limited(monkeypatch):
    from app.config import settings as live

    def fake_post(url, headers=None, json=None, timeout=None):
        return StubResp(payload=ok_payload())

    monkeypatch.setattr(relay.httpx, "post", fake_post)
    monkeypatch.setenv("AGENT_TURNS_PER_MINUTE", "2")
    live.agent_turns_per_minute = 2
    try:
        for _ in range(2):
            assert client.post("/agent/turn", json={"messages": []}).status_code == 200
        r = client.post("/agent/turn", json={"messages": []})
        assert r.status_code == 429
        assert r.headers["Retry-After"]
    finally:
        live.agent_turns_per_minute = 20


# --- the walk itself: who answers, and what the logs say ------------


def test_turn_logs_the_chosen_route(monkeypatch, caplog):
    """Provider identity is forbidden in the response, so the log is
    the only place an operator can see the decision."""

    def fake_post(url, headers=None, json=None, timeout=None):
        return StubResp(payload=ok_payload())

    monkeypatch.setattr(relay.httpx, "post", fake_post)
    with caplog.at_level("INFO", logger="uxnweb.relay"):
        assert client.post("/agent/turn", json={"messages": []}).status_code == 200
    assert "relay turn answered by groq/llama-3.3-70b-versatile" in caplog.text


def test_turn_serves_a_local_model_server_first(monkeypatch, caplog):
    """LLM_DEV_BASE_URL is a full candidate, not a pinned escape
    hatch: free tier and `local`, it answers before any host."""
    seen: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(url)
        return StubResp(payload=ok_payload())

    monkeypatch.setenv("LLM_DEV_BASE_URL", "http://127.0.0.1:11500/v1")
    monkeypatch.setenv("LLM_DEV_MODEL", "local")
    monkeypatch.setattr(relay.httpx, "post", fake_post)
    with caplog.at_level("INFO", logger="uxnweb.relay"):
        assert client.post("/agent/turn", json={"messages": []}).status_code == 200
    assert seen == ["http://127.0.0.1:11500/v1/chat/completions"]
    assert "answered by dev-local/local" in caplog.text


def test_turn_serves_the_only_keyed_provider(monkeypatch, caplog):
    """The production shape: one key in the environment (an
    aggregator's), a free model picked, and the caller learns nothing
    beyond the reply."""
    seen: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "auth": (headers or {}).get("Authorization"), "model": json["model"]})
        return StubResp(payload=ok_payload())

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(relay.httpx, "post", fake_post)
    with caplog.at_level("INFO", logger="uxnweb.relay"):
        r = client.post("/agent/turn", json={"messages": [{"role": "user", "content": "build a maze"}]})
    assert r.status_code == 200, r.text
    assert len(seen) == 1
    assert seen[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen[0]["auth"] == "Bearer sk-or-test"  # the key never leaves the server
    assert seen[0]["model"].endswith(":free")
    assert "sk-or-test" not in r.text  # and never reaches the client
    assert "relay turn answered by openrouter/" in caplog.text


def test_turn_skips_ranked_but_uncallable_providers(monkeypatch, caplog):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setattr(relay.httpx, "post", lambda *a, **kw: StubResp(payload=ok_payload()))
    with caplog.at_level("INFO", logger="uxnweb.relay"):
        r = client.post("/agent/turn", json={"messages": []})
    assert r.status_code == 503  # ranked, but none of them callable
    assert "relay skips anthropic" in caplog.text
    db = SessionLocal()
    try:
        # Skipping is not failing: no breaker trip for a row never called.
        assert llm.get_state(db, "anthropic").fails == 0
    finally:
        db.close()

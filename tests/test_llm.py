"""LLM routing: registry sanity, filter/rank/budget/health behavior,
service auth, usage ledger. DB is sqlite (same models as Postgres)."""
from __future__ import annotations

import os
import tempfile

_db = tempfile.NamedTemporaryFile(prefix="uxnweb-llm-", suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db.name}")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm  # noqa: E402
from app import llm_registry  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
SVC = {"X-API-Key": "operator-secret"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from app import limits

    limits.reset_limits()
    monkeypatch.setenv("API_KEYS", "operator-secret")
    for var in [p.get("key_env") for p in llm_registry.PROVIDERS if p.get("key_env")]:
        monkeypatch.delenv(var, raising=False)
    # The dev endpoint and the dynamic model ids are resolved from the
    # environment on every call: clear them so a developer's shell
    # cannot add a candidate the tests did not ask for.
    for var in ("LLM_DEV_BASE_URL", "LLM_DEV_KEY_ENV", "LLM_DEV_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(var, raising=False)
    db = SessionLocal()
    try:
        from app.models import LLMProviderState, LLMUsage

        db.query(LLMUsage).delete()
        db.query(LLMProviderState).delete()
        db.commit()
    finally:
        db.close()
    yield


def test_registry_sane():
    ids = [p["id"] for p in llm_registry.PROVIDERS]
    assert len(ids) == len(set(ids))
    for p in llm_registry.PROVIDERS:
        assert isinstance(p["weight"], int)
        for m in p.get("models", []):
            assert isinstance(m["tools"], bool)
            assert m["tier"] in (0, 1, 2, 3)


def test_free_means_the_free_suffix():
    """On an aggregator a model is free because of its id, not
    because we decided so. If a `:free` id is edited to something
    paid, `free: true` would quietly spend money — so the two are
    pinned together in both directions."""
    aggregator = llm_registry.get_provider("openrouter")
    for m in llm_registry.models_of(aggregator):
        assert m["free"] == m["id"].endswith(":free"), m["id"]
        assert m["tools"] is True, m["id"]  # the agent cannot run without tools


def test_no_keys_means_nothing_keyed_eligible():
    db = SessionLocal()
    try:
        ranked = llm.rank_models(db)
        assert ranked["candidates"] == []
        assert any("no key" in e["reason"] for e in ranked["excluded"])
    finally:
        db.close()


def test_free_first_then_cheapest(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    db = SessionLocal()
    try:
        ranked = llm.rank_models(db)
        assert ranked["candidates"][0]["provider"] == "groq"
        assert ranked["candidates"][0]["free"] is True
        paid = [c for c in ranked["candidates"] if not c["free"]]
        assert paid[0]["tier"] <= paid[-1]["tier"]
        assert llm.choose(db)["provider"] == "groq"
    finally:
        db.close()


def test_disabled_budget_cooldown(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    db = SessionLocal()
    try:
        from app.models import LLMProviderState

        db.add(LLMProviderState(provider="groq", enabled_override=False))
        db.commit()
        ranked = llm.rank_models(db)
        assert "groq" not in [c["provider"] for c in ranked["candidates"]]
        assert any(e["provider"] == "groq" and "disabled" in e["reason"] for e in ranked["excluded"])

        llm.record_usage(db, "openai", "gpt-4o-mini", 60, 40)
        state = llm.get_state(db, "openai")
        state.daily_token_budget = 100
        db.commit()
        ranked = llm.rank_models(db)
        assert "openai" not in [c["provider"] for c in ranked["candidates"]]
        assert llm.spent_today(db, "openai") == 100

        llm.report_result(db, "openai", False)
        ranked = llm.rank_models(db)
        assert any(e["provider"] == "openai" and "cooling" in e["reason"] for e in ranked["excluded"])
        llm.report_result(db, "openai", True)
        state = llm.get_state(db, "openai")
        assert state.fails == 0 and state.cooldown_until is None
    finally:
        db.close()


def test_route_preview_and_pin(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    r = client.post("/agent/route", json={}, headers=SVC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chosen"]["provider"] == "groq"
    assert body["excluded"]

    ok = client.post("/agent/route", json={"provider": "groq"}, headers=SVC)
    assert ok.json()["chosen"]["provider"] == "groq"

    bad = client.post("/agent/route", json={"provider": "nope"}, headers=SVC)
    assert bad.status_code == 400
    ineligible = client.post("/agent/route", json={"provider": "anthropic"}, headers=SVC)
    assert ineligible.status_code == 400  # no key set


def test_usage_and_admin(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    r = client.post(
        "/agent/usage",
        json={"provider": "deepseek", "model": "deepseek-chat", "in_tokens": 10, "out_tokens": 5},
        headers=SVC,
    )
    assert r.status_code == 200
    st = client.get("/admin/llm/status", headers=SVC).json()
    ds = next(p for p in st if p["id"] == "deepseek")
    assert ds["spent_today"] == 15 and ds["key_present"] is True
    assert ds["key_env"] == "DEEPSEEK_API_KEY"  # names are public…
    blob = str(st)
    for secret in ("operator-secret",):
        assert secret not in blob  # …values never leak

    cfg = client.post(
        "/admin/llm/provider",
        json={"provider": "deepseek", "enabled": False, "daily_token_budget": 1000},
        headers=SVC,
    )
    assert cfg.status_code == 200
    st2 = client.get("/admin/llm/status", headers=SVC).json()
    assert next(p for p in st2 if p["id"] == "deepseek")["enabled"] is False


def test_service_auth():
    assert client.post("/agent/route", json={}).status_code == 401
    assert client.get("/admin/llm/status").status_code == 401
    r = client.post("/agent/route", json={}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_providers_catalog_uses_normal_auth(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert client.get("/agent/providers").status_code == 401  # auth required…
    r = client.get("/agent/providers", headers=SVC)  # …but any caller type works
    assert r.status_code == 200
    groq = next(p for p in r.json() if p["id"] == "groq")
    assert groq["key_present"] is True and groq["models"]


# --- the automatic selector itself ---------------------------------


def test_preview_is_the_turn_decision(monkeypatch):
    """What the operator previews and what the relay calls must be one
    function: a second ranking path is how a "who answers?" answer
    starts lying."""
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    preview = client.post("/agent/route", json={}, headers=SVC).json()
    db = SessionLocal()
    try:
        plan = llm.plan(db)
        assert plan["chosen"] == plan["candidates"][0]
        assert plan["chosen"] == llm.choose(db)
        assert preview["chosen"]["provider"] == plan["chosen"]["provider"]
        assert preview["chosen"]["model"] == plan["chosen"]["model"]
        assert [c["model"] for c in preview["candidates"]] == [c["model"] for c in plan["candidates"]]
    finally:
        db.close()


def test_local_model_wins_when_its_id_is_set(monkeypatch):
    """A `dynamic` row has no fixed model list, but automatic routing
    still needs one: OLLAMA_MODEL names it, and `local: true` puts it
    ahead of every hosted model of the same tier — even a hosted free
    model with a higher quality number."""
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:latest")
    db = SessionLocal()
    try:
        from app.models import LLMProviderState

        db.add(LLMProviderState(provider="ollama", enabled_override=True))
        db.commit()
        chosen = llm.choose(db)
        assert (chosen["provider"], chosen["model"]) == ("ollama", "llama3.1:latest")
        assert "local" in chosen["reason"]
        preview = client.post("/agent/route", json={}, headers=SVC).json()
        assert preview["chosen"]["model"] == "llama3.1:latest"
        catalog = client.get("/agent/providers", headers=SVC).json()
        assert "llama3.1:latest" in next(p for p in catalog if p["id"] == "ollama")["models"]
    finally:
        db.close()


def test_dev_endpoint_answers_before_hosted_free_models(monkeypatch):
    """The escape hatch is `local`, not just heavy: a dev endpoint
    outranks a hosted free model even when the hosted one claims the
    higher quality."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_DEV_BASE_URL", "http://127.0.0.1:11500/v1")
    db = SessionLocal()
    try:
        chosen = llm.choose(db)
        assert (chosen["provider"], chosen["model"]) == ("dev-local", "local")
    finally:
        db.close()


def test_enabled_local_server_without_a_model_is_excluded_not_lost(monkeypatch):
    """A row that cannot be asked for a model must say so; silently
    dropping out of the ranking is the failure mode this guards."""
    monkeypatch.setenv("GROQ_API_KEY", "g")
    db = SessionLocal()
    try:
        from app.models import LLMProviderState

        db.add(LLMProviderState(provider="ollama", enabled_override=True))
        db.commit()
        ranked = llm.rank_models(db)
        assert "ollama" not in [c["provider"] for c in ranked["candidates"]]
        reason = next(e["reason"] for e in ranked["excluded"] if e["provider"] == "ollama")
        assert "OLLAMA_MODEL" in reason
    finally:
        db.close()


def test_dev_endpoint_registers_live(monkeypatch):
    """The dev escape hatch follows the same read-live rule as the
    keys: set after import, it is routable; unset, it does not exist."""
    assert "dev-local" not in [p["id"] for p in llm_registry.providers()]
    monkeypatch.setenv("LLM_DEV_BASE_URL", "http://127.0.0.1:11500/v1")
    row = llm_registry.get_provider("dev-local")
    assert row is not None and row["base_url"] == "http://127.0.0.1:11500/v1"
    assert [m["id"] for m in llm_registry.models_of(row)] == ["local"]  # default id
    monkeypatch.setenv("LLM_DEV_MODEL", "qwen2.5-coder")
    assert [m["id"] for m in llm_registry.models_of(row)] == ["qwen2.5-coder"]
    monkeypatch.delenv("LLM_DEV_BASE_URL")
    assert llm_registry.get_provider("dev-local") is None


def test_one_key_is_enough_and_free_wins(monkeypatch):
    """The whole promise of the selector: a key in the environment is
    the only thing an operator does, and free models are what get
    picked — ahead of every paid model of every other provider."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    db = SessionLocal()
    try:
        ranked = llm.rank_models(db)
        assert {c["provider"] for c in ranked["candidates"]} == {"openrouter"}
        free = [c for c in ranked["candidates"] if c["free"]]
        assert free, "no free model available to the one keyed provider"
        # Free ids come first; the paid fallbacks are still ranked,
        # just behind them.
        assert ranked["candidates"][: len(free)] == free
        assert llm.choose(db)["model"].endswith(":free")
    finally:
        db.close()


def test_free_beats_a_paid_key(monkeypatch):
    """A free model anywhere outranks a paid one, whatever the
    weights say: the tier is the whole point of the ranking."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-paid")
    db = SessionLocal()
    try:
        ranked = llm.rank_models(db)
        assert ranked["candidates"][0]["provider"] == "openrouter"
        first_paid = next(i for i, c in enumerate(ranked["candidates"]) if not c["free"])
        assert all(c["free"] for c in ranked["candidates"][:first_paid])
    finally:
        db.close()


def test_keyless_providers_stay_out(monkeypatch):
    """No key, no candidate — and the reason names the variable, so
    adding the key is the fix an operator can read off the log."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk")
    db = SessionLocal()
    try:
        ranked = llm.rank_models(db)
        providers = {c["provider"] for c in ranked["candidates"]}
        assert "openai" not in providers and "anthropic" not in providers
        reason = next(e["reason"] for e in ranked["excluded"] if e["provider"] == "openai")
        assert reason == "no key (OPENAI_API_KEY)"
    finally:
        db.close()

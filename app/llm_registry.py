"""LLM registry: what exists, what it costs (in tiers, not hallucinated
prices), what it can do, and which env var holds its key.

Money is deliberately coarse: `tier` 0 = free, 1 = budget, 2 =
standard, 3 = premium. Token ledgers count tokens; dollar budgets
arrive later with a pinned price table. Add a row per new provider —
no code changes needed elsewhere.

`PROVIDERS` is the static part of the catalog. `providers()` is the
catalog as of now: it resolves the dev endpoint and, for `dynamic`
rows, the model id (both from the environment, on every call), so
nothing here is frozen at import.
"""
from __future__ import annotations

import os

TIER_FREE = 0
TIER_BUDGET = 1
TIER_STANDARD = 2
TIER_PREMIUM = 3


# quality: 1 (draft) .. 3 (flagship) — breaks cost ties only, and
# only between two hosted providers or two local ones (see below).
# base_url: OpenAI-compatible chat-completions endpoint. Providers
# without one (native-only APIs) cannot serve the relay yet.
# local: your own hardware or an endpoint you pointed at — your data
# never leaves, so it outranks any hosted model of the same tier,
# whatever the quality numbers say. An operator who went to the
# trouble of running a model has already voted.
PROVIDERS: list[dict] = [
    {
        "id": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "weight": 90,
        "models": [
            {"id": "llama-3.3-70b-versatile", "tools": True, "free": True, "tier": TIER_FREE, "quality": 2},
        ],
    },
    {
        "id": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "weight": 80,
        "models": [
            {"id": "llama-3.3-70b", "tools": True, "free": True, "tier": TIER_FREE, "quality": 2},
        ],
    },
    {
        "id": "google",
        "key_env": "GOOGLE_API_KEY",
        "weight": 70,
        "models": [
            {"id": "gemini-2.0-flash", "tools": True, "free": True, "tier": TIER_FREE, "quality": 2},
            {"id": "gemini-2.5-pro", "tools": True, "free": False, "tier": TIER_STANDARD, "quality": 3},
        ],
    },
    {
        "id": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
        "weight": 60,
        "models": [
            {"id": "mistral-small-latest", "tools": True, "free": False, "tier": TIER_BUDGET, "quality": 2},
            {"id": "mistral-large-latest", "tools": True, "free": False, "tier": TIER_STANDARD, "quality": 3},
        ],
    },
    {
        "id": "deepseek",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "weight": 60,
        "models": [
            {"id": "deepseek-chat", "tools": True, "free": False, "tier": TIER_BUDGET, "quality": 2},
        ],
    },
    {
        "id": "openai",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "weight": 50,
        "models": [
            {"id": "gpt-4o-mini", "tools": True, "free": False, "tier": TIER_BUDGET, "quality": 2},
            {"id": "gpt-4o", "tools": True, "free": False, "tier": TIER_STANDARD, "quality": 3},
        ],
    },
    {
        "id": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "weight": 50,
        "models": [
            {"id": "claude-haiku-4-5", "tools": True, "free": False, "tier": TIER_BUDGET, "quality": 2},
            {"id": "claude-sonnet-4-5", "tools": True, "free": False, "tier": TIER_STANDARD, "quality": 3},
        ],
    },
    {
        "id": "xai",
        "base_url": "https://api.x.ai/v1",
        "key_env": "XAI_API_KEY",
        "weight": 40,
        "models": [
            {"id": "grok-3-mini", "tools": True, "free": False, "tier": TIER_BUDGET, "quality": 2},
        ],
    },
    {
        # One key, many models: an aggregator that speaks the same
        # OpenAI protocol. Its `:free` ids cost nothing and come first
        # (free tier, so they outrank every paid model anywhere) — but
        # they are rate-limited and the lineup churns, so refresh the
        # list with `uv run python scripts/refresh_free_models.py`.
        # A `free: true` row must end in `:free`: that suffix is the
        # only thing that makes it free, and tests check it.
        "id": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "weight": 70,
        "models": [
            {"id": "nvidia/nemotron-3-super-120b-a12b:free", "tools": True, "free": True, "tier": TIER_FREE, "quality": 3},
            {"id": "qwen/qwen3.8-27b:free", "tools": True, "free": True, "tier": TIER_FREE, "quality": 3},
            {"id": "google/gemma-4-31b-it:free", "tools": True, "free": True, "tier": TIER_FREE, "quality": 2},
            {"id": "liquid/lfm-2.5-2.6b:free", "tools": True, "free": True, "tier": TIER_FREE, "quality": 1},
            # Paid fallbacks: only reached when the free ids are
            # rate-limited *and* the key has credit on it.
            {"id": "anthropic/claude-sonnet-4.5", "tools": True, "free": False, "tier": TIER_STANDARD, "quality": 3},
            {"id": "openai/gpt-4o-mini", "tools": True, "free": False, "tier": TIER_BUDGET, "quality": 2},
        ],
    },
    {
        # Keyless local server. No fixed model list: the id comes from
        # OLLAMA_MODEL (see models_of), so any local model joins
        # routing the moment the variable is set. Off until an
        # operator enables it with POST /admin/llm/provider.
        # `local` is what makes it answer before any hosted model of
        # the same tier — not the weight (see the sort key in llm.py).
        "id": "ollama",
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
        "weight": 100,
        "enabled_default": False,
        "dynamic": True,
        "local": True,
        "model_env": "OLLAMA_MODEL",
        "models": [],
    },
]


def dev_provider() -> dict | None:
    """LLM_DEV_BASE_URL registers any OpenAI-compatible endpoint as a
    first-class candidate: local models, a mock in tests, or a
    self-hosted gateway. Free tier, and `local` so it answers before
    any hosted model of the same tier — and nothing exists in
    production unless an operator sets the variable."""
    base = (os.environ.get("LLM_DEV_BASE_URL") or "").strip()
    if not base:
        return None
    key_env = (os.environ.get("LLM_DEV_KEY_ENV") or "").strip() or None
    return {
        "id": "dev-local",
        "base_url": base,
        "key_env": key_env,
        "weight": 100,
        "enabled_default": True,
        "dynamic": True,
        "local": True,
        "model_env": "LLM_DEV_MODEL",
        "default_model": "local",
        "models": [],
    }


def providers() -> list[dict]:
    """The catalog as of right now: the static rows plus the dev
    endpoint when LLM_DEV_BASE_URL is set. Resolved per call, never at
    import, so every knob — endpoint, key, model id — follows the same
    "read the environment live" rule."""
    dev = dev_provider()
    return [dev, *PROVIDERS] if dev is not None else list(PROVIDERS)


def models_of(provider: dict) -> list[dict]:
    """The models a provider can serve right now. A `dynamic` row has
    no fixed list: it accepts any model id, and the one automatic
    routing may pick is named by `model_env` (falling back to
    `default_model` when the variable is empty)."""
    models = provider.get("models") or []
    if models or not provider.get("dynamic"):
        return models
    env = provider.get("model_env")
    model_id = (os.environ.get(env, "").strip() if env else "") or (provider.get("default_model") or "")
    if not model_id:
        return []
    return [{"id": model_id, "tools": True, "free": True, "tier": TIER_FREE, "quality": 2}]


def get_provider(provider_id: str) -> dict | None:
    return next((p for p in providers() if p["id"] == provider_id), None)

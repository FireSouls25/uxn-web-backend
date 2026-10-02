"""Provider/model selection: hard filters, deterministic ranking,
budgets, health breakers. No learning here by design — every input is
an exact fact (registry, env, ledger, clock), so every decision is
explainable and logged.

`plan()` is the one entry point (ranked list + chosen + log line);
`choose()` and the relay both go through it.
"""
from __future__ import annotations

import datetime
import logging
import os
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import llm_registry

log = logging.getLogger("uxn.llm")


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def key_present(provider: dict) -> bool:
    env = provider.get("key_env")
    if env is None:
        return True  # keyless local server
    return bool(os.environ.get(env, "").strip())


def get_state(db: Session, provider_id: str):
    from .models import LLMProviderState

    state = db.get(LLMProviderState, provider_id)
    if state is None:
        state = LLMProviderState(provider=provider_id, fails=0)
        db.add(state)
        db.commit()
    return state


def spent_today(db: Session, provider_id: str) -> int:
    from .models import LLMUsage

    total = (
        db.query(func.coalesce(func.sum(LLMUsage.in_tokens + LLMUsage.out_tokens), 0))
        .filter(LLMUsage.provider == provider_id, LLMUsage.day == today())
        .scalar()
    )
    return int(total or 0)


def rank_models(db: Session, capability: str = "tools") -> dict:
    """Returns {candidates: [...ranked...], excluded: [...with reasons...]}."""
    from .models import LLMProviderState  # noqa: F401  (ensures table mapping)

    now = utcnow()
    candidates: list[dict] = []
    excluded: list[dict] = []
    for provider in llm_registry.providers():
        pid = provider["id"]
        state = get_state(db, pid)
        enabled = state.enabled_override if state.enabled_override is not None else provider.get("enabled_default", True)
        if not enabled:
            excluded.append({"provider": pid, "model": None, "reason": "disabled by operator"})
            continue
        if not key_present(provider):
            excluded.append({"provider": pid, "model": None, "reason": f"no key ({provider.get('key_env')})"})
            continue
        if state.cooldown_until and state.cooldown_until.replace(tzinfo=datetime.timezone.utc) > now:
            excluded.append({"provider": pid, "model": None, "reason": f"cooling down until {state.cooldown_until.isoformat()}"})
            continue
        budget = state.daily_token_budget
        spent = spent_today(db, pid)
        if budget is not None and spent >= budget:
            excluded.append({"provider": pid, "model": None, "reason": f"daily budget spent ({spent}/{budget} tokens)"})
            continue
        models = llm_registry.models_of(provider)
        if not models and provider.get("dynamic"):
            # A dynamic row (local server) is registered but cannot be
            # asked for a model id yet. Say so, instead of letting it
            # vanish from a catalog that claims to explain itself.
            excluded.append(
                {
                    "provider": pid,
                    "model": None,
                    "reason": f"no model configured ({provider.get('model_env')})",
                }
            )
        for model in models:
            if capability == "tools" and not model.get("tools", False):
                excluded.append({"provider": pid, "model": model["id"], "reason": "no tool calling"})
                continue
            candidates.append(
                {
                    "provider": pid,
                    "model": model["id"],
                    "free": bool(model.get("free", False)),
                    "tier": int(model.get("tier", 2)),
                    "local": bool(provider.get("local", False)),
                    "quality": int(model.get("quality", 2)),
                    "weight": int(provider.get("weight", 50)),
                }
            )
    # Free first, then cheapest tier, then your own hardware before
    # anyone else's cloud, then quality, then admin weight.
    # The `local` step is what keeps the promise in the registry
    # ("self-hosting wins when configured"): without it, a hosted
    # quality-3 free model would outrank the dev mock or an ollama
    # row (both quality 2), and a magic weight would be the only
    # thing standing between them. Sort is stable, so two models
    # that tie on everything keep registry order.
    candidates.sort(key=lambda c: (not c["free"], c["tier"], not c["local"], -c["quality"], -c["weight"]))
    for i, c in enumerate(candidates):
        c["reason"] = (
            f"rank {i + 1}: {'free tier' if c['free'] else f'tier {c['tier']}'}"
            f"{', local' if c['local'] else ''}"
            f", quality {c['quality']}, weight {c['weight']}"
        )
    return {"candidates": candidates, "excluded": excluded}


def plan(db: Session, capability: str = "tools") -> dict:
    """The one decision: the ranked list, its head, and the log line
    that explains it. The operator preview (`POST /agent/route`) and
    the relay's real turn both call this, so what an operator is shown
    and what actually answers can never drift apart."""
    ranked = rank_models(db, capability)
    chosen = ranked["candidates"][0] if ranked["candidates"] else None
    log.info(
        "llm route capability=%s chosen=%s candidates=%d excluded=%d",
        capability,
        f"{chosen['provider']}/{chosen['model']}" if chosen else None,
        len(ranked["candidates"]),
        len(ranked["excluded"]),
    )
    return {"chosen": chosen, **ranked}


def choose(db: Session, capability: str = "tools") -> dict | None:
    return plan(db, capability)["chosen"]


def record_usage(db: Session, provider: str, model: str, in_tokens: int, out_tokens: int) -> None:
    from .models import LLMUsage

    db.add(
        LLMUsage(
            id=str(uuid.uuid4()),
            day=today(),
            provider=provider,
            model=model,
            in_tokens=max(0, int(in_tokens)),
            out_tokens=max(0, int(out_tokens)),
        )
    )
    db.commit()


def report_result(db: Session, provider: str, ok: bool) -> None:
    """Success resets the breaker; failure backs it off exponentially."""
    state = get_state(db, provider)
    if ok:
        state.fails = 0
        state.cooldown_until = None
    else:
        state.fails = (state.fails or 0) + 1
        delay = min(300, 30 * (2 ** min(state.fails - 1, 3)))
        state.cooldown_until = utcnow() + datetime.timedelta(seconds=delay)
    db.commit()

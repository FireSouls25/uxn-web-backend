"""OpenAI-compatible relay: the browser loop never sees keys.

The client (pi-agent-core custom streamFn) POSTs pi-shaped messages;
this module translates to OpenAI chat-completions, calls the provider
with the server-held key, records usage, and feeds the breaker.
Providers without base_url (native-only APIs) are refused with a
clear reason — no silent mistranslation.
"""
from __future__ import annotations

import json
import logging
import os

import httpx

from . import llm, llm_registry

log = logging.getLogger("uxnweb.relay")

# Long enough for a big prompt on a slow model, short enough that a
# stalled provider still trips the breaker instead of hanging a turn.
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "120"))


class RelayError(Exception):
    def __init__(self, message: str, provider_down: bool = False):
        super().__init__(message)
        self.provider_down = provider_down


def _clean_tool_call(c: dict) -> dict:
    """One assistant tool call, normalized. OpenAI's own shape, or the
    flattened `{id, name, arguments}` the SDK uses internally — both
    appear in transcripts, and providers only accept the first."""
    fn = c.get("function")
    name = (fn or c).get("name")
    if not name:
        raise RelayError("tool call without a name")
    raw = (fn or c).get("arguments", "{}")
    return {
        "id": c.get("id", ""),
        "type": "function",
        "function": {
            "name": name,
            "arguments": raw if isinstance(raw, str) else json.dumps(raw),
        },
    }


def translate_messages(messages: list[dict]) -> list[dict]:
    """The client already speaks OpenAI chat-completions (see
    rag/agent-contract.md), so this is a validation pass, not a
    translation: roles are checked, tool calls are passed through, and
    a malformed one is refused loudly instead of reaching a provider
    half-formed."""
    out = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": m.get("content", "")})
        elif role == "system":
            out.append({"role": "system", "content": m.get("content", "")})
        elif role == "assistant":
            entry: dict = {"role": "assistant", "content": m.get("content") or None}
            calls = m.get("tool_calls") or []
            if calls:
                entry["tool_calls"] = [_clean_tool_call(c) for c in calls]
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
            )
        else:
            raise RelayError(f"unknown role {role!r}")
    return out


def translate_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def call(db, provider_id: str, model: str, messages: list[dict], tools: list[dict]) -> dict:
    provider = llm_registry.get_provider(provider_id)
    if provider is None:
        raise RelayError(f"unknown provider {provider_id}")
    base_url = provider.get("base_url")
    if not base_url:
        raise RelayError(f"provider {provider_id} needs a native API (relay supports OpenAI-compatible only)")
    key = os.environ.get(provider.get("key_env") or "", "").strip() if provider.get("key_env") else ""
    if provider.get("key_env") and not key:
        raise RelayError(f"no key configured ({provider.get('key_env')})")
    payload = {
        "model": model,
        "messages": translate_messages(messages),
        "tool_choice": "auto",
    }
    if tools:
        payload["tools"] = translate_tools(tools)
    try:
        resp = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            json=payload,
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        llm.report_result(db, provider_id, False)
        raise RelayError(f"transport to {provider_id}: {e}", provider_down=True)
    if resp.status_code == 429 or resp.status_code >= 500:
        llm.report_result(db, provider_id, False)
        raise RelayError(f"{provider_id} HTTP {resp.status_code}", provider_down=True)
    if resp.status_code != 200:
        raise RelayError(f"{provider_id} HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
        choice = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as e:
        raise RelayError(f"bad payload from {provider_id}: {e}")
    usage = data.get("usage", {})
    llm.record_usage(db, provider_id, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    llm.report_result(db, provider_id, True)
    calls = [
        {"id": c["id"], "name": c["function"]["name"], "arguments": c["function"].get("arguments", "{}")}
        for c in (choice.get("tool_calls") or [])
    ]
    return {
        "content": choice.get("content"),
        "tool_calls": calls,
        "usage": {"in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0)},
        "model": data.get("model", model),
    }


def turn(db, messages: list[dict], tools: list[dict], capability: str = "tools") -> dict:
    """One agent turn with the routing decision made here, not by the
    client. Walks the ranked candidates until one answers: transport
    errors, rate limits and 5xx trip the breaker and move on; a
    non-down failure (bad request, refused key) also moves on, since
    the next provider speaks the same protocol.
    Provider identities stay in the logs — callers only ever learn
    whether a model answered.
    """
    plan = llm.plan(db, capability)
    total = len(plan["candidates"])
    attempts: list[str] = []
    for position, candidate in enumerate(plan["candidates"], start=1):
        provider = llm_registry.get_provider(candidate["provider"])
        if provider is None or not provider.get("base_url"):
            log.info(
                "relay skips %s/%s: no OpenAI-compatible endpoint",
                candidate["provider"],
                candidate["model"],
            )
            continue  # native-only API: the relay can't speak it
        try:
            reply = call(db, candidate["provider"], candidate["model"], messages, tools)
        except RelayError as e:
            attempts.append(f"{candidate['provider']}: {e}")
            log.warning("relay candidate %d/%d failed: %s", position, total, attempts[-1])
            continue
        log.info(
            "relay turn answered by %s/%s (rank %d of %d, %d failed first)",
            candidate["provider"],
            candidate["model"],
            position,
            total,
            len(attempts),
        )
        return reply
    if not attempts:
        log.info("relay no candidate: %s", plan["excluded"])
        raise RelayError("no model available")
    log.warning("relay exhausted %d candidates", len(attempts))
    raise RelayError("no model answered", provider_down=True)

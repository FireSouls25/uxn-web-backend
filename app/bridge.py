"""Agent bridge: lets an outside agent (an MCP client such as Claude
Code) drive the studio tab a user has open.

The studio's tools run in the browser, on the project in that tab
(projects live in the user's browser, not on this server). So the
server cannot run them itself; it relays:

  MCP client ──HTTP /mcp──▶ server ──WS /ws/bridge──▶ studio tab
                                    ◀── tool result ──

Pairing: a logged-in user creates a key in the studio (POST
/bridge/keys). The tab connects with it (`/ws/bridge?key=`), and the
MCP client sends the same key as `Authorization: Bearer <key>`. Only
the SHA-256 of a key is stored. One tab per key: a second tab with the
same key replaces the first.

Socket protocol (JSON text frames):
  tab → {"type": "hello", "tools": [{name, description, params}],
         "project": {"id", "name"}}
        {"type": "result", "id": n, "ok": bool, "message": str, "data": {...}}
        {"type": "project", "project": {...}}       when the user switches
  srv → {"type": "ready"}
        {"type": "call", "id": n, "name": str, "args": {...}}
        {"type": "activity", "name": str}            (unused by older tabs)

The registry is in memory: run one server process (the default).
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import itertools
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .db import SessionLocal
from .models import BridgeKey

log = logging.getLogger("uxnweb.bridge")

KEY_PREFIX = "uxf_"
KEY_DAYS = 30
CALL_TIMEOUT = 90.0
CLOSE_BAD_KEY = 4401
CLOSE_REPLACED = 4409


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _aware(d: datetime.datetime) -> datetime.datetime:
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def new_key(owner_id: str | None, label: str = "") -> tuple[str, BridgeKey]:
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    now = _now()
    row = BridgeKey(
        sha=key_hash(key),
        owner_id=owner_id,
        label=label[:64],
        created_at=now,
        expires_at=now + datetime.timedelta(days=KEY_DAYS),
    )
    return key, row


def lookup(key: str | None) -> BridgeKey | None:
    """The live key row, or None (unknown, expired or malformed)."""
    if not key or not key.startswith(KEY_PREFIX) or len(key) > 80:
        return None
    with SessionLocal() as db:
        row = db.get(BridgeKey, key_hash(key))
        if row is None or _aware(row.expires_at) < _now():
            return None
        return row


@dataclass
class Tab:
    ws: WebSocket
    tools: list[dict[str, Any]] = field(default_factory=list)
    project: dict[str, Any] = field(default_factory=dict)
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    connected_at: datetime.datetime = field(default_factory=_now)


class Registry:
    def __init__(self) -> None:
        self.tabs: dict[str, Tab] = {}
        self._ids = itertools.count(1)

    def tab(self, sha: str) -> Tab | None:
        return self.tabs.get(sha)

    async def call(self, sha: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tab = self.tabs.get(sha)
        if tab is None:
            raise LookupError("studio not connected")
        call_id = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        tab.pending[call_id] = fut
        try:
            await tab.ws.send_json({"type": "call", "id": call_id, "name": name, "args": args})
            return await asyncio.wait_for(fut, CALL_TIMEOUT)
        finally:
            tab.pending.pop(call_id, None)

    async def serve(self, ws: WebSocket) -> None:
        row = lookup(ws.query_params.get("key"))
        if row is None:
            await ws.close(code=CLOSE_BAD_KEY)
            return
        await ws.accept()
        sha = row.sha
        old = self.tabs.get(sha)
        tab = Tab(ws=ws)
        self.tabs[sha] = tab
        if old is not None:
            _fail(old, "replaced by another studio tab")
            try:
                await old.ws.close(code=CLOSE_REPLACED)
            except Exception:  # noqa: BLE001 — already gone
                pass
        await ws.send_json({"type": "ready"})
        try:
            while True:
                msg = await ws.receive_json()
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "hello":
                    tools = msg.get("tools")
                    tab.tools = [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)] if isinstance(tools, list) else []
                    if isinstance(msg.get("project"), dict):
                        tab.project = msg["project"]
                elif kind == "project" and isinstance(msg.get("project"), dict):
                    tab.project = msg["project"]
                elif kind == "result":
                    fut = tab.pending.get(msg.get("id"))  # type: ignore[arg-type]
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            if self.tabs.get(sha) is tab:
                del self.tabs[sha]
            _fail(tab, "studio tab disconnected")


def _fail(tab: Tab, why: str) -> None:
    for fut in tab.pending.values():
        if not fut.done():
            fut.set_exception(LookupError(why))


registry = Registry()

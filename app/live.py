"""Live check over WebSocket: the editor streams its source, the server
streams back compiler diagnostics as the user types.

Why a socket instead of POST /compile: the editor sends a new version
every pause in typing. One long-lived connection carries those without
a request handshake each time, and the server can apply "latest wins"
(a check that arrives while one is compiling replaces any queued one,
so stale versions are never compiled) and push status as it changes
(`checking` → `result`).

Protocol (JSON text frames):
  client → {"type": "check", "id": n, "entry": "main.ux",
            "files": {path: text}, "lang": "en"|"es"}
  server → {"type": "hello", "etal_version": ...}             on connect
           {"type": "checking", "id": n}                       compile started
           {"type": "result", "id": n, "ok": bool, "message": str,
            "diagnostics": [...], "rom_bytes": int, "ms": int, "cached": bool}
           {"type": "error", "id": n|None, "message": str}     bad request / busy
Auth: `?token=<access token>` (browsers can't set headers on a socket)
or `?api_key=`; open in dev mode when no API keys are configured, the
same rules as POST /compile. Rejected sockets close with 4401.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from . import auth, compiler, limits
from .auth import TokenError
from .config import settings
from .db import SessionLocal
from .i18n import resolve_lang, t
from .limits import ServerBusy
from .models import User

log = logging.getLogger("uxnweb.live")

CLOSE_UNAUTHORIZED = 4401


def _authorized(ws: WebSocket, api_keys: list[str]) -> bool:
    token = ws.query_params.get("token")
    if token:
        try:
            payload = auth.decode_token(token, "access")
        except TokenError:
            return False
        with SessionLocal() as db:
            return db.get(User, payload["sub"]) is not None
    key = ws.query_params.get("api_key")
    if key is not None and key in api_keys:
        return True
    return not api_keys


def _check(entry: str, files: dict[str, str]) -> compiler.CompileResult:
    """ROM build of the tree: the fastest mode that still runs the full
    compiler + assembler (so budget errors like zero-page overflow show)."""
    with limits.compile_slot():
        return compiler.run_compile("web", "rom", entry, files)


async def live_check(ws: WebSocket, api_keys: list[str]) -> None:
    if not _authorized(ws, api_keys):
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    await ws.accept()
    await ws.send_json({"type": "hello", "etal_version": settings.etal_version})

    latest: dict[str, Any] | None = None
    wake = asyncio.Event()
    closed = False
    last: tuple[str, dict[str, Any]] | None = None  # (files sha, result frame)

    async def receive() -> None:
        nonlocal latest, closed
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    await ws.send_json({"type": "error", "id": None, "message": "invalid JSON"})
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "check":
                    await ws.send_json({"type": "error", "id": None, "message": "unknown message"})
                    continue
                latest = msg  # latest wins: an unstarted older request is dropped
                wake.set()
        except WebSocketDisconnect:
            pass
        finally:
            closed = True
            wake.set()

    async def work() -> None:
        nonlocal latest, last
        while True:
            await wake.wait()
            wake.clear()
            if closed:
                return
            msg, latest = latest, None
            if msg is None:
                continue
            await ws.send_json(await handle(msg))

    async def handle(msg: dict[str, Any]) -> dict[str, Any]:
        nonlocal last
        rid = msg.get("id")
        lang = resolve_lang(msg.get("lang"), None)
        files = msg.get("files")
        entry = msg.get("entry") or "main.ux"
        if not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in files.items()
        ):
            return {"type": "error", "id": rid, "message": "files must map path to text"}
        verr = compiler.validate_files(files, entry)
        if verr:
            return {"type": "error", "id": rid, "message": t(verr.key, lang, **verr.params)}
        sha = compiler.files_sha(files)
        if last is not None and last[0] == sha:
            return {**last[1], "id": rid, "cached": True, "ms": 0}
        await ws.send_json({"type": "checking", "id": rid})
        started = time.monotonic()
        try:
            result = await run_in_threadpool(_check, entry, files)
        except ServerBusy:
            return {"type": "error", "id": rid, "message": t("error.server_busy", lang)}
        except FileNotFoundError:
            return {"type": "error", "id": rid, "message": t("error.compiler_missing", lang, detail=settings.etal_bin)}
        except subprocess.TimeoutExpired:
            return {"type": "error", "id": rid, "message": t("error.timeout", lang, seconds=settings.compile_timeout)}
        frame = {
            "type": "result",
            "id": rid,
            "ok": result.ok,
            "message": t("ok.compiled", lang) if result.ok else t("error.compile_failed", lang),
            "diagnostics": result.diagnostics,
            "rom_bytes": len(result.artifacts.get("rom", b"")),
            "ms": int((time.monotonic() - started) * 1000),
            "cached": False,
        }
        last = (sha, frame)
        return frame

    worker = asyncio.create_task(work())
    try:
        await receive()
    finally:
        worker.cancel()
        try:
            await worker
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass

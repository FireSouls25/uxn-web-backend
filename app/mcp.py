"""MCP server: an outside agent gets the studio agent's tools and docs.

Transport: MCP "Streamable HTTP" in its simplest form. The client
POSTs JSON-RPC 2.0 to /mcp and gets one JSON response back; there is no
server-initiated stream. Auth: `Authorization: Bearer <bridge key>`
(see app/bridge.py for pairing).

What it serves:
* tools that run here: studio_status, list_docs, read_doc,
  search_docs, compile_check;
* every tool of the paired studio tab, relayed over the bridge socket
  with the tab's own names, descriptions and parameters (the same set
  the in-app agent uses);
* the docs as resources (uxnforge://docs/<name>).
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import compiler, limits
from .bridge import lookup, registry
from .config import BACKEND_DIR, settings
from .limits import ServerBusy

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DOCS_DIR = BACKEND_DIR / "rag"
DOC_NAMES = ("engine", "craft", "etal", "tools", "examples", "varvara")

INSTRUCTIONS = """You are connected to uxn·forge, a studio for Uxn games and apps.
Tools named like the studio's (describe, scene_map, draw_sprite, add_event, write_script, check_build, …) run in the user's open studio tab on the project they have open, and the user sees every change live. studio_status says whether a tab is connected.

How to work:
- Start with studio_status, then describe (or project_context) for the current ids. Never invent ids: look them up.
- Read the docs before building: read_doc engine (the engine model), craft (the default look: shaded art with draw_sprite, tile layers, headline type, title/end screens), etal (the script language), tools.
- Templates + events for the game's shape, ETAL script functions for rules and data, called from blocks.
- Before saying done: validate, then check_build; fix every error and check again.
- Read-only example projects can't be edited: ask the user to press "Duplicate to edit"."""


def _doc(name: str) -> str | None:
    if name not in DOC_NAMES:
        return None
    path = DOCS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def _chunks() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for name in DOC_NAMES:
        text = _doc(name) or ""
        parts = re.split(r"^## ", text, flags=re.M)
        head = parts.pop(0) if parts else text
        if head.strip():
            out.append((name, name, head.strip()))
        for part in parts:
            title, _, body = part.partition("\n")
            out.append((name, title.strip(), f"## {title.strip()}\n{body.strip()}"))
    return out


def search_docs(query: str, limit: int = 4) -> list[dict[str, str]]:
    words = [w for w in re.findall(r"[a-z0-9_]+", query.lower()) if len(w) > 1]
    scored = []
    for doc, title, text in _chunks():
        low = text.lower()
        tlow = title.lower()
        score = sum(low.count(w) + 3 * tlow.count(w) for w in words)
        if score:
            scored.append((score, doc, title, text))
    scored.sort(key=lambda x: -x[0])
    return [{"doc": d, "section": t, "text": x[:1800]} for _, d, t, x in scored[:limit]]


def _schema(params: Any) -> dict[str, Any]:
    """The studio's {name: {type, required, description}} → JSON Schema."""
    props: dict[str, Any] = {}
    required: list[str] = []
    if isinstance(params, dict):
        for name, spec in params.items():
            spec = spec if isinstance(spec, dict) else {}
            prop: dict[str, Any] = {}
            kind = spec.get("type")
            if kind in ("string", "number", "boolean", "array", "object", "integer"):
                prop["type"] = kind
            if kind == "array":
                prop["items"] = {}
            if isinstance(spec.get("description"), str):
                prop["description"] = spec["description"]
            props[name] = prop
            if spec.get("required"):
                required.append(name)
    out: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


LOCAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "studio_status",
        "description": "Whether a studio tab is paired and connected, which project it has open, and how many studio tools it offers. Call this first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_docs",
        "description": "The uxn·forge docs available to read_doc, with one line each.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_doc",
        "description": "Read one doc in full: engine (engine model and runtime contract), craft (the polished default look and how to draw it), etal (script language reference), tools (how the studio tools combine), examples (how the bundled examples are built), varvara (devices).",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "enum": list(DOC_NAMES)}}, "required": ["name"]},
    },
    {
        "name": "search_docs",
        "description": "Keyword search over the docs; returns the best-matching sections.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
    },
    {
        "name": "compile_check",
        "description": "Compile ETAL files on the server (no studio tab needed) and return {ok, diagnostics: [{file, line, col, msg}], rom_bytes}. For the open project use the studio's check_build instead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {"type": "object", "description": "path → source, e.g. {\"main.ux\": \"main :: fn() { }\"}"},
                "entry": {"type": "string", "description": "Entry file, default main.ux"},
            },
            "required": ["files"],
        },
    },
]
LOCAL_NAMES = {t["name"] for t in LOCAL_TOOLS}

DOC_LINES = {
    "engine": "Engine model: scenes, templates, events → actions, scripts contract, limits.",
    "craft": "The default polished style and the recipe to draw it.",
    "etal": "ETAL language reference and common compiler errors.",
    "tools": "How the studio tools fit together; the checking loop.",
    "examples": "How Chess, Life Lab, Sprout Run and Star Catcher are built.",
    "varvara": "Varvara devices (screen, audio, controller, mouse).",
}


def _text(value: Any, is_error: bool = False) -> dict[str, Any]:
    body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


def _compile_check(args: dict[str, Any]) -> dict[str, Any]:
    files = args.get("files")
    entry = args.get("entry") or "main.ux"
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return _text("files must be an object of path → source text", True)
    err = compiler.validate_files(files, entry)
    if err:
        return _text(f"bad files: {err.key} {err.params}", True)
    try:
        with limits.compile_slot():
            result = compiler.run_compile("web", "rom", entry, files)
    except ServerBusy:
        return _text("server busy compiling, try again shortly", True)
    rom = result.artifacts.get("rom", b"")
    return _text({"ok": result.ok, "diagnostics": result.diagnostics, "rom_bytes": len(rom)}, not result.ok)


async def _call(sha: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tab = registry.tab(sha)
    if name == "studio_status":
        if tab is None:
            return _text({"connected": False, "how": "Open the studio, then Agent → Connect an agent, and keep that tab open."})
        return _text({"connected": True, "project": tab.project, "tools": len(tab.tools)})
    if name == "list_docs":
        return _text(DOC_LINES)
    if name == "read_doc":
        doc = _doc(str(args.get("name", "")))
        return _text(doc) if doc else _text(f"unknown doc; one of {', '.join(DOC_NAMES)}", True)
    if name == "search_docs":
        limit = args.get("limit")
        return _text(search_docs(str(args.get("query", "")), int(limit) if isinstance(limit, int) and 0 < limit <= 10 else 4))
    if name == "compile_check":
        return await run_in_threadpool(_compile_check, args)
    if tab is None:
        return _text("The studio tab is not connected. Ask the user to open the studio and press Agent → Connect an agent (and keep the tab open).", True)
    if not any(t.get("name") == name for t in tab.tools):
        return _text(f"unknown tool '{name}'", True)
    try:
        res = await registry.call(sha, name, args)
    except LookupError as e:
        return _text(str(e), True)
    except TimeoutError:
        return _text(f"'{name}' got no answer from the studio tab in time", True)
    out = {k: res.get(k) for k in ("ok", "message", "data") if res.get(k) is not None}
    return _text(out, not res.get("ok", False))


def _tools(sha: str) -> list[dict[str, Any]]:
    tools = list(LOCAL_TOOLS)
    tab = registry.tab(sha)
    if tab is not None:
        for t in tab.tools:
            if t["name"] in LOCAL_NAMES:
                continue
            tools.append({"name": t["name"], "description": str(t.get("description", "")), "inputSchema": _schema(t.get("params"))})
    return tools


async def _dispatch(sha: str, msg: Any) -> dict[str, Any] | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    rid = msg.get("id")
    method = msg["method"]
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if rid is None:  # a notification (initialized, cancelled, …): nothing to answer
        return None

    def ok(result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({
            "protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}, "resources": {}},
            "serverInfo": {"name": "uxn-forge", "version": settings.etal_version},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tools(sha)})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not isinstance(name, str):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "tools/call needs a name"}}
        return ok(await _call(sha, name, args))
    if method == "resources/list":
        return ok({"resources": [{"uri": f"uxnforge://docs/{n}", "name": n, "description": DOC_LINES[n], "mimeType": "text/markdown"} for n in DOC_NAMES]})
    if method == "resources/read":
        uri = str(params.get("uri", ""))
        doc = _doc(uri.rsplit("/", 1)[-1]) if uri.startswith("uxnforge://docs/") else None
        if doc is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown resource {uri}"}}
        return ok({"contents": [{"uri": uri, "mimeType": "text/markdown", "text": doc}]})
    if method == "prompts/list":
        return ok({"prompts": []})
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("key")


async def handle(request: Request) -> Response:
    row = lookup(_bearer(request))
    if row is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "missing or expired bridge key: create one in the studio (Agent → Connect an agent)"}},
            status_code=401,
        )
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    headers = {}
    if isinstance(body, dict) and body.get("method") == "initialize":
        headers["Mcp-Session-Id"] = secrets.token_hex(16)
    if isinstance(body, list):
        replies = [r for r in [await _dispatch(row.sha, m) for m in body] if r is not None]
        return JSONResponse(replies, headers=headers) if replies else Response(status_code=202)
    reply = await _dispatch(row.sha, body)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply, headers=headers)


"""Agent bridge + MCP: a fake studio tab on the socket, an MCP client
over HTTP, and the relay between them."""
from __future__ import annotations

import threading

from tests.test_api import client  # shares the sqlite test database


def _key() -> str:
    r = client.post("/bridge/keys")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key"].startswith("uxf_")
    assert body["mcp_url"].endswith("/mcp")
    return body["key"]


def _rpc(key: str, method: str, params: dict | None = None, rid: int = 1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return client.post("/mcp", json=msg, headers={"Authorization": f"Bearer {key}"})


TAB_TOOLS = [
    {"name": "describe", "description": "Compact project snapshot.", "params": {}},
    {
        "name": "draw_sprite",
        "description": "Draw art from shapes.",
        "params": {"name": {"type": "string", "required": True}, "ops": {"type": "array", "required": True}},
    },
]


def test_mcp_needs_a_key():
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert _rpc("uxf_nope", "ping").status_code == 401


def test_docs_and_compile_work_without_a_tab():
    key = _key()
    init = _rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert "craft" in init["result"]["instructions"]
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"Authorization": f"Bearer {key}"}).status_code == 202

    names = {t["name"] for t in _rpc(key, "tools/list").json()["result"]["tools"]}
    assert {"studio_status", "read_doc", "search_docs", "compile_check"} <= names
    assert "describe" not in names  # no tab yet

    status = _rpc(key, "tools/call", {"name": "studio_status", "arguments": {}}).json()["result"]
    assert '"connected": false' in status["content"][0]["text"]
    craft = _rpc(key, "tools/call", {"name": "read_doc", "arguments": {"name": "craft"}}).json()["result"]
    assert "draw_sprite" in craft["content"][0]["text"]
    hits = _rpc(key, "tools/call", {"name": "search_docs", "arguments": {"query": "zero page overflow"}}).json()["result"]
    assert "zero" in hits["content"][0]["text"].lower()

    good = _rpc(key, "tools/call", {"name": "compile_check", "arguments": {"files": {"main.ux": 'main :: fn() {\n    print("hi");\n}\n'}}}).json()["result"]
    assert good["isError"] is False
    bad = _rpc(key, "tools/call", {"name": "compile_check", "arguments": {"files": {"main.ux": "main :: fn() { nope(); }\n"}}}).json()["result"]
    assert bad["isError"] is True

    res = _rpc(key, "resources/list").json()["result"]["resources"]
    assert any(r["uri"] == "uxnforge://docs/engine" for r in res)
    doc = _rpc(key, "resources/read", {"uri": "uxnforge://docs/etal"}).json()["result"]["contents"][0]
    assert doc["mimeType"] == "text/markdown"
    # Studio tools without a tab are a clear error, not a hang.
    miss = _rpc(key, "tools/call", {"name": "describe", "arguments": {}}).json()["result"]
    assert miss["isError"] is True and "not connected" in miss["content"][0]["text"]


def test_relays_studio_tools_to_the_tab():
    from fastapi.testclient import TestClient

    from app.main import app

    key = _key()

    def _rpc(key: str, method: str, params: dict | None = None, rid: int = 1):
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params is not None else {})}
        return shared.post("/mcp", json=msg, headers={"Authorization": f"Bearer {key}"})

    # One event loop for the socket and the requests, as in production
    # (the bare TestClient runs each request on a loop of its own).
    with TestClient(app) as shared, shared.websocket_connect(f"/ws/bridge?key={key}") as tab:
        assert tab.receive_json() == {"type": "ready"}
        tab.send_json({"type": "hello", "tools": TAB_TOOLS, "project": {"id": "p1", "name": "Life Lab"}})
        # The hello is processed before the next request lands.
        status = _rpc(key, "tools/call", {"name": "studio_status", "arguments": {}}).json()["result"]
        assert "Life Lab" in status["content"][0]["text"]

        tools = {t["name"]: t for t in _rpc(key, "tools/list").json()["result"]["tools"]}
        assert tools["draw_sprite"]["inputSchema"]["required"] == ["name", "ops"]
        assert tools["draw_sprite"]["inputSchema"]["properties"]["ops"]["type"] == "array"

        out: dict = {}

        def agent() -> None:
            out["r"] = _rpc(key, "tools/call", {"name": "draw_sprite", "arguments": {"name": "orb", "ops": [{"op": "disc"}]}}, rid=7).json()

        th = threading.Thread(target=agent)
        th.start()
        call = tab.receive_json()
        assert call["type"] == "call" and call["name"] == "draw_sprite"
        assert call["args"]["name"] == "orb"
        tab.send_json({"type": "result", "id": call["id"], "ok": True, "message": "drew orb", "data": {"id": "orb"}})
        th.join(10)
        result = out["r"]["result"]
        assert result["isError"] is False
        assert "drew orb" in result["content"][0]["text"]

        unknown = _rpc(key, "tools/call", {"name": "rm_rf", "arguments": {}}).json()["result"]
        assert unknown["isError"] is True


def test_bad_or_revoked_keys_are_refused_on_the_socket():
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/bridge?key=uxf_wrong") as ws:
            ws.receive_json()
    key = _key()
    assert client.delete("/bridge/keys", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert _rpc(key, "ping").status_code == 401

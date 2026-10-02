"""Mock OpenAI-compatible endpoint for local agent testing.

Not part of the app: with
`LLM_DEV_BASE_URL=http://127.0.0.1:11500/v1 uv run uvicorn app.main:app`
the agent routes here, so the whole path (frontend → /agent/turn →
provider) can be exercised with no key, no network and no spend.
The endpoint is free tier and `local`, so it answers before any
hosted provider; the variable is read per turn, so it can be set
(here) while the server runs. Answers with one tool call on the first
turn and text after, to prove the loop closes.

Usage: uv run python scripts/mock_llm.py [port]   (default 11500 —
11434 is usually taken by a local Ollama)
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11500
STATE = {"tools_seen": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        tool_results = [m for m in messages if m.get("role") == "tool"]
        print(
            f"[mock] {body.get('model')} · {len(messages)} msgs"
            f" · {len(body.get('tools') or [])} tools · {len(tool_results)} results"
        )
        if tool_results:
            message = {
                "role": "assistant",
                "content": "Added the knight sprite and put it on the play scene.",
            }
        else:
            message = {
                "role": "assistant",
                "content": "One knight sprite, then the scene.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "create_sprite",
                            "arguments": json.dumps({"name": "knight", "w": 8, "h": 8}),
                        },
                    }
                ],
            }
        payload = {
            "id": "mock",
            "model": body.get("model", "local"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 24},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    print(f"mock openai-compatible endpoint on http://127.0.0.1:{PORT}/v1")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

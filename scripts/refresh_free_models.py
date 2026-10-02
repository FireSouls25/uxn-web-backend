"""Print OpenRouter's current free models — a snapshot helper, not app code.

The free lineup changes often and the registry (`app/llm_registry.py`)
is a checked-in snapshot of it, so refresh it when agent replies start
looking dumber than they should. Network-only, no key, never run by
the tests:

    uv run python scripts/refresh_free_models.py

Paste the ids into the `openrouter` row. Two rules the tests enforce,
so check them before pasting: a `free: true` model must end in
`:free` (that suffix is the whole reason it is free), and only models
that support tool calling belong in the list at all.
"""
from __future__ import annotations

import json
import sys
import urllib.request

MODELS_URL = "https://openrouter.ai/api/v1/models"


def fetch() -> list[dict]:
    with urllib.request.urlopen(MODELS_URL, timeout=30) as r:  # noqa: S310 (fixed https url)
        return json.load(r)["data"]


def main() -> int:
    try:
        models = fetch()
    except OSError as e:
        print(f"could not reach {MODELS_URL}: {e}")
        return 1
    free = [m for m in models if m["id"].endswith(":free")]
    with_tools = [m for m in free if "tools" in (m.get("supported_parameters") or [])]
    print(f"{len(models)} models, {len(free)} free, {len(with_tools)} free with tool calling\n")
    for m in sorted(with_tools, key=lambda m: m["id"]):
        print(f'{{"id": "{m["id"]}", "tools": True, "free": True, "tier": TIER_FREE, "quality": 2}},')
    skipped = [m["id"] for m in free if m not in with_tools]
    if skipped:
        print(f"\nskipped (no tool calling): {', '.join(sorted(skipped))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

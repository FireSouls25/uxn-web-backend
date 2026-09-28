"""Concurrency slots + per-key rate limiting (in-memory, single worker).

Limits read live settings so tests can tune them. Multi-worker
deployments need a shared store (Redis); until then run one uvicorn
worker (see docs/operations.md).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from .config import settings


class ServerBusy(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


_slot_lock = threading.Lock()
_slots_in_use = 0


@contextmanager
def compile_slot() -> Iterator[None]:
    global _slots_in_use
    with _slot_lock:
        if _slots_in_use >= settings.max_concurrent_compiles:
            raise ServerBusy()
        _slots_in_use += 1
    try:
        yield
    finally:
        with _slot_lock:
            _slots_in_use -= 1


_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def check_rate_limit(key: str, limit: int | None = None) -> None:
    limit = settings.rate_limit_per_minute if limit is None else limit
    if limit <= 0:
        return
    now = time.monotonic()
    with _hits_lock:
        recent = [ts for ts in _hits.get(key, []) if now - ts < 60.0]
        if len(recent) >= limit:
            oldest = min(recent)
            raise RateLimited(retry_after=max(1, int(60.0 - (now - oldest)) + 1))
        recent.append(now)
        _hits[key] = recent


def reset_limits() -> None:
    global _slots_in_use
    with _slot_lock:
        _slots_in_use = 0
    with _hits_lock:
        _hits.clear()

"""Response cache and rate limiting for model calls.

Hosted model tiers have per-minute request limits and on-device models are slow. Matches are seeded
and deterministic, so a validated model answer for a given moment can be reused: replays (and demo
retakes) are instant and free, and `python -m mcr.agents.prewarm` can fill the cache ahead of time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, Any] = {}
        if path and path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def key(model: str, agent: str, payload: dict) -> str:
        stable = {k: v for k, v in payload.items() if k not in ("context", "feedback")}
        raw = json.dumps([model, agent, stable], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        if self.path is None:
            return
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


class RateLimiter:
    """Spaces calls so at most `rpm` start per minute (shared by all agents of one crew)."""

    def __init__(self, rpm: float | None):
        self.interval = 60.0 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self.interval:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


def default_cache(backend: str) -> ResponseCache:
    if backend == "offline" or os.getenv("MCR_CACHE", "1") == "0":
        return ResponseCache(None)
    base = Path(os.getenv("MCR_CACHE_DIR", Path(__file__).resolve().parents[2] / ".cache"))
    return ResponseCache(base / f"responses-{backend}.json")


def default_limiter(backend: str) -> RateLimiter:
    rpm = os.getenv("MCR_LLM_RPM")
    if rpm:
        return RateLimiter(float(rpm))
    return RateLimiter(None)


_slots: dict[str, asyncio.Semaphore] = {}


def call_slots(backend: str) -> asyncio.Semaphore | None:
    """Process-wide cap on concurrent model calls. On-device models (Foundry Local) crash under parallel
    requests, so they default to one call at a time; MCR_LLM_CONCURRENCY overrides."""
    n = int(os.getenv("MCR_LLM_CONCURRENCY", "1" if backend == "foundry_local" else "0"))
    if n <= 0:
        return None
    if backend not in _slots:
        _slots[backend] = asyncio.Semaphore(n)
    return _slots[backend]

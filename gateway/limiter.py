"""Concurrency semaphore and per-key minute-window rate limiting."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_concurrency: int = 8):
        self._max = max(1, max_concurrency)
        self._pending: int | None = None
        self._sem = asyncio.Semaphore(self._max)
        self._active = 0
        self._hits: dict[int, deque] = defaultdict(deque)

    def configure(self, max_concurrency: int) -> None:
        # Defer the semaphore swap until fully drained: releases from in-flight
        # requests would otherwise land on the new semaphore and inflate its capacity.
        self._pending = max(1, max_concurrency)
        self._apply_pending()

    def _apply_pending(self) -> None:
        if self._pending is not None and self._active == 0:
            if self._pending != self._max:
                self._max = self._pending
                self._sem = asyncio.Semaphore(self._max)
            self._pending = None

    @property
    def active(self) -> int:
        return self._active

    async def acquire(self) -> None:
        await self._sem.acquire()
        self._active += 1

    def release(self) -> None:
        if self._active > 0:
            self._active -= 1
            self._sem.release()
            self._apply_pending()

    def check_key(self, key_id: int, rpm: int | None) -> bool:
        """True when allowed; consumes one slot of the minute window."""
        if rpm is None:
            return True
        now = time.time()
        dq = self._hits[key_id]
        while dq and dq[0] < now - 60:
            dq.popleft()
        if len(dq) >= rpm:
            return False
        dq.append(now)
        return True

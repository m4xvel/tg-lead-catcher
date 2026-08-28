"""Глобальный лимит отправки (R62): скользящее окно 60с в памяти процесса worker.

Не читает `delivery_queue` для подсчёта — считает в памяти, потому что `worker`
живёт одним процессом внутри `userbot` и это единственный отправитель.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit_per_min: int, *, window_seconds: float = 60.0) -> None:
        self.limit_per_min = max(limit_per_min, 1)
        self._window = window_seconds
        self._sent_at: deque[float] = deque()

    def allow(self, now: float) -> bool:
        """Пробует занять слот на момент `now` (монотонные секунды). True — слот занят."""
        self._trim(now)
        if len(self._sent_at) >= self.limit_per_min:
            return False
        self._sent_at.append(now)
        return True

    def seconds_until_slot(self, now: float) -> float:
        self._trim(now)
        if len(self._sent_at) < self.limit_per_min:
            return 0.0
        return max(self._sent_at[0] + self._window - now, 0.0)

    def _trim(self, now: float) -> None:
        cutoff = now - self._window
        while self._sent_at and self._sent_at[0] <= cutoff:
            self._sent_at.popleft()

    async def wait_for_slot(self, *, now=time.monotonic, sleep=asyncio.sleep) -> None:
        """Блокируется, пока не появится свободный слот, затем занимает его."""
        current = now()
        while not self.allow(current):
            wait = self.seconds_until_slot(current)
            await sleep(wait if wait > 0 else 0.001)
            current = now()

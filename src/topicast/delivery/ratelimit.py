"""Async token buckets that keep us under Telegram's flood limits."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class TokenBucket:
    """Classic token bucket. `acquire()` waits until a token is available.

    `pause(seconds)` blocks the bucket entirely (used when Telegram answers 429).
    """

    def __init__(
        self,
        rate: float,
        per: float,
        *,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate <= 0 or per <= 0:
            msg = "rate and per must be positive"
            raise ValueError(msg)
        self._fill_rate = rate / per
        self._capacity = capacity if capacity is not None else max(1.0, min(rate, rate / per))
        self._tokens = self._capacity
        self._clock = clock
        self._updated = clock()
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        self._tokens = min(self._capacity, self._tokens + elapsed * self._fill_rate)
        self._updated = now

    def delay(self) -> float:
        """Seconds to wait before a token is available (0 if one is available now)."""
        now = self._clock()
        self._refill(now)
        pause = max(0.0, self._paused_until - now)
        if self._tokens >= 1:
            return pause
        return max(pause, (1 - self._tokens) / self._fill_rate)

    async def acquire(self) -> None:
        async with self._lock:
            while (wait := self.delay()) > 0:
                await asyncio.sleep(wait)
            self._tokens -= 1

    def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, self._clock() + seconds)


class RateLimiter:
    """One bucket per bot (global limit) and one per chat."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def configure(self, key: str, rate: float, per: float) -> None:
        self._buckets[key] = TokenBucket(rate, per)

    async def acquire(self, *keys: str) -> None:
        # Take the chat token first so a busy chat doesn't hold the bot's global tokens.
        for key in reversed(keys):
            bucket = self._buckets.get(key)
            if bucket is not None:
                await bucket.acquire()

    def pause(self, key: str, seconds: float) -> None:
        bucket = self._buckets.get(key)
        if bucket is not None:
            bucket.pause(seconds)


def bot_key(bot: str) -> str:
    return f"bot:{bot}"


def chat_key(chat: str) -> str:
    return f"chat:{chat}"

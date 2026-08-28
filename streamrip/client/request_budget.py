"""Asynchronous fixed-interval request budget."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

DEFAULT_429_STRIKE_LIMIT = 12


class RateLimitGuard:
    """Count 429 responses and trip once at a bounded per-run threshold."""

    def __init__(self, strike_limit: int = DEFAULT_429_STRIKE_LIMIT) -> None:
        if strike_limit <= 0:
            raise ValueError("strike_limit must be positive")
        self.strike_limit = strike_limit
        self.strikes = 0
        self.tripped = False

    def note_rate_limited(self) -> bool:
        self.strikes += 1
        if self.strikes >= self.strike_limit and not self.tripped:
            self.tripped = True
            return True
        return False


class SharedRequestBudget:
    """Pace all API requests sharing this instance at one combined RPM."""

    def __init__(
        self,
        requests_per_minute: int = 60,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        rpm = requests_per_minute if requests_per_minute > 0 else 60
        self.interval = 60.0 / rpm
        self._clock = clock
        self._sleep = sleeper or asyncio.sleep
        self._jitter = jitter or (lambda: random.uniform(0, 0.3))
        self._last = float("-inf")
        self._lock = asyncio.Lock()
        self.request_count = 0

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        return asyncio.get_running_loop().time()

    async def acquire(self) -> None:
        """Wait for and consume exactly one real API-request slot."""

        async with self._lock:
            wait = self.interval - (self._now() - self._last) + self._jitter()
            if wait > 0:
                await self._sleep(wait)
            self._last = self._now()
            self.request_count += 1

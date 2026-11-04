"""Retry policy with exponential backoff and jitter."""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class RetryPolicy:
    attempts: int = 3                 # total attempts, not extra ones
    base_delay: float = 0.4
    factor: float = 2.0
    max_delay: float = 8.0
    jitter: float = 0.2               # +/- fraction of the computed delay
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    @classmethod
    def from_settings(cls, settings) -> RetryPolicy:
        return cls(attempts=max(1, settings.max_retries + 1), base_delay=settings.retry_base_delay)

    def delay_for(self, attempt: int) -> float:
        """attempt is 1-based: the delay after attempt N before attempt N+1."""
        raw = min(self.max_delay, self.base_delay * (self.factor ** max(0, attempt - 1)))
        if self.jitter <= 0:
            return raw
        spread = raw * self.jitter
        return max(0.0, raw + random.uniform(-spread, spread))


async def call_with_retry(
    fn: Callable[[int], Awaitable[Any]],
    policy: RetryPolicy,
    *,
    on_retry: Callable[[int, BaseException, float], Awaitable[None]] | None = None,
) -> Any:
    """Run fn(attempt) until it succeeds, the attempts run out, or it is cancelled."""
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn(attempt)
        except asyncio.CancelledError:
            raise
        except policy.retry_on as exc:  # noqa: PERF203 - retry loop is the point
            last = exc
            if attempt >= policy.attempts:
                break
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                await on_retry(attempt, exc, delay)
            if delay > 0:
                await asyncio.sleep(delay)
    assert last is not None
    raise last

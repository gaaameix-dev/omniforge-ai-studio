"""Token-bucket rate limiting, per identity or per client address."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    per_minute: int = 240
    burst: int = 60
    _buckets: dict[str, Bucket] = field(default_factory=dict)

    @property
    def rate_per_second(self) -> float:
        return max(0.001, self.per_minute / 60.0)

    def check(self, key: str, cost: float = 1.0, now: float | None = None) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        if self.per_minute <= 0:
            return True, 0.0
        now = now if now is not None else time.monotonic()
        bucket = self._buckets.get(key)
        capacity = float(max(self.burst, 1))
        if bucket is None:
            bucket = Bucket(tokens=capacity, updated=now)
            self._buckets[key] = bucket
        elapsed = max(0.0, now - bucket.updated)
        bucket.tokens = min(capacity, bucket.tokens + elapsed * self.rate_per_second)
        bucket.updated = now
        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True, 0.0
        deficit = cost - bucket.tokens
        return False, round(deficit / self.rate_per_second, 2)

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)

    def size(self) -> int:
        return len(self._buckets)

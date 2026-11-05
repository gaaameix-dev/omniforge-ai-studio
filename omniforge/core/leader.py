"""Leader lease so only one scheduler ticks when several replicas run.

A single row in the `leases` table, taken with one conditional UPDATE. It is not a
consensus protocol: it stops two schedulers from firing the same schedule twice,
which is the failure that actually hurts.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


def _iso(offset_seconds: float = 0) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.isoformat(timespec="seconds")


class LeaderLease:
    def __init__(self, store, name: str = "scheduler", owner: str | None = None,
                 ttl_seconds: int = 60) -> None:
        self.store = store
        self.name = name
        self.owner = owner or f"{name}-{int(time.time()) % 100000}"
        self.ttl_seconds = ttl_seconds

    def acquire(self) -> bool:
        return self.store.acquire_lease(self.name, self.owner, _iso(self.ttl_seconds), _iso())

    def renew(self) -> bool:
        return self.store.renew_lease(self.name, self.owner, _iso(self.ttl_seconds), _iso())

    def release(self) -> bool:
        return self.store.release_leader(self.name, self.owner)

    def holder(self) -> str | None:
        record = self.store.get_lease(self.name)
        if not record:
            return None
        if record.get("expires_at") and record["expires_at"] < _iso():
            return None
        return record.get("owner")

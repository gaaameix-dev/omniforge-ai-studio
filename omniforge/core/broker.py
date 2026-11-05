"""Queue backends for workers.

Both brokers expose the same small synchronous surface (`enqueue`, `claim`,
`release`, `stats`) because redis-py and SQLite are both synchronous; the worker
calls them from inside its async loop.

The database claim in `RunStore` is the default and needs nothing. A Redis list is
available when several API replicas and workers share a broker: the run id travels
through Redis, the lease lives in Redis too, and a crashed worker's id is requeued
after the lease expires.
"""
from __future__ import annotations

import json
import time
from typing import Any


class BrokerError(RuntimeError):
    pass


class DbBroker:
    """Default: the runs table is the queue."""

    name = "database"

    def __init__(self, store, lease_seconds: int = 300) -> None:
        self.store = store
        self.lease_seconds = lease_seconds

    def enqueue(self, run_id: str) -> None:
        return None  # the row is already the queue entry

    def claim(self, worker_id: str, lease_seconds: int | None = None) -> dict | None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        expires = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                time.gmtime(time.time() + (lease_seconds or self.lease_seconds)))
        return self.store.claim_next_run(worker_id, lease_seconds or self.lease_seconds, now, expires)

    def release(self, run_id: str) -> None:
        self.store.release_lease(run_id)

    def stats(self) -> dict[str, Any]:
        return {"backend": self.name, "queued": self.store.queued_count()}


class RedisBroker:
    """Redis list + lease keys. Works with redis-py and fakeredis."""

    name = "redis"

    def __init__(self, client, *, queue: str = "omniforge:queue",
                 processing: str = "omniforge:processing", lease_seconds: int = 300) -> None:
        self.client = client
        self.queue = queue
        self.processing = processing
        self.lease_seconds = lease_seconds

    def enqueue(self, run_id: str) -> None:
        self.client.rpush(self.queue, run_id)

    def claim(self, worker_id: str, lease_seconds: int | None = None) -> dict | None:
        ttl = lease_seconds or self.lease_seconds
        run_id = self.client.lmove(self.queue, self.processing, "LEFT", "RIGHT")
        if not run_id:
            return None
        if isinstance(run_id, bytes):
            run_id = run_id.decode()
        self.client.hset(f"{self.processing}:leases", run_id,
                         json.dumps({"worker": worker_id, "until": time.time() + ttl}))
        if ttl > 0:                     # a negative ttl means "already expired"
            self.client.expire(f"{self.processing}:leases", max(60, int(ttl * 2)))
        return {"id": run_id, "worker": worker_id, "lease_seconds": ttl}

    def release(self, run_id: str) -> None:
        self.client.hdel(f"{self.processing}:leases", run_id)
        self.client.lrem(self.processing, 0, run_id)

    def requeue_expired(self) -> list[str]:
        leases = self.client.hgetall(f"{self.processing}:leases") or {}
        now = time.time()
        requeued = []
        for raw_id, raw_lease in leases.items():
            run_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            try:
                lease = json.loads(raw_lease)
            except (TypeError, ValueError):
                continue
            if float(lease.get("until", 0)) < now:
                self.client.hdel(f"{self.processing}:leases", run_id)
                self.client.lrem(self.processing, 0, run_id)
                self.client.rpush(self.queue, run_id)
                requeued.append(run_id)
        return requeued

    def stats(self) -> dict[str, Any]:
        return {"backend": self.name, "queued": int(self.client.llen(self.queue)),
                "in_flight": int(self.client.llen(self.processing))}


def build_broker(settings, store):
    """Redis when a URL is configured and the driver is installed, else the database."""
    url = getattr(settings, "broker_url", "")
    if not url:
        return DbBroker(store)
    try:
        import redis
    except ImportError:
        raise BrokerError("OMNIFORGE_BROKER_URL is set but redis-py is not installed") from None
    client = redis.Redis.from_url(url, decode_responses=False)
    return RedisBroker(client, lease_seconds=getattr(settings, "worker_lease_seconds", 300))

"""Queue-backed workers.

With `OMNIFORGE_WORKER_MODE=true` a submitted run is written to the database as
`queued` instead of executing in the API process. A worker claims it with a lease,
runs it, and releases the lease. Several workers can share one database: the claim
is a single atomic UPDATE, so a run is only ever picked up once, and an expired
lease is requeued.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("omniforge.worker")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Worker:
    def __init__(self, orchestrator, *, worker_id: str | None = None,
                 lease_seconds: int = 300, poll_seconds: float = 0.5) -> None:
        self.orchestrator = orchestrator
        self.store = orchestrator.store
        self.worker_id = worker_id or f"worker-{os.getpid()}-{int(time.time()) % 10000}"
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.processed = 0
        self.failures = 0
        self.broker = None
        if getattr(orchestrator.settings, "broker_url", ""):
            from .broker import build_broker

            self.broker = build_broker(orchestrator.settings, self.store)

    # ------------------------------------------------------------------ claim
    def _lease_window(self) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=self.lease_seconds)).isoformat(timespec="seconds")
        return now.isoformat(timespec="seconds"), expires

    def claim(self) -> dict | None:
        if self.broker is not None and self.broker.name == "redis":
            claimed = self.broker.claim(self.worker_id, self.lease_seconds)
            if not claimed:
                return None
            record = self.store.get_run(claimed["id"])
            if record is None:
                self.broker.release(claimed["id"])
                return None
            return record
        now, expires = self._lease_window()
        return self.store.claim_next_run(self.worker_id, self.lease_seconds, now, expires)

    # ------------------------------------------------------------------- work
    async def run_once(self) -> dict | None:
        self.store.requeue_expired(utcnow())
        record = self.claim()
        if record is None:
            return None
        run = self.orchestrator._rehydrate(record)
        plan_known = run.plan is not None and bool(run.plan.tasks)
        run.status = self.orchestrator_run_status()
        self.orchestrator.runs[run.id] = run
        from .budget import Budget

        budget = Budget(
            limit_tokens=self.orchestrator.settings.budget_tokens,
            price_per_1k_input=self.orchestrator.settings.price_per_1k_input,
            price_per_1k_output=self.orchestrator.settings.price_per_1k_output,
        )
        budget.add(run.tokens, 0)
        self.orchestrator._budgets[run.id] = budget
        try:
            await self.orchestrator._drive(run, resume=plan_known)
            self.processed += 1
            self.orchestrator.metrics.inc("worker.processed")
            return {"run_id": run.id, "status": run.status.value, "worker": self.worker_id}
        except Exception as exc:  # noqa: BLE001 - a worker must survive a bad run
            self.failures += 1
            log.exception("worker %s failed on run %s", self.worker_id, run.id)
            self.orchestrator.metrics.inc("worker.failures")
            return {"run_id": run.id, "status": "error", "error": str(exc),
                    "worker": self.worker_id}
        finally:
            if self.broker is not None and self.broker.name == "redis":
                self.broker.release(run.id)
            self.store.release_lease(run.id)

    def orchestrator_run_status(self):
        from .tasks import RunStatus

        return RunStatus.RUNNING

    async def run_forever(self, *, max_runs: int | None = None) -> dict:
        started = time.time()
        while max_runs is None or self.processed < max_runs:
            result = await self.run_once()
            if result is None:
                await asyncio.sleep(self.poll_seconds)
                continue
        return self.status(started)

    def status(self, started: float | None = None) -> dict:
        payload = {
            "worker_id": self.worker_id,
            "processed": self.processed,
            "failures": self.failures,
            "queued": self.store.queued_count(),
            "lease_seconds": self.lease_seconds,
        }
        if started:
            payload["uptime_s"] = round(time.time() - started, 1)
        return payload

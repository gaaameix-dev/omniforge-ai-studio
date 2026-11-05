"""Scheduled runs.

A schedule stores either a goal (planned fresh) or a workflow (replayed plan).
The loop is explicit and testable: `tick_once()` finds due schedules, fires them
and computes the next run time, so tests never sleep on a background timer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("omniforge.scheduler")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def compute_next(schedule: dict, now: datetime | None = None) -> str:
    """Interval schedules add their period; daily schedules hit the next HH:MM."""
    now = now or datetime.now(timezone.utc)
    kind = schedule.get("kind") or "interval"
    if kind == "daily":
        hour = int(schedule.get("at_hour") if schedule.get("at_hour") is not None else 9)
        minute = int(schedule.get("at_minute") or 0)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")
    seconds = max(30, int(schedule.get("every_seconds") or 3600))
    return (now + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class Scheduler:
    def __init__(self, orchestrator, store, *, tick_seconds: float = 5.0) -> None:
        self.orchestrator = orchestrator
        self.store = store
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self.fired: list[dict] = []
        self.leader = None

    # ------------------------------------------------------------------ loop
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="omniforge-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001 - the loop must survive a bad schedule
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    # ------------------------------------------------------------------ tick
    async def tick_once(self, now: str | None = None) -> list[dict]:
        if self.leader is not None:
            if not (self.leader.renew() or self.leader.acquire()):
                return []          # another replica is the scheduler
        now = now or utcnow()
        fired: list[dict] = []
        for schedule in self.store.due_schedules(now):
            result = await self.fire(schedule, now=now)
            fired.append(result)
        self.fired.extend(fired)
        self.fired = self.fired[-100:]
        return fired

    async def fire(self, schedule: dict, *, now: str | None = None) -> dict:
        now = now or utcnow()
        goal = schedule.get("goal") or ""
        run = None
        error = None
        try:
            if (schedule.get("job") or "run") == "backup":
                from .backup import create_backup

                data_dir = Path(self.orchestrator.settings.data_dir)
                archive = create_backup(data_dir)
                self.store.add_audit(None, "backup.scheduled", str(archive.get("archive")),
                                     f"{archive.get('files')} files", None, now)
                run = None
                return {
                    "schedule_id": schedule["id"],
                    "run_id": None,
                    "backup": archive.get("archive"),
                    "next_run_at": compute_next(schedule, parse(now)),
                    "error": None,
                }
            if schedule.get("workflow_id"):
                workflow = self.store.get_workflow(schedule["workflow_id"])
                if workflow is None:
                    error = "workflow not found"
                else:
                    run = await self.orchestrator.run_workflow(
                        workflow, {}, project_id=schedule.get("project_id"),
                        meta={"schedule_id": schedule["id"]},
                    )
            else:
                run = await self.orchestrator.submit(
                    goal or f"Scheduled run: {schedule['name']}",
                    auto_approve=True,
                    project_id=schedule.get("project_id"),
                    meta={"schedule_id": schedule["id"]},
                )
        except Exception as exc:  # noqa: BLE001 - reported on the schedule row
            error = f"{type(exc).__name__}: {exc}"
            log.warning("schedule %s failed to fire: %s", schedule.get("id"), error)

        next_at = compute_next(schedule, parse(now))
        self.store.update_schedule_run(schedule["id"], now, next_at, run.id if run else None)
        self.orchestrator.metrics.inc("schedules.fired")
        if error:
            self.orchestrator.metrics.inc("schedules.failed")
        return {"schedule_id": schedule["id"], "run_id": run.id if run else None,
                "next_run_at": next_at, "error": error}

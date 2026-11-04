"""Load harness: run N goals at a given concurrency and report the distribution.

It exercises the real orchestrator (planning, routing, review, persistence) with
whatever model backend is configured. In mock mode it is a pipeline test; with a
live key it measures the model path too.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

DEFAULT_GOALS = [
    "Build a mobile puzzle game with 20 levels",
    "Design a postgres schema for invoicing",
    "Write a launch plan for a SaaS dashboard",
    "Produce a test plan for a web storefront",
    "Prototype a Unity VR training simulator",
]


async def run_load(
    orchestrator,
    *,
    runs: int = 5,
    concurrency: int = 3,
    goals: list[str] | None = None,
    auto_approve: bool = True,
) -> dict[str, Any]:
    goals = goals or DEFAULT_GOALS
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[dict[str, Any]] = []
    started_all = time.perf_counter()

    async def one(index: int) -> None:
        goal = goals[index % len(goals)]
        async with semaphore:
            started = time.perf_counter()
            try:
                run = await orchestrator.run_to_completion(goal, auto_approve=auto_approve)
                results.append({
                    "run_id": run.id,
                    "status": run.status.value,
                    "duration_s": round(time.perf_counter() - started, 3),
                    "tasks": len(run.plan.tasks) if run.plan else 0,
                    "tokens": run.tokens,
                    "error": run.error,
                })
            except Exception as exc:  # noqa: BLE001 - a load test records failures
                results.append({"run_id": None, "status": "error",
                                "duration_s": round(time.perf_counter() - started, 3),
                                "tasks": 0, "tokens": 0, "error": f"{type(exc).__name__}: {exc}"})

    await asyncio.gather(*(one(i) for i in range(runs)))
    total = time.perf_counter() - started_all
    durations = sorted(r["duration_s"] for r in results)
    done = [r for r in results if r["status"] == "done"]

    def percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, int(round((p / 100) * (len(values) - 1)))))
        return round(values[index], 3)

    return {
        "runs": len(results),
        "concurrency": concurrency,
        "done": len(done),
        "failed": len(results) - len(done),
        "wall_time_s": round(total, 3),
        "throughput_runs_per_s": round(len(results) / total, 3) if total else 0.0,
        "latency": {
            "min": durations[0] if durations else 0.0,
            "p50": percentile(durations, 50),
            "p95": percentile(durations, 95),
            "max": durations[-1] if durations else 0.0,
            "mean": round(statistics.fmean(durations), 3) if durations else 0.0,
        },
        "tokens": sum(r["tokens"] for r in results),
        "results": results,
    }

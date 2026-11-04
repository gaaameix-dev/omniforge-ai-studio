"""Eval runner: fresh orchestrator per case, explicit pass/fail checks."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from ..agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
from ..config import Settings
from ..core.llm import build_llm
from ..core.metrics import Metrics
from ..core.orchestrator import Orchestrator
from ..core.store import RunStore
from .fixtures import CASES


def _settings(data_dir: Path, base: Settings | None = None) -> Settings:
    cfg = base or Settings.from_env()
    return Settings(
        llm_base_url=cfg.llm_base_url, llm_api_key=cfg.llm_api_key, llm_model=cfg.llm_model,
        mock_llm=cfg.mock_llm, data_dir=data_dir, max_parallel=cfg.max_parallel,
        max_retries=cfg.max_retries, retry_base_delay=0.0, review_enabled=cfg.review_enabled,
        max_rework=cfg.max_rework, budget_tokens=cfg.budget_tokens, max_tasks_per_run=cfg.max_tasks_per_run,
    )


def build_orchestrator(settings: Settings) -> Orchestrator:
    skills = build_skill_registry()
    store = RunStore(settings.db_path)
    return Orchestrator(
        settings=settings, llm=build_llm(settings), skills=skills,
        agents=build_agent_registry(skills), tools=build_tool_registry(str(settings.data_dir), settings),
        store=store, metrics=Metrics(),
    )


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


async def run_case(case: dict[str, Any], base: Settings | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="omniforge-eval-") as tmp:
        settings = _settings(Path(tmp), base)
        orch = build_orchestrator(settings)
        run = await orch.run_to_completion(case["goal"], auto_approve=True)
        expect = case.get("expect", {})
        tasks = run.plan.tasks if run.plan else []
        done = [t for t in tasks if t.status.value == "done"]
        checks: list[dict[str, Any]] = [
            _check("status", run.status.value == expect.get("status", "done"), run.status.value),
            _check("min_done", len(done) >= expect.get("min_done", 1), f"{len(done)} done"),
            _check("max_tasks", len(tasks) <= expect.get("max_tasks", 99), f"{len(tasks)} tasks"),
            _check(
                "no_task_left_running",
                all(t.status.value not in {"running", "pending"} for t in tasks),
                ",".join(sorted({t.status.value for t in tasks})),
            ),
        ]
        for department in expect.get("departments", []):
            checks.append(
                _check(f"department:{department}", any(t.department == department for t in done),
                       "present" if any(t.department == department for t in done) else "missing")
            )
        floor = expect.get("min_review_score")
        if floor is not None:
            scored = [t.review_score for t in done if t.review_score is not None]
            worst = min(scored) if scored else 0.0
            checks.append(_check("review_scores", worst >= floor, f"worst {worst:.2f}"))
        artifacts = sorted(p.name for p in (Path(orch.artifacts.root) / run.id).glob("*")) if run.id else []
        checks.append(_check("artifacts_written", bool(artifacts), f"{len(artifacts)} files"))
        passed = all(c["passed"] for c in checks)
        result = {
            "case": case["name"],
            "goal": case["goal"],
            "passed": passed,
            "run_id": run.id,
            "status": run.status.value,
            "tasks": len(tasks),
            "done": len(done),
            "tokens": run.tokens,
            "duration_s": round(time.perf_counter() - started, 2),
            "checks": checks,
        }
        orch.store.close()
        return result


async def run_all(cases: list[dict[str, Any]] | None = None, base: Settings | None = None) -> dict[str, Any]:
    results = []
    for case in cases or CASES:
        results.append(await run_case(case, base))
    passed = sum(1 for r in results if r["passed"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "ok": passed == len(results),
        "results": results,
    }

"""Master Orchestrator: plan -> route -> gate -> execute -> review -> merge.

Owns task state, decides which agent runs which task, retries what can be
retried, sends weak output back for rework, accepts delegated subtasks, and is
the only place that mutates a plan while a run is live.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from .agent import Agent
from .budget import Budget
from .events import Event
from .integrations import Integrations
from .knowledge import KnowledgeBase
from .llm import BaseLLM, heuristic_plan
from .memory import ArtifactStore, Blackboard
from .message_bus import MessageBus
from .metrics import Metrics
from .moderation import Policy
from .moderation import scan as moderation_scan
from .registry import AgentRegistry, SkillRegistry, ToolRegistry
from .retry import RetryPolicy, call_with_retry
from .review import Reviewer
from .store import RunStore
from .tasks import Plan, Run, RunStatus, Task, TaskStatus, new_id, utcnow

log = logging.getLogger("omniforge.orchestrator")
MAX_DELEGATION_DEPTH = 2


class Orchestrator:
    def __init__(
        self,
        *,
        settings,
        llm: BaseLLM,
        skills: SkillRegistry,
        agents: AgentRegistry,
        tools: ToolRegistry,
        bus: MessageBus | None = None,
        store: RunStore | None = None,
        metrics: Metrics | None = None,
        integrations: Integrations | None = None,
        mcp_clients: list[Any] | None = None,
        notifier: Any | None = None,
        billing: Any | None = None,
        mailer: Any | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.skills = skills
        self.agents = agents
        self.tools = tools
        self.bus = bus or MessageBus()
        self.metrics = metrics or Metrics()
        self.blackboard = Blackboard()
        self.artifacts = ArtifactStore(Path(settings.data_dir) / "artifacts")
        self.store = store or RunStore(settings.db_path)
        self.knowledge = KnowledgeBase(self.store)
        self.reviewer = Reviewer(settings, llm)
        self.retry_policy = RetryPolicy.from_settings(settings)
        self.integrations = integrations or Integrations(settings, mcp_clients=mcp_clients)
        self.notifier = notifier
        self.billing = billing
        self.mailer = mailer
        self.policy = Policy(
            enabled=settings.moderation_enabled,
            blocked_terms=tuple(getattr(settings, "moderation_blocked_terms", ()) or ()),
        )

        self.runs: dict[str, Run] = {}
        self._budgets: dict[str, Budget] = {}
        self._jobs: dict[str, asyncio.Task] = {}
        self._approvals: dict[tuple[str, str], asyncio.Future] = {}
        self._sem = asyncio.Semaphore(max(1, settings.max_parallel))

        self._paused = asyncio.Event()
        self._paused.set()               # set == running
        self._stopped = False
        self._checkpoint_seq: dict[str, int] = {}

        self.bus.add_listener(self._on_event)
        interrupted = self.store.fail_interrupted()
        if interrupted:
            log.warning("marked %s interrupted run(s) as failed at startup", interrupted)
        self.metrics.set("runs_in_memory", 0)

    # ------------------------------------------------------------- plumbing
    def _on_event(self, event: Event) -> None:
        self.store.add_event(event)
        self.metrics.inc(f"events.{event.type}")

    def _save(self, run: Run) -> None:
        run.touch()
        self.store.save_run(run)

    @property
    def catalog(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for skill in self.skills.all():
            out.setdefault(skill.department, []).append(skill.name)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def describe(self) -> dict[str, Any]:
        return {
            "framework": "OmniForge AI Studio",
            "version": "0.6.0",
            "backend": self.llm.name,
            "live_mode": self.settings.live_mode,
            "departments": [
                {"key": dept, "skills": skills, "agents": len(self.agents.by_department(dept))}
                for dept, skills in self.catalog.items()
            ],
            "counts": {
                "departments": len(self.catalog),
                "skills": len(self.skills),
                "agents": len(self.agents),
                "tools": len(self.tools),
            },
            "policy": {
                "max_parallel": self.settings.max_parallel,
                "max_retries": self.settings.max_retries,
                "review_enabled": self.settings.review_enabled,
                "max_rework": self.settings.max_rework,
                "require_approval": self.settings.require_approval,
                "budget_tokens": self.settings.budget_tokens,
                "max_tasks_per_run": self.settings.max_tasks_per_run,
            },
            "knowledge": self.knowledge.stats(),
            "storage": self.store.stats(),
            "integrations": self.integrations.snapshot(),
        }

    # ------------------------------------------------------------------ runs
    async def submit(
        self,
        goal: str,
        *,
        auto_approve: bool | None = None,
        meta: dict | None = None,
        project_id: str | None = None,
        priority: int = 5,
        idempotency_key: str | None = None,
        workspace_id: str | None = None,
    ) -> Run:
        if not goal or not goal.strip():
            raise ValueError("goal must not be empty")
        self._check_policy(goal)
        if self._stopped:
            raise RuntimeError("execution is stopped (emergency stop); clear it before submitting")
        if idempotency_key:
            existing = self.store.find_run_by_idempotency(idempotency_key)
            if existing is not None:
                self.metrics.inc("runs.deduplicated")
                cached = self.runs.get(existing["id"])
                return cached if cached is not None else self._rehydrate(existing)
        run = Run(
            goal=goal.strip(),
            auto_approve=(not self.settings.require_approval) if auto_approve is None else auto_approve,
            meta=meta or {},
            project_id=project_id,
            priority=int(priority),
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
        )
        self.runs[run.id] = run
        self._budgets[run.id] = Budget(
            limit_tokens=self.settings.budget_tokens,
            price_per_1k_input=self.settings.price_per_1k_input,
            price_per_1k_output=self.settings.price_per_1k_output,
        )
        self._save(run)
        self.metrics.set("runs_in_memory", len(self.runs))
        self.metrics.inc("runs.submitted")
        self._jobs[run.id] = asyncio.create_task(self._drive(run), name=f"run:{run.id}")
        return run

    async def submit_plan(
        self,
        goal: str,
        plan_data: dict[str, Any],
        *,
        auto_approve: bool = True,
        project_id: str | None = None,
        priority: int = 5,
        meta: dict | None = None,
        workspace_id: str | None = None,
    ) -> Run:
        """Start a run from an existing plan (workflow replay) instead of planning."""
        self._check_policy(goal)
        if self._stopped:
            raise RuntimeError("execution is stopped (emergency stop); clear it before submitting")
        plan = Plan.from_dict(plan_data)
        plan.goal = goal
        # Replayed plans must not reuse the original task ids: they belong to
        # another run's rows. Remap ids and dependencies together.
        id_map: dict[str, str] = {}
        for task in plan.tasks:
            id_map[task.id] = new_id("task")
        for task in plan.tasks:
            task.id = id_map.get(task.id, new_id("task"))
            task.depends_on = [id_map[d] for d in task.depends_on if d in id_map]
            if task.parent_id:
                task.parent_id = id_map.get(task.parent_id, task.parent_id)
        for task in plan.tasks:
            task.status = TaskStatus.PENDING
            task.result = None
            task.error = None
            task.attempts = 0
            task.started_at = None
            task.finished_at = None
            task.review_score = None
            task.requires_approval = self.settings.require_approval
            self._assign_agent(task)
        run = Run(
            goal=goal, plan=plan, auto_approve=auto_approve, meta=meta or {},
            project_id=project_id, priority=int(priority), workspace_id=workspace_id,
        )
        run.status = RunStatus.PLANNING
        self.runs[run.id] = run
        self._budgets[run.id] = Budget(
            limit_tokens=self.settings.budget_tokens,
            price_per_1k_input=self.settings.price_per_1k_input,
            price_per_1k_output=self.settings.price_per_1k_output,
        )
        self._save(run)
        self._checkpoint(run)
        self.metrics.inc("runs.from_plan")
        self._jobs[run.id] = asyncio.create_task(self._drive(run, resume=True), name=f"plan:{run.id}")
        return run

    @staticmethod
    def _condition_met(condition: Any, params: dict[str, Any]) -> bool:
        """Declarative conditions only - no expressions are evaluated.

        {"param": "env", "equals": "prod"}
        {"param": "tier", "in": ["pro", "enterprise"]}
        {"param": "notes", "exists": true}
        {"param": "env", "not_equals": "dev"}
        """
        if not condition:
            return True
        if not isinstance(condition, dict):
            return False
        key = condition.get("param")
        if key is None:
            return True
        value = params.get(key)
        if "exists" in condition:
            return (value is not None) == bool(condition["exists"])
        if "equals" in condition:
            return str(value) == str(condition["equals"])
        if "not_equals" in condition:
            return str(value) != str(condition["not_equals"])
        if "in" in condition:
            allowed = condition["in"] or []
            return str(value) in [str(item) for item in allowed]
        return True

    def _apply_conditions(self, plan_data: dict[str, Any], params: dict[str, Any]) -> list[str]:
        """Drop tasks whose `when` condition fails and re-link their dependents."""
        tasks = plan_data.get("tasks", [])
        keep, dropped = [], []
        for task in tasks:
            if self._condition_met(task.get("when"), params):
                keep.append(task)
            else:
                dropped.append(task.get("key") or task.get("title") or "task")
        if not dropped:
            return []
        kept_keys = {t.get("key") for t in keep if t.get("key")}
        dropped_keys = {t.get("key") for t in tasks if t.get("key")} - kept_keys
        inherited: dict[str, list[str]] = {}
        for task in tasks:
            if task.get("key") in dropped_keys:
                inherited[task["key"]] = list(task.get("depends_on") or [])
        for task in keep:
            deps: list[str] = []
            for dep in task.get("depends_on") or []:
                if dep in dropped_keys:
                    deps.extend(inherited.get(dep, []))
                elif dep in kept_keys:
                    deps.append(dep)
            task["depends_on"] = [d for d in dict.fromkeys(deps) if d in kept_keys]
        plan_data["tasks"] = keep
        return dropped

    async def run_workflow(
        self,
        workflow: dict[str, Any],
        params: dict[str, Any] | None = None,
        *,
        project_id: str | None = None,
        meta: dict | None = None,
    ) -> Run:
        """Replay a stored workflow, substituting {{placeholders}} from params."""
        params = params or {}
        if "plan" not in workflow and workflow.get("id"):
            stored = self.store.get_workflow(workflow["id"])
            if stored is None:
                raise KeyError(f"unknown workflow '{workflow['id']}'")
            workflow = stored
        plan_data = json.loads(workflow["plan"])
        goal_template = workflow.get("goal_template") or workflow.get("name", "workflow")

        def fill(text: str) -> str:
            out = text or ""
            for key, value in params.items():
                out = out.replace("{{" + key + "}}", str(value))
                out = out.replace("{" + key + "}", str(value))
            return out

        plan_data["goal"] = fill(goal_template)
        for task in plan_data.get("tasks", []):
            task["title"] = fill(task.get("title", ""))
            task["description"] = fill(task.get("description", ""))
        dropped = self._apply_conditions(plan_data, params)
        if not plan_data.get("tasks"):
            raise ValueError("every task in this workflow was skipped by its condition")
        run = await self.submit_plan(
            fill(goal_template), plan_data, auto_approve=True, project_id=project_id,
            meta={**(meta or {}), "workflow_id": workflow.get("id"), "params": params,
                  "dropped_by_condition": dropped},
        )
        self.metrics.inc("workflows.run")
        return run

    async def run_to_completion(self, goal: str, **kw) -> Run:
        run = await self.submit(goal, **kw)
        await self.wait(run.id)
        return run

    async def wait(self, run_id: str, timeout: float | None = None) -> Run:
        job = self._jobs.get(run_id)
        if job is None:
            return self.require_run(run_id)
        if timeout:
            await asyncio.wait_for(asyncio.shield(job), timeout=timeout)
        else:
            await asyncio.shield(job)
        return self.require_run(run_id)

    def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def require_run(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(f"unknown run '{run_id}'")
        return run

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        """In-memory run if we own it, otherwise the durable record."""
        run = self.runs.get(run_id)
        if run is not None:
            data = run.to_dict()
            data["events"] = self.bus.history(run_id=run_id, limit=500)
            data["budget"] = self._budgets[run_id].snapshot() if run_id in self._budgets else None
            return data
        return self.store.get_run(run_id)

    def list_runs(self, limit: int = 50) -> list[dict]:
        rows = self.store.list_runs(limit=limit)
        for row in rows:
            run = self.runs.get(row["id"])
            if run is not None:
                row["status"] = run.status.value
                row["counts"] = run.counts()
                row["summary"] = run.summary
        return rows

    def budget_of(self, run_id: str) -> dict | None:
        budget = self._budgets.get(run_id)
        return budget.snapshot() if budget else None

    async def cancel(self, run_id: str) -> Run:
        run = self.require_run(run_id)
        job = self._jobs.get(run_id)
        if job and not job.done():
            job.cancel()
        run.status = RunStatus.CANCELLED
        self._save(run)
        self.metrics.inc("runs.cancelled")
        await self.bus.emit("run.cancelled", run_id=run.id, payload={"goal": run.goal})
        return run

    # --------------------------------------------------------------- control
    @property
    def paused(self) -> bool:
        return not self._paused.is_set()

    @property
    def stopped(self) -> bool:
        return self._stopped

    def pause(self) -> dict[str, Any]:
        """Global pause: running tasks finish, no new wave starts."""
        self._paused.clear()
        self.metrics.set("paused", 1)
        return {"paused": True, "runs_in_memory": len(self.runs)}

    def unpause(self) -> dict[str, Any]:
        self._paused.set()
        self.metrics.set("paused", 0)
        return {"paused": False, "runs_in_memory": len(self.runs)}

    async def emergency_stop(self, reason: str = "emergency stop") -> dict[str, Any]:
        """Cancel every in-flight run and refuse new submissions until cleared."""
        self._stopped = True
        self._paused.set()
        cancelled: list[str] = []
        for run_id, job in list(self._jobs.items()):
            if not job.done():
                job.cancel()
                cancelled.append(run_id)
            run = self.runs.get(run_id)
            if run is not None and run.status in {RunStatus.PLANNING, RunStatus.RUNNING,
                                                  RunStatus.AWAITING_APPROVAL}:
                run.status = RunStatus.CANCELLED
                run.error = reason
                self._save(run)
        self.metrics.inc("runs.emergency_stopped", len(cancelled))
        self.store.add_audit(None, "control.emergency_stop", None, reason, None, utcnow())
        await self.bus.emit("control.stopped", payload={"reason": reason, "cancelled": cancelled})
        return {"stopped": True, "cancelled": cancelled}

    def clear_stop(self) -> dict[str, Any]:
        self._stopped = False
        self.store.add_audit(None, "control.cleared", None, "execution resumed", None, utcnow())
        return {"stopped": False}

    async def _pause_gate(self) -> None:
        while not self._paused.is_set():
            await asyncio.sleep(0.05)

    def control_state(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "stopped": self._stopped,
            "active_runs": sum(1 for j in self._jobs.values() if not j.done()),
            "pending_approvals": len(self.pending_approvals()),
        }

    # ----------------------------------------------------------- checkpoints
    def _checkpoint(self, run: Run) -> None:
        if run.plan is None:
            return
        seq = self._checkpoint_seq.get(run.id, 0) + 1
        self._checkpoint_seq[run.id] = seq
        try:
            self.store.save_checkpoint(run.id, seq, json.dumps(run.plan.to_dict()), utcnow())
        except Exception as exc:  # noqa: BLE001 - a checkpoint must never break a run
            log.warning("checkpoint failed for %s: %s", run.id, exc)

    def _rehydrate(self, record: dict[str, Any]) -> Run:
        """Rebuild an in-memory Run from the store (used by resume and idempotency)."""
        checkpoint = self.store.latest_checkpoint(record["id"])
        plan = Plan.from_dict(json.loads(checkpoint["plan"])) if checkpoint else None
        run = Run(
            goal=record["goal"],
            plan=plan,
            auto_approve=bool(record.get("auto_approve")),
            meta=_as_dict(record.get("meta")),
            project_id=record.get("project_id"),
            priority=int(record.get("priority") or 5),
            idempotency_key=record.get("idempotency_key"),
            workspace_id=record.get("workspace_id"),
        )
        run.id = record["id"]
        run.created_at = record["created_at"]
        run.tokens = int(record.get("tokens") or 0)
        run.cost_usd = float(record.get("cost_usd") or 0.0)
        try:
            run.status = RunStatus(record["status"])
        except ValueError:
            run.status = RunStatus.FAILED
        run.summary = record.get("summary")
        run.error = record.get("error")
        self.runs.setdefault(run.id, run)
        return run

    async def resume(self, run_id: str) -> Run:
        """Continue an interrupted run from its last checkpoint."""
        record = self.store.get_run(run_id)
        if record is None:
            raise KeyError(f"unknown run '{run_id}'")
        checkpoint = self.store.latest_checkpoint(run_id)
        if checkpoint is None:
            raise ValueError("this run has no checkpoint to resume from")
        if run_id in self._jobs and not self._jobs[run_id].done():
            raise ValueError("this run is already executing")

        run = self._rehydrate(record)
        assert run.plan is not None
        for task in run.plan.tasks:
            if task.status in {TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.READY}:
                task.status = TaskStatus.PENDING
                task.started_at = None
        run.status = RunStatus.RUNNING
        run.error = None
        budget = Budget(
            limit_tokens=self.settings.budget_tokens,
            price_per_1k_input=self.settings.price_per_1k_input,
            price_per_1k_output=self.settings.price_per_1k_output,
        )
        budget.add(run.tokens, 0)
        self._budgets[run.id] = budget
        self._save(run)
        self.metrics.inc("runs.resumed")
        self.store.add_audit(None, "run.resumed", run.id, f"seq={checkpoint['seq']}", None, utcnow())
        await self.bus.emit("run.resumed", run_id=run.id,
                            payload={"checkpoint": checkpoint["seq"], "tokens": run.tokens})
        self._jobs[run.id] = asyncio.create_task(self._drive(run, resume=True), name=f"resume:{run.id}")
        return run

    # ------------------------------------------------------------- approvals
    def approve(self, run_id: str, task_id: str, approved: bool, note: str = "") -> bool:
        fut = self._approvals.get((run_id, task_id))
        if fut is None or fut.done():
            return False
        fut.set_result({"approved": approved, "note": note})
        return True

    def pending_approvals(self, run_id: str | None = None) -> list[dict]:
        out = []
        for (rid, tid), fut in self._approvals.items():
            if fut.done() or (run_id and rid != run_id):
                continue
            run = self.runs.get(rid)
            task = run.plan.by_id().get(tid) if run and run.plan else None
            out.append({"run_id": rid, "task_id": tid, "task": task.title if task else tid})
        return out

    # ----------------------------------------------------------------- drive
    async def _drive(self, run: Run, resume: bool = False) -> None:
        budget = self._budgets[run.id]
        started = time.perf_counter()
        deadline = (time.perf_counter() + self.settings.run_timeout) if self.settings.run_timeout else None
        if not resume:
            await self.bus.emit("run.created", run_id=run.id, payload={"goal": run.goal})
        try:
            if not resume:
                run.plan = await self._make_plan(run)
                budget.add_estimate(run.goal, str(run.plan.to_dict())[:20000])
            run.status = RunStatus.RUNNING
            self._save(run)
            if resume:
                self._checkpoint(run)
                await self.bus.emit(
                    "plan.restored", run_id=run.id,
                    payload={"tasks": [t.to_dict() for t in run.plan.tasks]},
                )
                await self._schedule(run, budget, deadline)
                await self._finish(run, budget)
                return
            await self.bus.emit(
                "plan.created", run_id=run.id,
                payload={"tasks": [t.to_dict() for t in run.plan.tasks], "strategy": run.plan.strategy},
            )
            self._checkpoint(run)
            await self._schedule(run, budget, deadline)
            await self._finish(run, budget)
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            self._save(run)
            await self.bus.emit("run.cancelled", run_id=run.id)
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced on the run record
            log.exception("run %s failed", run.id)
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            self._save(run)
            self.metrics.inc("runs.failed")
            await self.bus.emit("run.failed", run_id=run.id, payload={"error": run.error})
        finally:
            run.tokens = budget.tokens
            run.cost_usd = budget.cost_usd
            self._save(run)
            self.metrics.observe("run.duration", time.perf_counter() - started)
            self.metrics.inc(f"runs.{run.status.value}")

    async def _schedule(self, run: Run, budget: Budget, deadline: float | None) -> None:
        """Wave loop that tolerates plan growth (delegated tasks appear as PENDING)."""
        assert run.plan is not None
        while True:
            await self._pause_gate()
            pending = [t for t in run.plan.tasks if t.status is TaskStatus.PENDING]
            if not pending:
                return
            by_id = run.plan.by_id()
            ready = [t for t in pending if all(by_id[d].status is TaskStatus.DONE for d in t.depends_on)]
            ready.sort(key=lambda t: (-getattr(t, "priority", 0), run.plan.tasks.index(t)))
            if not ready:
                await self._skip_remaining(run, "blocked by a task that did not complete")
                return
            if deadline is not None and time.perf_counter() > deadline:
                run.error = "run exceeded its wall-clock timeout"
                await self.bus.emit("run.timeout", run_id=run.id, payload={"error": run.error})
                await self._skip_remaining(run, run.error)
                return
            if budget.exceeded:
                run.error = "token budget exceeded"
                await self.bus.emit(
                    "budget.exceeded", run_id=run.id,
                    payload={"tokens": budget.tokens, "limit": budget.limit_tokens},
                )
                await self._skip_remaining(run, run.error)
                return

            results = await asyncio.gather(*(self._execute_task(run, t, budget) for t in ready))
            self._checkpoint(run)
            if any(result is False for result in results) and self.settings.fail_fast:
                await self._skip_remaining(run, "run stopped after a failed task")
                return

    async def _make_plan(self, run: Run) -> Plan:
        raw = await self.llm.plan(run.goal, self.catalog)
        plan = self._build_plan(run.goal, raw)
        problems = plan.validate()
        if problems:
            log.warning("plan invalid (%s); falling back", "; ".join(problems))
            self.metrics.inc("plans.repaired")
            plan = self._build_plan(run.goal, heuristic_plan(run.goal, self.catalog))
            plan.strategy += "+repaired"
        for task in plan.tasks:
            task.requires_approval = self.settings.require_approval
        self.metrics.inc("plans.created")
        self.metrics.observe("plan.tasks", len(plan.tasks))
        return plan

    def _build_plan(self, goal: str, raw: dict[str, Any]) -> Plan:
        plan = Plan(goal=goal, strategy=str(raw.get("strategy", "llm")))
        catalog = self.catalog
        key_to_id: dict[str, str] = {}
        drafts: list[dict[str, Any]] = []

        for item in raw.get("tasks", []):
            if not isinstance(item, dict):
                continue
            department = str(item.get("department", "")).strip()
            if department not in catalog:
                department = next(iter(catalog)) if catalog else "research"
            skill = str(item.get("skill", "")).strip()
            if skill not in catalog.get(department, []):
                skill = catalog.get(department, [""])[0] if catalog.get(department) else ""
            task = Task(
                title=str(item.get("title") or f"{department} task").strip(),
                description=str(item.get("description", "")).strip(),
                department=department,
                skill=skill,
                priority=int(item.get("priority") or 0),
            )
            key = str(item.get("key") or new_id("k"))
            key_to_id[key] = task.id
            drafts.append({"task": task, "deps": item.get("depends_on") or []})
            plan.tasks.append(task)

        for draft in drafts:
            deps: list[str] = []
            for dep in draft["deps"]:
                dep_id = key_to_id.get(str(dep))
                if dep_id and dep_id != draft["task"].id and dep_id not in deps:
                    deps.append(dep_id)
            draft["task"].depends_on = deps

        for task in plan.tasks:
            self._assign_agent(task)
        return plan

    def _assign_agent(self, task: Task) -> None:
        template = self.agents.best_match(task.department, task.skill)
        task.agent_id = template.id if template else None

    # ------------------------------------------------------------- execution
    async def _execute_task(self, run: Run, task: Task, budget: Budget) -> bool:
        """True = task reached a terminal state; False = it failed permanently."""
        assert run.plan is not None
        by_id = run.plan.by_id()
        if any(by_id[d].status is not TaskStatus.DONE for d in task.depends_on):
            task.status = TaskStatus.SKIPPED
            task.error = "upstream task did not complete"
            self._save(run)
            await self.bus.emit("task.skipped", run_id=run.id, task_id=task.id, payload={"title": task.title})
            return True

        if not run.auto_approve and task.requires_approval:
            if not await self._gate(run, task):
                return True

        template = self.agents.get(task.agent_id) if task.agent_id else None
        if template is None:
            template = self.agents.best_match(task.department, task.skill)
            task.agent_id = template.id if template else None
        if template is None:
            task.status = TaskStatus.FAILED
            task.error = "no agent available for this department"
            self._save(run)
            await self.bus.emit("task.failed", run_id=run.id, task_id=task.id, payload={"error": task.error})
            return False

        async with self._sem:
            task.status = TaskStatus.RUNNING
            task.started_at = task.started_at or utcnow()
            self._save(run)
            self.metrics.inc("tasks.started")
            await self.bus.emit(
                "task.started", run_id=run.id, task_id=task.id, agent_id=task.agent_id,
                payload={"title": task.title, "department": task.department, "skill": task.skill},
            )

            agent = Agent(
                template, llm=self.llm, skills=self.skills, tools=self.tools,
                bus=self.bus, blackboard=self.blackboard, budget=budget,
            )
            feedback: str | None = None

            async def attempt(_attempt_no: int) -> str:
                nonlocal feedback
                task.attempts += 1
                context = self._context_for(run, task)
                output = await agent.execute(
                    task, run, context,
                    feedback=feedback,
                    delegate=lambda spec: self._delegate(run, task, spec),
                    tool=self._tool_for(run, template),
                    retrieved=context,
                )
                screened, findings = self._screen_output(output)
                if findings:
                    await self.bus.emit("task.moderated", run_id=run.id, task_id=task.id,
                                        payload={"title": task.title, "findings": findings})
                    self.metrics.inc("moderation.findings", len(findings))
                output = screened
                if not self.settings.review_enabled:
                    return output
                review = await self.reviewer.review(task, output, self.skills.get(task.skill), context)
                task.review_score = review.score
                await self.bus.emit(
                    "task.reviewed", run_id=run.id, task_id=task.id,
                    payload={"title": task.title, **review.to_dict()},
                )
                self.metrics.observe("review.score", review.score)
                if review.passed:
                    return output
                self.metrics.inc("reviews.failed")
                feedback = "; ".join(review.issues) or "output was not good enough"
                raise ReviewRejected(f"review failed (score {review.score:.2f}): {feedback}")

            async def on_retry(attempt_no: int, exc: BaseException, delay: float) -> None:
                self.metrics.inc("tasks.retried")
                await self.bus.emit(
                    "task.retrying", run_id=run.id, task_id=task.id,
                    payload={"attempt": attempt_no, "delay": round(delay, 3),
                             "reason": f"{type(exc).__name__}: {exc}"},
                )
                if not isinstance(exc, ReviewRejected):
                    await asyncio.sleep(0)  # yield before the backoff sleep

            try:
                output = await call_with_retry(attempt, self.retry_policy, on_retry=on_retry)
                task.result = output
                task.status = TaskStatus.DONE
                self.store.add_memory(run.id, template.id, task.title, output[:4000], utcnow())
                task.error = None
                task.finished_at = utcnow()
                self.metrics.inc("tasks.finished")
                self._save(run)
                await self.bus.emit(
                    "task.finished", run_id=run.id, task_id=task.id, agent_id=task.agent_id,
                    payload={"title": task.title, "chars": len(output),
                             "review_score": task.review_score},
                )
                return True
            except Exception as exc:  # noqa: BLE001
                task.status = TaskStatus.FAILED
                task.error = f"{type(exc).__name__}: {exc}"
                task.finished_at = utcnow()
                self.metrics.inc("tasks.failed")
                self._save(run)
                await self.bus.emit(
                    "task.failed", run_id=run.id, task_id=task.id,
                    payload={"title": task.title, "error": task.error},
                )
                return False

    def _context_for(self, run: Run, task: Task) -> str:
        parts = [self.blackboard.context_block(run.id)]
        query = f"{task.title} {task.description} {task.department}"
        past = self.knowledge.context_for(query, limit=3, exclude_run=run.id)
        if past:
            parts.append("## Relevant work from earlier runs\n" + past)
        return "\n\n".join(p for p in parts if p)

    def _check_policy(self, text: str) -> None:
        report = moderation_scan(text, self.policy)
        if report.blocked:
            rules = ", ".join(sorted({f["rule"] for f in report.findings}))
            raise ValueError(f"goal blocked by content policy ({rules})")

    def _screen_output(self, output: str) -> tuple[str, list[dict[str, str]]]:
        report = moderation_scan(output, self.policy)
        return report.text, [{"category": f["category"], "rule": f["rule"]}
                             for f in report.findings]

    # ------------------------------------------------------------------ tools
    def _log_tool_call(self, record: dict[str, Any]) -> None:
        """Every tool call is recorded, whether it succeeded or not."""
        self.store.add_tool_call(record, utcnow())
        self.metrics.inc("tool_calls")
        if not record.get("ok"):
            self.metrics.inc("tool_calls.failed")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self.bus.emit(
                "tool.called",
                run_id=record.get("run_id"),
                agent_id=record.get("agent_id"),
                payload={
                    "tool": record.get("tool"),
                    "ok": record.get("ok"),
                    "duration_s": record.get("duration_s"),
                    "error": record.get("error") or None,
                },
            )
        )

    def _tool_for(self, run: Run, template) -> Any:
        approved = bool((run.meta or {}).get("allow_destructive"))

        async def call(name: str, **kwargs) -> Any:
            return await self.tools.call_checked(
                name,
                kwargs,
                permissions=getattr(template, "permissions", ()),
                approved=approved,
                run_id=run.id,
                agent_id=getattr(template, "id", None),
                logger=self._log_tool_call,
            )

        return call

    # -------------------------------------------------------------- projects
    def create_project(self, name: str, meta: dict | None = None) -> dict[str, Any]:
        project = self.store.create_project(name, utcnow(), meta)
        self.metrics.inc("projects.created")
        return project

    def list_projects(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_projects(limit=limit)

    # ------------------------------------------------------------ delegation
    def _delegate(self, run: Run, parent: Task, spec: dict[str, Any]) -> dict[str, Any]:
        assert run.plan is not None
        title = str(spec.get("title", "")).strip()
        if not title:
            return {"ok": False, "reason": "a delegated task needs a title"}
        if len(run.plan.tasks) >= self.settings.max_tasks_per_run:
            self.metrics.inc("delegations.rejected")
            return {"ok": False, "reason": f"task cap of {self.settings.max_tasks_per_run} reached"}

        existing = next((t for t in run.plan.tasks if t.title.lower() == title.lower()), None)
        if existing is not None:
            return {"ok": True, "task_id": existing.id, "title": existing.title, "deduplicated": True}

        depth, cursor = 1, parent
        by_id = run.plan.by_id()
        while cursor.parent_id and depth <= MAX_DELEGATION_DEPTH + 1:
            cursor = by_id.get(cursor.parent_id, cursor)
            depth += 1
        if depth > MAX_DELEGATION_DEPTH:
            self.metrics.inc("delegations.rejected")
            return {"ok": False, "reason": f"delegation depth limit ({MAX_DELEGATION_DEPTH}) reached"}

        catalog = self.catalog
        department = str(spec.get("department") or parent.department)
        if department not in catalog:
            department = parent.department
        skill = str(spec.get("skill") or "")
        if skill not in catalog.get(department, []):
            skill = catalog.get(department, [""])[0] if catalog.get(department) else parent.skill

        child = Task(
            title=title,
            description=str(spec.get("description", "")).strip(),
            department=department,
            skill=skill,
            depends_on=[parent.id],
            requires_approval=self.settings.require_approval,
            parent_id=parent.id,
        )
        self._assign_agent(child)
        run.plan.tasks.append(child)
        self._save(run)
        self.metrics.inc("delegations")
        self.metrics.observe("plan.tasks", len(run.plan.tasks))
        return {"ok": True, "task_id": child.id, "title": child.title, "department": department, "skill": skill}

    # ---------------------------------------------------------------- gates
    async def _gate(self, run: Run, task: Task) -> bool:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._approvals[(run.id, task.id)] = fut
        task.status = TaskStatus.AWAITING_APPROVAL
        run.status = RunStatus.AWAITING_APPROVAL
        self._save(run)
        await self.bus.emit(
            "approval.requested", run_id=run.id, task_id=task.id,
            payload={"title": task.title, "department": task.department, "agent_id": task.agent_id},
        )
        try:
            decision = await fut
        finally:
            self._approvals.pop((run.id, task.id), None)
            run.status = RunStatus.RUNNING
        if decision.get("approved"):
            task.status = TaskStatus.PENDING
            self._save(run)
            await self.bus.emit("approval.granted", run_id=run.id, task_id=task.id,
                                payload={"note": decision.get("note", "")})
            return True
        task.status = TaskStatus.SKIPPED
        task.error = f"rejected by human: {decision.get('note', '')}".strip()
        self._save(run)
        await self.bus.emit("approval.rejected", run_id=run.id, task_id=task.id,
                            payload={"note": decision.get("note", "")})
        return False

    async def _skip_remaining(self, run: Run, reason: str) -> None:
        assert run.plan is not None
        for task in run.plan.tasks:
            if task.status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.AWAITING_APPROVAL}:
                task.status = TaskStatus.SKIPPED
                task.error = task.error or reason
                await self.bus.emit("task.skipped", run_id=run.id, task_id=task.id,
                                    payload={"title": task.title, "reason": reason})
        self._save(run)

    # --------------------------------------------------------------- finish
    async def _finish(self, run: Run, budget: Budget) -> None:
        assert run.plan is not None
        done = [t for t in run.plan.tasks if t.status is TaskStatus.DONE]
        failed = [t for t in run.plan.tasks if t.status is TaskStatus.FAILED]
        if done:
            try:
                run.summary = await self.llm.synthesize(
                    run.goal,
                    [{"title": t.title, "department": t.department, "result": t.result or ""} for t in done],
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("synthesis failed: %s", exc)
            if run.summary:
                budget.add_estimate(run.goal, run.summary)

        run.tokens = budget.tokens
        run.cost_usd = budget.cost_usd
        if not done:
            run.status = RunStatus.FAILED
            run.error = run.error or "no task completed"
        elif failed:
            run.status = RunStatus.DONE
            run.error = f"{len(failed)} task(s) failed"
        else:
            run.status = RunStatus.DONE

        if run.summary:
            path = self.artifacts.write(run.id, "summary.md", f"# {run.goal}\n\n{run.summary}\n")
            self._record_provenance(run, None, path, "summary.md")
        for task in done:
            if task.result:
                name = f"{task.department}_{task.skill}.md"
                path = self.artifacts.write(run.id, name, f"# {task.title}\n\n{task.result}\n")
                self._record_provenance(run, task, path, name)
        if self.billing is not None:
            try:
                self.billing.record(
                    user_id=(run.meta or {}).get("user_id"),
                    run_id=run.id,
                    model=getattr(self.llm, "model", None) or self.llm.name,
                    input_tokens=budget.input_tokens,
                    output_tokens=budget.output_tokens,
                    ts=utcnow(),
                )
            except Exception as exc:  # noqa: BLE001 - accounting must not fail a run
                log.warning("usage recording failed: %s", exc)
        self._index_knowledge(run, done)
        self._save(run)
        await self._alert(run)
        await self._notify_run(run)
        await self.bus.emit(
            "run.finished", run_id=run.id,
            payload={
                "status": run.status.value,
                "counts": run.counts(),
                "summary": run.summary,
                "tokens": run.tokens,
                "cost_usd": round(run.cost_usd, 6),
            },
        )

    async def _alert(self, run: Run) -> None:
        """Email the operator when a run fails (only if an address is configured)."""
        if self.mailer is None or not getattr(self.settings, "alert_email", ""):
            return
        if not getattr(self.settings, "alert_on_failure", True):
            return
        if run.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
            return
        body = (
            f"Run {run.id} ended as {run.status.value}.\n\n"
            f"Goal: {run.goal}\nError: {run.error or 'n/a'}\n"
            f"Tasks: {run.counts()}\nTokens: {run.tokens}\n"
        )
        try:
            sent = await self.mailer.send(self.settings.alert_email,
                                          f"[OmniForge] run {run.status.value}: {run.id}", body)
            self.metrics.inc("alerts.sent" if sent else "alerts.failed")
        except Exception as exc:  # noqa: BLE001 - an alert must never break a run
            log.warning("alert delivery failed: %s", exc)

    async def _notify_run(self, run: Run) -> None:
        if self.notifier is None or not getattr(self.notifier, "enabled", False):
            return
        payload = {
            "event": "run.finished",
            "run_id": run.id,
            "goal": run.goal,
            "status": run.status.value,
            "counts": run.counts(),
            "tokens": run.tokens,
            "cost_usd": round(run.cost_usd, 6),
            "summary": (run.summary or "")[:2000],
            "url": f"{self.settings.public_base_url.rstrip('/')}/#run={run.id}",
        }
        result = await self.notifier.send(payload)
        self.store.add_notification(
            run.id, "run.finished", getattr(self.notifier, "url", None),
            bool(result.get("ok")), int(result.get("attempts") or 0),
            result.get("error"), utcnow(),
        )
        self.metrics.inc("notifications.sent" if result.get("ok") else "notifications.failed")

    def _record_provenance(self, run: Run, task: Task | None, path: str, name: str) -> None:
        """Every generated artifact gets a provenance row with a checksum."""
        try:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            digest = ""
        self.store.add_provenance({
            "run_id": run.id,
            "task_id": task.id if task else None,
            "agent_id": task.agent_id if task else None,
            "artifact": name,
            "licence": "generated:model-output",
            "source": f"run:{run.id}",
            "checksum": digest,
            "created_at": utcnow(),
        })

    def _index_knowledge(self, run: Run, done: list[Task]) -> None:
        indexed = 0
        for task in done:
            if not task.result:
                continue
            self.knowledge.add(
                run.id, f"{task.department}: {task.title}", task.result, task.finished_at or utcnow()
            )
            indexed += 1
        if run.summary:
            self.knowledge.add(run.id, f"summary: {run.goal}", run.summary, utcnow())
            indexed += 1
        if indexed:
            self.metrics.inc("kb.indexed", indexed)


def _as_dict(value: Any) -> dict:
    """Store rows may hand back either JSON text or an already-parsed dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class ReviewRejected(RuntimeError):
    """Raised internally when a review fails, so the retry loop reworks the task."""

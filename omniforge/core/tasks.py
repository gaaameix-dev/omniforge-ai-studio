"""Task, Plan and Run records - the unit of work the orchestrator moves around."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    title: str
    description: str = ""
    department: str = ""
    skill: str = ""
    agent_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    requires_approval: bool = False
    priority: int = 0
    id: str = field(default_factory=lambda: new_id("task"))
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    review_score: float | None = None
    parent_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        task = cls(
            title=data.get("title", "task"),
            description=data.get("description", "") or "",
            department=data.get("department", "") or "",
            skill=data.get("skill", "") or "",
            agent_id=data.get("agent_id"),
            depends_on=list(data.get("depends_on") or []),
            requires_approval=bool(data.get("requires_approval")),
        )
        task.id = data.get("id", task.id)
        task.status = TaskStatus(data.get("status", "pending"))
        task.result = data.get("result")
        task.error = data.get("error")
        task.attempts = int(data.get("attempts") or 0)
        task.started_at = data.get("started_at")
        task.finished_at = data.get("finished_at")
        task.review_score = data.get("review_score")
        task.parent_id = data.get("parent_id")
        task.priority = int(data.get("priority") or 0)
        return task

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "department": self.department,
            "skill": self.skill,
            "agent_id": self.agent_id,
            "depends_on": self.depends_on,
            "requires_approval": self.requires_approval,
            "priority": self.priority,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "review_score": self.review_score,
            "parent_id": self.parent_id,
        }


@dataclass
class Plan:
    goal: str
    tasks: list[Task] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("plan"))
    strategy: str = "llm"
    notes: str = ""

    def by_id(self) -> dict[str, Task]:
        return {t.id: t for t in self.tasks}

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the plan is executable."""
        problems: list[str] = []
        ids = self.by_id()
        for t in self.tasks:
            for dep in t.depends_on:
                if dep not in ids:
                    problems.append(f"task {t.id} depends on unknown task {dep}")
                if dep == t.id:
                    problems.append(f"task {t.id} depends on itself")
        if problems:
            return problems
        try:
            self.levels()
        except ValueError as exc:
            problems.append(str(exc))
        return problems

    def levels(self) -> list[list[Task]]:
        """Group tasks into dependency waves (Kahn layering). Raises on cycles."""
        ids = self.by_id()
        remaining = {t.id: set(d for d in t.depends_on if d in ids) for t in self.tasks}
        ordered: list[list[Task]] = []
        placed: set[str] = set()
        while remaining:
            wave = [tid for tid, deps in remaining.items() if deps <= placed]
            if not wave:
                raise ValueError("dependency cycle detected in plan")
            ordered.append([ids[tid] for tid in wave])
            placed.update(wave)
            for tid in wave:
                remaining.pop(tid)
        return ordered

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        plan = cls(goal=data.get("goal", ""), strategy=data.get("strategy", "restored"))
        plan.id = data.get("id", plan.id)
        plan.tasks = [Task.from_dict(t) for t in data.get("tasks", [])]
        plan.notes = data.get("notes", "") or ""
        return plan

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "strategy": self.strategy,
            "notes": self.notes,
            "tasks": [t.to_dict() for t in self.tasks],
        }


@dataclass
class Run:
    goal: str
    plan: Plan | None = None
    id: str = field(default_factory=lambda: new_id("run"))
    status: RunStatus = RunStatus.PLANNING
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    auto_approve: bool = False
    project_id: str | None = None
    summary: str | None = None
    error: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    priority: int = 5
    idempotency_key: str | None = None
    workspace_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = utcnow()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in (self.plan.tasks if self.plan else []):
            out[t.status.value] = out.get(t.status.value, 0) + 1
        return out

    def to_dict(self, include_events: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "auto_approve": self.auto_approve,
            "project_id": self.project_id,
            "summary": self.summary,
            "error": self.error,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "priority": self.priority,
            "idempotency_key": self.idempotency_key,
            "workspace_id": self.workspace_id,
            "counts": self.counts(),
            "plan": self.plan.to_dict() if self.plan else None,
        }
        return data

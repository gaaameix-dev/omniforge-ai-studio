"""Registries for skills, agents and tools.

Everything the orchestrator can dispatch to lives here, addressed by name, so a
new capability is a registration rather than a change to the core loop.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Name -> object store with duplicate protection."""

    kind = "item"

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T, *, replace: bool = False) -> T:
        if name in self._items and not replace:
            raise ValueError(f"{self.kind} '{name}' is already registered")
        self._items[name] = item
        return item

    def get(self, name: str) -> T | None:
        return self._items.get(name)

    def require(self, name: str) -> T:
        item = self._items.get(name)
        if item is None:
            raise KeyError(f"unknown {self.kind} '{name}'")
        return item

    def all(self) -> list[T]:
        return list(self._items.values())

    def remove(self, name: str) -> bool:
        return self._items.pop(name, None) is not None

    def names(self) -> list[str]:
        return sorted(self._items)

    def filter(self, predicate) -> list[T]:
        return [i for i in self._items.values() if predicate(i)]

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items


class SkillRegistry(Registry):
    kind = "skill"

    def by_department(self, department: str) -> list:
        return self.filter(lambda s: s.department == department)

    def by_tag(self, tag: str) -> list:
        return self.filter(lambda s: tag in s.tags)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "department": s.department,
                "description": s.description,
                "tags": sorted(s.tags),
                "deterministic": s.handler is not None,
            }
            for s in sorted(self.all(), key=lambda s: (s.department, s.name))
        ]


class AgentRegistry(Registry):
    kind = "agent"

    def by_department(self, department: str) -> list:
        return self.filter(lambda a: a.department == department)

    def by_skill(self, skill: str) -> list:
        return self.filter(lambda a: skill in a.skills)

    def best_match(self, department: str, skill: str = "") -> Any | None:
        """Pick the agent that covers the skill inside the department.

        Scoring: department match dominates, then skill coverage, then a stable
        tie-break on declared preference order.
        """
        candidates = self.all()
        if not candidates:
            return None
        best, best_score = None, -1.0
        for idx, agent in enumerate(candidates):
            score = 0.0
            if agent.department == department:
                score += 10.0
            if skill and skill in agent.skills:
                score += 5.0
            if skill and skill in agent.primary_skills:
                score += 4.0
            if skill:
                skill_tokens = {t for t in re.split(r"[^a-z0-9]+", skill.lower()) if t}
                score += 3.0 * len(skill_tokens & getattr(agent, "name_tokens", set()))
            score -= idx * 0.001
            if score > best_score:
                best, best_score = agent, score
        return best if best_score > 0 else candidates[0]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": a.id,
                "name": a.name,
                "department": a.department,
                "description": a.description,
                "skills": list(a.skills),
                "tools": list(a.tools),
            }
            for a in self.all()
        ]


class ToolRegistry(Registry):
    kind = "tool"

    async def call(self, name: str, **kwargs) -> Any:
        """Unchecked call. Internal use and tests only."""
        tool = self.require(name)
        return await tool.run(**kwargs)

    async def call_checked(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        permissions: Iterable[str] = (),
        approved: bool = False,
        run_id: str | None = None,
        agent_id: str | None = None,
        logger: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        """Enforce permissions, approvals and logging around a tool call."""
        from ..tools.base import ApprovalRequired, PermissionDenied, ToolError

        tool = self.require(name)
        args = arguments or {}
        summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:4])
        started = time.perf_counter()
        ok, error = True, ""
        try:
            if not getattr(tool, "enabled", True):
                raise ToolError(f"tool '{name}' is disabled in this configuration")
            granted = set(permissions)
            missing = [p for p in getattr(tool, "permissions", ()) if p not in granted]
            if missing:
                raise PermissionDenied(
                    f"agent {agent_id or 'unknown'} lacks {', '.join(missing)} "
                    f"required by tool '{name}'"
                )
            if getattr(tool, "destructive", False) and not approved:
                raise ApprovalRequired(
                    f"tool '{name}' is destructive and needs a human approval for this run"
                )
            return await tool.run(**args)
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if logger is not None:
                logger({
                    "tool": name,
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "ok": ok,
                    "duration_s": round(time.perf_counter() - started, 4),
                    "error": error,
                    "args_summary": summary,
                })

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.all()]

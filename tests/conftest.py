"""Shared helpers for the v2 test suite."""
from __future__ import annotations

import pytest

from omniforge.agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
from omniforge.config import Settings
from omniforge.core.llm import MockLLM
from omniforge.core.metrics import Metrics
from omniforge.core.orchestrator import Orchestrator
from omniforge.core.store import RunStore


class FixedPlanner(MockLLM):
    """Mock backend that returns a canned plan instead of the heuristic one."""

    name = "mock"

    def __init__(self, tasks: list[dict]) -> None:
        self._tasks = tasks

    async def plan(self, goal: str, catalog: dict) -> dict:
        return {"strategy": "fixed", "tasks": self._tasks}


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(mock_llm=True, data_dir=tmp_path, max_parallel=4, retry_base_delay=0.0)
    base.update(kw)
    return Settings(**base)


def make_orchestrator(tmp_path, *, llm=None, skills=None, notifier=None, **kw) -> Orchestrator:
    settings = make_settings(tmp_path, **kw)
    skills = skills or build_skill_registry()
    return Orchestrator(
        settings=settings,
        llm=llm or MockLLM(),
        skills=skills,
        agents=build_agent_registry(skills),
        tools=build_tool_registry(str(tmp_path), settings),
        store=RunStore(settings.db_path),
        metrics=Metrics(),
        notifier=notifier,
    )


@pytest.fixture
def settings_factory(tmp_path):
    def factory(**kw):
        return make_settings(tmp_path, **kw)
    return factory


@pytest.fixture
def orch_factory(tmp_path):
    def factory(**kw):
        return make_orchestrator(tmp_path, **kw)
    return factory

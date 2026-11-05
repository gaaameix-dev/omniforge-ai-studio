"""Every shipped skill must produce a real artifact with no model at all."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from omniforge.agents.roles import build_skill_registry
from omniforge.core.tasks import Run, Task
from omniforge.skills import generators
from omniforge.skills.base import Skill


class Ctx:
    def __init__(self, name: str = "skill", department: str = "research") -> None:
        self.task = Task(title="Deliverable", department=department, skill=name)
        self.run = Run(goal="Build a 2D mobile puzzle game with a leaderboard for Android")
        self.agent = None
        self.context = ("### Research brief\n- players want short sessions\n"
                        "### Constraints\n- 20 levels, offline first")
        self.blackboard = None
        self.feedback = None
        self.retrieved = ""
        self.spawned: list[str] = []

    def spawn(self, title: str = "t", department: str = "qa", skill: str = "test_plan",
              description: str = "") -> dict:
        self.spawned.append(title)
        return {"ok": True, "task_id": "task_1", "title": title}

    async def call_tool(self, name: str, **kwargs) -> str:
        return '{"characters": 10, "words": 2, "lines": 1}'


def _run(handler, ctx):
    result = handler(ctx)
    if inspect.isawaitable(result):
        result = asyncio.get_event_loop().run_until_complete(result) if False else asyncio.run(result)
    return result


SKILLS = build_skill_registry()


def test_every_skill_has_a_deterministic_implementation():
    missing = [s.name for s in SKILLS.all() if s.handler is None]
    assert missing == [], f"prompt-only skills left: {missing}"


def test_generator_count_matches_the_library():
    names = {s.name for s in SKILLS.all()}
    covered = names & set(generators.GENERATORS)
    assert len(covered) >= 40, f"only {len(covered)} skills backed by a generator"


@pytest.mark.parametrize("skill_name", sorted(s.name for s in SKILLS.all()))
def test_skill_output_is_real(skill_name):
    skill = SKILLS.get(skill_name)
    ctx = Ctx(skill_name, skill.department)
    out = _run(skill.handler, ctx)
    assert isinstance(out, str)
    assert len(out) >= 120, f"{skill_name} produced only {len(out)} chars"
    assert "[mock output]" not in out
    assert "TODO" not in out
    # a heading, a table, code or a checklist - some structure must exist
    assert any(marker in out for marker in ("#", "|", "```", "- [ ]", "--", "name:", "jobs:"))


@pytest.mark.parametrize("skill_name", sorted(s.name for s in SKILLS.all()))
def test_skill_is_deterministic(skill_name):
    skill = SKILLS.get(skill_name)
    first = _run(skill.handler, Ctx(skill_name, skill.department))
    second = _run(skill.handler, Ctx(skill_name, skill.department))
    assert first == second


@pytest.mark.parametrize("skill_name,needle", [
    ("android_implementation", "kotlin"),
    ("ios_implementation", "swift"),
    ("automated_tests", "def test_"),
    ("containerization", "FROM python"),
    ("metrics_report", "SELECT"),
    ("infra_as_code", "resource "),
    ("load_test", "locust"),
    ("game_concept_design", "Core loop"),
    ("npc_behavior", "Behaviour tree"),
    ("auth_flow", "PBKDF2"),
    ("security_review", "pip-audit"),
    ("monitoring_setup", "alert"),
    ("3d_modeling", "Triangles"),
    ("video_edit", "Transition"),
    ("web_research", "source"),
    ("roadmap_planning", "Exit criteria"),
    ("rag_pipeline", "chunk"),
    ("prompt_design", "System prompt"),
])
def test_skill_specific_content(skill_name, needle):
    skill = SKILLS.get(skill_name)
    out = _run(skill.handler, Ctx(skill_name, skill.department))
    assert needle.lower() in out.lower(), f"{skill_name} did not mention {needle}"


async def test_run_uses_generators_instead_of_placeholder(tmp_path):
    from conftest import make_orchestrator

    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Build a 2D mobile puzzle game", auto_approve=True)
    assert run.status.value == "done"
    for task in run.plan.tasks:
        assert task.result and "[mock output]" not in task.result
        assert task.review_score and task.review_score >= 0.9


class _LiveModel:
    """A non-mock model: planning falls back to the deterministic planner,
    completion returns text we can recognise."""

    name = "test-model"
    model = "test-model"

    async def complete(self, system, prompt, json_mode=False):
        from omniforge.core.llm import LLMResponse

        return LLMResponse(text="# Model answer\n\n- written by the model\n- not a generator",
                           model="test-model")

    async def plan(self, goal, catalog):
        from omniforge.core.llm import heuristic_plan

        return heuristic_plan(goal, catalog)


async def test_generators_win_by_default_even_with_a_live_model(tmp_path):
    from conftest import make_orchestrator

    orch = make_orchestrator(tmp_path, llm=_LiveModel())
    run = await orch.run_to_completion("Build a 2D mobile puzzle game", auto_approve=True)
    assert "written by the model" not in (run.plan.tasks[0].result or "")
    assert "# Research brief" in (run.plan.tasks[0].result or "")


async def test_prompt_path_is_used_when_generators_are_off(tmp_path):
    from conftest import make_orchestrator

    orch = make_orchestrator(tmp_path, llm=_LiveModel(), prefer_generators=False)
    run = await orch.run_to_completion("Build a 2D mobile puzzle game", auto_approve=True)
    assert any("written by the model" in (t.result or "") for t in run.plan.tasks)


async def test_custom_skill_without_a_generator_still_works(tmp_path):
    from conftest import FixedPlanner, make_orchestrator

    skills = build_skill_registry()
    skills.register("adhoc_skill", Skill(name="adhoc_skill", department="research",
                                         description="no generator, prompt only"))
    orch = make_orchestrator(
        tmp_path, skills=skills,
        llm=FixedPlanner([{"key": "a", "title": "Ad hoc", "department": "research",
                           "skill": "adhoc_skill", "depends_on": []}]),
    )
    run = await orch.run_to_completion("something", auto_approve=True)
    assert run.plan.tasks[0].status.value == "done"


def test_artifacts_do_not_stack_titles(tmp_path):
    from conftest import make_orchestrator

    orch = make_orchestrator(tmp_path)
    run = asyncio.run(orch.run_to_completion("Build a 2D mobile puzzle game", auto_approve=True))
    folder = tmp_path / "artifacts" / run.id
    for path in folder.glob("*.md"):
        text = path.read_text()
        lines = [line for line in text.splitlines() if line.startswith("# ")]
        assert len(lines) == 1, f"{path.name} has {len(lines)} top-level headings"

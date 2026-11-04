"""End-to-end and unit tests. No API key, no network: everything runs on MockLLM."""
from __future__ import annotations

import asyncio

import pytest

from omniforge.agents.departments import DEPARTMENTS, ROLES
from omniforge.agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
from omniforge.config import Settings
from omniforge.core.agent import AgentTemplate
from omniforge.core.llm import MockLLM, heuristic_plan
from omniforge.core.memory import Blackboard
from omniforge.core.message_bus import MessageBus
from omniforge.core.orchestrator import Orchestrator
from omniforge.core.tasks import Plan, Task, TaskStatus


# --------------------------------------------------------------------- fixtures
def make_settings(tmp_path, **kw) -> Settings:
    return Settings(
        mock_llm=True,
        data_dir=tmp_path,
        max_parallel=4,
        **kw,
    )


def make_orchestrator(tmp_path, **kw) -> Orchestrator:
    settings = make_settings(tmp_path, **kw)
    skills = build_skill_registry()
    return Orchestrator(
        settings=settings,
        llm=MockLLM(),
        skills=skills,
        agents=build_agent_registry(skills),
        tools=build_tool_registry(str(tmp_path)),
    )


# ------------------------------------------------------------------- catalogues
def test_departments_and_counts():
    skills = build_skill_registry()
    agents = build_agent_registry(skills)
    assert len(DEPARTMENTS) == 10
    assert sum(len(v) for v in ROLES.values()) == 120
    assert len(agents) == 120
    assert len(skills) == 51
    assert len(build_tool_registry()) >= 5
    for key in DEPARTMENTS:
        assert len(skills.by_department(key)) >= 5
        assert len(agents.by_department(key)) == 12


def test_agent_ids_are_unique_and_slugged():
    agents = build_agent_registry(build_skill_registry())
    ids = [a.id for a in agents.all()]
    assert len(ids) == len(set(ids))
    assert all("__" in i and " " not in i for i in ids)


@pytest.mark.parametrize(
    "department,skill,expected",
    [
        ("web", "database_schema", "Database Architect"),
        ("web", "frontend_implementation", "Frontend Developer"),
        ("ai_agent_systems", "rag_pipeline", "RAG Engineer"),
        ("mobile", "android_implementation", "Android Developer"),
        ("three_d", "rigging", "Character Rigger"),
        ("devops", "deploy_checklist", "Release Manager"),
        ("media", "music_compose", "Music Composer"),
    ],
)
def test_routing_picks_the_specialist(department, skill, expected):
    agents = build_agent_registry(build_skill_registry())
    assert agents.best_match(department, skill).name == expected


def test_every_skill_routes_to_an_agent_in_its_department():
    skills = build_skill_registry()
    agents = build_agent_registry(skills)
    for skill in skills.all():
        match = agents.best_match(skill.department, skill.name)
        assert match is not None and match.department == skill.department


# ----------------------------------------------------------------------- plans
def test_plan_levels_respect_dependencies():
    a, b, c = Task(title="a"), Task(title="b"), Task(title="c")
    b.depends_on = [a.id]
    c.depends_on = [b.id]
    plan = Plan(goal="g", tasks=[a, b, c])
    levels = plan.levels()
    assert [[t.title for t in wave] for wave in levels] == [["a"], ["b"], ["c"]]
    assert plan.validate() == []


def test_plan_detects_cycles_and_unknown_deps():
    a, b = Task(title="a"), Task(title="b")
    a.depends_on, b.depends_on = [b.id], [a.id]
    assert any("cycle" in p for p in Plan(goal="g", tasks=[a, b]).validate())
    lone = Task(title="x", depends_on=["task_missing"])
    assert any("unknown" in p for p in Plan(goal="g", tasks=[lone]).validate())


def test_heuristic_planner_shape_and_deps():
    catalog = {k: [s.name for s in build_skill_registry().by_department(k)] for k in DEPARTMENTS}
    raw = heuristic_plan("Build a 2D mobile puzzle game with a leaderboard", catalog)
    keys = {t["key"] for t in raw["tasks"]}
    assert "research" in keys and "qa" in keys and "summary" in keys
    depts = {t["department"] for t in raw["tasks"]}
    assert "mobile" in depts and "game_development" in depts
    for task in raw["tasks"]:
        assert set(task["depends_on"]) <= keys
        assert task["skill"] in catalog[task["department"]]


def test_plan_builder_repairs_unknown_department_and_skill(tmp_path):
    orch = make_orchestrator(tmp_path)
    raw = {"tasks": [
        {"key": "x", "title": "bogus", "department": "not_a_dept", "skill": "not_a_skill", "depends_on": ["nope"]},
    ]}
    plan = orch._build_plan("goal", raw)
    assert plan.tasks[0].department in DEPARTMENTS
    assert plan.tasks[0].skill in orch.catalog[plan.tasks[0].department]
    assert plan.tasks[0].depends_on == []


# ------------------------------------------------------------------- execution
async def test_end_to_end_run(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Build a 2D mobile puzzle game with 20 levels and a leaderboard")
    assert run.status.value == "done", run.error
    assert run.plan is not None
    assert all(t.status is TaskStatus.DONE for t in run.plan.tasks)
    assert run.summary
    # deterministic handlers really ran: QA task produced a test plan, not a stub
    qa = next(t for t in run.plan.tasks if t.department == "qa")
    assert "Test plan" in (qa.result or "")
    summary_file = tmp_path / "artifacts" / run.id / "summary.md"
    assert summary_file.exists()
    assert orch.store.get_run(run.id) is not None
    assert run.tokens > 0


async def test_blackboard_accumulates_upstream_results(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Design a SaaS dashboard with auth and postgres", auto_approve=True)
    board = orch.blackboard.all(run.id)
    assert len(board) == len(run.plan.tasks)
    assert "Research" in " ".join(board) or board


async def test_events_are_published_in_order(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Create a Unity racing game prototype")
    types = [e["type"] for e in orch.bus.history(run_id=run.id)]
    assert types[0] == "run.created"
    assert "plan.created" in types
    assert types[-1] == "run.finished"
    assert types.count("task.finished") == len(run.plan.tasks)


async def _drive_approvals(orch, run, approve: bool, limit: int = 500) -> int:
    """Approve (or reject) every gate the run raises, until it finishes."""
    seen = 0
    for _ in range(limit):
        pending = orch.pending_approvals(run.id)
        for item in pending:
            orch.approve(run.id, item["task_id"], approve, "test decision")
            seen += 1
        if orch._jobs[run.id].done():
            return seen
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish while approvals were being answered")


async def test_approval_gate_blocks_until_a_human_answers(tmp_path):
    orch = make_orchestrator(tmp_path, require_approval=True)
    run = await orch.submit("Write a product spec for a fitness app")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if orch.pending_approvals(run.id):
            break
    pending = orch.pending_approvals(run.id)
    assert pending, "no approval was requested"
    assert run.status.value == "awaiting_approval"
    assert orch.approve(run.id, pending[0]["task_id"], True, "looks fine")
    assert await _drive_approvals(orch, run, approve=True) >= 1
    await orch.wait(run.id)
    assert run.status.value == "done"
    assert all(t.status is TaskStatus.DONE for t in run.plan.tasks)


async def test_unknown_approval_is_rejected(tmp_path):
    orch = make_orchestrator(tmp_path, require_approval=True)
    run = await orch.submit("Plan a web storefront")
    assert orch.approve(run.id, "task_missing", True) is False


async def test_rejection_skips_the_task_and_its_dependents(tmp_path):
    orch = make_orchestrator(tmp_path, require_approval=True)
    run = await orch.submit("Plan a web storefront")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if orch.pending_approvals(run.id):
            break
    pending = orch.pending_approvals(run.id)
    assert pending
    orch.approve(run.id, pending[0]["task_id"], False, "out of scope")
    await orch.wait(run.id)
    assert run.status.value == "failed"
    assert all(t.status is not TaskStatus.DONE for t in run.plan.tasks)


async def test_cancel_stops_a_run(tmp_path):
    orch = make_orchestrator(tmp_path, require_approval=True)
    run = await orch.submit("Build a large open world RPG")
    await asyncio.sleep(0.05)
    await orch.cancel(run.id)
    assert run.status.value == "cancelled"


# ------------------------------------------------------------------ extensibility
async def test_can_add_a_new_agent_role(tmp_path):
    """The 'VR Game Engineer' example: bundle existing skills into a new role."""
    skills = build_skill_registry()
    agents = build_agent_registry(skills)
    agents.register(
        "game_development__vr_game_engineer",
        AgentTemplate(
            id="game_development__vr_game_engineer",
            name="VR Game Engineer",
            department="game_development",
            description="Bundles 3D, physics and XR skills.",
            skills=["3d_modeling", "gameplay_logic", "engine_optimization"],
            primary_skills=["3d_modeling"],
            tools=["now"],
            system_prompt="You are a VR specialist.",
        ),
    )
    assert agents.best_match("game_development", "3d_modeling").name == "VR Game Engineer"
    assert len(agents) == 121


def test_custom_skill_handler_runs_without_a_model(tmp_path):
    from omniforge.core.agent import Agent
    from omniforge.core.tasks import Run
    from omniforge.skills.base import Skill

    skills = build_skill_registry()
    skills.register("shout", Skill(name="shout", department="research", description="uppercases",
                                  handler=lambda ctx: ctx.task.title.upper()))
    template = AgentTemplate(id="a", name="A", department="research", skills=["shout"], primary_skills=["shout"])
    agent = Agent(template, llm=MockLLM(), skills=skills, tools=build_tool_registry(str(tmp_path)),
                  bus=MessageBus(), blackboard=Blackboard())
    run = Run(goal="g")
    task = Task(title="hello world", department="research", skill="shout")
    assert asyncio.run(agent.execute(task, run, "")) == "HELLO WORLD"


# ------------------------------------------------------------------------ bus
async def test_bus_filters_by_run_and_type():
    bus = MessageBus()
    sub = bus.subscribe(run_id="r1", types={"task.finished"})
    await bus.emit("task.finished", run_id="r1")
    await bus.emit("task.finished", run_id="r2")
    await bus.emit("task.started", run_id="r1")
    got = await sub.next(timeout=0.2)
    assert got is not None and got.run_id == "r1" and got.type == "task.finished"
    assert await sub.next(timeout=0.1) is None
    assert len(bus.history(run_id="r1")) == 2


# ------------------------------------------------------------------------ API
def test_api_smoke(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from omniforge.api.app import create_app

    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["backend"] == "mock"
        assert len(client.get("/api/agents").json()) == 120
        assert len(client.get("/api/skills").json()) == 51
        assert len(client.get("/api/departments").json()) == 10

        created = client.post("/api/runs", json={"goal": "Build a mobile recipe app", "auto_approve": True})
        assert created.status_code == 201
        run_id = created.json()["id"]

        run = {}
        for _ in range(150):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] in {"done", "failed"}:
                break
            time.sleep(0.02)
        assert run["status"] == "done"
        assert run["counts"]["done"] == len(run["plan"]["tasks"])
        assert client.get("/api/runs/does-not-exist").status_code == 404
        assert client.get("/").status_code == 200


def test_websocket_streams_the_whole_run(tmp_path):
    from fastapi.testclient import TestClient

    from omniforge.api.app import create_app

    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"goal": "Build a web storefront", "auto_approve": True}).json()
        types: list[str] = []
        with client.websocket_connect(f"/ws/runs/{run['id']}") as ws:
            for _ in range(80):
                msg = ws.receive_json()
                if msg.get("type"):
                    types.append(msg["type"])
                if types and types[-1] == "run.finished":
                    break
        assert types[0] == "run.created"
        assert types[-1] == "run.finished"
        assert "task.finished" in types

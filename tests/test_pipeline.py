"""Retry, review, budget, delegation, knowledge and metrics."""
from __future__ import annotations

import asyncio

import pytest
from conftest import FixedPlanner, make_orchestrator, make_settings

from omniforge.core.budget import Budget
from omniforge.core.knowledge import KnowledgeBase, tokenize
from omniforge.core.llm import MockLLM
from omniforge.core.metrics import Metrics
from omniforge.core.retry import RetryPolicy, call_with_retry
from omniforge.core.review import Reviewer
from omniforge.core.store import RunStore
from omniforge.core.tasks import Task, TaskStatus
from omniforge.skills.base import Skill


# ------------------------------------------------------------------- retries
async def test_retry_succeeds_after_failures():
    calls = {"n": 0}

    async def flaky(attempt):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert await call_with_retry(flaky, RetryPolicy(attempts=3, base_delay=0.0)) == "ok"
    assert calls["n"] == 3


async def test_retry_exhausts_and_raises():
    async def always_fail(attempt):
        raise ValueError(f"boom {attempt}")

    with pytest.raises(ValueError):
        await call_with_retry(always_fail, RetryPolicy(attempts=2, base_delay=0.0))


async def test_retry_reports_each_attempt():
    seen = []

    async def flaky(attempt):
        if attempt < 3:
            raise RuntimeError("nope")
        return "fine"

    async def on_retry(attempt, exc, delay):
        seen.append((attempt, type(exc).__name__, delay))

    await call_with_retry(flaky, RetryPolicy(attempts=3, base_delay=0.0), on_retry=on_retry)
    assert seen == [(1, "RuntimeError", 0.0), (2, "RuntimeError", 0.0)]


async def test_retry_does_not_swallow_cancellation():
    async def cancelled(attempt):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await call_with_retry(cancelled, RetryPolicy(attempts=3, base_delay=0.0))


def test_delay_backs_off_and_is_capped():
    policy = RetryPolicy(base_delay=1.0, factor=2.0, max_delay=4.0, jitter=0.0)
    assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]


def test_policy_from_settings():
    settings = make_settings("/tmp", max_retries=4, retry_base_delay=0.25)
    policy = RetryPolicy.from_settings(settings)
    assert policy.attempts == 5 and policy.base_delay == 0.25


# -------------------------------------------------------------------- review
def _task(title="t", description="d") -> Task:
    return Task(title=title, description=description, department="research", skill="web_research")


async def test_review_flags_prompt_echo():
    settings = make_settings("/tmp")
    reviewer = Reviewer(settings, MockLLM())
    out = "# x\n\n- Deliver the work product for your task now\n- more text here to pass length"
    result = await reviewer.review(_task(), out)
    assert not result.passed
    assert any("echoes the prompt" in i for i in result.issues)


async def test_review_passes_structured_output():
    settings = make_settings("/tmp")
    reviewer = Reviewer(settings, MockLLM())
    out = "# Research brief\n\n- Claim one with a source\n- Claim two with a source\n\n## Gaps\n- pricing"
    result = await reviewer.review(_task(), out)
    assert result.passed and result.score >= 0.9


async def test_review_placeholder_passes_in_mock_mode():
    settings = make_settings("/tmp")
    reviewer = Reviewer(settings, MockLLM())
    result = await reviewer.review(_task(), "[mock output] placeholder deliverable for: x")
    assert result.passed and result.placeholder


async def test_review_requires_skill_sections():
    settings = make_settings("/tmp")
    reviewer = Reviewer(settings, MockLLM())
    skill = Skill(name="test_plan", department="qa", description="x", required_any=("## Test levels",))
    result = await reviewer.review(_task(), "# Plan\n\n- something\n- another line\n- third line", skill)
    assert not result.passed
    assert any("missing required section" in i for i in result.issues)


async def test_review_rejects_copy_of_upstream():
    settings = make_settings("/tmp")
    reviewer = Reviewer(settings, MockLLM())
    context = "### previous\n# Brief\n\n- a\n- b\n- c\n"
    result = await reviewer.review(_task(), "# Brief\n\n- a\n- b\n- c", context=context)
    assert not result.passed
    assert any("copy of upstream" in i for i in result.issues)


async def test_review_empty_output_fails():
    result = await Reviewer(make_settings("/tmp"), MockLLM()).review(_task(), "   ")
    assert not result.passed and result.score == 0.0


async def test_rework_loop_retries_until_review_passes(tmp_path):
    """A skill that produces junk once, then a real artifact, must pass on attempt 2."""
    state = {"calls": 0}

    def handler(ctx):
        state["calls"] += 1
        if state["calls"] == 1:
            return "too short"
        return "# Deliverable\n\n- item one\n- item two\n- item three with detail"

    skills = __import__("omniforge.agents.roles", fromlist=["build_skill_registry"]).build_skill_registry()
    skills.register("rework_demo", Skill(name="rework_demo", department="research",
                                         description="demo", handler=handler))
    orch = make_orchestrator(
        tmp_path,
        skills=skills,
        llm=FixedPlanner([{"key": "a", "title": "Rework demo", "department": "research",
                           "skill": "rework_demo", "depends_on": []}]),
        max_retries=2,
    )
    run = await orch.run_to_completion("rework please", auto_approve=True)
    task = run.plan.tasks[0]
    assert task.status is TaskStatus.DONE
    assert state["calls"] == 2
    assert task.attempts >= 2


# -------------------------------------------------------------------- budget
def test_budget_accounting():
    budget = Budget(limit_tokens=100, price_per_1k_input=1.0, price_per_1k_output=2.0)
    budget.add(600, 400)
    assert budget.tokens == 1000 and budget.exceeded
    assert round(budget.cost_usd, 4) == 1.4
    small = Budget(limit_tokens=1000)
    small.add_estimate("x" * 400, "y" * 800)
    assert small.tokens == 300 and not small.exceeded


async def test_budget_cap_stops_the_run(tmp_path):
    orch = make_orchestrator(tmp_path, budget_tokens=1)
    run = await orch.run_to_completion("Build a mobile app with a leaderboard", auto_approve=True)
    assert run.status.value == "failed"
    assert "budget" in (run.error or "")


# --------------------------------------------------------------- delegation
async def test_delegation_spawns_and_runs_subtasks(tmp_path):
    orch = make_orchestrator(
        tmp_path,
        llm=FixedPlanner([{"key": "a", "title": "Break the work down", "department": "product_ops",
                           "skill": "work_breakdown", "depends_on": []}]),
    )
    run = await orch.run_to_completion("Plan a product launch", auto_approve=True)
    titles = [t.title for t in run.plan.tasks]
    assert len(run.plan.tasks) == 3, titles
    children = [t for t in run.plan.tasks if t.parent_id]
    assert len(children) == 2
    assert {t.department for t in children} == {"qa", "devops"}
    assert all(t.status is TaskStatus.DONE for t in children)
    types = [e["type"] for e in orch.bus.history(run_id=run.id)]
    assert "task.finished" in types
    assert orch.metrics.counters["delegations"] == 2


async def test_delegation_respects_task_cap(tmp_path):
    orch = make_orchestrator(
        tmp_path,
        llm=FixedPlanner([{"key": "a", "title": "Break the work down", "department": "product_ops",
                           "skill": "work_breakdown", "depends_on": []}]),
        max_tasks_per_run=1,
    )
    run = await orch.run_to_completion("Plan a product launch", auto_approve=True)
    assert len(run.plan.tasks) == 1
    assert orch.metrics.counters["delegations.rejected"] == 2
    assert run.status.value == "done"


async def test_delegation_dedupes_titles(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.submit("Plan a product launch", auto_approve=True)
    await orch.wait(run.id)
    parent = run.plan.tasks[0]
    first = orch._delegate(run, parent, {"title": "Unique child", "department": "qa", "skill": "test_plan"})
    second = orch._delegate(run, parent, {"title": "Unique child", "department": "qa", "skill": "test_plan"})
    assert first["ok"] and second["ok"]
    assert first["task_id"] == second["task_id"]
    assert second.get("deduplicated") is True


async def test_delegation_depth_limit(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.submit("Plan a product launch", auto_approve=True)
    await orch.wait(run.id)
    root = run.plan.tasks[0]
    level1 = orch._delegate(run, root, {"title": "l1", "department": "qa", "skill": "test_plan"})
    t1 = run.plan.by_id()[level1["task_id"]]
    level2 = orch._delegate(run, t1, {"title": "l2", "department": "qa", "skill": "test_plan"})
    t2 = run.plan.by_id()[level2["task_id"]]
    level3 = orch._delegate(run, t2, {"title": "l3", "department": "qa", "skill": "test_plan"})
    assert level3["ok"] is False and "depth" in level3["reason"]


# ---------------------------------------------------------------- knowledge
def test_tokenize_drops_stopwords_and_stems():
    tokens = tokenize("The running builds are shipping models")
    assert "the" not in tokens and "are" not in tokens
    assert "run" in tokens and "build" in tokens


def test_bm25_ranks_the_relevant_document(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    kb = KnowledgeBase(store)
    kb.add("r1", "postgres schema", "indexes on foreign keys and vacuum settings", "t")
    kb.add("r2", "audio mixing", "compressor thresholds and loudness targets", "t")
    kb.add("r3", "postgres tuning", "vacuum autovacuum and index bloat", "t")
    hits = kb.search("vacuum index postgres", limit=2)
    assert hits and hits[0]["run_id"] in {"r1", "r3"}
    assert all("audio" not in h["title"] for h in hits)
    store.close()


def test_knowledge_context_and_exclusion(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    kb = KnowledgeBase(store)
    kb.add("r1", "shader notes", "fresnel rim lighting for stylised water", "t")
    assert "fresnel" in kb.context_for("water shader lighting", exclude_run="r2")
    assert kb.context_for("water shader lighting", exclude_run="r1") == ""
    assert kb.stats()["documents"] == 1
    store.close()


async def test_run_indexes_its_output_for_later_runs(tmp_path):
    orch = make_orchestrator(tmp_path)
    run = await orch.run_to_completion("Design a postgres schema for invoicing", auto_approve=True)
    hits = orch.knowledge.search("postgres invoicing schema", limit=5)
    assert hits, "finished run should be indexed"
    assert all(h["run_id"] == run.id for h in hits)


# ------------------------------------------------------------------ metrics
async def test_metrics_after_a_run(tmp_path):
    orch = make_orchestrator(tmp_path)
    await orch.run_to_completion("Build a mobile recipe app", auto_approve=True)
    snap = orch.metrics.snapshot()
    assert snap["counters"]["runs.submitted"] == 1
    assert snap["counters"]["tasks.finished"] >= 1
    assert snap["timings"]["run.duration"]["count"] == 1
    text = orch.metrics.prometheus()
    assert "omniforge_runs_submitted_total 1" in text
    assert "omniforge_run_duration_seconds" in text


def test_metrics_gauges_and_summary():
    m = Metrics()
    m.inc("x", 2)
    m.set("g", 5)
    for v in (1.0, 2.0, 3.0):
        m.observe("lat", v)
    snap = m.snapshot()
    assert snap["counters"]["x"] == 2 and snap["gauges"]["g"] == 5
    assert snap["timings"]["lat"]["p95"] == 3.0
    assert m.summary("missing")["count"] == 0


async def test_parallel_runs_do_not_interfere(tmp_path):
    orch = make_orchestrator(tmp_path, max_parallel=6)
    goals = [
        "Build a mobile puzzle game",
        "Design a postgres schema for invoicing",
        "Produce a launch video with sound design",
    ]
    runs = [await orch.submit(g, auto_approve=True) for g in goals]
    for run in runs:
        await orch.wait(run.id)
    assert all(r.status.value == "done" for r in runs)
    ids = {r.id for r in runs}
    for run in runs:
        assert run.plan is not None
        assert all(t.status is TaskStatus.DONE for t in run.plan.tasks)
        board = orch.blackboard.all(run.id)
        assert board, "each run must have its own blackboard entries"
    assert len(ids) == 3
    # knowledge base holds all three runs, attributed to the right run
    hits = orch.knowledge.search("postgres schema invoicing", limit=5)
    assert any(h["run_id"] == runs[1].id for h in hits)


async def test_budget_reports_tokens_and_cost(tmp_path):
    orch = make_orchestrator(tmp_path, price_per_1k_input=1.0, price_per_1k_output=1.0)
    run = await orch.run_to_completion("Write a test plan for a web storefront", auto_approve=True)
    assert run.tokens > 0
    assert run.cost_usd > 0
    assert orch.budget_of(run.id)["tokens"] == run.tokens


async def test_fail_fast_marks_dependents_skipped(tmp_path):
    def boom(ctx):
        raise RuntimeError("handler exploded")

    from omniforge.agents.roles import build_skill_registry

    skills = build_skill_registry()
    skills.register("boom", Skill(name="boom", department="research", description="fails", handler=boom))
    orch = make_orchestrator(
        tmp_path,
        skills=skills,
        max_retries=0,
        llm=FixedPlanner([
            {"key": "a", "title": "Explode", "department": "research", "skill": "boom", "depends_on": []},
            {"key": "b", "title": "After", "department": "qa", "skill": "test_plan", "depends_on": ["a"]},
        ]),
    )
    run = await orch.run_to_completion("make it explode", auto_approve=True)
    assert run.status.value == "failed"
    by_title = {t.title: t for t in run.plan.tasks}
    assert by_title["Explode"].status is TaskStatus.FAILED
    assert by_title["After"].status is TaskStatus.SKIPPED
    assert "handler exploded" in (by_title["Explode"].error or "")

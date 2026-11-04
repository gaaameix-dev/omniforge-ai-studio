"""Deterministic skill handlers - real work with no model call."""
from __future__ import annotations

import re
from typing import Any


def _sections(context: str) -> list[str]:
    return [line.strip("# ").strip() for line in context.splitlines() if line.startswith("### ")]


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text))


async def data_profile(ctx: Any) -> str:
    ctx_text = ctx.context or ""
    sections = _sections(ctx_text)
    stats = {"characters": len(ctx_text), "words": _words(ctx_text), "lines": len(ctx_text.splitlines())}
    try:
        import json as _json

        raw = await ctx.call_tool("text_stats", text=ctx_text or ctx.run.goal)
        stats = _json.loads(raw)
    except Exception:  # noqa: BLE001 - profiling must not fail on a tool problem
        pass
    lines = [
        f"# Data profile - {ctx.task.title}",
        "",
        "## Inputs seen",
        f"- Upstream sections available: {len(sections)}",
        f"- Context size: {stats.get('characters', 0)} characters, "
        f"{stats.get('words', 0)} words, {stats.get('lines', 0)} lines",
    ]
    if sections:
        lines.append("- Sections: " + ", ".join(f"`{s}`" for s in sections))
    lines += [
        "",
        "## Quality checks",
        "- [ ] Every upstream section maps to a task in the plan",
        "- [ ] No section is empty or placeholder-only",
        "- [ ] Numeric claims are traceable to a source",
        "",
        "## Recommended next step",
        "Feed the sections above into the synthesis skill once gaps are closed.",
    ]
    return "\n".join(lines)


def insight_synthesis(ctx: Any) -> str:
    sections = _sections(ctx.context or "")
    lines = [f"# Synthesis - {ctx.task.title}", "", f"Goal: {ctx.run.goal}", "", "## What we have"]
    if not sections:
        lines.append("- No upstream material yet; this is a goal-only pass.")
    for name in sections:
        lines.append(f"- **{name}** - completed, ready to fold into the deliverable")
    lines += [
        "",
        "## Open questions",
        "- Which constraints are hard vs. negotiable?",
        "- Who signs off on the final artifact?",
        "",
        "## Suggested next actions",
        "1. Validate the assumptions listed by each department.",
        "2. Turn the highest-risk item into a spike task.",
        "3. Schedule the human review gate.",
    ]
    return "\n".join(lines)


def test_plan(ctx: Any) -> str:
    sections = _sections(ctx.context or "") or ["the deliverable"]
    lines = [
        f"# Test plan - {ctx.run.goal}",
        "",
        "## Scope",
        f"{len(sections)} produced artifact(s) under test.",
        "",
        "## Test levels",
        "| Level | What is checked | Owner agent |",
        "|---|---|---|",
        "| Unit | deterministic skill handlers, plan validation | QA Engineer |",
        "| Integration | orchestrator -> agent -> tool round trip | Automation Test Engineer |",
        "| End-to-end | goal in, artifacts out, no orphan tasks | QA Engineer |",
        "| Non-functional | latency per task, retry behaviour, approval gates | Performance Test Engineer |",
        "",
        "## Cases per artifact",
    ]
    for name in sections:
        lines.append(f"- **{name}**: happy path, empty input, malformed upstream result, retry after failure")
    lines += [
        "",
        "## Exit criteria",
        "- [ ] All end-to-end cases green",
        "- [ ] No task left in `running` state after a run finishes",
        "- [ ] Failed task marks its dependents `skipped`, never `done`",
    ]
    return "\n".join(lines)


def deploy_checklist(ctx: Any) -> str:
    return "\n".join(
        [
            f"# Deployment checklist - {ctx.run.goal}",
            "",
            "## Before deploy",
            "- [ ] All QA exit criteria met",
            "- [ ] Secrets come from env vars, never committed",
            "- [ ] Rollback path documented and tested",
            "- [ ] Health endpoint and logs wired",
            "",
            "## Pipeline stages",
            "1. lint + unit tests",
            "2. build artifact (immutable tag)",
            "3. deploy to staging, run smoke tests",
            "4. human approval gate",
            "5. deploy to production, watch error rate for 15 minutes",
            "",
            "## After deploy",
            "- [ ] Smoke test the public URL",
            "- [ ] Alerting and dashboards verified",
            "- [ ] Release note posted",
        ]
    )


def product_spec(ctx: Any) -> str:
    sections = _sections(ctx.context or "")
    return "\n".join(
        [
            f"# Product spec - {ctx.run.goal}",
            "",
            "## Problem",
            "State the user problem in one sentence, then the evidence behind it.",
            "",
            "## Scope",
            f"Built from {len(sections)} department deliverables: " + (", ".join(sections) if sections else "goal only"),
            "",
            "## Requirements",
            "| ID | Requirement | Priority | Source |",
            "|---|---|---|---|",
            "| R1 | Master orchestrator decomposes the goal into dependent tasks | P0 | orchestrator |",
            "| R2 | Each task is routed to a department agent with the matching skill | P0 | registry |",
            "| R3 | Human approval gate before irreversible work | P1 | approvals |",
            "| R4 | Every run leaves a replayable event log | P1 | message bus |",
            "",
            "## Open risks",
            "- Model cost and latency at scale",
            "- Quality drift when an agent is re-routed across departments",
            "",
            "## Next steps",
            "1. Pick the thinnest end-to-end slice and ship it.",
            "2. Instrument cost and latency per task.",
            "3. Add evaluation fixtures before widening the skill library.",
        ]
    )


def risk_register(ctx: Any) -> str:
    return "\n".join(
        [
            f"# Risk register - {ctx.run.goal}",
            "",
            "| Risk | Likelihood | Impact | Mitigation | Owner |",
            "|---|---|---|---|---|",
            "| Model hallucinates an unbuildable plan | Medium | High | plan validation + heuristic fallback | Master Orchestrator |",
            "| Runaway token spend | Medium | Medium | max_parallel cap, per-task budgets | AI Safety Reviewer |",
            "| Agent writes outside its scope | Low | High | tool allow-list per role | Security Hardening Engineer |",
            "| Approval gate bypassed in automation | Low | High | require_approval forced by policy | Approval Coordinator |",
        ]
    )


def launch_checklist(ctx: Any) -> str:
    return "\n".join(
        [
            f"# Launch checklist - {ctx.run.goal}",
            "",
            "## T-7 days",
            "- [ ] Freeze scope, confirm owner and rollback plan",
            "## T-1 day",
            "- [ ] Staging sign-off, support runbook ready",
            "## Launch",
            "- [ ] Announce, monitor error rate and latency",
            "## T+1 day",
            "- [ ] Collect feedback, file top 3 defects, publish retro",
        ]
    )


def database_schema(ctx: Any) -> str:
    return "\n".join(
        [
            "-- Starter schema generated by the database_schema skill",
            "-- Adjust names and types to the product before shipping.",
            "",
            "CREATE TABLE runs (",
            "    id          TEXT PRIMARY KEY,",
            "    goal        TEXT NOT NULL,",
            "    status      TEXT NOT NULL,",
            "    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),",
            "    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()",
            ");",
            "",
            "CREATE TABLE tasks (",
            "    id          TEXT PRIMARY KEY,",
            "    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,",
            "    title       TEXT NOT NULL,",
            "    department  TEXT NOT NULL,",
            "    skill       TEXT NOT NULL,",
            "    agent_id    TEXT,",
            "    status      TEXT NOT NULL,",
            "    result      TEXT,",
            "    error       TEXT",
            ");",
            "",
            "CREATE TABLE events (",
            "    id          BIGSERIAL PRIMARY KEY,",
            "    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,",
            "    type        TEXT NOT NULL,",
            "    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,",
            "    ts          TIMESTAMPTZ NOT NULL DEFAULT now()",
            ");",
            "",
            "CREATE INDEX idx_tasks_run ON tasks(run_id);",
            "CREATE INDEX idx_events_run ON events(run_id, ts DESC);",
        ]
    )


def ci_pipeline(ctx: Any) -> str:
    return "\n".join(
        [
            "name: ci",
            "on:",
            "  push:",
            "    branches: [main]",
            "  pull_request:",
            "jobs:",
            "  test:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-python@v5",
            "        with:",
            "          python-version: '3.12'",
            "      - run: pip install -e .[dev]",
            "      - run: pytest -q",
        ]
    )


def work_breakdown(ctx: Any) -> str:
    """Decomposes the run goal and hands two subtasks to other departments."""
    spawned = []
    spawned.append(ctx.spawn(
        title="Compliance and security review of the plan",
        department="qa",
        skill="security_review",
        description=f"Threat model and fixes for: {ctx.run.goal}",
    ))
    spawned.append(ctx.spawn(
        title="Deployment and containerisation plan",
        department="devops",
        skill="containerization",
        description=f"Ship it: {ctx.run.goal}",
    ))
    lines = [f"# Work breakdown - {ctx.task.title}", "", f"Goal: {ctx.run.goal}", "", "## Delegated"]
    for item in spawned:
        if item.get("ok"):
            lines.append(f"- `{item['task_id']}` {item['title']} -> {item.get('department', '')}/{item.get('skill', '')}")
        else:
            lines.append(f"- not delegated: {item.get('reason')}")
    lines += [
        "",
        "## Why",
        "Both follow-ups depend on this breakdown, so the orchestrator schedules them "
        "as a new wave instead of blocking this task.",
    ]
    return "\n".join(lines)


HANDLERS = {
    "data_profile": data_profile,
    "insight_synthesis": insight_synthesis,
    "test_plan": test_plan,
    "deploy_checklist": deploy_checklist,
    "product_spec": product_spec,
    "risk_register": risk_register,
    "launch_checklist": launch_checklist,
    "database_schema": database_schema,
    "ci_pipeline": ci_pipeline,
    "work_breakdown": work_breakdown,
}

"""Engine validation, UI audit, pgvector, tool/LLM spans and live verification."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import make_orchestrator, make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core.engine_build import project_looks_valid, resolve_validate_command, validate
from omniforge.core.engines import scaffold
from omniforge.core.ui_audit import audit

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- UI audit
def test_ui_audit_passes_on_the_shipped_dashboard():
    report = audit()
    assert report["score"] == 1.0, [c for c in report["checks"] if not c["ok"]]
    assert report["total"] >= 9


def test_ui_audit_detects_problems(tmp_path):
    broken = tmp_path / "index.html"
    broken.write_text("<html><body><div style='width:1200px'>x</div></body></html>")
    report = audit(broken)
    failed = {c["check"] for c in report["checks"] if not c["ok"]}
    assert {"viewport_meta", "media_queries", "aria_live", "lang_attribute"} <= failed


def test_ui_audit_endpoint(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        report = client.get("/api/ui-audit").json()
        assert report["score"] == 1.0


# --------------------------------------------------------- engine validate
def test_static_project_check(tmp_path):
    project = tmp_path / "game"
    project.mkdir()
    assert project_looks_valid("godot", project)["ok"] is False
    (project / "project.godot").write_text("config_version=5\n")
    assert project_looks_valid("godot", project)["ok"] is True


def test_validate_command_resolution(tmp_path):
    command = resolve_validate_command("godot", tmp_path, make_settings(tmp_path))
    assert command[0] == "godot" and "--headless" in command


async def test_validate_reports_missing_engine(tmp_path):
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    result = await validate("godot", project, settings=make_settings(tmp_path))
    assert result["ok"] is False
    assert "not installed" in result["error"]


async def test_validate_runs_a_real_project(tmp_path):
    """With Godot on PATH, a scaffolded project must open cleanly."""
    godot = shutil.which("godot") or os.getenv("OMNIFORGE_TEST_GODOT", "")
    if not godot:
        pytest.skip("Godot is not installed in this environment")
    project = tmp_path / "game"
    project.mkdir()
    for rel, content in scaffold("godot", name="Neon Drift").items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    settings = make_settings(tmp_path, engine_validate_commands={
        "godot": f"{godot} --headless --path {{project}} --quit"})
    result = await validate("godot", project, settings=settings, timeout=120)
    assert result["ok"] is True, result
    assert result["static"]["ok"] is True


# ------------------------------------------------------------- pgvector
@pytest.fixture(scope="module")
def pg_url():
    pytest.importorskip("pgserver")
    pytest.importorskip("psycopg")
    import tempfile

    import pgserver

    return pgserver.get_server(tempfile.mkdtemp()).get_uri()


def test_pgvector_reports_availability(pg_url):
    from omniforge.core.vector_pg import available

    report = available(pg_url)
    assert report["available"] is True


def test_pgvector_index_search(pg_url):
    from omniforge.core.vector_pg import PgVectorIndex

    index = PgVectorIndex(pg_url, dim=4, table="doc_embeddings_test")
    index.ensure_schema()
    index.put(1, [1.0, 0.0, 0.0, 0.0], {"title": "postgres"})
    index.put(2, [0.0, 1.0, 0.0, 0.0], {"title": "audio"})
    index.put(3, [0.95, 0.05, 0.0, 0.0], {"title": "database"})
    assert index.count() == 3
    hits = index.search([1.0, 0.02, 0.0, 0.0], limit=2)
    assert [hit["document_id"] for hit in hits] == [1, 3]
    assert hits[0]["score"] > hits[1]["score"] > 0.9
    assert index.delete(2) == 1 and index.count() == 2


def test_pgvector_rejects_wrong_dimension(pg_url):
    from omniforge.core.vector_pg import PgVectorIndex, VectorError

    index = PgVectorIndex(pg_url, dim=3, table="doc_embeddings_dim")
    index.ensure_schema()
    with pytest.raises(VectorError):
        index.put(1, [1.0, 2.0])


def test_vector_status_endpoint(tmp_path, pg_url):
    settings = make_settings(tmp_path, db_url=pg_url)
    with TestClient(create_app(settings)) as client:
        info = client.get("/api/vectors").json()
        assert info["backend"] == "pgvector"
        assert info["pgvector"]["available"] is True


# ----------------------------------------------------------------- spans
async def test_tool_and_llm_spans_are_recorded(tmp_path):
    class Model:
        name = "span-model"
        model = "span-model"

        async def plan(self, goal, catalog):
            from omniforge.core.llm import heuristic_plan

            return heuristic_plan(goal, catalog)

        async def complete(self, system, prompt, json_mode=False):
            from omniforge.core.llm import LLMResponse

            return LLMResponse(text="# Answer\n\n- one point\n- two points", model="span-model")

        async def stream(self, system, prompt, on_token, *, json_mode=False):
            return await self.complete(system, prompt, json_mode)

    orch = make_orchestrator(tmp_path, llm=Model(), prefer_generators=False,
                             review_enabled=False)
    await orch.run_to_completion("Build a mobile game", auto_approve=True)
    names = set(orch.tracer.summary()["by_name"])
    assert "task" in names and "llm" in names


def test_tool_span_via_handler(tmp_path):
    from omniforge.agents.roles import build_skill_registry
    from omniforge.skills.base import Skill

    async def handler(ctx):
        await ctx.call_tool("now")
        return "# Done\n\n- called a tool\n- and returned"

    skills = build_skill_registry()
    skills.register("span_tool", Skill(name="span_tool", department="research",
                                       description="d", handler=handler))

    async def run():
        from conftest import FixedPlanner

        orch = make_orchestrator(tmp_path, skills=skills,
                                 llm=FixedPlanner([{"key": "a", "title": "Tool",
                                                    "department": "research",
                                                    "skill": "span_tool",
                                                    "depends_on": []}]))
        await orch.run_to_completion("call a tool", auto_approve=True)
        return orch

    orch = asyncio.run(run())
    assert "tool" in orch.tracer.summary()["by_name"]


# --------------------------------------------------------- live verify CLI
def test_verify_live_without_keys_is_honest(tmp_path):
    env = {**os.environ, "OMNIFORGE_DATA_DIR": str(tmp_path), "PYTHONPATH": str(ROOT)}
    for key in ("OMNIFORGE_LLM_API_KEY", "OMNIFORGE_MEDIA_API_KEY",
                "OMNIFORGE_EMBEDDING_API_KEY", "OMNIFORGE_MEDIA_JOB_URL", "OPENAI_API_KEY"):
        env.pop(key, None)
    done = subprocess.run([sys.executable, "-m", "omniforge.cli", "verify", "--live"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
    assert done.returncode == 0
    assert "providers answered" in done.stdout
    assert "mock backend" in done.stdout


def test_ui_audit_cli(tmp_path):
    env = {**os.environ, "OMNIFORGE_DATA_DIR": str(tmp_path), "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-m", "omniforge.cli", "ui-audit"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
    assert done.returncode == 0
    assert "10/10" in done.stdout

"""The eval harness must pass its own gate."""
from __future__ import annotations

from conftest import make_settings

from omniforge.evals.fixtures import CASES
from omniforge.evals.runner import run_all, run_case


async def test_single_case_passes(tmp_path):
    case = next(c for c in CASES if c["name"] == "mobile_game")
    result = await run_case(case, make_settings(tmp_path))
    assert result["passed"], [c for c in result["checks"] if not c["passed"]]
    assert result["done"] >= 4
    assert result["tokens"] > 0
    assert all(c["passed"] for c in result["checks"])


async def test_suite_reports_failures_honestly(tmp_path):
    broken = {
        "name": "impossible",
        "goal": "Build a mobile app",
        "expect": {"status": "done", "departments": ["does_not_exist"], "min_done": 99},
    }
    summary = await run_all([broken], make_settings(tmp_path))
    assert summary["ok"] is False
    assert summary["failed"] == 1
    failed_names = {c["check"] for c in summary["results"][0]["checks"] if not c["passed"]}
    assert {"min_done", "department:does_not_exist"} <= failed_names


def test_fixtures_are_well_formed():
    assert len(CASES) >= 5
    for case in CASES:
        assert case["goal"] and case["name"]
        assert case["expect"]["status"] == "done"

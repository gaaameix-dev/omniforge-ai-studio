"""CLI smoke tests: every read-only command must run and exit cleanly."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OMNIFORGE_DATA_DIR": str(tmp_path), "PYTHONPATH": str(ROOT),
           "OMNIFORGE_MOCK_LLM": "true"}
    return subprocess.run(
        [sys.executable, "-m", "omniforge.cli", *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )


@pytest.mark.parametrize("args,expect", [
    (("info",), '"framework"'),
    (("skills",), "skills"),
    (("agents", "--department", "qa"), "QA Engineer"),
    (("departments",), "10 departments"),
    (("tools",), "http_get"),
    (("usage", "--pricing"), "gpt-4o-mini"),
    (("db",), '"active"'),
    (("alerts",), "alert rules"),
    (("automation", "workflows"), "workflow"),
    (("automation", "schedules"), "schedule"),
    (("plugins",), "plugins"),
    (("history",), ""),
    (("kb",), "documents"),
    (("metrics",), "runs"),
    (("vault",), "fernet"),
    (("integrations",), "SQLite"),
])
def test_read_only_commands(tmp_path, args, expect):
    result = run_cli(tmp_path, *args)
    assert result.returncode == 0, result.stderr[-400:]
    if expect:
        assert expect.lower() in result.stdout.lower()


def test_run_and_history_roundtrip(tmp_path):
    run = run_cli(tmp_path, "run", "Write a test plan for a web storefront", "--json")
    assert run.returncode == 0, run.stderr[-400:]
    payload = json.loads(run.stdout[run.stdout.index("{"):])
    assert payload["status"] == "done"

    history = run_cli(tmp_path, "history", "--limit", "3")
    assert payload["id"] in history.stdout

    replay = run_cli(tmp_path, "replay", payload["id"])
    assert replay.returncode == 0


def test_users_backup_and_restore(tmp_path):
    created = run_cli(tmp_path, "users", "--create", "cli@example.com",
                      "--password", "clilocalpass1", "--role", "owner")
    assert created.returncode == 0 and "created" in created.stdout

    listing = run_cli(tmp_path, "users")
    assert "cli@example.com" in listing.stdout

    backup = run_cli(tmp_path, "backup")
    assert backup.returncode == 0
    archive = json.loads(backup.stdout)["archive"]
    verified = run_cli(tmp_path, "backup", "--verify", archive)
    assert verified.returncode == 0 and json.loads(verified.stdout)["ok"] is True


def test_alert_rule_lifecycle(tmp_path):
    created = run_cli(tmp_path, "alerts", "--create", "spend,run_cost_usd,gt,1.0")
    assert created.returncode == 0
    rule_id = created.stdout.split("created ")[1].split(":")[0]
    listed = run_cli(tmp_path, "alerts")
    assert rule_id in listed.stdout
    assert run_cli(tmp_path, "alerts", "--delete", rule_id).returncode == 0


def test_doctor_and_loadtest(tmp_path):
    doctor = run_cli(tmp_path, "doctor")
    assert doctor.returncode == 0 and "healthy" in doctor.stdout

    load = run_cli(tmp_path, "loadtest", "--runs", "2", "--concurrency", "2")
    assert load.returncode == 0 and "latency" in load.stdout


def test_eval_single_case(tmp_path):
    result = run_cli(tmp_path, "eval", "--case", "mobile_game")
    assert result.returncode == 0 and "PASS" in result.stdout


def test_bad_arguments_exit_nonzero(tmp_path):
    assert run_cli(tmp_path, "run").returncode != 0
    assert run_cli(tmp_path, "alerts", "--create", "bad").returncode == 2

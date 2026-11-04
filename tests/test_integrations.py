"""Integration status must reflect reality: configured, verified, or plainly missing."""
from __future__ import annotations

import httpx
import pytest
from conftest import make_settings

from omniforge.core.integrations import Integrations


def _transport(status: int = 200, payload: dict | None = None):
    return httpx.MockTransport(lambda request: httpx.Response(status, json=payload or {}))


class FakeMCP:
    def __init__(self, tools, fail: bool = False):
        self._tools = tools
        self.fail = fail

    async def list_tools(self):
        if self.fail:
            raise RuntimeError("server died")
        return self._tools


def test_unconfigured_state_is_honest(tmp_path):
    snapshot = {i["key"]: i for i in Integrations(make_settings(tmp_path)).snapshot()}
    assert snapshot["llm"]["status"] == "not_configured"
    assert "OMNIFORGE_LLM_API_KEY" in snapshot["llm"]["requires"]
    assert snapshot["github"]["status"] == "not_configured"
    assert snapshot["sqlite"]["status"] == "connected"
    assert snapshot["sandbox"]["status"] == "not_configured"
    assert snapshot["mcp"]["status"] == "not_configured"


def test_connected_keys_only_lists_verified(tmp_path):
    integrations = Integrations(make_settings(tmp_path))
    assert integrations.connected_keys() == ["sqlite"]


async def test_verify_sqlite_reads_the_database(tmp_path):
    result = await Integrations(make_settings(tmp_path)).verify("sqlite")
    assert result["status"] == "connected" and "readable" in result["detail"]
    assert result["checked_at"]


async def test_verify_llm_without_a_key_stays_not_configured(tmp_path):
    result = await Integrations(make_settings(tmp_path)).verify("llm")
    assert result["status"] == "not_configured"
    assert "mock" in result["detail"]


async def test_verify_llm_with_key_and_reachable_endpoint(tmp_path):
    settings = make_settings(tmp_path, mock_llm=False, llm_api_key="sk-test")
    integrations = Integrations(settings, transport=_transport(payload={"data": [{"id": "gpt-4o-mini"}]}))
    result = await integrations.verify("llm")
    assert result["status"] == "connected"
    assert result["meta"]["models"] == ["gpt-4o-mini"]


async def test_verify_github_reports_the_login(tmp_path):
    settings = make_settings(tmp_path, github_token="tok")
    integrations = Integrations(settings, transport=_transport(payload={"login": "prince"}))
    result = await integrations.verify("github")
    assert result["status"] == "connected" and "prince" in result["detail"]


async def test_verify_github_rejects_a_bad_token(tmp_path):
    settings = make_settings(tmp_path, github_token="bad")
    result = await Integrations(settings, transport=_transport(status=401)).verify("github")
    assert result["status"] == "error" and "401" in result["detail"]


async def test_verify_mcp_counts_tools(tmp_path):
    client = FakeMCP([{"name": "word_count"}, {"name": "reverse"}])
    integrations = Integrations(make_settings(tmp_path), mcp_clients=[client])
    result = await integrations.verify("mcp")
    assert result["status"] == "connected" and "2 tool" in result["detail"]


async def test_verify_mcp_failure_is_reported(tmp_path):
    integrations = Integrations(make_settings(tmp_path), mcp_clients=[FakeMCP([], fail=True)])
    result = await integrations.verify("mcp")
    assert result["status"] == "error" and "server died" in result["detail"]


async def test_verify_http_probe(tmp_path):
    ok = await Integrations(make_settings(tmp_path), transport=_transport()).verify("http")
    assert ok["status"] == "connected"
    blocked = await Integrations(make_settings(tmp_path),
                                 transport=httpx.MockTransport(
                                     lambda r: (_ for _ in ()).throw(httpx.ConnectError("blocked"))
                                 )).verify("http")
    assert blocked["status"] == "error" and "blocked" in blocked["detail"]


async def test_verify_sandbox_when_enabled(tmp_path):
    settings = make_settings(tmp_path, allow_code_exec=True)
    result = await Integrations(settings).verify("sandbox")
    assert result["status"] == "connected" and "sandbox ok" in result["detail"]


async def test_unknown_integration_raises(tmp_path):
    with pytest.raises(KeyError):
        await Integrations(make_settings(tmp_path)).verify("carrier-pigeon")


async def test_verify_all_covers_every_key(tmp_path):
    integrations = Integrations(make_settings(tmp_path), transport=_transport())
    results = await integrations.verify_all()
    assert {r["key"] for r in results} == {"llm", "github", "mcp", "sqlite", "sandbox", "http"}
    assert all(r["checked_at"] for r in results)

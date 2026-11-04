"""GitHub client: real request shapes, honest failures, no fake success."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from omniforge.tools.base import IntegrationNotConfigured, ToolError
from omniforge.tools.github import GitHubClient, register_github_tools


def _transport(monkeypatch_log: list | None = None, *, status: int = 200, payload=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if monkeypatch_log is not None:
            monkeypatch_log.append({
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "body": json.loads(request.content) if request.content else None,
            })
        return httpx.Response(status, json=payload if payload is not None else {})
    return httpx.MockTransport(handler)


async def test_whoami_uses_the_token():
    calls: list = []
    client = GitHubClient("tok123", transport=_transport(calls, payload={"login": "octocat"}))
    assert (await client.whoami())["login"] == "octocat"
    assert calls[0]["auth"] == "Bearer tok123"


async def test_get_file_decodes_base64():
    content = base64.b64encode(b"hello from github").decode()
    client = GitHubClient("t", transport=_transport(payload={"content": content, "sha": "abc"}))
    data = await client.get_file("owner/repo", "README.md")
    assert data["decoded"] == "hello from github"


async def test_write_without_token_is_not_attempted():
    client = GitHubClient("", transport=_transport())
    with pytest.raises(IntegrationNotConfigured) as exc:
        await client.put_file("owner/repo", "a.txt", "x", "msg")
    assert "GITHUB_TOKEN" in str(exc.value)


async def test_put_file_sends_base64_and_branch():
    calls: list = []
    client = GitHubClient("tok", transport=_transport(calls, payload={"commit": {"sha": "deadbeef"}}))
    await client.put_file("owner/repo", "docs/a.md", "hello", "docs: add", branch="feat/x")
    body = calls[0]["body"]
    assert base64.b64decode(body["content"]).decode() == "hello"
    assert body["branch"] == "feat/x" and body["message"] == "docs: add"


async def test_create_branch_reads_then_writes():
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"object": {"sha": "base123"}})
        return httpx.Response(201, json={"ref": "refs/heads/new"})

    client = GitHubClient("tok", transport=httpx.MockTransport(handler))
    await client.create_branch("owner/repo", "new", "main")
    assert calls == ["GET", "POST"]


async def test_bad_token_raises_configuration_error():
    client = GitHubClient("bad", transport=_transport(status=401))
    with pytest.raises(IntegrationNotConfigured):
        await client.whoami()


async def test_server_error_raises_tool_error():
    client = GitHubClient("tok", transport=_transport(status=500))
    with pytest.raises(ToolError):
        await client.get_repo("owner/repo")


def test_registered_tools_declare_permissions():
    from omniforge.core.registry import ToolRegistry

    registry = ToolRegistry()
    register_github_tools(registry, None)
    names = set(registry.names())
    assert {"github_whoami", "github_put_file", "github_open_pr", "github_trigger_workflow"} <= names
    write_tool = registry.require("github_put_file")
    assert write_tool.destructive and write_tool.requires_approval
    assert write_tool.permissions == ("github.write",)
    assert registry.require("github_get_repo").permissions == ("github.read",)

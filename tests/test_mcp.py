"""MCP bridge tests against a local fake server."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omniforge.core.registry import ToolRegistry
from omniforge.tools.mcp import MCPClient, MCPError, register_mcp_tools

SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))


@pytest.fixture
async def client():
    c = MCPClient([sys.executable, SERVER])
    await c.start()
    try:
        yield c
    finally:
        await c.close()


async def test_initialize_and_list_tools(client):
    assert client.server_info["serverInfo"]["name"] == "fake"
    names = {t["name"] for t in client.tools}
    assert names == {"word_count", "reverse"}


async def test_call_tool_returns_text(client):
    assert await client.call_tool("word_count", {"text": "one two three"}) == "3"
    assert await client.call_tool("reverse", {"text": "abc"}) == "cba"


async def test_unknown_tool_raises(client):
    with pytest.raises(MCPError):
        await client.call_tool("nope", {})


async def test_tools_become_callable_by_agents(client):
    registry = ToolRegistry()
    registered = register_mcp_tools(registry, client)
    assert set(registered) == {"mcp_word_count", "mcp_reverse"}
    assert await registry.call("mcp_word_count", text="a b c d") == "4"
    assert "text" in registry.require("mcp_reverse").schema()["parameters"]


async def test_close_terminates_the_server(client):
    proc = client._proc
    await client.close()
    assert proc.returncode is not None

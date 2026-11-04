"""MCP over streamable HTTP, exercised with an injected transport."""
from __future__ import annotations

import json

import httpx
import pytest

from omniforge.core.registry import ToolRegistry
from omniforge.tools.mcp import MCPError, MCPHttpClient, register_mcp_tools

TOOLS = [{"name": "echo", "description": "Echo text back",
          "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]


def _handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload.get("method")
    if "id" not in payload:
        return httpx.Response(202)
    if method == "initialize":
        return httpx.Response(200, headers={"mcp-session-id": "sess-1"},
                              json={"jsonrpc": "2.0", "id": payload["id"],
                                    "result": {"serverInfo": {"name": "http-fake"}}})
    if method == "tools/list":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": TOOLS}})
    if method == "tools/call":
        args = payload["params"].get("arguments") or {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
                                         "result": {"content": [{"type": "text",
                                                                 "text": str(args.get("text", ""))}]}})
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
                                     "error": {"code": -32601, "message": "nope"}})


def _sse_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if "id" not in payload:
        return httpx.Response(202)
    body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": TOOLS}}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          text=f"event: message\ndata: {json.dumps(body)}\n\n")


async def test_http_initialize_and_call():
    client = MCPHttpClient("https://mcp.example/rpc", transport=httpx.MockTransport(_handler))
    await client.start()
    assert client.server_info["serverInfo"]["name"] == "http-fake"
    assert client.session_id == "sess-1"
    assert [t["name"] for t in client.tools] == ["echo"]
    assert await client.call_tool("echo", {"text": "hi"}) == "hi"


async def test_http_sse_reply_is_parsed():
    client = MCPHttpClient("https://mcp.example/rpc", transport=httpx.MockTransport(_sse_handler))
    tools = await client.list_tools()
    assert tools[0]["name"] == "echo"


async def test_http_error_status_raises():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    client = MCPHttpClient("https://mcp.example/rpc", transport=transport)
    with pytest.raises(MCPError):
        await client.start()


async def test_http_tools_register_into_the_registry():
    client = MCPHttpClient("https://mcp.example/rpc", transport=httpx.MockTransport(_handler))
    await client.start()
    registry = ToolRegistry()
    assert register_mcp_tools(registry, client) == ["mcp_echo"]
    assert await registry.call("mcp_echo", text="agent call") == "agent call"
    assert registry.require("mcp_echo").schema()["parameters"]["text"] == {"type": "string"}

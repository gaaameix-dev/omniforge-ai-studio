"""Model Context Protocol bridge (stdio transport).

Lets the framework borrow tools from any MCP server instead of reimplementing
them: start the server, list its tools, register each one in the tool registry
under a prefix. Agents then call them like native tools.

    client = MCPClient([sys.executable, "server.py"])
    await client.start()
    register_mcp_tools(orch.tools, client, prefix="mcp_")
    ...
    await client.close()
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.command = command
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task | None = None
        self.server_info: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- transport
    async def start(self) -> MCPClient:
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.env,
            cwd=self.cwd,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "omniforge", "version": "0.1.0"},
            },
        )
        self.server_info = result or {}
        await self._notify("notifications/initialized", {})
        self.tools = (await self._rpc("tools/list", {})).get("tools", [])
        return self

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue
            msg_id = message.get("id")
            if msg_id is None:
                continue  # server-initiated notification; nothing to resolve
            fut = self._pending.pop(msg_id, None)
            if fut and not fut.done():
                if "error" in message:
                    fut.set_exception(MCPError(str(message["error"])))
                else:
                    fut.set_result(message.get("result", {}))
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPError("MCP server closed the connection"))
        self._pending.clear()

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("MCP client is not started")
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return await asyncio.wait_for(fut, timeout=self.timeout)

    # -------------------------------------------------------------------- tools
    async def list_tools(self) -> list[dict[str, Any]]:
        self.tools = (await self._rpc("tools/list", {})).get("tools", [])
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPError(_flatten(result) or f"MCP tool '{name}' failed")
        return _flatten(result)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.stdin and self._proc.stdin.close()
            finally:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._proc.kill()

    async def __aenter__(self) -> MCPClient:
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()


class MCPHttpClient:
    """MCP over streamable HTTP (JSON-RPC POST, JSON or SSE replies)."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        transport: Any | None = None,
    ) -> None:
        self.url = url
        self.headers = {"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream", **(headers or {})}
        self.timeout = timeout
        self.transport = transport       # injectable for tests
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []
        self._next_id = 0

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        async with self._client() as client:
            return await client.post(self.url, json=payload, headers=headers)

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        resp = await self._post({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        if resp.status_code >= 400:
            raise MCPError(f"HTTP {resp.status_code} from MCP server: {resp.text[:200]}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        body = resp.text
        content_type = resp.headers.get("content-type", "")
        message: dict[str, Any] | None = None
        if "text/event-stream" in content_type:
            for line in body.splitlines():
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    try:
                        candidate = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if candidate.get("id") == msg_id:
                        message = candidate
                        break
        else:
            try:
                message = resp.json()
            except ValueError:
                message = None
        if message is None:
            raise MCPError(f"no JSON-RPC reply for {method}")
        if "error" in message:
            raise MCPError(str(message["error"]))
        return message.get("result", {})

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        try:
            await self._post({"jsonrpc": "2.0", "method": method, "params": params})
        except Exception:  # noqa: BLE001 - notifications are best effort
            pass

    async def start(self) -> MCPHttpClient:
        self.server_info = await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "omniforge", "version": "0.6.0"},
            },
        )
        await self._notify("notifications/initialized", {})
        self.tools = (await self._rpc("tools/list", {})).get("tools", [])
        return self

    async def list_tools(self) -> list[dict[str, Any]]:
        self.tools = (await self._rpc("tools/list", {})).get("tools", [])
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPError(_flatten(result) or f"MCP tool '{name}' failed")
        return _flatten(result)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> MCPHttpClient:
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()


def _flatten(result: dict[str, Any]) -> str:
    """MCP returns a content list; agents want text."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict):
            parts.append(json.dumps(block, default=str))
    return "\n".join(parts)


def register_mcp_tools(registry, client, *, prefix: str = "mcp_", replace: bool = False) -> list[str]:
    """Expose an MCP server's tools to every agent, named '<prefix><tool>'."""
    from .base import Tool

    registered: list[str] = []
    for spec in client.tools:
        name = f"{prefix}{spec['name']}"
        schema = spec.get("inputSchema") or {}

        def make_runner(tool_name: str):
            async def runner(**kwargs) -> Any:
                return await client.call_tool(tool_name, kwargs)
            return runner

        registry.register(
            name,
            Tool(
                name=name,
                description=spec.get("description", "") or f"MCP tool {spec['name']}",
                run=make_runner(spec["name"]),
                parameters=schema.get("properties", {}),
                requires_network=True,
            ),
            replace=replace,
        )
        registered.append(name)
    return registered

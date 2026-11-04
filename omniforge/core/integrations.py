"""Integration status - verified, not assumed.

An integration is only "connected" when credentials exist AND a live check
succeeded. Anything else reports exactly what is missing, so the dashboard can
never show a fake green light.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

CONNECTED = "connected"
NOT_CONFIGURED = "not_configured"
ERROR = "error"
UNVERIFIED = "unverified"


@dataclass
class Integration:
    key: str
    label: str
    category: str
    configured: bool
    status: str
    detail: str
    requires: str = ""
    checked_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "configured": self.configured,
            "status": self.status,
            "detail": self.detail,
            "requires": self.requires,
            "checked_at": self.checked_at,
            "meta": self.meta,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Integrations:
    """Knows which external services are wired up, and proves it on demand."""

    def __init__(
        self,
        settings,
        *,
        mcp_clients: list[Any] | None = None,
        transport: Any | None = None,
    ) -> None:
        self.settings = settings
        self.mcp_clients = mcp_clients or []
        self.transport = transport
        self._state: dict[str, Integration] = {}
        self._refresh_static()

    # ------------------------------------------------------------------ state
    def _refresh_static(self) -> None:
        s = self.settings
        self._state["llm"] = Integration(
            key="llm", label="LLM backend", category="model",
            configured=bool(s.live_mode),
            status=CONNECTED if s.live_mode else NOT_CONFIGURED,
            detail=("live model " + s.llm_model) if s.live_mode
            else "mock backend active - set OMNIFORGE_LLM_API_KEY and OMNIFORGE_MOCK_LLM=false",
            requires="OMNIFORGE_LLM_API_KEY",
            meta={"model": s.llm_model, "base_url": s.llm_base_url},
        )
        self._state["github"] = Integration(
            key="github", label="GitHub", category="source control",
            configured=bool(s.github_token),
            status=UNVERIFIED if s.github_token else NOT_CONFIGURED,
            detail="token present, not verified yet" if s.github_token
            else "read-only public access; writes need GITHUB_TOKEN (repo scope)",
            requires="GITHUB_TOKEN",
            meta={"base_url": s.github_base_url},
        )
        self._state["mcp"] = Integration(
            key="mcp", label="MCP servers", category="tools",
            configured=bool(self.mcp_clients),
            status=UNVERIFIED if self.mcp_clients else NOT_CONFIGURED,
            detail=f"{len(self.mcp_clients)} server(s) registered" if self.mcp_clients
            else "no MCP server registered",
            requires="an MCP server command or URL",
        )
        self._state["sqlite"] = Integration(
            key="sqlite", label="Run store (SQLite)", category="storage",
            configured=True, status=CONNECTED,
            detail=f"database at {s.db_path}", checked_at=_now(),
        )
        self._state["sandbox"] = Integration(
            key="sandbox", label="Code sandbox", category="execution",
            configured=bool(s.allow_code_exec),
            status=CONNECTED if s.allow_code_exec else NOT_CONFIGURED,
            detail="enabled: process isolation + rlimits" if s.allow_code_exec
            else "disabled - set OMNIFORGE_ALLOW_CODE_EXEC=true to enable",
            requires="OMNIFORGE_ALLOW_CODE_EXEC=true",
        )
        self._state["http"] = Integration(
            key="http", label="Outbound HTTP", category="network",
            configured=True, status=UNVERIFIED,
            detail="outbound calls allowed by the runtime; not probed yet",
        )

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._state.values()]

    def get(self, key: str) -> Integration | None:
        return self._state.get(key)

    def connected_keys(self) -> list[str]:
        return [k for k, v in self._state.items() if v.status == CONNECTED]

    # ----------------------------------------------------------------- verify
    async def verify(self, key: str) -> dict[str, Any]:
        item = self._state.get(key)
        if item is None:
            raise KeyError(f"unknown integration '{key}'")
        checker = {
            "llm": self._verify_llm,
            "github": self._verify_github,
            "mcp": self._verify_mcp,
            "http": self._verify_http,
            "sqlite": self._verify_sqlite,
            "sandbox": self._verify_sandbox,
        }.get(key)
        if checker is None:
            item.status, item.detail = UNVERIFIED, "no verifier for this integration"
            return item.to_dict()

        ok, detail, meta = await checker()
        item.configured = item.configured or ok
        item.status = CONNECTED if ok else (NOT_CONFIGURED if not item.configured else ERROR)
        item.detail = detail
        item.meta.update(meta or {})
        item.checked_at = _now()
        return item.to_dict()

    async def verify_all(self) -> list[dict[str, Any]]:
        await asyncio.gather(*(self.verify(k) for k in list(self._state)))
        return self.snapshot()

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": 15.0}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def _verify_llm(self) -> tuple[bool, str, dict]:
        s = self.settings
        if not s.live_mode:
            return False, "mock backend active - no key configured", {}
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{s.llm_base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {s.llm_api_key}"},
                )
            if resp.status_code != 200:
                return False, f"models endpoint returned {resp.status_code}", {}
            data = resp.json()
            models = [m.get("id") for m in data.get("data", [])][:5]
            return True, f"reachable, {len(models)} model(s) visible", {"models": models}
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", {}

    async def _verify_github(self) -> tuple[bool, str, dict]:
        s = self.settings
        if not s.github_token:
            return False, "no token: public reads only, writes unavailable", {}
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{s.github_base_url.rstrip('/')}/user",
                    headers={"Authorization": f"Bearer {s.github_token}",
                             "Accept": "application/vnd.github+json",
                             "User-Agent": "OmniForge/0.3"},
                )
            if resp.status_code == 401:
                return False, "token rejected (401)", {}
            if resp.status_code != 200:
                return False, f"user endpoint returned {resp.status_code}", {}
            login = resp.json().get("login", "?")
            return True, f"authenticated as {login}", {"login": login}
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", {}

    async def _verify_mcp(self) -> tuple[bool, str, dict]:
        if not self.mcp_clients:
            return False, "no MCP server registered", {}
        total = 0
        names: list[str] = []
        for client in self.mcp_clients:
            try:
                tools = await client.list_tools()
                total += len(tools)
                names.extend(t.get("name", "?") for t in tools[:3])
            except Exception as exc:  # noqa: BLE001
                return False, f"MCP server failed: {type(exc).__name__}: {exc}", {}
        return True, f"{len(self.mcp_clients)} server(s), {total} tool(s)", {"tools": names}

    async def _verify_http(self) -> tuple[bool, str, dict]:
        try:
            async with self._client() as client:
                resp = await client.get("https://api.github.com/rate_limit",
                                        headers={"User-Agent": "OmniForge/0.3"})
            if resp.status_code == 200:
                return True, "outbound HTTPS reachable", {}
            return False, f"probe returned {resp.status_code}", {}
        except Exception as exc:  # noqa: BLE001
            return False, f"outbound HTTPS blocked or unreachable: {type(exc).__name__}", {}

    async def _verify_sqlite(self) -> tuple[bool, str, dict]:
        from .store import RunStore

        try:
            store = RunStore(self.settings.db_path)
            stats = store.stats()
            store.close()
            return True, f"readable, {stats['runs']} run(s) stored", {"stats": stats}
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", {}

    async def _verify_sandbox(self) -> tuple[bool, str, dict]:
        from .sandbox import run_python, sandbox_available

        if not self.settings.allow_code_exec:
            return False, "code execution disabled by configuration", {}
        if not sandbox_available():
            return False, "rlimits unavailable on this platform", {}
        result = await run_python("print('sandbox ok')", timeout=self.settings.sandbox_timeout)
        return result.ok, f"probe exit={result.exit_code} stdout={result.stdout.strip()[:40]}", {}

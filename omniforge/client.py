"""Small async client for the OmniForge HTTP API.

    async with OmniForgeClient("http://localhost:8000", token="ofk_...") as client:
        run = await client.run("Build a mobile puzzle game")
        print(run["status"], run["summary"])

It is intentionally thin: one method per endpoint, no hidden retries, and errors
raised as `OmniForgeError` carrying the server's message.
"""
from __future__ import annotations

from typing import Any

import httpx


class OmniForgeError(RuntimeError):
    def __init__(self, message: str, status: int = 0, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class OmniForgeClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        token: str = "",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    # ------------------------------------------------------------- plumbing
    async def __aenter__(self) -> OmniForgeClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = await self._client.request(method, path, headers=self._headers(), **kwargs)
        if resp.status_code >= 400:
            detail: Any
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise OmniForgeError(f"{method} {path} -> {resp.status_code}: {detail}",
                                 status=resp.status_code, payload=detail)
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.content

    # ----------------------------------------------------------------- meta
    async def health(self) -> dict:
        return await self._request("GET", "/api/health")

    async def framework(self) -> dict:
        return await self._request("GET", "/api/framework")

    async def stats(self) -> dict:
        return await self._request("GET", "/api/stats")

    async def monitor(self) -> dict:
        return await self._request("GET", "/api/monitor")

    async def agents(self, department: str | None = None, q: str | None = None) -> list[dict]:
        params = {k: v for k, v in {"department": department, "q": q}.items() if v}
        return await self._request("GET", "/api/agents", params=params)

    async def skills(self, department: str | None = None) -> list[dict]:
        params = {"department": department} if department else {}
        return await self._request("GET", "/api/skills", params=params)

    async def tools(self) -> list[dict]:
        return await self._request("GET", "/api/tools")

    async def departments(self) -> list[dict]:
        return await self._request("GET", "/api/departments")

    # ----------------------------------------------------------------- runs
    async def create_run(self, goal: str, **options: Any) -> dict:
        return await self._request("POST", "/api/runs", json={"goal": goal, **options})

    async def get_run(self, run_id: str, events: bool = False) -> dict:
        return await self._request("GET", f"/api/runs/{run_id}",
                                   params={"events": str(events).lower()})

    async def list_runs(self, limit: int = 20, status: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return await self._request("GET", "/api/runs", params=params)

    async def wait(self, run_id: str, *, timeout: float = 300.0,
                   interval: float = 0.5) -> dict:
        import asyncio
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = await self.get_run(run_id)
            if run.get("status") in {"done", "failed", "cancelled"}:
                return run
            await asyncio.sleep(interval)
        raise TimeoutError(f"run {run_id} did not finish within {timeout}s")

    async def run(self, goal: str, *, timeout: float = 300.0, **options: Any) -> dict:
        created = await self.create_run(goal, **options)
        return await self.wait(created["id"], timeout=timeout)

    async def cancel(self, run_id: str) -> dict:
        return await self._request("POST", f"/api/runs/{run_id}/cancel")

    async def replay(self, run_id: str) -> dict:
        return await self._request("POST", f"/api/runs/{run_id}/replay")

    async def resume(self, run_id: str) -> dict:
        return await self._request("POST", f"/api/runs/{run_id}/resume")

    async def approve(self, run_id: str, task_id: str, approved: bool = True,
                      note: str = "") -> dict:
        return await self._request("POST", f"/api/runs/{run_id}/approvals/{task_id}",
                                   json={"approved": approved, "note": note})

    async def artifacts(self, run_id: str) -> list[dict]:
        return await self._request("GET", f"/api/runs/{run_id}/artifacts")

    async def download_artifact(self, run_id: str, name: str) -> bytes:
        return await self._request("GET", f"/api/runs/{run_id}/artifacts/{name}")

    # ----------------------------------------------------------- automation
    async def workflows(self) -> list[dict]:
        return await self._request("GET", "/api/workflows")

    async def workflow_from_run(self, run_id: str, name: str) -> dict:
        return await self._request("POST", f"/api/workflows/from-run/{run_id}",
                                   json={"name": name})

    async def run_workflow(self, workflow_id: str, params: dict | None = None) -> dict:
        return await self._request("POST", f"/api/workflows/{workflow_id}/run",
                                   json={"params": params or {}})

    async def schedules(self) -> list[dict]:
        return await self._request("GET", "/api/schedules")

    async def create_schedule(self, name: str, *, goal: str = "", workflow_id: str | None = None,
                              every_seconds: int = 3600) -> dict:
        return await self._request("POST", "/api/schedules", json={
            "name": name, "goal": goal, "workflow_id": workflow_id,
            "every_seconds": every_seconds,
        })

    # ------------------------------------------------------------- platform
    async def usage(self) -> dict:
        return await self._request("GET", "/api/billing/usage")

    async def pricing(self) -> dict:
        return await self._request("GET", "/api/billing/pricing")

    async def plugins(self) -> list[dict]:
        return await self._request("GET", "/api/plugins")

    async def install_plugin(self, payload: bytes, filename: str = "plugin.zip") -> dict:
        files = {"file": (filename, payload, "application/zip")}
        return await self._request("POST", "/api/plugins/install", files=files)

    async def integrations(self) -> dict:
        return await self._request("GET", "/api/integrations")

    async def verify_integration(self, key: str) -> dict:
        return await self._request("POST", f"/api/integrations/{key}/verify")

    async def control(self, action: str) -> dict:
        if action not in {"pause", "unpause", "stop", "clear"}:
            raise ValueError("action must be pause, unpause, stop or clear")
        return await self._request("POST", f"/api/control/{action}")

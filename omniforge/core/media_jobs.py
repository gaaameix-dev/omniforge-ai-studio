"""Job-based media generation: video, music and 3D models.

There is no single standard for these providers, so this speaks one small contract
that a thin shim can satisfy for most of them:

    POST {base}/jobs          {"kind": "video|music|model3d", "prompt": "...", "options": {...}}
      -> {"id": "...", "status": "pending"}
    GET  {base}/jobs/{id}     -> {"status": "pending|done|failed", "asset_url": "https://...",
                                 "b64": "...", "error": "..."}   (asset_url or b64 when done)

Images and speech use the OpenAI-compatible endpoints in `core/media.py` because
those really are standard. Everything here is provider-agnostic on purpose: point
`OMNIFORGE_MEDIA_JOB_URL` at your shim and the rest works unchanged.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

KINDS = ("video", "music", "model3d")


class JobMediaError(RuntimeError):
    pass


def validate_gltf(payload: bytes) -> dict[str, Any]:
    """Cheap sanity check for a glTF/GLB file before we store it."""
    if payload[:4] == b"glTF":
        return {"valid": True, "format": "glb", "bytes": len(payload)}
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"valid": False, "reason": "not a GLB header and not JSON"}
    asset = document.get("asset") if isinstance(document, dict) else None
    if not isinstance(asset, dict) or "version" not in asset:
        return {"valid": False, "reason": "glTF JSON has no asset.version"}
    return {"valid": True, "format": "gltf", "version": asset.get("version"),
            "meshes": len(document.get("meshes") or [])}


class JobMediaProvider:
    def __init__(self, base_url: str, api_key: str = "", *, timeout: float = 30.0,
                 poll_interval: float = 2.0, max_wait: float = 300.0,
                 transport: Any | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "OmniForge/1.2", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def submit(self, kind: str, prompt: str, options: dict[str, Any] | None = None) -> str:
        if kind not in KINDS:
            raise JobMediaError(f"kind must be one of {list(KINDS)}")
        if not self.enabled:
            raise JobMediaError("no job media provider configured (set OMNIFORGE_MEDIA_JOB_URL)")
        if not (prompt or "").strip():
            raise JobMediaError("a prompt is required")
        payload = {"kind": kind, "prompt": prompt[:4000], "options": options or {}}
        async with self._client() as client:
            response = await client.post(f"{self.base_url}/jobs", json=payload,
                                         headers=self._headers())
        if response.status_code >= 400:
            raise JobMediaError(f"submit failed ({response.status_code}): {response.text[:200]}")
        data = response.json()
        job_id = str(data.get("id") or data.get("job_id") or "")
        if not job_id:
            raise JobMediaError("provider did not return a job id")
        return job_id

    async def poll(self, job_id: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/jobs/{job_id}", headers=self._headers())
        if response.status_code >= 400:
            raise JobMediaError(f"poll failed ({response.status_code})")
        return response.json()

    async def wait(self, job_id: str) -> dict[str, Any]:
        waited = 0.0
        while waited <= self.max_wait:
            state = await self.poll(job_id)
            status = str(state.get("status") or "").lower()
            if status in {"done", "succeeded", "complete"}:
                return await self._fetch_asset(state)
            if status in {"failed", "error", "cancelled"}:
                raise JobMediaError(f"job failed: {state.get('error') or 'no reason given'}")
            await asyncio.sleep(self.poll_interval)
            waited += self.poll_interval
        raise JobMediaError(f"job {job_id} did not finish within {self.max_wait:.0f}s")

    async def _fetch_asset(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("b64"):
            payload = base64.b64decode(state["b64"])
        elif state.get("asset_url"):
            async with self._client() as client:
                asset = await client.get(state["asset_url"])
            if asset.status_code >= 400:
                raise JobMediaError(f"could not download the asset ({asset.status_code})")
            payload = asset.content
        else:
            raise JobMediaError("job finished without an asset")
        return {"bytes": payload, "size": len(payload), "job": state}

    async def generate(self, kind: str, prompt: str,
                       options: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = await self.submit(kind, prompt, options)
        result = await self.wait(job_id)
        result["job_id"] = job_id
        result["kind"] = kind
        if kind == "model3d":
            result["validation"] = validate_gltf(result["bytes"])
            if not result["validation"]["valid"]:
                raise JobMediaError(f"provider returned an invalid 3D asset: "
                                    f"{result['validation'].get('reason')}")
        return result

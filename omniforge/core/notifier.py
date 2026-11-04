"""Outbound notifications (webhook) with retries and an audit trail.

Fire-and-forget would lose failures, so every attempt is recorded in
`notifications` and the caller learns whether delivery worked.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

log = logging.getLogger("omniforge.notifier")


class Notifier:
    def __init__(self, url: str = "", *, transport: Any | None = None,
                 max_attempts: int = 3, base_delay: float = 0.3, timeout: float = 10.0) -> None:
        self.url = (url or "").strip()
        self.transport = transport
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def status(self) -> dict:
        return {"enabled": self.enabled, "target": self.url or None,
                "attempts": self.max_attempts}

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "attempts": 0, "error": "no webhook configured"}
        body = json.dumps(payload, default=str)
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                kwargs: dict[str, Any] = {"timeout": self.timeout}
                if self.transport is not None:
                    kwargs["transport"] = self.transport
                async with httpx.AsyncClient(**kwargs) as client:
                    resp = await client.post(
                        self.url, content=body,
                        headers={"Content-Type": "application/json",
                                 "User-Agent": "OmniForge/0.5",
                                 "X-OmniForge-Event": str(payload.get("event", "unknown"))},
                    )
                if 200 <= resp.status_code < 300:
                    return {"ok": True, "attempts": attempt, "status": resp.status_code}
                last_error = f"HTTP {resp.status_code}"
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.max_attempts:
                await asyncio.sleep(self.base_delay * (2 ** (attempt - 1)))
        log.warning("webhook delivery failed after %s attempts: %s", self.max_attempts, last_error)
        return {"ok": False, "attempts": self.max_attempts, "error": last_error}

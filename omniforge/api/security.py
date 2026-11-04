"""Optional bearer-token guard. Off by default; enabled by OMNIFORGE_API_KEY."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

PUBLIC_PATHS = {"/", "/api/health", "/metrics", "/favicon.ico", "/i18n.js"}


def _matches(expected: str, supplied: str) -> bool:
    return hmac.compare_digest(expected.encode(), supplied.encode())


def check_request(settings, request: Request) -> None:
    if not settings.api_key:
        return
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        token = request.query_params.get("token", "")
    if not token or not _matches(settings.api_key, token):
        raise HTTPException(status_code=401, detail="invalid or missing API token",
                            headers={"WWW-Authenticate": "Bearer"})


def check_websocket(settings, token: str | None) -> bool:
    if not settings.api_key:
        return True
    return bool(token) and _matches(settings.api_key, token or "")

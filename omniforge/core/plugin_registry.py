"""A registry index for plugin packages.

The index is a JSON document served over HTTPS:

    {"plugins": [
      {"name": "acme-pack", "version": "1.0.0",
       "url": "https://example.com/acme-pack-1.0.0.zip",
       "sha256": "<hex>", "description": "...", "permissions": ["net.read"]}
    ]}

Install fetches the index, finds the entry, downloads the package, checks the
SHA-256 from the index, then hands it to the normal installer - so signing,
permission review and compatibility checks all still apply.
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx

from .plugins import PluginError, install


def _client(transport=None) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {"timeout": 30.0, "follow_redirects": True}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


def parse_index(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        entries = payload.get("plugins")
    else:
        entries = payload
    if not isinstance(entries, list):
        raise PluginError("registry index must contain a 'plugins' list")
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("url"):
            continue
        if not str(entry["url"]).lower().startswith("https://"):
            continue
        cleaned.append({
            "name": str(entry["name"]),
            "version": str(entry.get("version") or "0.0.0"),
            "url": str(entry["url"]),
            "sha256": str(entry.get("sha256") or ""),
            "description": str(entry.get("description") or ""),
            "permissions": list(entry.get("permissions") or []),
        })
    return cleaned


async def fetch_index(url: str, *, transport=None) -> list[dict]:
    if not url.lower().startswith("https://"):
        raise PluginError("the registry index must be served over https")
    try:
        async with _client(transport) as client:
            response = await client.get(url, headers={"User-Agent": "OmniForge/0.7"})
            if response.status_code != 200:
                raise PluginError(f"registry index returned {response.status_code}")
            payload = response.json()
    except httpx.HTTPError as exc:
        raise PluginError(f"could not reach the registry: {type(exc).__name__}") from None
    except ValueError as exc:
        raise PluginError(f"registry index is not valid JSON: {exc}") from None
    return parse_index(payload)


def find(entries: list[dict], name: str, version: str | None = None) -> dict:
    matches = [e for e in entries if e["name"] == name]
    if version:
        matches = [e for e in matches if e["version"] == version]
    if not matches:
        raise PluginError(f"'{name}' is not in this registry" + (f" at {version}" if version else ""))
    return sorted(matches, key=lambda e: e["version"], reverse=True)[0]


async def install_from_registry(
    store, data_dir, index_url: str, name: str, settings, owner_id: str | None, ts: str,
    *, version: str | None = None, transport=None,
) -> dict:
    entries = await fetch_index(index_url, transport=transport)
    entry = find(entries, name, version)
    try:
        async with _client(transport) as client:
            response = await client.get(entry["url"], headers={"User-Agent": "OmniForge/0.7"})
            if response.status_code != 200:
                raise PluginError(f"package download failed ({response.status_code})")
            payload = response.content
    except httpx.HTTPError as exc:
        raise PluginError(f"could not download the package: {type(exc).__name__}") from None

    if len(payload) > 5 * 1024 * 1024:
        raise PluginError("package is larger than the 5 MB limit")
    digest = hashlib.sha256(payload).hexdigest()
    if entry["sha256"] and digest != entry["sha256"]:
        raise PluginError(
            f"checksum mismatch for {name}: index says {entry['sha256'][:12]}…, got {digest[:12]}…"
        )
    record = install(store, data_dir, payload, settings, owner_id, ts)
    record.update({"source_url": entry["url"], "registry": index_url,
                   "checksum_verified": bool(entry["sha256"])})
    return record

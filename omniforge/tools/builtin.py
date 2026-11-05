"""Default tool set: read-only web/GitHub access, artifact writes, local helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..core.guard import sanitize, wrap_untrusted
from .base import Tool

MAX_BODY = 20_000


def host_allowed(url: str, allowlist: tuple[str, ...] | list[str]) -> bool:
    """Empty allowlist = any host. Otherwise the host must match or be a subdomain."""
    if not allowlist:
        return True
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == entry or host.endswith("." + entry) for entry in allowlist)


async def http_get(url: str, timeout: float = 20.0, transport: Any = None,
                   allowlist: tuple[str, ...] | list[str] = ()) -> str:
    """Fetch a URL. Content is treated as untrusted: scanned and wrapped."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are allowed")
    if not host_allowed(url, allowlist):
        raise PermissionError(f"host not in OMNIFORGE_HTTP_ALLOWLIST: {url[:80]}")
    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    if transport is not None:
        kwargs["transport"] = transport
    async with httpx.AsyncClient(**kwargs) as client:
        resp = await client.get(url, headers={"User-Agent": "OmniForge/0.3"})
        resp.raise_for_status()
        return wrap_untrusted(resp.text[:MAX_BODY], source=f"http_get:{url[:60]}")


async def github_get(path: str, token: str | None = None) -> Any:
    token = token or os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "OmniForge/0.3"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def write_artifact(run_id: str, name: str, content: str, data_dir: str = "./data") -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    folder = Path(data_dir) / "artifacts" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe
    path.write_text(content, encoding="utf-8")
    return str(path)


async def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def json_query(payload: str, path: str) -> Any:
    """Read a dotted path out of a JSON string, e.g. 'items.0.name'."""
    data = json.loads(payload)
    for part in path.split("."):
        data = data[int(part)] if part.isdigit() else data[part]
    return data


async def text_stats(text: str) -> str:
    report = sanitize(text, max_chars=200_000)
    words = len((report.sanitized or "").split())
    return json.dumps({
        "characters": len(text),
        "words": words,
        "lines": len(text.splitlines()),
        "injection_findings": len(report.findings),
    })


async def image_generate(prompt: str, run_id: str, name: str = "image.png",
                         size: str = "1024x1024", data_dir: str = "./data",
                         settings=None) -> str:
    """Generate an image and write it into the run's artifacts."""
    from ..core.media import ImageProvider

    provider = ImageProvider(
        getattr(settings, "media_api_key", ""),
        model=getattr(settings, "media_model", "gpt-image-1"),
        base_url=getattr(settings, "media_base_url", "https://api.openai.com/v1"),
    )
    payload = await provider.generate(prompt, size=size)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    if not safe.lower().endswith(".png"):
        safe += ".png"
    folder = Path(data_dir) / "artifacts" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe
    path.write_bytes(payload)
    return f"{path} ({len(payload)} bytes)"


async def engine_scaffold(engine: str, run_id: str, name: str = "MyGame", genre: str = "",
                          notes: str = "", data_dir: str = "./data") -> str:
    """Write an engine project skeleton into the run's artifacts."""
    from ..core.engines import scaffold, summarise

    files = scaffold(engine, name=name, genre=genre, notes=notes)
    folder = Path(data_dir) / "artifacts" / run_id / f"{engine}_project"
    for relative, content in files.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    summary = summarise(files)
    return f"{folder} -> {summary['count']} files, {summary['bytes']} bytes"


def build_default_tools(data_dir: str = "./data", settings=None):
    """Register the built-in tools. Code execution is opt-in via settings."""
    from ..core.registry import ToolRegistry

    registry = ToolRegistry()
    allowlist = tuple(getattr(settings, "http_allowlist", ()) or ())
    registry.register("http_get", Tool(
        name="http_get",
        description="Fetch a URL over HTTPS and return sanitised text (untrusted data block).",
        run=lambda url, timeout=20.0: http_get(url, timeout, allowlist=allowlist),
        parameters={"url": "string", "timeout": "number (optional)"},
        permissions=("net.read",),
        category="web",
        requires_network=True,
    ))
    registry.register("github_get", Tool(
        name="github_get",
        description="Read a GitHub REST path or URL (token from GITHUB_TOKEN if set).",
        run=github_get,
        parameters={"path": "string, e.g. /repos/owner/repo", "token": "string (optional)"},
        permissions=("github.read",),
        category="github",
        requires_network=True,
        provider="github",
    ))
    registry.register("write_artifact", Tool(
        name="write_artifact",
        description="Write a deliverable file into the run's artifact folder.",
        run=lambda run_id, name, content: write_artifact(run_id, name, content, data_dir),
        parameters={"run_id": "string", "name": "string", "content": "string"},
        returns="path",
        permissions=("fs.write",),
        category="files",
    ))
    registry.register("now", Tool(
        name="now",
        description="Current UTC timestamp.",
        run=now,
        returns="ISO-8601 string",
    ))
    registry.register("json_query", Tool(
        name="json_query",
        description="Extract a dotted path from a JSON string.",
        run=json_query,
        parameters={"payload": "string (JSON)", "path": "string"},
        category="data",
    ))
    registry.register("text_stats", Tool(
        name="text_stats",
        description="Count characters, words and lines; report injection findings.",
        run=text_stats,
        parameters={"text": "string"},
        returns="JSON string",
        category="data",
    ))

    if settings is not None and getattr(settings, "media_api_key", ""):
        registry.register("image_generate", Tool(
            name="image_generate",
            description="Generate a raster image from a prompt and save it as an artifact.",
            run=lambda prompt, run_id, name="image.png", size="1024x1024": image_generate(
                prompt, run_id, name, size, data_dir, settings),
            parameters={"prompt": "string", "run_id": "string", "name": "string",
                        "size": "1024x1024 | 512x512"},
            returns="path",
            permissions=("media.generate",),
            category="media",
            requires_network=True,
            provider="media",
        ))
    registry.register("engine_scaffold", Tool(
        name="engine_scaffold",
        description="Write a Unity/Unreal/Godot project skeleton into the run's artifacts.",
        run=lambda engine, run_id, name="MyGame", genre="", notes="": engine_scaffold(
            engine, run_id, name, genre, notes, data_dir),
        parameters={"engine": "unity | unreal | godot", "run_id": "string", "name": "string",
                    "genre": "string", "notes": "string"},
        returns="path",
        permissions=("fs.write",),
        category="game",
    ))

    # GitHub write access and code execution are wired only when configured.
    from .github import register_github_tools

    register_github_tools(registry, settings)
    if settings is not None and getattr(settings, "allow_code_exec", False):
        from .code import register_code_tools

        register_code_tools(registry, settings)
    return registry

"""Declarative plugin packages.

A plugin is data, not code: a manifest that adds prompt-driven skills and agent
templates. Nothing from a plugin is executed, so installing one cannot run
arbitrary code - the worst it can do is declare a tool permission, which is
checked against the operator's allowlist before activation.

Packages can be signed with HMAC-SHA256 using the deployment's secret key; a valid
signature marks the plugin `verified`, otherwise it is `unsigned` and only installs
when OMNIFORGE_PLUGIN_ALLOW_UNSIGNED is on.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from .. import __version__


class PluginError(ValueError):
    pass


def canonical(manifest: dict[str, Any]) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(manifest: dict[str, Any], secret: str) -> str:
    return hmac.new(secret.encode(), canonical(manifest), hashlib.sha256).hexdigest()


def verify_signature(manifest: dict[str, Any], secret: str) -> bool:
    signature = manifest.get("signature") or ""
    if not secret or not signature:
        return False
    return hmac.compare_digest(sign(manifest, secret), str(signature))


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(text).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def compatible(requires: dict[str, Any] | None, current: str = __version__) -> bool:
    if not requires:
        return True
    spec = str(requires.get("omniforge", "")).strip()
    if not spec:
        return True
    for op in (">=", "<=", "==", ">", "<"):
        if spec.startswith(op):
            target = _version_tuple(spec[len(op):])
            here = _version_tuple(current)
            return {
                ">=": here >= target, "<=": here <= target, "==": here == target,
                ">": here > target, "<": here < target,
            }[op]
    return True


def validate_manifest(manifest: dict[str, Any], settings) -> list[str]:
    issues: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    for field in ("name", "version"):
        if not str(manifest.get(field, "")).strip():
            issues.append(f"manifest is missing '{field}'")
    if not compatible(manifest.get("requires"), __version__):
        issues.append(f"plugin requires omniforge {manifest['requires'].get('omniforge')}; "
                      f"this build is {__version__}")

    allowed = tuple(getattr(settings, "plugin_allowed_permissions", ()) or ())
    for permission in manifest.get("permissions", []) or []:
        if permission not in allowed:
            issues.append(f"permission '{permission}' is not in the operator allowlist {list(allowed)}")

    skills = manifest.get("skills", []) or []
    agents = manifest.get("agents", []) or []
    if not skills and not agents:
        issues.append("plugin declares no skills or agents")
    for skill in skills:
        if not str(skill.get("name", "")).strip():
            issues.append("a skill is missing 'name'")
        if not str(skill.get("department", "")).strip():
            issues.append(f"skill '{skill.get('name')}' is missing 'department'")
    known = {s.get("name") for s in skills}
    for agent in agents:
        if not str(agent.get("name", "")).strip():
            issues.append("an agent is missing 'name'")
        for skill_name in agent.get("skills", []) or []:
            if skill_name not in known:
                issues.append(f"agent '{agent.get('name')}' uses unknown skill '{skill_name}'")
        for permission in agent.get("permissions", []) or []:
            if permission not in allowed:
                issues.append(f"agent permission '{permission}' is not allowed")
    return issues


def install(store, data_dir: Path | str, payload: bytes, settings, owner_id: str | None,
            ts: str) -> dict:
    if not getattr(settings, "plugins_enabled", True):
        raise PluginError("plugins are disabled (OMNIFORGE_PLUGINS_ENABLED=false)")
    data_dir = Path(data_dir)
    tmp = data_dir / "plugins" / f".incoming-{os.urandom(4).hex()}.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(payload)
    try:
        with zipfile.ZipFile(tmp) as bundle:
            if "manifest.json" not in bundle.namelist():
                raise PluginError("package has no manifest.json")
            raw = bundle.read("manifest.json")
            try:
                manifest = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PluginError(f"manifest.json is not valid JSON: {exc}") from None
            issues = validate_manifest(manifest, settings)
            if issues:
                raise PluginError("; ".join(issues))

            trust = "verified" if verify_signature(manifest, settings.secret_key or "") else "unsigned"
            if trust == "unsigned" and getattr(settings, "plugin_require_signature", False):
                raise PluginError("this deployment accepts signed plugins only")
            if trust == "unsigned" and not getattr(settings, "plugin_allow_unsigned", True):
                raise PluginError("unsigned plugins are not allowed on this deployment")

            folder = data_dir / "plugins" / f"{manifest['name']}-{manifest['version']}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            for name in bundle.namelist():
                if name.endswith("/") or name == "manifest.json":
                    continue
                target = folder / Path(name).name
                target.write_bytes(bundle.read(name))

            record = {
                "id": f"plg_{os.urandom(5).hex()}",
                "name": str(manifest["name"]),
                "version": str(manifest["version"]),
                "manifest": json.dumps(manifest),
                "trust": trust,
                "enabled": 0,
                "installed_at": ts,
                "path": str(folder),
                "owner_id": owner_id,
                "checksum": hashlib.sha256(raw).hexdigest(),
            }
            store.add_plugin(record)
            return {k: v for k, v in record.items() if k != "manifest"} | {
                "skills": [s["name"] for s in manifest.get("skills", [])],
                "agents": [a["name"] for a in manifest.get("agents", [])],
                "permissions": manifest.get("permissions", []),
                "trust": trust,
            }
    finally:
        tmp.unlink(missing_ok=True)


def activate(orchestrator, store, plugin_id: str) -> dict:
    """Register the plugin's skills and agents into the live registries."""
    from ..skills.base import Skill
    from .agent import AgentTemplate

    record = store.get_plugin(plugin_id)
    if record is None:
        raise PluginError("plugin not found")
    manifest = json.loads(record["manifest"])
    added_skills: list[str] = []
    added_agents: list[str] = []

    for spec in manifest.get("skills", []):
        name = str(spec["name"])
        if name in orchestrator.skills:
            continue
        orchestrator.skills.register(name, Skill(
            name=name,
            department=str(spec.get("department")),
            description=str(spec.get("description", "")),
            tags={"plugin", record["name"]},
            min_chars=int(spec.get("min_chars", 40)),
            required_any=tuple(spec.get("required_any", ()) or ()),
        ))
        added_skills.append(name)

    for spec in manifest.get("agents", []):
        name = str(spec["name"])
        agent_id = f"{spec.get('department')}__{name.lower().replace(' ', '_')}"
        if agent_id in orchestrator.agents:
            continue
        permissions = tuple(spec.get("permissions") or ("net.read",))
        orchestrator.agents.register(agent_id, AgentTemplate(
            id=agent_id,
            name=name,
            department=str(spec.get("department")),
            description=str(spec.get("description", "")),
            skills=list(spec.get("skills") or []),
            primary_skills=list(spec.get("primary_skills") or spec.get("skills") or []),
            tools=["now", "write_artifact", "text_stats"],
            system_prompt=str(spec.get("system_prompt") or f"You are {name}, a plugin-provided specialist."),
            permissions=permissions,
        ))
        added_agents.append(agent_id)

    store.set_plugin_enabled(plugin_id, True)
    return {"plugin_id": plugin_id, "enabled": True, "skills": added_skills,
            "agents": added_agents}


def deactivate(orchestrator, store, plugin_id: str) -> dict:
    record = store.get_plugin(plugin_id)
    if record is None:
        raise PluginError("plugin not found")
    manifest = json.loads(record["manifest"])
    removed_skills, removed_agents = [], []
    for spec in manifest.get("skills", []):
        if orchestrator.skills.remove(str(spec["name"])):
            removed_skills.append(str(spec["name"]))
    for spec in manifest.get("agents", []):
        agent_id = f"{spec.get('department')}__{str(spec.get('name')).lower().replace(' ', '_')}"
        if orchestrator.agents.remove(agent_id):
            removed_agents.append(agent_id)
    store.set_plugin_enabled(plugin_id, False)
    return {"plugin_id": plugin_id, "enabled": False, "skills": removed_skills,
            "agents": removed_agents}


def uninstall(store, plugin_id: str) -> dict:
    record = store.get_plugin(plugin_id)
    if record is None:
        raise PluginError("plugin not found")
    deleted = store.delete_plugin(plugin_id)
    path = Path(record["path"])
    removed_files = 0
    if path.is_dir() and path.parent.name == "plugins":
        for child in path.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
                removed_files += 1
        path.rmdir()
    return {"deleted": deleted, "files_removed": removed_files}


async def install_from_url(store, data_dir, url: str, settings, owner_id: str | None, ts: str,
                           *, transport=None, max_bytes: int = 5 * 1024 * 1024) -> dict:
    """Fetch a package over HTTPS and install it. Signing rules apply as usual."""
    import httpx

    if not url.lower().startswith("https://"):
        raise PluginError("plugins can only be fetched over https")
    kwargs: dict = {"timeout": 30.0, "follow_redirects": True}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(url, headers={"User-Agent": "OmniForge/0.6"})
            if resp.status_code != 200:
                raise PluginError(f"could not fetch the package ({resp.status_code})")
            payload = resp.content
    except httpx.HTTPError as exc:
        raise PluginError(f"could not reach {url}: {type(exc).__name__}") from None
    if len(payload) > max_bytes:
        raise PluginError("package is larger than the 5 MB limit")
    record = install(store, data_dir, payload, settings, owner_id, ts)
    record["source_url"] = url
    return record


def _manifest_path(store, data_dir, plugin: dict) -> Path:
    """Where the manifest lives: the recorded path, else name-version."""
    recorded = plugin.get("path")
    if recorded:
        candidate = Path(recorded)
        if candidate.is_dir():
            candidate = candidate / "manifest.json"
        if candidate.exists():
            return candidate
    return Path(data_dir) / "plugins" / f"{plugin['name']}-{plugin['version']}" / "manifest.json"


def build_index(store, data_dir, settings, *, base_url: str = "") -> dict:
    """A registry index describing the packages this deployment can serve."""
    import hashlib
    import io
    import zipfile

    entries = []
    for plugin in store.list_plugins():
        manifest_path = _manifest_path(store, data_dir, plugin)
        if not manifest_path.exists():
            continue
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("manifest.json", manifest_path.read_text())
        payload = buffer.getvalue()
        manifest = json.loads(manifest_path.read_text())
        entries.append({
            "name": plugin["name"],
            "version": plugin["version"],
            "url": f"{base_url.rstrip('/')}/api/plugins/{plugin['id']}/export" if base_url else "",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "description": manifest.get("description", ""),
            "permissions": manifest.get("permissions", []),
            "trust": plugin["trust"],
        })
    from datetime import datetime, timezone

    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(entries), "plugins": entries}


def export_package(store, data_dir, plugin_id: str) -> bytes:
    """Rebuild a distributable package for one installed plugin."""
    import io
    import zipfile

    plugin = store.get_plugin(plugin_id)
    if plugin is None:
        raise PluginError("plugin not found")
    manifest_path = _manifest_path(store, data_dir, plugin)
    if not manifest_path.exists():
        raise PluginError("this plugin has no manifest on disk")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("manifest.json", manifest_path.read_text())
    return buffer.getvalue()

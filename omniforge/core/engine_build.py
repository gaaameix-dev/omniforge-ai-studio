"""Run an installed game engine's CLI against a scaffolded project.

The scaffold in `core/engines.py` produces text files. Building them needs the
engine itself, so this module only shells out to a configured command, captures the
log and reports honestly when the tool is not installed.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

# A project can be validated without export templates: the editor opens it headless,
# parses project files and reports script errors, then quits.
VALIDATE_COMMANDS = {
    "godot": ["godot", "--headless", "--path", "{project}", "--quit"],
    "unity": ["unity", "-batchmode", "-quit", "-projectPath", "{project}", "-logFile", "-"],
    "unreal": ["UnrealEditor-Cmd", "{project}", "-run=ResavePackages", "-unattended", "-nullrhi"],
}

DEFAULT_COMMANDS = {
    "godot": ["godot", "--headless", "--path", "{project}", "--export-release", "Linux"],
    "unity": ["unity", "-batchmode", "-quit", "-projectPath", "{project}",
              "-executeMethod", "BuildScript.PerformBuild"],
    "unreal": ["UnrealBuildTool", "-projectfiles", "-project={project}"],
}


class EngineBuildError(RuntimeError):
    pass


def resolve_command(engine: str, project: Path, settings=None) -> list[str]:
    custom = getattr(settings, "engine_commands", {}) or {}
    template = custom.get(engine) or DEFAULT_COMMANDS.get(engine)
    if not template:
        raise EngineBuildError(f"no build command known for '{engine}'")
    if isinstance(template, str):
        template = template.split()
    return [part.replace("{project}", str(project)) for part in template]


def resolve_validate_command(engine: str, project: Path, settings=None) -> list[str]:
    custom = getattr(settings, "engine_validate_commands", {}) or {}
    template = custom.get(engine) or VALIDATE_COMMANDS.get(engine)
    if not template:
        raise EngineBuildError(f"no validation command known for '{engine}'")
    if isinstance(template, str):
        template = template.split()
    return [part.replace("{project}", str(project)) for part in template]


def project_looks_valid(engine: str, project: Path) -> dict[str, Any]:
    """Static check that the folder is what the engine expects."""
    expected = {
        "godot": ["project.godot"],
        "unity": ["ProjectSettings/ProjectVersion.txt"],
        "unreal": ["Config/DefaultEngine.ini"],
    }.get(engine, [])
    missing = [name for name in expected if not (project / name).exists()]
    return {"ok": not missing, "missing": missing, "expected": expected}


async def validate(engine: str, project: str | Path, *, settings=None,
                   timeout: float = 180.0) -> dict[str, Any]:
    """Open the project with the engine in headless mode; no export templates needed."""
    project = Path(project)
    if not project.is_dir():
        raise EngineBuildError(f"project folder does not exist: {project}")
    static = project_looks_valid(engine, project)
    try:
        command = resolve_validate_command(engine, project, settings)
    except EngineBuildError as exc:
        return {"engine": engine, "ok": False, "error": str(exc), "static": static}
    if not shutil.which(command[0]):
        return {"engine": engine, "ok": False, "command": command, "static": static,
                "error": f"'{command[0]}' is not installed on this machine"}

    process = await asyncio.create_subprocess_exec(
        *command, cwd=str(project),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        timed_out = False
    except TimeoutError:
        process.kill()
        stdout, _ = await process.communicate()
        timed_out = True
    log = (stdout or b"").decode("utf-8", "replace")
    errors = [line for line in log.splitlines()
              if any(marker in line.upper() for marker in ("ERROR", "SCRIPT ERROR", "FAILED"))]
    return {
        "engine": engine,
        "ok": process.returncode == 0 and not timed_out and not errors,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "static": static,
        "errors": errors[:10],
        "log_tail": log[-2000:],
        "command": command,
    }


def available(engine: str, settings=None) -> dict[str, Any]:
    try:
        command = resolve_command(engine, Path("."), settings)
    except EngineBuildError as exc:
        return {"engine": engine, "configured": False, "reason": str(exc)}
    binary = command[0]
    path = shutil.which(binary)
    return {"engine": engine, "configured": True, "binary": binary,
            "found": bool(path), "path": path,
            "hint": "" if path else "install the engine or set OMNIFORGE_ENGINE_COMMANDS"}


async def build(engine: str, project: str | Path, *, settings=None,
                timeout: float = 600.0) -> dict[str, Any]:
    """Invoke the engine CLI. Never raises for a missing binary - it reports it."""
    project = Path(project)
    if not project.is_dir():
        raise EngineBuildError(f"project folder does not exist: {project}")
    try:
        command = resolve_command(engine, project, settings)
    except EngineBuildError as exc:
        return {"engine": engine, "ok": False, "error": str(exc), "command": []}
    if not shutil.which(command[0]):
        return {"engine": engine, "ok": False, "command": command,
                "error": f"'{command[0]}' is not installed on this machine",
                "hint": "set OMNIFORGE_ENGINE_COMMANDS or install the engine"}

    process = await asyncio.create_subprocess_exec(
        *command, cwd=str(project),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        timed_out = False
    except TimeoutError:
        process.kill()
        stdout, _ = await process.communicate()
        timed_out = True
    log = (stdout or b"").decode("utf-8", "replace")
    return {
        "engine": engine,
        "ok": (process.returncode == 0) and not timed_out,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "command": command,
        "log_tail": log[-4000:],
    }

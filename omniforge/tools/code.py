"""Sandboxed code execution as a tool - opt-in, destructive, approval-gated."""
from __future__ import annotations

from ..core.sandbox import run_python, sandbox_available
from .base import Tool


async def code_exec(code: str, timeout: float = 5.0, memory_mb: int = 256) -> dict:
    result = await run_python(code, timeout=timeout, memory_mb=memory_mb)
    return result.to_dict()


def register_code_tools(registry, settings) -> None:
    registry.register("code_exec", Tool(
        name="code_exec",
        description=("Run a Python snippet in a restricted child process (isolated interpreter, "
                     "rlimits, temp cwd, wall-clock timeout). Process isolation, not a container."),
        run=code_exec,
        parameters={"code": "string", "timeout": "number (seconds)", "memory_mb": "integer"},
        returns="JSON {ok, stdout, stderr, exit_code, duration_s, timed_out}",
        permissions=("code.exec",),
        destructive=True,
        requires_approval=True,
        enabled=sandbox_available(),
        category="execution",
        provider="sandbox",
    ))

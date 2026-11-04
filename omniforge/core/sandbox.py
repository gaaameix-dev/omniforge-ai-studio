"""Restricted Python execution for the code_exec tool.

Isolation used here: separate process, isolated interpreter (-I -S), empty env,
temporary working directory, CPU and address-space rlimits, wall-clock timeout and
capped output. That stops accidents and runaway loops. It is NOT a container: a
determined attacker on the same host still has kernel-level options. Do not expose
this to untrusted users without putting it in a real sandbox (container, gVisor,
firejail) - which is why it is disabled unless OMNIFORGE_ALLOW_CODE_EXEC=true.
"""
from __future__ import annotations

import asyncio
import os
import resource
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT = 20_000


@dataclass
class SandboxResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "timed_out": self.timed_out,
        }


def _limits(cpu_seconds: int, memory_mb: int):
    def apply() -> None:  # pragma: no cover - runs in the child process
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if memory_mb:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))

    return apply


async def run_python(
    code: str,
    *,
    timeout: float = 5.0,
    memory_mb: int = 256,
    allow_network: bool = False,
) -> SandboxResult:
    """Run a snippet in a locked-down child interpreter."""
    if not code.strip():
        return SandboxResult(ok=False, stdout="", stderr="no code supplied",
                             exit_code=None, duration_s=0.0)
    if len(code) > 20_000:
        return SandboxResult(ok=False, stdout="", stderr="code too large (20k char limit)",
                             exit_code=None, duration_s=0.0)

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="omniforge-sbx-") as tmp:
        script = Path(tmp) / "snippet.py"
        script.write_text(code, encoding="utf-8")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONHASHSEED": "0",
               "HOME": tmp, "TMPDIR": tmp}
        if allow_network:
            env["OMNIFORGE_SANDBOX_NET"] = "1"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-S", "-B", str(script),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp,
            env=env,
            preexec_fn=_limits(int(timeout) + 1, memory_mb),
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            out, err = await proc.communicate()
        duration = time.perf_counter() - started

    stdout = (out or b"").decode("utf-8", "replace")[:MAX_OUTPUT]
    stderr = (err or b"").decode("utf-8", "replace")[:MAX_OUTPUT]
    exit_code = proc.returncode
    ok = (not timed_out) and exit_code == 0
    if timed_out:
        stderr = (stderr + f"\n[timeout after {timeout}s]").strip()
    return SandboxResult(ok=ok, stdout=stdout, stderr=stderr, exit_code=exit_code,
                         duration_s=duration, timed_out=timed_out)


def sandbox_available() -> bool:
    return os.name == "posix" and hasattr(resource, "RLIMIT_CPU")

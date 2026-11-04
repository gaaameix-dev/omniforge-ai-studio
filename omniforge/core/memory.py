"""Run-scoped blackboard plus an artifact store on disk.

Agents read upstream results from the blackboard instead of holding private
copies, so a retried or re-routed task sees the same shared truth.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Blackboard:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def _board(self, run_id: str) -> dict[str, Any]:
        return self._data.setdefault(run_id, {})

    def set(self, run_id: str, key: str, value: Any) -> None:
        self._board(run_id)[key] = value

    def get(self, run_id: str, key: str, default: Any = None) -> Any:
        return self._board(run_id).get(key, default)

    def all(self, run_id: str) -> dict[str, Any]:
        return dict(self._board(run_id))

    def clear(self, run_id: str) -> None:
        self._data.pop(run_id, None)

    def context_block(self, run_id: str, limit_chars: int = 6000) -> str:
        """Flatten upstream results into a prompt-friendly block."""
        parts: list[str] = []
        used = 0
        for key, value in self._board(run_id).items():
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            chunk = f"### {key}\n{text}\n"
            if used + len(chunk) > limit_chars:
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n".join(parts)


class ArtifactStore:
    """Writes deliverables to <data_dir>/artifacts/<run_id>/<name>."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, run_id: str, name: str, content: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        folder = self.root / run_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe
        path.write_text(content, encoding="utf-8")
        return str(path)

    def list(self, run_id: str) -> list[str]:
        folder = self.root / run_id
        if not folder.exists():
            return []
        return sorted(str(p) for p in folder.iterdir() if p.is_file())

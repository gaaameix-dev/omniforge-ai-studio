"""Portable project format: one zip with the project, its runs and its files.

Layout:
  project.json        project row, runs, tasks, plans
  artifacts/<run>/... deliverables
  uploads/<name>      project files (content preserved, names de-duplicated)

Import is additive: it always creates new ids, so importing twice cannot
overwrite anything that already exists.
"""
from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = "omniforge-project/1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def export_project(store, project_id: str, artifacts_root: Path | str) -> tuple[bytes, str]:
    project = store.get_project(project_id)
    if project is None:
        raise KeyError(f"unknown project '{project_id}'")
    artifacts_root = Path(artifacts_root)
    payload: dict[str, Any] = {
        "format": FORMAT,
        "exported_at": _now(),
        "project": {k: project.get(k) for k in ("id", "name", "created_at", "meta")},
        "runs": [],
        "files": [],
    }
    for run in project.get("runs", []):
        record = store.get_run(run["id"])
        if record:
            payload["runs"].append({
                "id": record["id"], "goal": record["goal"], "status": record["status"],
                "created_at": record["created_at"], "summary": record["summary"],
                "tokens": record["tokens"], "plan": record["plan"],
            })
    payload["files"] = [
        {k: f[k] for k in ("id", "name", "content_type", "bytes", "sha256", "created_at")}
        for f in store.list_files(project_id)
    ]

    archive = Path("/tmp") / f"omniforge-export-{project_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("project.json", json.dumps(payload, indent=2, default=str))
        for run in payload["runs"]:
            folder = artifacts_root / run["id"]
            if not folder.exists():
                continue
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    bundle.write(path, f"artifacts/{run['id']}/{path.name}")
        for file_record in store.list_files(project_id):
            source = Path(file_record["path"])
            if source.is_file():
                bundle.write(source, f"uploads/{file_record['id']}__{file_record['name']}")
        bundle.writestr("README.txt",
                        "OmniForge portable project export.\n"
                        "Import with: POST /api/projects/import (multipart 'file')\n"
                        "or: omniforge import --file <this.zip>\n")
    data = archive.read_bytes()
    archive.unlink(missing_ok=True)
    return data, f"omniforge-project-{project_id}.zip"


def import_project(store, data: bytes, data_dir: Path | str, owner_id: str | None = None) -> dict:
    data_dir = Path(data_dir)
    tmp = data_dir / "imports" / f"import_{os.urandom(5).hex()}.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    imported_runs: list[str] = []
    imported_files: list[str] = []
    try:
        with zipfile.ZipFile(tmp) as bundle:
            if "project.json" not in bundle.namelist():
                raise ValueError("not an OmniForge project export: project.json missing")
            payload = json.loads(bundle.read("project.json"))
            if payload.get("format") != FORMAT:
                raise ValueError(f"unsupported export format: {payload.get('format')!r}")

            project = store.create_project(
                f"{payload['project']['name']} (imported)", _now(), payload["project"].get("meta") or {}
            )
            store._conn.execute("UPDATE projects SET owner_id=? WHERE id=?", (owner_id, project["id"]))
            store._conn.commit()

            for run in payload.get("runs", []):
                run_id = f"run_{os.urandom(5).hex()}"
                store._conn.execute(
                    """INSERT INTO runs (id, goal, status, strategy, created_at, updated_at,
                                         auto_approve, summary, error, tokens, cost_usd, project_id,
                                         idempotency_key, priority, workspace_id, meta)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id, run.get("goal", ""), "done", "imported",
                        run.get("created_at", _now()), _now(), 1,
                        run.get("summary"), None, int(run.get("tokens") or 0), 0.0,
                        project["id"], None, 5, None,
                        json.dumps({"imported_from": run.get("id")}),
                    ),
                )
                for idx, task in enumerate((run.get("plan") or {}).get("tasks", [])):
                    store._conn.execute(
                        """INSERT INTO tasks (id, run_id, seq, title, description, department, skill,
                                              agent_id, status, result, error, attempts,
                                              requires_approval, depends_on, started_at, finished_at,
                                              review_score, parent_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            f"task_{os.urandom(6).hex()}", run_id, idx, task.get("title", ""),
                            task.get("description"), task.get("department"), task.get("skill"),
                            task.get("agent_id"), task.get("status", "done"), task.get("result"),
                            task.get("error"), int(task.get("attempts") or 0), 0, "[]",
                            task.get("started_at"), task.get("finished_at"),
                            task.get("review_score"), None,
                        ),
                    )
                store._conn.commit()
                imported_runs.append(run_id)

                prefix = f"artifacts/{run.get('id')}/"
                for name in bundle.namelist():
                    if name.startswith(prefix) and not name.endswith("/"):
                        target = data_dir / "artifacts" / run_id / Path(name).name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(bundle.read(name))

            uploads = data_dir / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            for name in bundle.namelist():
                if not name.startswith("uploads/") or name.endswith("/"):
                    continue
                payload_bytes = bundle.read(name)
                file_id = f"file_{os.urandom(6).hex()}"
                safe = Path(name).name.split("__", 1)[-1]
                target = uploads / f"{file_id}__{safe}"
                target.write_bytes(payload_bytes)
                import hashlib

                record = {
                    "id": file_id, "project_id": project["id"], "name": safe,
                    "content_type": "application/octet-stream", "bytes": len(payload_bytes),
                    "sha256": hashlib.sha256(payload_bytes).hexdigest(), "path": str(target),
                    "created_at": _now(), "owner_id": owner_id,
                }
                store.add_file(record)
                imported_files.append(file_id)
    finally:
        tmp.unlink(missing_ok=True)

    return {
        "project_id": project["id"],
        "project_name": project["name"],
        "runs": imported_runs,
        "files": imported_files,
    }

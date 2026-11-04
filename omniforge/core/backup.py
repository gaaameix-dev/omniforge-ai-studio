"""Backups that are verified, not assumed.

A backup is a zip containing a consistent SQLite snapshot (via the sqlite3 backup
API, safe while the database is in use), the artifacts folder and a manifest with
SHA-256 hashes. Restore refuses to touch a live data directory unless forced, and
verifies every hash plus `PRAGMA integrity_check` before it does.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
DB_NAME = "omniforge.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(data_dir: Path | str, dest: Path | str | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = data_dir / DB_NAME
    if not db_path.exists():
        raise FileNotFoundError(f"no database at {db_path}")
    dest = Path(dest) if dest else data_dir / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = dest / f"omniforge-backup-{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="omniforge-backup-") as tmp:
        snapshot = Path(tmp) / DB_NAME
        source = sqlite3.connect(str(db_path))
        target = sqlite3.connect(str(snapshot))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        files: list[dict[str, Any]] = [{"path": DB_NAME, "bytes": snapshot.stat().st_size,
                                        "sha256": _sha256(snapshot)}]
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, DB_NAME)
            artifacts = data_dir / "artifacts"
            if artifacts.exists():
                for path in sorted(artifacts.rglob("*")):
                    if not path.is_file():
                        continue
                    arcname = f"artifacts/{path.relative_to(artifacts)}"
                    bundle.write(path, arcname)
                    files.append({"path": arcname, "bytes": path.stat().st_size,
                                  "sha256": _sha256(path)})
            manifest = {"created_at": _now(), "database": DB_NAME, "files": files,
                        "file_count": len(files)}
            bundle.writestr(MANIFEST, json.dumps(manifest, indent=2))
            bundle.writestr("RESTORE.txt",
                            "Unzip into an empty data directory, or run:\n"
                            "  omniforge restore --file <this archive>\n"
                            "Verify first with: omniforge backup --verify <this archive>\n")

    return {
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "files": len(files),
        "created_at": _now(),
    }


def verify_backup(archive: Path | str) -> dict[str, Any]:
    archive = Path(archive)
    if not archive.exists():
        raise FileNotFoundError(f"no such backup: {archive}")
    problems: list[str] = []
    checked = 0
    with tempfile.TemporaryDirectory(prefix="omniforge-verify-") as tmp:
        try:
            with zipfile.ZipFile(archive) as bundle:
                if MANIFEST not in bundle.namelist():
                    return {"ok": False, "problems": ["manifest.json missing"], "checked": 0}
                manifest = json.loads(bundle.read(MANIFEST))
                for entry in manifest.get("files", []):
                    try:
                        payload = bundle.read(entry["path"])
                    except KeyError:
                        problems.append(f"missing file in archive: {entry['path']}")
                        continue
                    if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                        problems.append(f"hash mismatch: {entry['path']}")
                    checked += 1
                db_bytes = bundle.read(manifest.get("database", DB_NAME))
                db_file = Path(tmp) / DB_NAME
                db_file.write_bytes(db_bytes)
                conn = sqlite3.connect(str(db_file))
                try:
                    row = conn.execute("PRAGMA integrity_check").fetchone()
                    if not row or row[0] != "ok":
                        problems.append(f"sqlite integrity_check: {row[0] if row else 'no result'}")
                    tables = {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if "runs" not in tables:
                        problems.append("database has no runs table")
                finally:
                    conn.close()
        except zipfile.BadZipFile:
            return {"ok": False, "problems": ["not a valid zip archive"], "checked": 0}
        except (zlib.error, NotImplementedError, OSError, KeyError) as exc:
            return {"ok": False, "problems": [f"unreadable archive: {type(exc).__name__}: {exc}"],
                    "checked": checked}

    return {
        "ok": not problems,
        "problems": problems,
        "checked": checked,
        "archive": str(archive),
        "created_at": (manifest or {}).get("created_at") if isinstance(manifest, dict) else None,
    }


def restore_backup(archive: Path | str, data_dir: Path | str, *, force: bool = False) -> dict[str, Any]:
    report = verify_backup(archive)
    if not report["ok"]:
        raise ValueError("backup failed verification: " + "; ".join(report["problems"]))
    data_dir = Path(data_dir)
    db_path = data_dir / DB_NAME
    if db_path.exists() and not force:
        raise FileExistsError(
            f"{db_path} already exists; stop the service and pass force=True (or --force) to replace it"
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        shutil.copy2(db_path, db_path.with_suffix(".db.bak"))
    with zipfile.ZipFile(archive) as bundle:
        bundle.extract(DB_NAME, data_dir)
        artifacts = [n for n in bundle.namelist() if n.startswith("artifacts/")]
        if artifacts:
            bundle.extractall(data_dir, members=artifacts)
    return {"restored_to": str(db_path), "files": len(artifacts) + 1, "verified": True}

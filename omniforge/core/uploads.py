"""File uploads with size limits, type allowlists, hashes and safe names.

Files land under <data_dir>/uploads with an opaque id prefix, so a hostile name
cannot escape the folder. The stored metadata keeps the checksum, so a download
can be verified and a duplicate detected.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

DEFAULT_TYPES = (
    "text/", "image/", "application/json", "application/pdf",
    "application/zip", "application/octet-stream",
)


class UploadError(ValueError):
    pass


def safe_name(name: str) -> str:
    base = os.path.basename((name or "file").replace("\\", "/"))
    cleaned = SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:120]


def type_allowed(content_type: str, allowlist: tuple[str, ...] = DEFAULT_TYPES) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    return any(ctype == entry or (entry.endswith("/") and ctype.startswith(entry))
               for entry in allowlist)


def save_upload(
    data_dir: Path | str,
    payload: bytes,
    name: str,
    content_type: str = "application/octet-stream",
    *,
    max_bytes: int = 10 * 1024 * 1024,
    allowlist: tuple[str, ...] = DEFAULT_TYPES,
) -> dict[str, Any]:
    if not payload:
        raise UploadError("the uploaded file is empty")
    if len(payload) > max_bytes:
        raise UploadError(f"file is larger than the {max_bytes // (1024 * 1024)} MB limit")
    if not type_allowed(content_type, allowlist):
        raise UploadError(f"content type '{content_type}' is not allowed")

    file_id = f"file_{os.urandom(6).hex()}"
    folder = Path(data_dir) / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}__{safe_name(name)}"
    path.write_bytes(payload)
    return {
        "id": file_id,
        "name": safe_name(name),
        "content_type": (content_type or "").split(";")[0].strip().lower(),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "path": str(path),
    }


def read_upload(record: dict[str, Any]) -> bytes:
    path = Path(record["path"]).resolve()
    uploads = path.parent
    if not path.is_file() or uploads.name != "uploads":
        raise UploadError("stored file is missing or outside the upload directory")
    return path.read_bytes()


def delete_upload(record: dict[str, Any]) -> bool:
    path = Path(record["path"])
    try:
        if path.is_file() and path.parent.name == "uploads":
            path.unlink()
            return True
    except OSError:
        return False
    return False

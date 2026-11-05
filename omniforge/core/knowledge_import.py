"""Bulk document import: text, markdown, CSV and zip archives.

Parsing is deterministic and offline - it never calls a model. Anything it cannot
decode is reported back as a skipped file with a reason instead of silently dropped.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import PurePosixPath
from typing import Any

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".log", ".yaml", ".yml", ".json"}
MAX_BYTES = 5 * 1024 * 1024
MAX_DOCS = 500


class ImportError_(ValueError):
    """A file that cannot be turned into documents."""


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("could not decode the file as text")


def _from_csv(text: str, name: str) -> list[dict[str, str]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ImportError_("the CSV is empty")
    header = [cell.strip().lower() for cell in rows[0]]
    has_header = any(cell in {"title", "name", "heading"} for cell in header) and any(
        cell in {"text", "body", "content"} for cell in header)
    documents = []
    if has_header:
        title_index = next(i for i, cell in enumerate(header)
                           if cell in {"title", "name", "heading"})
        text_index = next(i for i, cell in enumerate(header)
                          if cell in {"text", "body", "content"})
        for number, row in enumerate(rows[1:], start=2):
            if len(row) <= max(title_index, text_index):
                continue
            title = row[title_index].strip() or f"{name} row {number}"
            body = row[text_index].strip()
            if body:
                documents.append({"title": title[:200], "text": body[:20000]})
    else:
        for number, row in enumerate(rows, start=1):
            if not row or not any(cell.strip() for cell in row):
                continue
            title = (row[0].strip() or f"{name} row {number}")[:200]
            body = " | ".join(cell.strip() for cell in row[1:]).strip() or row[0].strip()
            documents.append({"title": title, "text": body[:20000]})
    if not documents:
        raise ImportError_("no usable rows found in the CSV")
    return documents


def _from_json(text: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportError_(f"invalid JSON: {exc}") from None
    entries = payload.get("documents") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ImportError_("expected a list of {title, text} objects")
    documents = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        body = str(entry.get("text") or entry.get("body") or "").strip()
        title = str(entry.get("title") or entry.get("name") or "untitled").strip()
        if body:
            documents.append({"title": title[:200], "text": body[:20000]})
    if not documents:
        raise ImportError_("no documents with text found")
    return documents


def parse_upload(filename: str, payload: bytes) -> dict[str, Any]:
    """Return {'documents': [...], 'skipped': [{'file', 'reason'}], 'files': n}."""
    if len(payload) > MAX_BYTES:
        raise ImportError_(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    suffix = PurePosixPath(filename or "").suffix.lower()
    documents: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    if suffix == ".zip":
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise ImportError_(f"not a zip archive: {exc}") from None
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = PurePosixPath(member.filename).name
            member_suffix = PurePosixPath(name).suffix.lower()
            if member_suffix == ".csv":
                try:
                    documents.extend(_from_csv(_decode(archive.read(member)), name))
                except ImportError_ as exc:
                    skipped.append({"file": name, "reason": str(exc)})
                continue
            if member_suffix not in TEXT_SUFFIXES:
                skipped.append({"file": name, "reason": f"unsupported type '{member_suffix}'"})
                continue
            body = _decode(archive.read(member)).strip()
            if body:
                documents.append({"title": name[:200], "text": body[:20000]})
            else:
                skipped.append({"file": name, "reason": "empty"})
    elif suffix == ".csv":
        documents = _from_csv(_decode(payload), filename)
    elif suffix == ".json":
        documents = _from_json(_decode(payload))
    elif suffix in TEXT_SUFFIXES or suffix == "":
        body = _decode(payload).strip()
        if not body:
            raise ImportError_("the file is empty")
        documents = [{"title": (filename or "untitled")[:200], "text": body[:20000]}]
    else:
        raise ImportError_(f"unsupported file type '{suffix}'")

    if len(documents) > MAX_DOCS:
        skipped.extend({"file": d["title"], "reason": "over the 500 document limit"}
                       for d in documents[MAX_DOCS:])
        documents = documents[:MAX_DOCS]
    return {"documents": documents, "skipped": skipped, "files": 1}

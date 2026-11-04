"""Project files, portable export/import, agent scoring, memory, provenance."""
from __future__ import annotations

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core import auth
from ..core.auth import Identity, allows
from ..core.portable import export_project, import_project
from ..core.uploads import UploadError, delete_upload, read_upload, save_upload
from . import access


class LicenceRequest(BaseModel):
    licence: str = Field(min_length=1, max_length=200)


class ScanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    run_id: str | None = None
    limit: int = 10


def _identity(request: Request) -> Identity:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return identity


def _require(identity: Identity, action: str) -> None:
    if not allows(identity.role, action):
        raise HTTPException(status_code=403, detail=f"role '{identity.role}' cannot {action}")


def register(app, *, orch) -> None:
    def store():
        return app.state.store

    def settings():
        return app.state.settings

    # ----------------------------------------------------------------- files
    @app.get("/api/files")
    async def list_files(request: Request, project_id: str | None = None) -> list[dict]:
        _require(_identity(request), "read")
        return [
            {k: f[k] for k in ("id", "project_id", "name", "content_type", "bytes",
                               "sha256", "created_at")}
            for f in store().list_files(project_id)
        ]

    @app.post("/api/projects/{project_id}/files", status_code=201)
    async def upload_file(project_id: str, request: Request, file: UploadFile = File(...)) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        access.ensure(identity, store(), project_id, "run")
        payload = await file.read()
        try:
            saved = save_upload(settings().data_dir, payload, file.filename or "file",
                                file.content_type or "application/octet-stream")
        except UploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        record = store().add_file({
            **saved, "project_id": project_id, "created_at": auth.utcnow(),
            "owner_id": identity.user_id,
        })
        store().add_audit(identity.email, "file.uploaded", record["id"],
                          f"{record['name']} ({record['bytes']}b)", None, auth.utcnow())
        return {k: record[k] for k in ("id", "project_id", "name", "content_type", "bytes",
                                       "sha256", "created_at")}

    @app.get("/api/files/{file_id}/download")
    async def download_file(file_id: str, request: Request) -> Response:
        _require(_identity(request), "read")
        record = store().get_file(file_id)
        if record is None:
            raise HTTPException(status_code=404, detail="file not found")
        try:
            data = read_upload(record)
        except UploadError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from None
        return Response(content=data, media_type=record["content_type"] or "application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{record["name"]}"',
                                 "X-Content-SHA256": record["sha256"]})

    @app.delete("/api/files/{file_id}")
    async def delete_file(file_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        record = store().get_file(file_id)
        if record is None:
            raise HTTPException(status_code=404, detail="file not found")
        removed = delete_upload(record)
        store().delete_file(file_id)
        store().add_audit(identity.email, "file.deleted", file_id, record["name"], None, auth.utcnow())
        return {"deleted": True, "bytes_removed": record["bytes"], "file_removed": removed}

    # -------------------------------------------------------- export/import
    @app.get("/api/projects/{project_id}/export")
    async def export(project_id: str, request: Request) -> Response:
        identity = _identity(request)
        _require(identity, "read")
        access.ensure(identity, store(), project_id, "read")
        try:
            data, filename = export_project(store(), project_id, orch().artifacts.root)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found") from None
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/projects/import", status_code=201)
    async def import_zip(request: Request, file: UploadFile = File(...)) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        payload = await file.read()
        try:
            result = import_project(store(), payload, settings().data_dir, identity.user_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - corrupt archives surface here
            raise HTTPException(status_code=400, detail=f"import failed: {exc}") from None
        store().add_audit(identity.email, "project.imported", result["project_id"],
                          f"{len(result['runs'])} runs", None, auth.utcnow())
        return result

    # -------------------------------------------------------- agent scoring
    @app.get("/api/agents/performance")
    async def agent_performance(request: Request, limit: int = 50) -> dict:
        _require(_identity(request), "read")
        stats = store().agent_stats()
        names = {a.id: a.name for a in orch().agents.all()}
        for row in stats:
            row["name"] = names.get(row["agent_id"], row["agent_id"])
        return {"agents": stats[:limit], "total": len(stats)}

    # -------------------------------------------------------------- memory
    @app.get("/api/memory")
    async def memory(request: Request, run_id: str | None = None, agent_id: str | None = None,
                     limit: int = 100) -> list[dict]:
        _require(_identity(request), "read")
        return store().list_memory(run_id=run_id, agent_id=agent_id, limit=limit)

    @app.delete("/api/memory/{memory_id}")
    async def delete_memory(memory_id: int, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        deleted = store().delete_memory(memory_id)
        store().add_audit(identity.email, "memory.deleted", str(memory_id), None, None, auth.utcnow())
        return {"deleted": deleted}

    @app.delete("/api/memory/run/{run_id}")
    async def clear_run_memory(run_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        return {"deleted": store().clear_memory(run_id)}

    @app.post("/api/memory/search")
    async def search_memory(req: MemorySearchRequest, request: Request) -> dict:
        _require(_identity(request), "read")
        entries = store().list_memory(run_id=req.run_id, limit=1000)
        needle = req.query.lower()
        hits = [
            e for e in entries
            if needle in (e["key"] or "").lower() or needle in (e["value"] or "").lower()
        ][: max(1, req.limit)]
        return {"query": req.query, "hits": hits, "scanned": len(entries)}

    @app.get("/api/knowledge/documents")
    async def knowledge_documents(request: Request, limit: int = 100) -> list[dict]:
        _require(_identity(request), "read")
        return store().documents(limit=limit)

    # ---------------------------------------------------------- provenance
    @app.get("/api/provenance")
    async def provenance(request: Request, run_id: str | None = None,
                         limit: int = 200) -> list[dict]:
        _require(_identity(request), "read")
        return store().list_provenance(run_id=run_id, limit=limit)

    @app.post("/api/provenance/{provenance_id}/licence")
    async def set_licence(provenance_id: int, req: LicenceRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        updated = store().set_licence(provenance_id, req.licence)
        if not updated:
            raise HTTPException(status_code=404, detail="provenance record not found")
        store().add_audit(identity.email, "provenance.licence", str(provenance_id), req.licence,
                          None, auth.utcnow())
        return {"id": provenance_id, "licence": req.licence}

    # ---------------------------------------------------------- moderation
    @app.get("/api/moderation")
    async def moderation_policy(request: Request) -> dict:
        _require(_identity(request), "read")
        policy = orch().policy
        return {
            "enabled": policy.enabled,
            "redact_secrets": policy.redact_secrets,
            "blocked_terms": list(policy.blocked_terms),
            "rules": [{"category": c, "rule": r} for c, r, _ in policy.rules()],
        }

    @app.post("/api/moderation/scan")
    async def moderation_scan_route(req: ScanRequest, request: Request) -> dict:
        _require(_identity(request), "read")
        from ..core.moderation import scan

        report = scan(req.text, orch().policy)
        return {**report.to_dict(), "redacted": report.text if report.findings else req.text}

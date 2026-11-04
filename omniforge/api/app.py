"""HTTP API, live event stream, metrics and the bundled dashboard."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
from ..config import Settings
from ..core import auth as auth_core
from ..core.auth import ANONYMOUS, Identity, action_for, allows, resolve_identity
from ..core.billing import Billing
from ..core.llm import build_llm
from ..core.mailer import build_mailer
from ..core.metrics import Metrics
from ..core.notifier import Notifier
from ..core.orchestrator import Orchestrator
from ..core.ratelimit import RateLimiter
from ..core.scheduler import Scheduler
from ..core.store import RunStore
from ..core.vault import Vault
from . import access, automation_routes, content_routes, identity_routes, platform_routes
from .security import PUBLIC_PATHS, check_request

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class ApprovalRequest(BaseModel):
    approved: bool
    note: str = ""


class RunRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=4000)
    auto_approve: bool | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    allow_destructive: bool = False
    priority: int = Field(default=5, ge=1, le=10)
    idempotency_key: str | None = Field(default=None, max_length=120)
    meta: dict[str, Any] | None = None


class ProjectRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    workspace_id: str | None = None
    token_quota: int = Field(default=0, ge=0)
    meta: dict[str, Any] | None = None


class ProjectMemberRequest(BaseModel):
    email: str
    role: str = "viewer"


class ProjectQuotaRequest(BaseModel):
    tokens: int = Field(ge=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings or Settings.from_env()
        skills = build_skill_registry()
        agents = build_agent_registry(skills)
        tools = build_tool_registry(str(cfg.data_dir), cfg)
        store = RunStore(cfg.db_path)
        metrics = Metrics()
        app.state.settings = cfg
        app.state.store = store
        app.state.metrics = metrics
        app.state.vault = Vault(cfg.data_dir, cfg.secret_key)
        app.state.mailer = build_mailer(cfg)
        app.state.notifier = Notifier(cfg.webhook_url)
        app.state.billing = Billing(cfg, store)
        app.state.limiter = RateLimiter(per_minute=cfg.rate_limit_per_minute, burst=cfg.rate_limit_burst)
        app.state.orchestrator = Orchestrator(
            settings=cfg, llm=build_llm(cfg), skills=skills, agents=agents,
            tools=tools, store=store, metrics=metrics, notifier=app.state.notifier,
            billing=app.state.billing, mailer=app.state.mailer,
        )
        app.state.scheduler = Scheduler(
            app.state.orchestrator, store, tick_seconds=cfg.scheduler_tick_seconds
        )
        if cfg.scheduler_enabled:
            await app.state.scheduler.start()
        try:
            yield
        finally:
            await app.state.scheduler.stop()
            store.close()

    app = FastAPI(title="OmniForge AI Studio", version=__version__, lifespan=lifespan)

    if settings and settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        cfg = app.state.settings
        path = request.url.path
        identity = resolve_identity(
            cfg, app.state.store,
            request.headers.get("authorization"), request.query_params.get("token"),
        )
        if identity is None and not cfg.auth_enabled:
            # single-user mode: the local operator is trusted, exactly as before
            identity = Identity(user_id=None, email=None, role="owner", kind="legacy")
        request.state.identity = identity

        if cfg.auth_enabled:
            public = (
                path in PUBLIC_PATHS
                or path.startswith((
                    "/docs", "/openapi",
                    "/api/auth/register", "/api/auth/login",
                    "/api/auth/forgot", "/api/auth/reset",
                ))
                # inbound webhooks carry their own secret token in the URL
                or (path.startswith("/api/hooks/") and path.endswith("/trigger"))
            )
            if not public:
                if identity is None:
                    if cfg.allow_anonymous and request.method == "GET":
                        identity = ANONYMOUS
                        request.state.identity = identity
                    else:
                        return JSONResponse(
                            {"detail": "authentication required"},
                            status_code=401, headers={"WWW-Authenticate": "Bearer"},
                        )
                action = action_for(request.method, path)
                if not allows(identity.role, action):
                    return JSONResponse(
                        {"detail": f"role '{identity.role}' cannot {action}"}, status_code=403
                    )
        else:
            try:
                check_request(cfg, request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                    headers=exc.headers or None)

        bucket = (
            f"user:{identity.user_id}" if identity and identity.user_id
            else f"kind:{identity.kind}" if identity
            else f"ip:{request.client.host if request.client else 'unknown'}"
        )
        allowed, retry_after = app.state.limiter.check(bucket)
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded", "retry_after": retry_after},
                status_code=429, headers={"Retry-After": str(max(1, int(retry_after)))},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Policy"] = f"{cfg.rate_limit_per_minute}/min"
        if identity is not None:
            response.headers["X-Identity"] = identity.kind
        return response

    def orch() -> Orchestrator:
        return app.state.orchestrator

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    async def health() -> dict:
        o = orch()
        return {
            "status": "ok",
            "version": __version__,
            "backend": o.llm.name,
            "live_mode": o.settings.live_mode,
            "runs": len(o.runs),
            "auth_required": bool(o.settings.api_key or o.settings.auth_enabled),
            "auth_mode": "multi-user" if o.settings.auth_enabled else ("single-key" if o.settings.api_key else "open"),
            "vault": app.state.vault.status()["backend"],
            "mail": app.state.mailer.status()["backend"],
            "webhook": app.state.notifier.enabled,
            "scheduler": app.state.scheduler.running,
            "moderation": o.settings.moderation_enabled,
        }

    @app.get("/api/framework")
    async def framework() -> dict:
        return orch().describe()

    @app.get("/api/stats")
    async def stats() -> dict:
        o = orch()
        return {
            "storage": o.store.stats(),
            "knowledge": o.knowledge.stats(),
            "runtime": o.metrics.snapshot(),
        }

    @app.get("/api/metrics")
    async def metrics_json() -> dict:
        return orch().metrics.snapshot()

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics_prometheus() -> str:
        return orch().metrics.prometheus()

    @app.get("/api/departments")
    async def departments() -> list[dict]:
        return orch().describe()["departments"]

    @app.get("/api/agents")
    async def agents(department: str | None = None, skill: str | None = None, q: str | None = None) -> list[dict]:
        items = orch().agents.all()
        if department:
            items = [a for a in items if a.department == department]
        if skill:
            items = [a for a in items if skill in a.skills]
        if q:
            needle = q.lower()
            items = [a for a in items if needle in a.name.lower() or needle in a.description.lower()]
        return [
            {"id": a.id, "name": a.name, "department": a.department,
             "description": a.description, "primary_skills": list(a.primary_skills)}
            for a in items
        ]

    @app.get("/api/skills")
    async def skills(department: str | None = None) -> list[dict]:
        rows = orch().skills.describe()
        return [r for r in rows if not department or r["department"] == department]

    @app.get("/api/tools")
    async def tools() -> list[dict]:
        return orch().tools.schemas()

    @app.get("/api/integrations")
    async def integrations() -> dict:
        o = orch()
        return {
            "integrations": o.integrations.snapshot(),
            "connected": o.integrations.connected_keys(),
            "code_exec_enabled": o.settings.allow_code_exec,
        }

    @app.post("/api/integrations/{key}/verify")
    async def verify_integration(key: str) -> dict:
        try:
            return await orch().integrations.verify(key)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown integration '{key}'") from None

    @app.post("/api/integrations/verify")
    async def verify_all_integrations() -> dict:
        return {"integrations": await orch().integrations.verify_all()}

    @app.get("/api/tool-calls")
    async def tool_calls(run_id: str | None = None, limit: int = 100) -> list[dict]:
        return orch().store.tool_calls(run_id=run_id, limit=limit)

    @app.get("/api/projects")
    async def list_projects(limit: int = 50) -> list[dict]:
        return orch().list_projects(limit=limit)

    @app.post("/api/projects", status_code=201)
    async def create_project(req: ProjectRequest, request: Request) -> dict:
        identity_here = getattr(request.state, "identity", None)
        project = orch().create_project(req.name, req.meta)
        if identity_here is not None and identity_here.user_id:
            app.state.store.add_project_member(project["id"], identity_here.user_id, "owner",
                                               auth_core.utcnow())
        if req.token_quota:
            app.state.store.set_project_quota(project["id"], req.token_quota)
        if req.workspace_id:
            app.state.store._conn.execute(
                "UPDATE projects SET workspace_id=? WHERE id=?", (req.workspace_id, project["id"])
            )
            app.state.store._conn.commit()
            project["workspace_id"] = req.workspace_id
        return project

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str, request: Request) -> dict:
        identity = getattr(request.state, "identity", None)
        if identity is not None:
            access.ensure(identity, app.state.store, project_id, "read")
        project = orch().store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    @app.get("/api/projects/{project_id}/members")
    async def project_members(project_id: str, request: Request) -> list[dict]:
        identity = getattr(request.state, "identity", None)
        if identity is not None:
            access.ensure(identity, app.state.store, project_id, "read")
        return app.state.store.list_project_members(project_id)

    @app.post("/api/projects/{project_id}/members", status_code=201)
    async def add_project_member(project_id: str, req: ProjectMemberRequest, request: Request) -> dict:
        identity = getattr(request.state, "identity", None)
        if identity is not None:
            access.ensure(identity, app.state.store, project_id, "manage")
        user = app.state.store.get_user_by_email(req.email)
        if user is None:
            raise HTTPException(status_code=404, detail="no account with that email")
        if req.role not in auth_core.ROLE_RANK:
            raise HTTPException(status_code=400, detail="role must be viewer, member, admin or owner")
        app.state.store.add_project_member(project_id, user["id"], req.role, auth_core.utcnow())
        app.state.store.add_audit(getattr(identity, "email", None), "project.member_added",
                                  project_id, f"{req.email}={req.role}", None, auth_core.utcnow())
        return {"project_id": project_id, "user_id": user["id"], "role": req.role}

    @app.delete("/api/projects/{project_id}/members/{user_id}")
    async def remove_project_member(project_id: str, user_id: str, request: Request) -> dict:
        identity = getattr(request.state, "identity", None)
        if identity is not None:
            access.ensure(identity, app.state.store, project_id, "manage")
        return {"removed": app.state.store.remove_project_member(project_id, user_id)}

    @app.post("/api/projects/{project_id}/quota")
    async def set_project_quota(project_id: str, req: ProjectQuotaRequest, request: Request) -> dict:
        identity = getattr(request.state, "identity", None)
        if identity is not None:
            access.ensure(identity, app.state.store, project_id, "manage")
        app.state.store.set_project_quota(project_id, req.tokens)
        return {"project_id": project_id, "token_quota": req.tokens,
                "tokens_used": app.state.store.project_usage_tokens(project_id)}

    @app.get("/api/knowledge/search")
    async def knowledge_search(q: str, limit: int = 5, mode: str = "lexical") -> dict:
        o = orch()
        if mode == "semantic":
            from ..core.embeddings import EmbeddingProvider

            provider = EmbeddingProvider(
                o.settings.embedding_api_key, model=o.settings.embedding_model,
                base_url=o.settings.llm_base_url,
            )
            if not provider.enabled:
                raise HTTPException(
                    status_code=409,
                    detail="semantic search needs OMNIFORGE_EMBEDDING_API_KEY (or OPENAI_API_KEY)",
                )
            from ..core.embeddings import embed_documents

            try:
                await embed_documents(o.knowledge, provider, o.store.documents())
                vector = (await provider.embed([q]))[0]
            except Exception as exc:  # noqa: BLE001 - provider problems are user-facing
                raise HTTPException(status_code=502, detail=f"embedding failed: {exc}") from None
            hits = o.knowledge.search_hybrid(q, vector, limit=limit)
            return {"query": q, "mode": "semantic", "hits": hits,
                    "stats": o.knowledge.stats()}
        return {"query": q, "mode": "lexical",
                "hits": o.knowledge.search(q, limit=limit), "stats": o.knowledge.stats()}

    @app.get("/api/db")
    async def db_status() -> dict:
        from ..core.pg import PostgresUnavailable, store_from_url

        settings_now = app.state.settings
        url = settings_now.db_url or f"sqlite:///{settings_now.db_path}"
        info = {"url": url, "driver": "sqlite" if url.startswith("sqlite") else "postgres"}
        try:
            store_from_url(url)
            info["ok"] = True
        except PostgresUnavailable as exc:
            info["ok"] = False
            info["error"] = str(exc)
        info["active"] = "sqlite"
        return info

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs", status_code=201)
    async def create_run(req: RunRequest, request: Request) -> dict:
        meta = dict(req.meta or {})
        if req.allow_destructive:
            meta["allow_destructive"] = True
        identity_for_run = getattr(request.state, "identity", None)
        if req.project_id and identity_for_run is not None:
            access.ensure(identity_for_run, app.state.store, req.project_id, "run")
        if req.project_id:
            project = app.state.store.get_project(req.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            quota = int(project.get("token_quota") or 0)
            if quota and app.state.store.project_usage_tokens(req.project_id) >= quota:
                raise HTTPException(status_code=402,
                                    detail="project token quota exhausted")
        if identity_for_run is not None and identity_for_run.user_id:
            meta["user_id"] = identity_for_run.user_id
        try:
            app.state.billing.check_quota(meta.get("user_id"))
        except PermissionError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from None
        try:
            run = await orch().submit(
                req.goal, auto_approve=req.auto_approve, meta=meta, project_id=req.project_id,
                priority=req.priority, idempotency_key=req.idempotency_key,
                workspace_id=req.workspace_id,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        identity = getattr(request.state, "identity", None)
        if identity is not None and identity.email:
            app.state.store.add_audit(identity.email, "run.created", run.id, req.goal[:120],
                                      request.client.host if request.client else None,
                                      auth_core.utcnow())
        return run.to_dict()

    @app.get("/api/runs")
    async def list_runs(
        limit: int = 50, status: str | None = None, project_id: str | None = None
    ) -> list[dict]:
        if status or project_id:
            return orch().store.list_runs(limit, status, project_id)
        return orch().list_runs(limit=limit)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, events: bool = False) -> dict:
        data = orch().load_run(run_id)
        if data is None:
            raise HTTPException(status_code=404, detail="run not found")
        if not events:
            data.pop("events", None)
        return data

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, limit: int = 500) -> list[dict]:
        o = orch()
        if o.get_run(run_id) is None and o.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        live = o.bus.history(run_id=run_id, limit=limit)
        return live or o.store.events(run_id, limit=limit)

    @app.get("/api/runs/{run_id}/artifacts")
    async def artifacts(run_id: str) -> list[dict]:
        o = orch()
        folder = Path(o.artifacts.root) / run_id
        if not folder.exists():
            if o.store.get_run(run_id) is None:
                raise HTTPException(status_code=404, detail="run not found")
            return []
        return [
            {"name": p.name, "bytes": p.stat().st_size}
            for p in sorted(folder.iterdir()) if p.is_file()
        ]

    @app.get("/api/runs/{run_id}/artifacts/{name}")
    async def artifact(run_id: str, name: str) -> FileResponse:
        o = orch()
        path = Path(o.artifacts.root) / run_id / name
        if not path.is_file() or path.parent.name != run_id:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path, media_type="text/markdown", filename=name)

    @app.post("/api/runs/{run_id}/replay", status_code=201)
    async def replay(run_id: str) -> dict:
        o = orch()
        data = o.load_run(run_id)
        if data is None:
            raise HTTPException(status_code=404, detail="run not found")
        run = await o.submit(
            data["goal"], auto_approve=True,
            meta={"replay_of": run_id},
        )
        return run.to_dict()

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict:
        try:
            run = await orch().cancel(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="run not found") from None
        return run.to_dict()

    @app.get("/api/runs/{run_id}/approvals")
    async def approvals(run_id: str) -> list[dict]:
        return orch().pending_approvals(run_id)

    @app.post("/api/runs/{run_id}/approvals/{task_id}")
    async def decide(run_id: str, task_id: str, req: ApprovalRequest) -> dict:
        ok = orch().approve(run_id, task_id, req.approved, req.note)
        if not ok:
            raise HTTPException(status_code=409, detail="no pending approval for that task")
        return {"run_id": run_id, "task_id": task_id, "approved": req.approved}

    # ------------------------------------------------------------- websocket
    @app.websocket("/ws/runs/{run_id}")
    async def ws_run(websocket: WebSocket, run_id: str) -> None:
        cfg = app.state.settings
        token = websocket.query_params.get("token")
        identity = resolve_identity(cfg, app.state.store, None, token)
        if cfg.auth_enabled:
            if identity is None or not allows(identity.role, "read"):
                await websocket.close(code=4401)
                return
        elif cfg.api_key and not identity:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        o = orch()
        sub = o.bus.subscribe(run_id=run_id)
        try:
            for event in o.bus.history(run_id=run_id, limit=200):
                await websocket.send_json({"kind": "history", **event})
            while True:
                event = await sub.next(timeout=20.0)
                if event is None:
                    await websocket.send_json({"kind": "ping"})
                    continue
                await websocket.send_json({"kind": "event", **event.to_dict()})
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            sub.close()

    identity_routes.register(app, orch=orch)
    automation_routes.register(app, orch=orch)
    content_routes.register(app, orch=orch)
    platform_routes.register(app, orch=orch)

    # ------------------------------------------------------------- dashboard
    @app.get("/i18n.js", response_class=PlainTextResponse)
    async def i18n_js() -> Any:
        asset = WEB_DIR / "i18n.js"
        if not asset.exists():
            raise HTTPException(status_code=404, detail="i18n bundle missing")
        return PlainTextResponse(asset.read_text(encoding="utf-8"), media_type="application/javascript")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Any:
        page = WEB_DIR / "index.html"
        if not page.exists():
            return JSONResponse({"detail": "dashboard not found; use the JSON API"}, status_code=404)
        return FileResponse(page)

    return app


app = create_app()

"""Workflows, schedules, inbound webhooks and notification history."""
from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ..core.auth import Identity, allows
from ..core.scheduler import Scheduler, compute_next, utcnow


class WorkflowRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    goal_template: str = Field(min_length=3, max_length=2000)
    plan: dict[str, Any] | None = None
    params: list[str] = []


class WorkflowFromRunRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class WorkflowRunRequest(BaseModel):
    params: dict[str, Any] = {}


class ScheduleRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    goal: str = ""
    workflow_id: str | None = None
    kind: str = Field(default="interval", pattern="^(interval|daily)$")
    job: str = Field(default="run", pattern="^(run|backup)$")
    every_seconds: int = Field(default=3600, ge=30)
    at_hour: int | None = Field(default=None, ge=0, le=23)
    at_minute: int | None = Field(default=None, ge=0, le=59)
    project_id: str | None = None


class HookRequest(BaseModel):
    workflow_id: str


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

    def scheduler() -> Scheduler | None:
        return getattr(app.state, "scheduler", None)

    # ----------------------------------------------------------- workflows
    @app.get("/api/workflows")
    async def list_workflows(request: Request, limit: int = 50) -> list[dict]:
        _require(_identity(request), "read")
        return store().list_workflows(limit=limit)

    @app.post("/api/workflows", status_code=201)
    async def create_workflow(req: WorkflowRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        plan = req.plan
        if plan is None:
            raise HTTPException(status_code=400, detail="a workflow needs a plan (or use /from-run)")
        if not plan.get("tasks"):
            raise HTTPException(status_code=400, detail="plan has no tasks")
        record = store().save_workflow(
            req.name, req.goal_template, json.dumps(plan), json.dumps(req.params),
            utcnow(), identity.user_id,
        )
        store().add_audit(identity.email, "workflow.created", record["id"], req.name, None, utcnow())
        return {**record, "goal_template": req.goal_template, "params": req.params}

    @app.post("/api/workflows/from-run/{run_id}", status_code=201)
    async def workflow_from_run(run_id: str, req: WorkflowFromRunRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        checkpoint = store().latest_checkpoint(run_id)
        if checkpoint is None:
            record = store().get_run(run_id)
            if record is None:
                raise HTTPException(status_code=404, detail="run not found")
            raise HTTPException(status_code=409, detail="that run has no plan checkpoint yet")
        plan = json.loads(checkpoint["plan"])
        record = store().save_workflow(
            req.name, plan.get("goal", req.name), json.dumps(plan), "[]", utcnow(),
            identity.user_id,
        )
        store().add_audit(identity.email, "workflow.created", record["id"], f"from run {run_id}",
                          None, utcnow())
        return {**record, "source_run": run_id, "tasks": len(plan.get("tasks", []))}

    @app.get("/api/workflows/{workflow_id}")
    async def get_workflow(workflow_id: str, request: Request) -> dict:
        _require(_identity(request), "read")
        workflow = store().get_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        workflow["plan"] = json.loads(workflow["plan"])
        workflow["params"] = json.loads(workflow["params"])
        return workflow

    @app.delete("/api/workflows/{workflow_id}")
    async def delete_workflow(workflow_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        deleted = store().delete_workflow(workflow_id)
        store().add_audit(identity.email, "workflow.deleted", workflow_id, None, None, utcnow())
        return {"deleted": deleted}

    @app.post("/api/workflows/{workflow_id}/run", status_code=201)
    async def run_workflow(workflow_id: str, req: WorkflowRunRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        workflow = store().get_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        try:
            run = await orch().run_workflow(workflow, req.params)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        store().add_audit(identity.email, "workflow.run", workflow_id, run.id, None, utcnow())
        return run.to_dict()

    # --------------------------------------------------------------- hooks
    @app.get("/api/hooks")
    async def list_hooks(request: Request) -> list[dict]:
        _require(_identity(request), "read")
        return store().list_hooks()

    @app.post("/api/hooks", status_code=201)
    async def create_hook(req: HookRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        if store().get_workflow(req.workflow_id) is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        token = "ofh_" + secrets.token_urlsafe(24)
        hook = store().create_hook(req.workflow_id, token, utcnow())
        store().add_audit(identity.email, "hook.created", hook["id"], req.workflow_id, None, utcnow())
        hook["url"] = f"/api/hooks/{token}/trigger"
        hook["warning"] = "anyone with this URL can start the workflow; treat it as a secret"
        return hook

    @app.delete("/api/hooks/{hook_id}")
    async def delete_hook(hook_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        deleted = store().delete_hook(hook_id)
        store().add_audit(identity.email, "hook.deleted", hook_id, None, None, utcnow())
        return {"deleted": deleted}

    @app.post("/api/hooks/{token}/trigger", status_code=202)
    async def trigger_hook(token: str, request: Request) -> dict:
        """Inbound webhook. The token is the credential, so no session is required."""
        hook = store().get_hook_by_token(token)
        if hook is None:
            raise HTTPException(status_code=404, detail="unknown or disabled hook")
        workflow = store().get_workflow(hook["workflow_id"])
        if workflow is None:
            raise HTTPException(status_code=409, detail="the workflow behind this hook is gone")
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - body is optional
            payload = {}
        params = payload.get("params") if isinstance(payload, dict) and "params" in payload else payload
        if not isinstance(params, dict):
            params = {}
        try:
            run = await orch().run_workflow(workflow, params, meta={"hook_id": hook["id"]})
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        store().touch_hook(hook["id"], utcnow())
        store().add_audit(f"hook:{hook['id']}", "hook.triggered", hook["workflow_id"], run.id,
                          request.client.host if request.client else None, utcnow())
        return {"run_id": run.id, "workflow_id": hook["workflow_id"], "params": params}

    # ----------------------------------------------------------- schedules
    @app.get("/api/schedules")
    async def list_schedules(request: Request) -> list[dict]:
        _require(_identity(request), "read")
        return store().list_schedules()

    @app.post("/api/schedules", status_code=201)
    async def create_schedule(req: ScheduleRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        if req.job == "run" and not req.goal and not req.workflow_id:
            raise HTTPException(status_code=400, detail="a schedule needs a goal or a workflow")
        if req.workflow_id and store().get_workflow(req.workflow_id) is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        data = {
            "name": req.name, "kind": req.kind, "every_seconds": req.every_seconds,
            "at_hour": req.at_hour, "at_minute": req.at_minute, "goal": req.goal,
            "job": req.job,
            "workflow_id": req.workflow_id, "project_id": req.project_id,
            "created_at": utcnow(), "owner_id": identity.user_id,
        }
        data["next_run_at"] = compute_next(data)
        schedule = store().create_schedule(data)
        store().add_audit(identity.email, "schedule.created", schedule["id"], req.name, None, utcnow())
        return schedule

    @app.post("/api/schedules/{schedule_id}/enable")
    async def enable_schedule(schedule_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        store().set_schedule_enabled(schedule_id, True)
        schedule = store().get_schedule(schedule_id) or {}
        return {"enabled": bool(schedule.get("enabled"))}

    @app.post("/api/schedules/{schedule_id}/disable")
    async def disable_schedule(schedule_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        store().set_schedule_enabled(schedule_id, False)
        schedule = store().get_schedule(schedule_id) or {}
        return {"enabled": bool(schedule.get("enabled"))}

    @app.post("/api/schedules/{schedule_id}/run", status_code=202)
    async def run_schedule_now(schedule_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        schedule = store().get_schedule(schedule_id)
        if schedule is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        engine = scheduler() or Scheduler(orch(), store(), tick_seconds=0)
        result = await engine.fire(schedule)
        store().add_audit(identity.email, "schedule.fired_manually", schedule_id,
                          result.get("run_id"), None, utcnow())
        return result

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        deleted = store().delete_schedule(schedule_id)
        store().add_audit(identity.email, "schedule.deleted", schedule_id, None, None, utcnow())
        return {"deleted": deleted}

    @app.post("/api/schedules/tick", status_code=200)
    async def tick(request: Request, now: str | None = None) -> dict:
        """Run one scheduler pass now (useful for cron-less deployments and tests).

        `now` lets an operator replay a pass at a specific ISO timestamp.
        """
        identity = _identity(request)
        _require(identity, "manage")
        engine = scheduler() or Scheduler(orch(), store(), tick_seconds=0)
        fired = await engine.tick_once(now=now)
        return {"fired": fired, "count": len(fired)}

    # ------------------------------------------------------- notifications
    @app.get("/api/notifications")
    async def notifications(request: Request, limit: int = 50) -> list[dict]:
        _require(_identity(request), "read")
        return store().list_notifications(limit=limit)

    @app.post("/api/notifications/test")
    async def notification_test(request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        notifier = getattr(app.state, "notifier", None)
        if notifier is None or not notifier.enabled:
            raise HTTPException(status_code=409,
                                detail="no webhook configured (OMNIFORGE_WEBHOOK_URL)")
        result = await notifier.send({"event": "test", "message": "OmniForge webhook test",
                                      "actor": identity.email})
        store().add_notification(None, "test", notifier.url, bool(result.get("ok")),
                                 int(result.get("attempts") or 0), result.get("error"), utcnow())
        return result

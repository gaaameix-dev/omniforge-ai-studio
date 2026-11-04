"""Identity, workspace, secret, control and monitoring routes.

Kept separate from app.py so the run/agent surface stays readable. Every route
here is a real database operation - nothing is a placeholder.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ..core import auth
from ..core.auth import AuthError, Identity, allows
from ..core.backup import verify_backup

log = logging.getLogger("omniforge.api.identity")


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
    code: str = ""            # TOTP or backup code when MFA is enrolled


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=256)


class MfaCodeRequest(BaseModel):
    code: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class MemberRequest(BaseModel):
    email: str
    role: str = "member"


class KeyRequest(BaseModel):
    name: str = Field(default="api key", max_length=80)


class SecretRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1)


def _identity(request: Request) -> Identity:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return identity


def _require(identity: Identity, action: str) -> None:
    if not allows(identity.role, action):
        raise HTTPException(status_code=403, detail=f"role '{identity.role}' cannot {action}")


def register(app, *, orch) -> None:
    """Routes resolve app.state lazily so they work before lifespan startup too."""
    def store():
        return app.state.store

    def settings():
        return app.state.settings

    def vault():
        return app.state.vault

    # ------------------------------------------------------------------ auth
    @app.post("/api/auth/register", status_code=201)
    async def register_user(req: RegisterRequest) -> dict:
        if not settings().auth_enabled:
            raise HTTPException(status_code=400, detail="multi-user mode is disabled (OMNIFORGE_AUTH_ENABLED=false)")
        try:
            user = auth.register_user(store(), req.email, req.password, req.name)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        token, _ = auth.login(store(), settings(), req.email, req.password)
        return {"user": {k: user[k] for k in ("id", "email", "name", "role")}, "token": token}

    @app.post("/api/auth/login")
    async def login(req: LoginRequest) -> dict:
        if not settings().auth_enabled:
            raise HTTPException(status_code=400, detail="multi-user mode is disabled")
        try:
            user = auth.authenticate(store(), req.email, req.password)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        if auth.second_factor_required(store(), user):
            if not req.code:
                raise HTTPException(
                    status_code=401, detail="mfa_required",
                    headers={"X-MFA-Required": "1"},
                )
            if not auth.check_second_factor(store(), user, req.code):
                store().add_audit(user["email"], "auth.mfa_failed", user["id"], None, None,
                                  auth.utcnow())
                raise HTTPException(status_code=401, detail="invalid second factor")
        token = auth.issue_session(store(), settings(), user,
                                   "session issued (mfa)" if req.code else "session issued")
        return {"user": {k: user[k] for k in ("id", "email", "name", "role")}, "token": token,
                "mfa": auth.second_factor_required(store(), user)}

    # ------------------------------------------------------ password reset
    @app.post("/api/auth/forgot")
    async def forgot_password(req: ForgotRequest) -> dict:
        if not settings().auth_enabled:
            raise HTTPException(status_code=400, detail="multi-user mode is disabled")
        token, user = auth.issue_reset_token(store(), settings(), req.email)
        mailer = app.state.mailer
        delivered = False
        if token and user:
            link = f"{settings().public_base_url.rstrip('/')}/#reset={token}"
            delivered = await mailer.send(
                user["email"],
                "Reset your OmniForge password",
                f"A password reset was requested for this account.\n\n"
                f"Open this link within {settings().reset_ttl_minutes} minutes:\n{link}\n\n"
                "If you did not ask for this, ignore the message; nothing changed.",
            )
        # identical answer either way: no account enumeration, so `delivered`
        # is deliberately not exposed here (it is logged instead).
        if token and not delivered:
            log.warning("reset mail could not be delivered for %s (backend=%s)",
                        user["email"], mailer.backend)
        return {"sent": True, "delivery": mailer.backend}

    @app.post("/api/auth/reset")
    async def reset_password(req: ResetRequest) -> dict:
        try:
            user = auth.reset_password(store(), req.token, req.new_password)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        return {"reset": True, "email": user["email"],
                "note": "all existing sessions were invalidated"}

    # ---------------------------------------------------------------- MFA
    @app.get("/api/auth/mfa")
    async def mfa_status(request: Request) -> dict:
        identity = _identity(request)
        if not identity.user_id:
            return {"enrolled": False, "backup_codes_left": 0}
        record = store().get_mfa(identity.user_id)
        return {
            "enrolled": bool(record and record["enabled"]),
            "backup_codes_left": store().count_unused_backup_codes(identity.user_id),
        }

    @app.post("/api/auth/mfa/setup")
    async def mfa_setup(request: Request) -> dict:
        from ..core import mfa as mfa_core

        identity = _identity(request)
        if not identity.user_id:
            raise HTTPException(status_code=400, detail="MFA needs a user account")
        secret = mfa_core.new_secret()
        store().set_mfa_secret(identity.user_id, secret, auth.utcnow(), enabled=False)
        store().add_audit(identity.email, "auth.mfa_setup_started", identity.user_id, None, None,
                          auth.utcnow())
        return {
            "secret": secret,
            "otpauth_url": mfa_core.provisioning_uri(secret, identity.email or identity.user_id),
            "digits": mfa_core.DIGITS,
            "period": mfa_core.STEP,
            "next": "POST /api/auth/mfa/enable with a code from your authenticator",
        }

    @app.post("/api/auth/mfa/enable")
    async def mfa_enable(req: MfaCodeRequest, request: Request) -> dict:
        from ..core import mfa as mfa_core

        identity = _identity(request)
        record = store().get_mfa(identity.user_id or "")
        if not record:
            raise HTTPException(status_code=409, detail="run /api/auth/mfa/setup first")
        if not mfa_core.verify(record["secret"], req.code):
            raise HTTPException(status_code=400, detail="that code is not valid")
        store().set_mfa_enabled(identity.user_id, True)
        codes = mfa_core.new_backup_codes()
        store().add_backup_codes(identity.user_id, [mfa_core.hash_code(c) for c in codes])
        store().add_audit(identity.email, "auth.mfa_enabled", identity.user_id, None, None,
                          auth.utcnow())
        return {"enabled": True, "backup_codes": codes,
                "warning": "store these codes now; each works once and they are not shown again"}

    @app.post("/api/auth/mfa/backup-codes")
    async def mfa_backup_codes(req: MfaCodeRequest, request: Request) -> dict:
        from ..core import mfa as mfa_core

        identity = _identity(request)
        record = store().get_mfa(identity.user_id or "")
        if not record or not record["enabled"]:
            raise HTTPException(status_code=409, detail="MFA is not enabled")
        if not auth.check_second_factor(store(), {"id": identity.user_id, "email": identity.email},
                                        req.code):
            raise HTTPException(status_code=403, detail="a valid code is required")
        codes = mfa_core.new_backup_codes()
        store().add_backup_codes(identity.user_id, [mfa_core.hash_code(c) for c in codes])
        return {"backup_codes": codes, "warning": "previous codes are now invalid"}

    @app.post("/api/auth/mfa/disable")
    async def mfa_disable(req: PasswordRequest, request: Request) -> dict:
        identity = _identity(request)
        user = store().get_user(identity.user_id or "")
        if not user or not auth.verify_password(req.current_password, user["password_hash"]):
            raise HTTPException(status_code=403, detail="current password is wrong")
        store().delete_mfa(user["id"])
        store().add_audit(user["email"], "auth.mfa_disabled", user["id"], None, None, auth.utcnow())
        return {"enabled": False}

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> dict:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        return {"logged_out": auth.logout(store(), token) if token else False}

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict:
        identity = getattr(request.state, "identity", None)
        if identity is None:
            if not settings().auth_enabled:
                return {"identity": None, "user": None, "auth_enabled": False}
            raise HTTPException(status_code=401, detail="authentication required")
        user = store().get_user(identity.user_id) if identity.user_id else None
        return {
            "identity": identity.to_dict(),
            "user": ({k: user[k] for k in ("id", "email", "name", "role", "created_at")}
                     if user else None),
            "auth_enabled": settings().auth_enabled,
        }

    @app.post("/api/auth/password")
    async def change_password(req: PasswordRequest, request: Request) -> dict:
        identity = _identity(request)
        if not identity.user_id:
            raise HTTPException(status_code=400, detail="session accounts only")
        user = store().get_user(identity.user_id)
        if not user or not auth.verify_password(req.current_password, user["password_hash"]):
            raise HTTPException(status_code=403, detail="current password is wrong")
        store().set_user_password(user["id"], auth.hash_password(req.new_password))
        store().add_audit(user["email"], "auth.password_changed", user["id"], None, None, auth.utcnow())
        return {"changed": True}

    @app.delete("/api/auth/me")
    async def delete_account(request: Request) -> dict:
        """Data deletion: removes the account, its sessions and its API keys."""
        identity = _identity(request)
        if not identity.user_id:
            raise HTTPException(status_code=400, detail="only session accounts can be deleted")
        if store().count_users() <= 1:
            raise HTTPException(status_code=409, detail="refusing to delete the last account")
        store().add_audit(identity.email, "user.deleted", identity.user_id, "self-service", None,
                        auth.utcnow())
        deleted = store().delete_user(identity.user_id)
        return {"deleted": deleted}

    # ------------------------------------------------------------ workspaces
    @app.get("/api/workspaces")
    async def list_workspaces(request: Request) -> list[dict]:
        identity = _identity(request)
        if identity.kind in {"legacy", "anonymous"} or not identity.user_id:
            return store().list_workspaces()
        return store().list_workspaces(identity.user_id)

    @app.post("/api/workspaces", status_code=201)
    async def create_workspace(req: WorkspaceRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "run")
        owner = identity.user_id or "legacy"
        workspace = store().create_workspace(req.name, owner, auth.utcnow())
        store().add_audit(identity.email or owner, "workspace.created", workspace["id"], req.name,
                        None, auth.utcnow())
        return workspace

    @app.get("/api/workspaces/{workspace_id}")
    async def get_workspace(workspace_id: str, request: Request) -> dict:
        identity = _identity(request)
        workspace = store().get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        if identity.user_id and identity.kind == "session":
            role = store().member_role(workspace_id, identity.user_id)
            if role is None:
                raise HTTPException(status_code=403, detail="not a member of this workspace")
        workspace["members"] = store().list_members(workspace_id)
        workspace["projects"] = [p for p in store().list_projects() if p.get("workspace_id") == workspace_id]
        return workspace

    @app.post("/api/workspaces/{workspace_id}/members", status_code=201)
    async def add_member(workspace_id: str, req: MemberRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        workspace = store().get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        user = store().get_user_by_email(req.email)
        if user is None:
            raise HTTPException(status_code=404, detail="no account with that email")
        if req.role not in auth.ROLE_RANK or req.role == "owner":
            raise HTTPException(status_code=400, detail="role must be viewer, member or admin")
        store().add_member(workspace_id, user["id"], req.role, auth.utcnow())
        store().add_audit(identity.email or identity.user_id, "workspace.member_added", workspace_id,
                        f"{user['email']}={req.role}", None, auth.utcnow())
        return {"workspace_id": workspace_id, "user_id": user["id"], "role": req.role}

    @app.delete("/api/workspaces/{workspace_id}/members/{user_id}")
    async def remove_member(workspace_id: str, user_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        removed = store().remove_member(workspace_id, user_id)
        store().add_audit(identity.email or identity.user_id, "workspace.member_removed", workspace_id,
                        user_id, None, auth.utcnow())
        return {"removed": removed}

    # -------------------------------------------------------------- api keys
    @app.get("/api/keys")
    async def list_keys(request: Request) -> list[dict]:
        identity = _identity(request)
        if not identity.user_id:
            return []
        return store().list_api_keys(identity.user_id)

    @app.post("/api/keys", status_code=201)
    async def create_key(req: KeyRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        if not identity.user_id:
            raise HTTPException(status_code=400, detail="API keys need a user account")
        token, record = auth.issue_api_key(store(), identity.user_id, req.name)
        # the plaintext token is shown exactly once, never stored
        return {"key": token, "record": record,
                "warning": "store this token now; it cannot be retrieved again"}

    @app.delete("/api/keys/{key_id}")
    async def revoke_key(key_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        revoked = store().revoke_api_key(key_id, identity.user_id)
        store().add_audit(identity.email or identity.user_id, "api_key.revoked", key_id, None, None,
                        auth.utcnow())
        return {"revoked": revoked}

    # ---------------------------------------------------------------- audit
    @app.get("/api/audit")
    async def audit_log(request: Request, limit: int = 100, action: str | None = None) -> list[dict]:
        identity = _identity(request)
        _require(identity, "manage")
        return store().list_audit(limit=limit, action=action)

    # -------------------------------------------------------------- secrets
    @app.get("/api/secrets")
    async def list_secrets(request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        return {
            "vault": vault().status(),
            "secrets": [
                {**row, "configured": True} for row in vault().names(store())
            ],
        }

    @app.post("/api/secrets", status_code=201)
    async def put_secret(req: SecretRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        try:
            vault().put(store(), req.name, req.value, identity.user_id, auth.utcnow())
        except Exception as exc:  # noqa: BLE001 - vault problems are user-facing
            raise HTTPException(status_code=503, detail=str(exc)) from None
        store().add_audit(identity.email or identity.user_id, "secret.written", req.name, None, None,
                        auth.utcnow())
        return {"name": req.name, "stored": True}

    @app.delete("/api/secrets/{name}")
    async def delete_secret(name: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        deleted = vault().delete(store(), name)
        store().add_audit(identity.email or identity.user_id, "secret.deleted", name, None, None,
                        auth.utcnow())
        return {"deleted": deleted}

    # -------------------------------------------------------------- control
    @app.get("/api/control")
    async def control_state() -> dict:
        return orch().control_state()

    @app.post("/api/control/pause")
    async def pause(request: Request) -> dict:
        _require(_identity(request), "manage")
        store().add_audit(_identity(request).email, "control.pause", None, None, None, auth.utcnow())
        return orch().pause()

    @app.post("/api/control/unpause")
    async def unpause(request: Request) -> dict:
        _require(_identity(request), "manage")
        store().add_audit(_identity(request).email, "control.unpause", None, None, None, auth.utcnow())
        return orch().unpause()

    @app.post("/api/control/stop")
    async def stop(request: Request) -> dict:
        _require(_identity(request), "manage")
        return await orch().emergency_stop(f"emergency stop by {_identity(request).email or 'operator'}")

    @app.post("/api/control/clear")
    async def clear(request: Request) -> dict:
        _require(_identity(request), "manage")
        return orch().clear_stop()

    # --------------------------------------------------------------- resume
    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume_run(run_id: str) -> dict:
        try:
            run = await orch().resume(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return run.to_dict()

    @app.get("/api/runs/{run_id}/checkpoint")
    async def checkpoint(run_id: str) -> dict:
        record = store().latest_checkpoint(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no checkpoint for this run")
        return {"run_id": run_id, "seq": record["seq"], "created_at": record["created_at"],
                "plan": record["plan"]}

    # -------------------------------------------------------------- monitor
    @app.get("/api/monitor")
    async def monitor() -> dict:
        o = orch()
        active = []
        task_counts: dict[str, int] = {}
        agents_busy: set[str] = set()
        for run in o.runs.values():
            if run.plan is None:
                continue
            if run.status.value in {"planning", "running", "awaiting_approval"}:
                active.append({"id": run.id, "goal": run.goal, "status": run.status.value,
                               "tokens": run.tokens})
            for task in run.plan.tasks:
                task_counts[task.status.value] = task_counts.get(task.status.value, 0) + 1
                if task.status.value == "running" and task.agent_id:
                    agents_busy.add(task.agent_id)
        metrics = o.metrics.snapshot()
        calls = o.store.tool_calls(limit=200)
        failures = [c for c in calls if not c["ok"]]
        return {
            "control": o.control_state(),
            "active_runs": active,
            "tasks": task_counts,
            "agents": {
                "total": len(o.agents),
                "busy": len(agents_busy),
                "idle": len(o.agents) - len(agents_busy),
                "busy_ids": sorted(agents_busy)[:20],
            },
            "tokens": metrics["counters"].get("events.run.finished", 0) and o.store.stats()["tokens"],
            "timings": metrics["timings"],
            "counters": metrics["counters"],
            "tool_calls": {"total": len(calls), "failed": len(failures),
                           "recent_failures": failures[:5]},
            "integrations": [{"key": i["key"], "status": i["status"]} for i in o.integrations.snapshot()],
            "storage": o.store.stats(),
            "knowledge": o.knowledge.stats(),
        }

    # -------------------------------------------------------------- backups
    @app.post("/api/backups/verify")
    async def verify_backup_route(request: Request, path: str) -> dict:
        _require(_identity(request), "manage")
        try:
            return verify_backup(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    # ------------------------------------------------------------- retention
    @app.get("/api/retention")
    async def retention(request: Request) -> dict:
        _require(_identity(request), "read")
        stats: dict[str, Any] = store().stats()
        return {
            "storage": stats,
            "policy": {
                "runs_retained": "unlimited by default; delete via API or store",
                "audit_retained": "unlimited; export via /api/audit",
                "sessions_purged": "expired sessions are removed on login",
            },
            "exports": ["/api/audit", "/api/runs", "/api/runs/{id}/artifacts"],
        }

    @app.post("/api/retention/purge")
    async def purge(request: Request) -> dict:
        _require(_identity(request), "manage")
        removed = store().purge_expired_sessions(auth.utcnow())
        store().add_audit(_identity(request).email, "retention.purged", None, f"sessions={removed}",
                        None, auth.utcnow())
        return {"sessions_removed": removed}

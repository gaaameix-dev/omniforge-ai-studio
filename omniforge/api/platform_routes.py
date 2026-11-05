"""Billing, usage and the plugin registry."""
from __future__ import annotations

import json
from typing import Any

from fastapi import File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from ..core import auth
from ..core import plugins as plugin_core
from ..core.auth import Identity, allows
from ..core.billing import Billing


class PluginUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=500)


class RegistryInstallRequest(BaseModel):
    index_url: str = Field(min_length=8, max_length=500)
    name: str = Field(min_length=1, max_length=120)
    version: str | None = None


class AlertRuleRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    metric: str
    comparator: str
    threshold: float
    channel: str = "email"
    target: str = ""
    cooldown_seconds: int = Field(default=3600, ge=0)


class DbSyncRequest(BaseModel):
    url: str = Field(min_length=8, max_length=500)


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class AlertRouteRequest(BaseModel):
    team: str = Field(min_length=2, max_length=80)
    channel: str
    target: str = Field(min_length=3, max_length=300)


# runtime-tunable settings; anything not listed stays environment-only
TUNABLE = {
    "llm_model": str, "debate_candidates": int, "prefer_generators": bool,
    "stream_tokens": bool, "review_enabled": bool, "max_parallel": int,
    "budget_tokens": int, "require_approval": bool, "moderation_enabled": bool,
    "alert_email": str, "worker_mode": bool,
}


class EstimateRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    tasks: int = Field(default=5, ge=1, le=40)
    model: str | None = None


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

    def billing() -> Billing:
        return app.state.billing

    # ------------------------------------------------------------- billing
    @app.get("/api/billing/pricing")
    async def pricing(request: Request) -> dict:
        _require(_identity(request), "read")
        return {
            "unit": "USD per 1M tokens",
            "models": billing().pricing(),
            "note": "override with OMNIFORGE_MODEL_PRICES=model:input:output",
        }

    @app.get("/api/billing/usage")
    async def usage(request: Request, user_id: str | None = None,
                    this_month: bool = True) -> dict:
        identity = _identity(request)
        _require(identity, "read")
        target = user_id if (identity.role in {"admin", "owner"} and user_id) else identity.user_id
        return billing().usage(target, this_month=this_month)

    @app.post("/api/billing/estimate")
    async def estimate(req: EstimateRequest, request: Request) -> dict:
        _require(_identity(request), "read")
        return billing().estimate(req.goal, tasks=req.tasks, model=req.model)

    @app.get("/api/billing/providers")
    async def provider_usage(request: Request) -> dict:
        _require(_identity(request), "read")
        return {
            "providers": billing().provider_usage(),
            "budgets": getattr(settings(), "provider_budgets", {}),
            "note": "budgets are USD per calendar month; 0/absent means unlimited",
        }

    @app.get("/api/billing/quota")
    async def quota(request: Request) -> dict:
        identity = _identity(request)
        remaining = billing().quota_remaining(identity.user_id)
        return {
            "quota_tokens": getattr(settings(), "user_monthly_tokens", 0),
            "remaining": remaining,
            "exhausted": remaining is not None and remaining <= 0,
        }

    # ------------------------------------------------------------- plugins
    @app.get("/api/plugins")
    async def list_plugins(request: Request) -> list[dict]:
        _require(_identity(request), "read")
        rows = store().list_plugins()
        for row in rows:
            row.pop("manifest", None)
        return rows

    @app.post("/api/plugins/install", status_code=201)
    async def install_plugin(request: Request, file: UploadFile = File(...)) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        payload = await file.read()
        try:
            record = plugin_core.install(store(), settings().data_dir, payload, settings(),
                                         identity.user_id, auth.utcnow())
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - corrupt zips land here
            raise HTTPException(status_code=400, detail=f"install failed: {exc}") from None
        store().add_audit(identity.email, "plugin.installed", record["id"],
                          f"{record['name']}@{record['version']} ({record['trust']})", None,
                          auth.utcnow())
        return record

    @app.post("/api/plugins/install-url", status_code=201)
    async def install_plugin_from_url(req: PluginUrlRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        try:
            record = await plugin_core.install_from_url(
                store(), settings().data_dir, req.url, settings(), identity.user_id, auth.utcnow()
            )
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        store().add_audit(identity.email, "plugin.installed", record["id"],
                          f"{record['name']} from {req.url}", None, auth.utcnow())
        return record

    @app.get("/api/plugins/registry")
    async def registry_index(request: Request, url: str) -> dict:
        _require(_identity(request), "read")
        from ..core.plugin_registry import fetch_index

        try:
            entries = await fetch_index(url)
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"index_url": url, "plugins": entries, "count": len(entries)}

    @app.post("/api/plugins/registry/install", status_code=201)
    async def registry_install(req: RegistryInstallRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        from ..core.plugin_registry import install_from_registry

        try:
            record = await install_from_registry(
                store(), settings().data_dir, req.index_url, req.name, settings(),
                identity.user_id, auth.utcnow(), version=req.version,
            )
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        store().add_audit(identity.email, "plugin.installed", record["id"],
                          f"{record['name']} from registry", None, auth.utcnow())
        return record

    @app.post("/api/plugins/{plugin_id}/enable")
    async def enable_plugin(plugin_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        try:
            result = plugin_core.activate(orch(), store(), plugin_id)
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        store().add_audit(identity.email, "plugin.enabled", plugin_id, None, None, auth.utcnow())
        return result

    @app.post("/api/plugins/{plugin_id}/disable")
    async def disable_plugin(plugin_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        try:
            result = plugin_core.deactivate(orch(), store(), plugin_id)
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        store().add_audit(identity.email, "plugin.disabled", plugin_id, None, None, auth.utcnow())
        return result

    @app.delete("/api/plugins/{plugin_id}")
    async def uninstall_plugin(plugin_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        try:
            plugin_core.deactivate(orch(), store(), plugin_id)
            result = plugin_core.uninstall(store(), plugin_id)
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        store().add_audit(identity.email, "plugin.uninstalled", plugin_id, None, None, auth.utcnow())
        return result

    # ---------------------------------------------------------- alert rules
    @app.get("/api/alert-rules")
    async def list_alert_rules(request: Request) -> dict:
        _require(_identity(request), "read")
        from ..core.alerts import CHANNELS, COMPARATORS, METRICS

        return {
            "rules": store().list_alert_rules(),
            "metrics": METRICS,
            "comparators": sorted(COMPARATORS),
            "channels": sorted(CHANNELS),
        }

    @app.post("/api/alert-rules", status_code=201)
    async def create_alert_rule(req: AlertRuleRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        engine = app.state.alerts
        try:
            rule = engine.create(**req.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        store().add_audit(identity.email, "alert_rule.created", rule["id"], req.name, None,
                          auth.utcnow())
        return rule

    @app.delete("/api/alert-rules/{rule_id}")
    async def delete_alert_rule(rule_id: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        deleted = store().delete_alert_rule(rule_id)
        store().add_audit(identity.email, "alert_rule.deleted", rule_id, None, None, auth.utcnow())
        return {"deleted": deleted}

    @app.post("/api/alert-rules/evaluate")
    async def evaluate_alerts(request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        fired = await app.state.alerts.evaluate(None)
        return {"fired": fired, "count": len(fired)}

    # ------------------------------------------------------- database mirror
    @app.post("/api/db/sync")
    async def db_sync(req: DbSyncRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        from ..core import pgmirror
        from ..core.pg import PostgresUnavailable

        try:
            result = pgmirror.sync(store(), req.url)
        except PostgresUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - connection problems are user-facing
            raise HTTPException(status_code=502, detail=f"sync failed: {exc}") from None
        store().add_audit(identity.email, "db.synced", req.url, str(result["copied"]), None,
                          auth.utcnow())
        return result

    @app.get("/api/db/verify")
    async def db_verify(request: Request, url: str) -> dict:
        _require(_identity(request), "manage")
        from ..core import pgmirror
        from ..core.pg import PostgresUnavailable

        try:
            return pgmirror.verify(url)
        except PostgresUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from None

    # ------------------------------------------------------------ settings
    @app.get("/api/settings")
    async def get_settings(request: Request) -> dict:
        _require(_identity(request), "read")
        current = settings()
        return {
            "tunable": sorted(TUNABLE),
            "values": {key: getattr(current, key, None) for key in TUNABLE},
            "overrides": store().get_settings(),
            "note": "overrides win over environment variables and apply immediately",
        }

    @app.patch("/api/settings")
    async def patch_settings(req: SettingsPatch, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        unknown = [key for key in req.values if key not in TUNABLE]
        if unknown:
            raise HTTPException(status_code=400, detail=f"not tunable: {unknown}")
        applied: dict[str, Any] = {}
        for key, raw in req.values.items():
            cast = TUNABLE[key]
            try:
                value = cast(raw) if cast is not bool else bool(raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"bad value for {key}") from None
            setattr(settings(), key, value)
            store().set_setting(key, json.dumps(value), auth.utcnow())
            applied[key] = value
        # the orchestrator shares the settings object, so the change is live
        if getattr(app.state, "orchestrator", None) is not None:
            app.state.orchestrator.settings = settings()
        store().add_audit(identity.email, "settings.updated", None, json.dumps(applied), None,
                          auth.utcnow())
        return {"applied": applied}

    @app.delete("/api/settings/{key}")
    async def reset_setting(key: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        removed = store().delete_setting(key)
        return {"removed": removed, "note": "restart to pick the environment value back up"}

    # -------------------------------------------------------- alert routes
    @app.get("/api/alert-routes")
    async def list_alert_routes(request: Request) -> dict:
        _require(_identity(request), "read")
        return {"routes": store().list_alert_routes()}

    @app.post("/api/alert-routes", status_code=201)
    async def set_alert_route(req: AlertRouteRequest, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        if req.channel not in ("email", "webhook"):
            raise HTTPException(status_code=400, detail="channel must be email or webhook")
        store().set_alert_route(req.team, req.channel, req.target, auth.utcnow())
        store().add_audit(identity.email, "alert_route.set", req.team, req.target, None,
                          auth.utcnow())
        return {"team": req.team, "channel": req.channel, "target": req.target}

    @app.delete("/api/alert-routes/{team}")
    async def delete_alert_route(team: str, request: Request) -> dict:
        identity = _identity(request)
        _require(identity, "manage")
        return {"removed": store().delete_alert_route(team)}

    # ------------------------------------------------------- marketplace
    @app.get("/api/plugins/index")
    async def plugin_index(request: Request, base_url: str = "") -> dict:
        _require(_identity(request), "read")
        return plugin_core.build_index(store(), settings().data_dir, settings(),
                                       base_url=base_url)

    @app.get("/api/plugins/{plugin_id}/export")
    async def plugin_export(plugin_id: str, request: Request) -> Response:
        _require(_identity(request), "read")
        try:
            payload = plugin_core.export_package(store(), settings().data_dir, plugin_id)
        except plugin_core.PluginError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return Response(content=payload, media_type="application/zip",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{plugin_id}.zip"'})

    # ---------------------------------------------------------- model status
    @app.get("/api/models")
    async def models(request: Request) -> dict:
        _require(_identity(request), "read")
        llm = orch().llm
        status: dict[str, Any] = {"backend": llm.name}
        if hasattr(llm, "status"):
            status.update(llm.status())
        else:
            status["providers"] = [llm.name]
            status["model"] = getattr(llm, "model", None)
        status["live"] = settings().live_mode
        return status

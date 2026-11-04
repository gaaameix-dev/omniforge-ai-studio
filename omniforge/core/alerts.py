"""Threshold alerts.

Rules are data: metric + comparator + threshold + channel + cooldown. They are
evaluated when a run finishes, so an operator can be told about failures, spend,
quota pressure or a spike in tool errors without watching a dashboard.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

METRICS = {
    "run_failed": "1 when the run failed, 0 otherwise",
    "run_tokens": "tokens spent by the run",
    "run_cost_usd": "cost of the run in USD",
    "quota_used_pct": "percent of the monthly token quota used",
    "tool_failure_rate": "share of recent tool calls that failed (0-1)",
}
COMPARATORS = {"gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
               "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b,
               "eq": lambda a, b: a == b}
CHANNELS = {"email", "webhook"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AlertRule:
    name: str
    metric: str
    comparator: str
    threshold: float
    channel: str = "email"
    target: str = ""
    cooldown_seconds: int = 3600
    enabled: bool = True
    id: str = field(default_factory=lambda: f"rule_{os.urandom(4).hex()}")
    last_fired_at: str | None = None
    created_at: str = field(default_factory=utcnow)


class AlertEngine:
    def __init__(self, store, settings, *, mailer=None, notifier=None, billing=None) -> None:
        self.store = store
        self.settings = settings
        self.mailer = mailer
        self.notifier = notifier
        self.billing = billing

    # ------------------------------------------------------------------ rules
    def create(self, **kwargs) -> dict:
        kwargs.setdefault("channel", "email")
        kwargs.setdefault("name", "rule")
        if kwargs.get("metric") not in METRICS:
            raise ValueError(f"metric must be one of {sorted(METRICS)}")
        if kwargs.get("comparator") not in COMPARATORS:
            raise ValueError(f"comparator must be one of {sorted(COMPARATORS)}")
        if kwargs.get("channel") not in CHANNELS:
            raise ValueError(f"channel must be one of {sorted(CHANNELS)}")
        rule = AlertRule(
            name=str(kwargs.get("name") or "rule"),
            metric=kwargs["metric"],
            comparator=kwargs["comparator"],
            threshold=float(kwargs["threshold"]),
            channel=kwargs.get("channel", "email"),
            target=str(kwargs.get("target") or ""),
            cooldown_seconds=(3600 if kwargs.get("cooldown_seconds") is None
                              else int(kwargs["cooldown_seconds"])),
            enabled=bool(kwargs.get("enabled", True)),
        )
        data = {
            "id": rule.id, "name": rule.name, "metric": rule.metric,
            "comparator": rule.comparator, "threshold": rule.threshold,
            "channel": rule.channel, "target": rule.target,
            "cooldown_seconds": rule.cooldown_seconds,
            "enabled": 1 if rule.enabled else 0, "created_at": rule.created_at,
        }
        self.store.add_alert_rule(data)
        return data

    def rules(self) -> list[dict]:
        return self.store.list_alert_rules()

    def delete(self, rule_id: str) -> int:
        return self.store.delete_alert_rule(rule_id)

    # --------------------------------------------------------------- metrics
    def measure(self, metric: str, run=None) -> float:
        if metric == "run_failed":
            return 1.0 if (run is not None and run.status.value in {"failed", "cancelled"}) else 0.0
        if metric == "run_tokens":
            return float(getattr(run, "tokens", 0) or 0)
        if metric == "run_cost_usd":
            return float(getattr(run, "cost_usd", 0.0) or 0.0)
        if metric == "quota_used_pct":
            quota = getattr(self.settings, "user_monthly_tokens", 0)
            if not quota or self.billing is None:
                return 0.0
            used = self.billing.usage(None)["tokens"]
            return round(100.0 * used / quota, 2)
        if metric == "tool_failure_rate":
            calls = self.store.tool_calls(limit=200)
            if not calls:
                return 0.0
            failed = sum(1 for call in calls if not call["ok"])
            return round(failed / len(calls), 4)
        return 0.0

    # -------------------------------------------------------------- evaluate
    async def evaluate(self, run=None) -> list[dict]:
        fired: list[dict] = []
        now = utcnow()
        for rule in self.rules():
            if not rule.get("enabled"):
                continue
            value = self.measure(rule["metric"], run)
            compare = COMPARATORS[rule["comparator"]]
            if not compare(value, float(rule["threshold"])):
                continue
            if self._in_cooldown(rule, now):
                continue
            delivered = await self._deliver(rule, value, run)
            self.store.touch_alert_rule(rule["id"], now)
            self.store.add_audit(None, "alert.fired", rule["id"],
                                 f"{rule['metric']}={value} {rule['comparator']} {rule['threshold']}",
                                 None, now)
            fired.append({"rule": rule["id"], "name": rule["name"], "metric": rule["metric"],
                          "value": value, "channel": rule["channel"], **delivered})
        return fired

    def _in_cooldown(self, rule: dict, now: str) -> bool:
        last = rule.get("last_fired_at")
        if not last:
            return False
        try:
            elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            return False
        return elapsed < int(rule.get("cooldown_seconds") or 0)

    async def _deliver(self, rule: dict, value: float, run) -> dict[str, Any]:
        subject = f"[OmniForge] alert: {rule['name']}"
        body = (
            f"Rule: {rule['name']} ({rule['id']})\n"
            f"Condition: {rule['metric']} {rule['comparator']} {rule['threshold']}\n"
            f"Observed: {value}\n"
            f"Run: {getattr(run, 'id', 'n/a')} {getattr(run, 'goal', '')[:120]}\n"
        )
        if rule["channel"] == "webhook":
            if self.notifier is None or not self.notifier.enabled:
                return {"delivered": False, "error": "no webhook configured"}
            result = await self.notifier.send({"event": "alert", "rule": rule["name"],
                                               "metric": rule["metric"], "value": value,
                                               "detail": body})
            return {"delivered": bool(result.get("ok")), "error": result.get("error")}
        target = rule.get("target") or getattr(self.settings, "alert_email", "")
        if not target:
            return {"delivered": False, "error": "no alert email configured"}
        if self.mailer is None:
            return {"delivered": False, "error": "no mailer configured"}
        sent = await self.mailer.send(target, subject, body)
        return {"delivered": bool(sent), "error": None if sent else "delivery failed"}

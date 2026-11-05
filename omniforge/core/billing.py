"""Usage accounting, provider prices and quotas.

Prices are per 1,000,000 tokens and can be overridden per model prefix through
OMNIFORGE_MODEL_PRICES. Unknown models cost 0 and are reported as unpriced rather
than guessed, so the usage page never invents a number.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# $ per 1M tokens (input, output) - list prices at the time of writing.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}


def month_start(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


class Billing:
    def __init__(self, settings, store) -> None:
        self.settings = settings
        self.store = store

    # ----------------------------------------------------------------- prices
    def price_table(self) -> dict[str, tuple[float, float]]:
        table = dict(DEFAULT_PRICES)
        for model, pair in (getattr(self.settings, "model_prices", {}) or {}).items():
            try:
                table[model] = (float(pair[0]), float(pair[1]))
            except (TypeError, IndexError, ValueError):
                continue
        return table

    def price_for(self, model: str) -> tuple[float, float] | None:
        model = (model or "").lower()
        table = self.price_table()
        if model in table:
            return table[model]
        # longest matching prefix wins, so "gpt-4o-mini-2024" maps to gpt-4o-mini
        candidates = [key for key in table if model.startswith(key.lower())]
        if not candidates:
            return None
        return table[max(candidates, key=len)]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.price_for(model)
        if price is None:
            return 0.0
        return (input_tokens / 1_000_000) * price[0] + (output_tokens / 1_000_000) * price[1]

    @staticmethod
    def provider_of(model: str) -> str:
        model = (model or "").lower()
        if model.startswith(("gpt", "o1", "o3", "text-embedding", "dall-e", "whisper")):
            return "openai"
        if model.startswith("claude"):
            return "anthropic"
        if model.startswith("gemini"):
            return "google"
        if model.startswith(("llama", "mistral", "qwen", "phi")):
            return "open-source"
        return "other"

    def provider_usage(self, *, this_month: bool = True) -> list[dict[str, Any]]:
        since = month_start() if this_month else None
        summary = self.store.usage_summary(since=since)
        budgets = getattr(self.settings, "provider_budgets", {}) or {}
        rows: dict[str, dict[str, Any]] = {}
        for entry in summary["by_model"]:
            provider = self.provider_of(entry["model"])
            row = rows.setdefault(provider, {"provider": provider, "cost_usd": 0.0,
                                             "tokens": 0, "models": []})
            row["cost_usd"] = round(row["cost_usd"] + entry["cost_usd"], 6)
            row["tokens"] += entry["input_tokens"] + entry["output_tokens"]
            row["models"].append(entry["model"])
        for provider, budget in budgets.items():
            row = rows.setdefault(provider, {"provider": provider, "cost_usd": 0.0,
                                             "tokens": 0, "models": []})
            row["budget_usd"] = budget
            row["remaining_usd"] = round(max(0.0, budget - row["cost_usd"]), 6)
            row["exhausted"] = row["cost_usd"] >= budget
        return sorted(rows.values(), key=lambda r: r["provider"])

    def check_provider_budget(self, model: str) -> None:
        provider = self.provider_of(model)
        budgets = getattr(self.settings, "provider_budgets", {}) or {}
        budget = budgets.get(provider)
        if not budget:
            return
        spent = next((r["cost_usd"] for r in self.provider_usage() if r["provider"] == provider), 0.0)
        if spent >= budget:
            raise PermissionError(
                f"monthly budget for provider '{provider}' is exhausted "
                f"(${spent:.2f} of ${budget:.2f})"
            )

    def pricing(self) -> list[dict[str, Any]]:
        return [
            {"model": model, "input_per_1m": in_price, "output_per_1m": out_price}
            for model, (in_price, out_price) in sorted(self.price_table().items())
        ]

    # ------------------------------------------------------------------ usage
    def record(self, *, user_id: str | None, run_id: str | None, model: str,
               input_tokens: int, output_tokens: int, ts: str) -> dict:
        cost = self.cost(model, input_tokens, output_tokens)
        self.store.add_usage(user_id, run_id, model, input_tokens, output_tokens, cost, ts)
        return {"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cost_usd": round(cost, 6), "priced": self.price_for(model) is not None}

    def usage(self, user_id: str | None = None, *, this_month: bool = True) -> dict:
        since = month_start() if this_month else None
        summary = self.store.usage_summary(user_id=user_id, since=since)
        summary["period"] = "month" if this_month else "all-time"
        summary["quota_tokens"] = getattr(self.settings, "user_monthly_tokens", 0)
        quota = summary["quota_tokens"]
        summary["quota_remaining"] = max(0, quota - summary["tokens"]) if quota else None
        summary["unpriced_models"] = sorted(
            {m["model"] for m in summary["by_model"] if self.price_for(m["model"]) is None}
        )
        return summary

    # ------------------------------------------------------------------ quota
    def quota_remaining(self, user_id: str | None) -> int | None:
        quota = getattr(self.settings, "user_monthly_tokens", 0)
        if not quota or not user_id:
            return None
        used = self.store.usage_summary(user_id=user_id, since=month_start())["tokens"]
        return max(0, quota - used)

    def check_quota(self, user_id: str | None) -> None:
        remaining = self.quota_remaining(user_id)
        if remaining is not None and remaining <= 0:
            raise PermissionError("monthly token quota exhausted; raise the quota or wait for reset")

    def estimate(self, goal: str, *, tasks: int = 5, model: str | None = None) -> dict:
        """Rough pre-flight estimate: plan + one call per task + synthesis."""
        model = model or getattr(self.settings, "llm_model", "gpt-4o-mini")
        per_call = 900 + min(1200, len(goal) // 2)
        calls = max(2, tasks + 2)
        input_tokens = per_call * calls
        output_tokens = 550 * calls
        return {
            "model": model,
            "calls": calls,
            "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": output_tokens,
            "estimated_cost_usd": round(self.cost(model, input_tokens, output_tokens), 6),
            "priced": self.price_for(model) is not None,
            "note": "estimate only: real usage depends on plan size and rework",
        }

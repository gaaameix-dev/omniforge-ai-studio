"""Counters, gauges and histograms in one place, exposed as JSON and Prometheus text."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


@dataclass
class Metrics:
    started_at: float = field(default_factory=time.time)
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    gauges: dict[str, float] = field(default_factory=dict)
    samples: dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=500)))

    def inc(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def set(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        self.samples[name].append(float(value))

    def summary(self, name: str) -> dict[str, float]:
        values = list(self.samples.get(name, []))
        if not values:
            return {"count": 0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
        return {
            "count": len(values),
            "avg": round(sum(values) / len(values), 4),
            "p50": round(_percentile(values, 50), 4),
            "p95": round(_percentile(values, 95), 4),
            "max": round(max(values), 4),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "counters": dict(sorted(self.counters.items())),
            "gauges": dict(sorted(self.gauges.items())),
            "timings": {k: self.summary(k) for k in sorted(self.samples)},
        }

    def prometheus(self) -> str:
        lines = ["# HELP omniforge_info Framework build info", "# TYPE omniforge_info gauge",
                 'omniforge_info{version="0.7.0"} 1']
        for name, value in sorted(self.counters.items()):
            metric = f"omniforge_{name.replace('.', '_')}_total"
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{metric} {value}")
        for name, value in sorted(self.gauges.items()):
            metric = f"omniforge_{name.replace('.', '_')}"
            lines.append(f"# TYPE {metric} gauge")
            lines.append(f"{metric} {value}")
        for name in sorted(self.samples):
            s = self.summary(name)
            metric = f"omniforge_{name.replace('.', '_')}_seconds"
            lines.append(f"# TYPE {metric} summary")
            for q, key in (("0.5", "p50"), ("0.95", "p95")):
                lines.append(f'{metric}{{quantile="{q}"}} {s[key]}')
            lines.append(f"{metric}_count {s['count']}")
            lines.append(f"{metric}_sum {round(s['avg'] * s['count'], 4)}")
        return "\n".join(lines) + "\n"

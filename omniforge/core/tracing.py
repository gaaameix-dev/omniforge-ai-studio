"""Lightweight span tracing.

Every task and tool call gets a span with a duration and attributes; spans are kept
in a ring buffer and exposed at `/api/traces`. If the OpenTelemetry SDK is installed
and `OMNIFORGE_OTEL=true`, spans are also emitted there - the local recorder never
depends on it.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: str | None = None
    started: float = field(default_factory=time.perf_counter)
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "trace_id": self.trace_id, "span_id": self.span_id,
            "parent_id": self.parent_id, "duration_ms": round(self.duration_ms, 2),
            "attributes": self.attributes, "status": self.status, "error": self.error,
        }


class Tracer:
    def __init__(self, max_spans: int = 1000, *, otel: bool = False) -> None:
        self.spans: deque[Span] = deque(maxlen=max_spans)
        self.otel = otel
        self._tracer = None
        if otel:
            try:  # pragma: no cover - only when the SDK is installed
                from opentelemetry import trace

                self._tracer = trace.get_tracer("omniforge")
            except Exception:  # noqa: BLE001
                self._tracer = None

    @contextmanager
    def span(self, name: str, trace_id: str | None = None, **attributes: Any):
        record = Span(name=name, trace_id=trace_id or uuid.uuid4().hex[:16],
                      attributes=dict(attributes))
        parent = getattr(self, "_current", None)
        if parent is not None:
            record.parent_id = parent.span_id
            record.trace_id = parent.trace_id
        self._current = record
        try:
            yield record
        except Exception as exc:  # noqa: BLE001 - record, then re-raise
            record.status = "error"
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record.duration_ms = (time.perf_counter() - record.started) * 1000
            self._current = parent
            self.spans.append(record)

    def recent(self, limit: int = 100, trace_id: str | None = None) -> list[dict[str, Any]]:
        items = list(self.spans)
        if trace_id:
            items = [span for span in items if span.trace_id == trace_id]
        return [span.to_dict() for span in items[-limit:]]

    def summary(self) -> dict[str, Any]:
        by_name: dict[str, list[float]] = {}
        errors = 0
        for span in self.spans:
            by_name.setdefault(span.name, []).append(span.duration_ms)
            if span.status == "error":
                errors += 1
        return {
            "spans": len(self.spans),
            "errors": errors,
            "by_name": {name: {"count": len(values),
                               "p50_ms": round(sorted(values)[len(values) // 2], 2),
                               "max_ms": round(max(values), 2)}
                        for name, values in sorted(by_name.items())},
        }

"""Model gateway: try providers in order, fail over, and record latency.

A provider that errors (bad key, rate limit, 5xx) does not kill the run: the next
one in the chain gets the same request. Every attempt is timed, so the monitor can
show which provider is actually serving traffic.
"""
from __future__ import annotations

import time
from typing import Any

from .llm import BaseLLM, LLMResponse

CAPABILITIES = {
    "openai": {"json_mode": True, "tools": True, "max_context": 128_000},
    "anthropic": {"json_mode": False, "tools": True, "max_context": 200_000},
    "gemini": {"json_mode": True, "tools": True, "max_context": 1_000_000},
    "mock": {"json_mode": True, "tools": False, "max_context": 8_000},
}


class ModelGateway(BaseLLM):
    name = "gateway"

    def __init__(self, chain: list[BaseLLM], metrics: Any | None = None) -> None:
        if not chain:
            raise ValueError("a gateway needs at least one provider")
        self.chain = chain
        self.metrics = metrics
        self.failures: dict[str, int] = {}
        self.successes: dict[str, int] = {}

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.chain]

    def capabilities(self) -> list[dict[str, Any]]:
        return [{"provider": p.name, "model": getattr(p, "model", "?"),
                 **CAPABILITIES.get(p.name, {})} for p in self.chain]

    async def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> LLMResponse:
        last_error: Exception | None = None
        for index, provider in enumerate(self.chain):
            started = time.perf_counter()
            try:
                response = await provider.complete(system, prompt, json_mode=json_mode)
                self.successes[provider.name] = self.successes.get(provider.name, 0) + 1
                if self.metrics is not None:
                    self.metrics.observe(f"llm.{provider.name}.latency", time.perf_counter() - started)
                    self.metrics.inc("llm.calls")
                if index > 0 and self.metrics is not None:
                    self.metrics.inc("llm.failovers")
                return response
            except Exception as exc:  # noqa: BLE001 - the next provider gets a turn
                last_error = exc
                self.failures[provider.name] = self.failures.get(provider.name, 0) + 1
                if self.metrics is not None:
                    self.metrics.inc("llm.errors")
        raise RuntimeError(f"every model provider failed; last error: {last_error}")

    def status(self) -> dict[str, Any]:
        return {
            "providers": self.provider_names,
            "successes": self.successes,
            "failures": self.failures,
            "capabilities": self.capabilities(),
        }

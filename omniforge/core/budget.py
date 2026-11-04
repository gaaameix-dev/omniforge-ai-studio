"""Token and cost accounting with a hard cap per run."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    limit_tokens: int = 0                 # 0 = unlimited
    price_per_1k_input: float = 0.0
    price_per_1k_output: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens / 1000.0 * self.price_per_1k_input
            + self.output_tokens / 1000.0 * self.price_per_1k_output
        )

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))

    def add_estimate(self, prompt: str, completion: str) -> None:
        """Fallback when the backend reports no usage: ~4 characters per token."""
        self.add(len(prompt) // 4, len(completion) // 4)

    @property
    def exceeded(self) -> bool:
        return bool(self.limit_tokens) and self.tokens >= self.limit_tokens

    def snapshot(self) -> dict:
        return {
            "limit_tokens": self.limit_tokens,
            "tokens": self.tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "exceeded": self.exceeded,
        }

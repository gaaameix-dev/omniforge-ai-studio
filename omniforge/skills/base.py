"""Skill = one granular capability an agent can perform.

A skill either carries a prompt template (the LLM does the work) or a Python
handler (deterministic work). Handlers win when both exist, which is how the
framework stays useful in mock mode.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SYSTEM = (
    "You are a senior specialist inside OmniForge AI Studio. Produce a concrete, "
    "directly usable work product - not a description of one. Use short headings "
    "and bullet lists. State assumptions explicitly."
)

PROMPT_TEMPLATE = """Goal of the whole project: {goal}

Your task: {title}
Brief: {description}
Department: {department}

What earlier agents produced (use it, do not repeat it):
{context}

Deliver the work product for your task now."""


@dataclass
class Skill:
    name: str
    department: str
    description: str
    tags: set[str] = field(default_factory=set)
    prompt_system: str = DEFAULT_SYSTEM
    handler: Callable[..., Any] | None = None
    handler_kind: str = "python"
    min_chars: int = 40
    required_any: tuple[str, ...] = ()

    def render(self, task, context: str, goal: str = "", feedback: str | None = None) -> str:
        prompt = PROMPT_TEMPLATE.format(
            goal=goal or task.title,
            title=task.title,
            description=task.description or "(no extra brief)",
            department=self.department,
            context=context or "(nothing yet - you are working from the goal alone)",
        )
        if feedback:
            prompt += (
                "\n\n## Reviewer feedback on your previous attempt\n"
                f"{feedback}\n\nAddress every point above and return the corrected work product."
            )
        return prompt


def register_library(registry, specs: list[dict[str, Any]], handlers: dict[str, Callable] | None = None) -> None:
    from .generators import GENERATORS

    handlers = dict(handlers or {})
    for name, generator in GENERATORS.items():
        handlers.setdefault(name, generator)
    for spec in specs:
        registry.register(
            spec["name"],
            Skill(
                name=spec["name"],
                department=spec["department"],
                description=spec["description"],
                tags=set(spec.get("tags", [])),
                handler=handlers.get(spec["name"]),
                min_chars=int(spec.get("min_chars", 40)),
                required_any=tuple(spec.get("required_any", ())),
            ),
        )

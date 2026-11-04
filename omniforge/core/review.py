"""Review / critique engine: scores a task's output before it is accepted.

Two layers, cheapest first:
  1. heuristics - empty, truncated, prompt echo, placeholder, missing structure
  2. optional LLM critique - only when a live model is configured and
     OMNIFORGE_REVIEW_WITH_LLM is on, because it costs a second call per task.
A failed review sends the task back to its agent with the issues attached.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import BaseLLM, parse_json_object

PROMPT_ECHO = "deliver the work product for your task now"
PLACEHOLDER_MARKERS = ("[mock output]", "placeholder deliverable")
CRITIQUE_SYSTEM = (
    "You are a strict reviewer. Score the deliverable 0.0-1.0 against its brief. "
    "Reply with JSON only: {\"score\": number, \"issues\": [string, ...]}."
)


@dataclass
class ReviewResult:
    score: float
    passed: bool
    issues: list[str] = field(default_factory=list)
    placeholder: bool = False
    source: str = "heuristic"

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "passed": self.passed,
            "issues": list(self.issues),
            "placeholder": self.placeholder,
            "source": self.source,
        }


class Reviewer:
    def __init__(self, settings, llm: BaseLLM | None = None) -> None:
        self.settings = settings
        self.llm = llm

    async def review(self, task, output: str, skill=None, context: str = "") -> ReviewResult:
        text = (output or "").strip()
        issues: list[str] = []
        critical = False
        placeholder = False

        if not text:
            return ReviewResult(score=0.0, passed=False, issues=["empty output"])

        lowered = text.lower()
        if PROMPT_ECHO in lowered:
            issues.append("output echoes the prompt instead of answering it")
            critical = True
        if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            placeholder = True
            issues.append("placeholder output (no live model connected)")

        min_chars = getattr(skill, "min_chars", None) or self.settings.review_min_chars
        if len(text) < min_chars:
            issues.append(f"output shorter than {min_chars} characters")

        required = getattr(skill, "required_any", ()) or ()
        if required and not any(marker.lower() in lowered for marker in required):
            issues.append("missing required section: " + " / ".join(required))
            critical = True

        if context and text and text in context:
            issues.append("output is a copy of upstream material")
            critical = True

        if not re.search(r"(^#{1,3} |\n#{1,3} |\n- |\n\* |\n\d+\. |\||```)", text):
            issues.append("no structure (headings, bullets, table or code)")

        # In mock mode a placeholder is the expected answer, not a failure: grade it
        # passable and say so, instead of burning rework attempts on it.
        if placeholder and self.llm is not None and self.llm.name == "mock":
            return ReviewResult(
                score=0.6, passed=True,
                issues=["placeholder output: connect a live model for real content"],
                placeholder=True, source="heuristic",
            )

        score = 1.0 - 0.3 * len(issues)
        if critical:
            score = min(score, 0.25)
        score = max(0.0, min(1.0, score))

        result = ReviewResult(score=score, passed=(not critical and score >= 0.5),
                              issues=issues, placeholder=placeholder)

        if self.settings.review_with_llm and self.llm is not None and self.llm.name != "mock":
            verdict = await self._llm_review(task, text)
            if verdict is not None:
                result.score = round((result.score + verdict[0]) / 2.0, 3)
                result.issues.extend(verdict[1])
                result.passed = result.passed and verdict[0] >= 0.5 and not critical
                result.source = "heuristic+llm"
        return result

    async def _llm_review(self, task, text: str) -> tuple[float, list[str]] | None:
        prompt = (
            f"Task: {task.title}\nBrief: {task.description}\n\nDeliverable:\n{text[:6000]}\n\n"
            "Score how completely and concretely it satisfies the brief."
        )
        try:
            resp = await self.llm.complete(CRITIQUE_SYSTEM, prompt, json_mode=True)
        except Exception:  # noqa: BLE001 - review must never fail a task by itself
            return None
        data = parse_json_object(resp.text)
        if not data:
            return None
        try:
            score = float(data.get("score", 0.0))
        except (TypeError, ValueError):
            return None
        issues = data.get("issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        return max(0.0, min(1.0, score)), [str(i) for i in issues][:6]

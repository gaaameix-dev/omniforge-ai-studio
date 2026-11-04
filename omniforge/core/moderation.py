"""Content policy: sensitive-data detection plus configurable blocked terms.

Two jobs:
  1. stop secrets and personal data from flowing into prompts, artifacts or logs
  2. let an operator add their own blocked patterns without touching code

It is a policy engine, not a classifier: patterns are explicit and the findings
say exactly what matched, so nothing is silently redacted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SENSITIVE_RULES: list[tuple[str, str, str]] = [
    ("secret", "openai_key", r"\bsk-[A-Za-z0-9]{16,}\b"),
    ("secret", "aws_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("secret", "github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("secret", "private_key", r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("secret", "bearer_token", r"\bBearer\s+[A-Za-z0-9._-]{20,}"),
    ("pii", "email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("pii", "card_number", r"\b(?:\d[ -]*?){13,19}\b"),
    ("pii", "aadhaar_like", r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
]

REDACTED = "[redacted]"


@dataclass
class ModerationReport:
    findings: list[dict[str, str]] = field(default_factory=list)
    text: str = ""
    blocked: bool = False

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict:
        return {
            "clean": self.clean,
            "blocked": self.blocked,
            "findings": [{"category": f["category"], "rule": f["rule"]} for f in self.findings],
        }


@dataclass
class Policy:
    enabled: bool = True
    redact_secrets: bool = True
    blocked_terms: tuple[str, ...] = ()
    allow_pii: bool = True          # PII is flagged, only secrets are redacted by default

    def rules(self) -> list[tuple[str, str, str]]:
        rules = list(SENSITIVE_RULES)
        for term in self.blocked_terms:
            if term:
                rules.append(("blocked", f"term:{term[:24]}", re.escape(term)))
        return rules


def scan(text: str, policy: Policy | None = None) -> ModerationReport:
    policy = policy or Policy()
    body = text or ""
    if not policy.enabled:
        return ModerationReport(findings=[], text=body)
    findings: list[dict[str, str]] = []
    for category, rule, pattern in policy.rules():
        for match in re.finditer(pattern, body):
            findings.append({"category": category, "rule": rule,
                             "match": match.group(0)[:8] + "…"})
    blocked = any(f["category"] == "blocked" for f in findings)
    cleaned = body
    if policy.redact_secrets:
        for category, _rule, pattern in policy.rules():
            if category != "secret":
                continue
            cleaned = re.sub(pattern, REDACTED, cleaned)
    return ModerationReport(findings=findings, text=cleaned, blocked=blocked)


def contains_blocked(text: str, policy: Policy | None = None) -> bool:
    return scan(text, policy).blocked


CLASSIFIER_SYSTEM = (
    "You are a content policy classifier. Judge the text against these categories: "
    "harassment, hate, sexual, violence, self_harm, illegal, malware, private_data, "
    "none. Reply with JSON only: {\"categories\": [..], \"block\": true|false, "
    "\"reason\": \"short\"}. Block only clear violations."
)

BLOCKING_CATEGORIES = {"harassment", "hate", "sexual", "violence", "self_harm",
                       "illegal", "malware", "private_data"}


class LlmModerator:
    """Optional model-based classifier. Fails open: if the model errors, the
    heuristic policy still applies and the call is reported as unchecked."""

    def __init__(self, llm, *, enabled: bool = False) -> None:
        self.llm = llm
        self.enabled = enabled and llm is not None and getattr(llm, "name", "") != "mock"

    async def check(self, text: str) -> dict:
        if not self.enabled:
            return {"checked": False, "block": False, "categories": [], "reason": "disabled"}
        from .llm import parse_json_object

        try:
            response = await self.llm.complete(CLASSIFIER_SYSTEM, text[:6000], json_mode=True)
        except Exception as exc:  # noqa: BLE001 - never fail a run over moderation
            return {"checked": False, "block": False, "categories": [],
                    "reason": f"classifier unavailable: {type(exc).__name__}"}
        data = parse_json_object(response.text) or {}
        categories = [str(c).lower() for c in (data.get("categories") or [])]
        block = bool(data.get("block")) or any(c in BLOCKING_CATEGORIES for c in categories)
        return {"checked": True, "block": block, "categories": categories,
                "reason": str(data.get("reason") or "")[:200]}

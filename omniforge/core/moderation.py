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

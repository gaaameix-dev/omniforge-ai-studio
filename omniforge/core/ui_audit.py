"""Static UI audit: what a browser check would look for, without a browser.

It parses the shipped dashboard and reports on the things that actually break on a
phone or with a screen reader: viewport meta, responsive breakpoints, touch-target
sizes, focus styles, ARIA landmarks, live regions and reduced-motion support.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

WEB = Path(__file__).resolve().parents[2] / "web"

CHECKS = (
    ("viewport_meta", "A viewport meta tag with width=device-width"),
    ("media_queries", "At least 3 responsive breakpoints"),
    ("touch_targets", "Interactive elements sized for a thumb (>= 40px)"),
    ("focus_visible", "Visible keyboard focus"),
    ("aria_live", "Live regions for streamed output"),
    ("aria_labels", "Labelled controls and navigation"),
    ("reduced_motion", "prefers-reduced-motion respected"),
    ("lang_attribute", "Document language set"),
    ("skip_link", "Skip-to-content link"),
    ("no_fixed_widths", "No layout pinned to a desktop width"),
)


def audit(path: Path | None = None) -> dict[str, Any]:
    page = (path or WEB / "index.html").read_text(encoding="utf-8")
    css = page
    findings: list[dict[str, Any]] = []

    def record(key: str, ok: bool, detail: str) -> None:
        findings.append({"check": key, "ok": ok, "detail": detail})

    viewport = re.search(r'<meta[^>]+name="viewport"[^>]*>', page, re.I)
    record("viewport_meta", bool(viewport and "width=device-width" in viewport.group(0)),
           viewport.group(0) if viewport else "missing")

    media = re.findall(r"@media[^{]+\{", css)
    record("media_queries", len(media) >= 3, f"{len(media)} breakpoint(s)")

    targets = re.findall(r"(?:min-height|height|padding):\s*(\d+)px", css)
    big = [int(value) for value in targets if int(value) >= 40]
    record("touch_targets", bool(big), f"{len(big)} rule(s) >= 40px")

    record("focus_visible", ":focus-visible" in css or ":focus" in css,
           "focus-visible present" if ":focus-visible" in css else "only :focus" if ":focus" in css
           else "no focus styling")

    live = len(re.findall(r'aria-live="(polite|assertive)"', page))
    record("aria_live", live >= 1, f"{live} live region(s)")

    labels = len(re.findall(r"aria-label(?:ledby)?=", page))
    record("aria_labels", labels >= 3, f"{labels} aria label(s)")

    record("reduced_motion", "prefers-reduced-motion" in css, "reduced motion block")
    lang = re.search(r'<html[^>]*lang="([a-z-]+)"', page, re.I)
    record("lang_attribute", bool(lang), lang.group(1) if lang else "missing")
    record("skip_link", "skip" in page.lower() and "href=\"#" in page.lower(), "skip link")
    # max-width / min-width are not fixed widths; only a hard `width:` counts
    fixed = re.findall(r"(?<!max-)(?<!min-)width:\s*(\d{3,4})px", css)
    wide = [value for value in fixed if int(value) > 900]
    record("no_fixed_widths", not wide, f"{len(wide)} fixed width(s) over 900px")

    passed = sum(1 for finding in findings if finding["ok"])
    return {
        "checks": findings,
        "passed": passed,
        "total": len(findings),
        "score": round(passed / len(findings), 2),
        "note": "static audit; a real browser pass is still better (see docs/REMAINING.md)",
    }

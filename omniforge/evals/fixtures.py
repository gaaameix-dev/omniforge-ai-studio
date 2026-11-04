"""Eval cases: goal -> expectations. Deterministic, so they gate CI honestly."""
from __future__ import annotations

from typing import Any

CASES: list[dict[str, Any]] = [
    {
        "name": "mobile_game",
        "goal": "Build a 2D mobile puzzle game with 20 levels and a leaderboard",
        "expect": {
            "status": "done",
            "min_done": 4,
            "departments": ["research", "qa", "product_ops"],
            "max_tasks": 12,
            "min_review_score": 0.5,
        },
    },
    {
        "name": "web_saas",
        "goal": "Design a SaaS invoicing dashboard with auth, postgres and a deploy plan",
        "expect": {
            "status": "done",
            "min_done": 4,
            "departments": ["web", "research", "qa"],
            "max_tasks": 12,
            "min_review_score": 0.5,
        },
    },
    {
        "name": "game_engine",
        "goal": "Prototype a Unity VR training simulator with physics interaction",
        "expect": {
            "status": "done",
            "min_done": 4,
            "departments": ["game_development", "qa"],
            "max_tasks": 12,
        },
    },
    {
        "name": "media_launch",
        "goal": "Produce a launch video with sound design and a marketing graphic",
        "expect": {
            "status": "done",
            "min_done": 4,
            "departments": ["media", "product_ops"],
            "max_tasks": 12,
        },
    },
    {
        "name": "ai_agent_platform",
        "goal": "Design a RAG pipeline and agent architecture for an internal assistant",
        "expect": {
            "status": "done",
            "min_done": 4,
            "departments": ["ai_agent_systems", "qa"],
            "max_tasks": 12,
        },
    },
]

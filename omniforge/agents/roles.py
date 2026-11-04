"""Builds the agent registry (120 roles) on top of the skill and tool registries."""
from __future__ import annotations

import re

from ..core.agent import AgentTemplate
from ..core.registry import AgentRegistry, SkillRegistry
from ..skills.base import register_library
from ..skills.handlers import HANDLERS
from ..skills.library import SKILLS
from ..tools.builtin import build_default_tools
from .departments import DEPARTMENTS, ROLES

DEFAULT_TOOLS = ["http_get", "github_get", "write_artifact", "now", "json_query", "text_stats"]

# Least privilege: every role can read the web and write its own artifacts.
# Extra capabilities are granted per role name, never wholesale.
DEFAULT_PERMISSIONS: tuple[str, ...] = ("net.read", "fs.write", "github.read")
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "DevOps Engineer": ("github.write",),
    "CI/CD Engineer": ("github.write",),
    "Release Manager": ("github.write",),
    "Build & Deployment Agent": ("github.write",),
    "Security Hardening Engineer": ("github.write",),
    "Automation Test Engineer": ("code.exec",),
    "QA Engineer": ("code.exec",),
    "Security Tester": ("code.exec",),
}

# Words that mean the same thing on the role side and the skill side, so
# "Database Architect" lands on database_schema and "QA Engineer" on test_plan.
SYNONYMS = {
    "qa": {"test", "quality", "bug"},
    "test": {"qa", "check"},
    "deploy": {"devops", "release", "ship"},
    "release": {"deploy", "store", "ship"},
    "api": {"backend", "endpoint"},
    "backend": {"api", "server"},
    "frontend": {"ui", "web"},
    "ui": {"frontend", "ux", "design"},
    "ux": {"ui", "design"},
    "doc": {"documentation", "write", "writer"},
    "docs": {"documentation", "write", "writer"},
    "audio": {"sound", "music", "mix"},
    "sound": {"audio", "mix"},
    "3d": {"model", "mesh", "texture", "rig"},
    "model": {"3d", "ml"},
    "ml": {"model", "train"},
    "security": {"secure", "threat", "harden"},
    "perf": {"performance", "speed", "optimize"},
    "perfomance": {"performance"},
    "animator": {"animation", "animate"},
    "rigger": {"rig"},
    "writer": {"writing", "write"},
    "designer": {"design"},
    "architect": {"architecture"},
    "planner": {"planning", "plan"},
    "tester": {"test"},
    "composer": {"music", "compose"},
}

# Words too generic to prove a role/skill match (every department has them).
GENERIC_NAME_TOKENS = {
    "character", "game", "web", "app", "design", "engineer", "developer",
    "specialist", "analyst", "manager", "artist", "writer", "architect",
    "planner", "agent", "system", "systems", "level", "mobile", "test",
}


def _tokens(text: str) -> set[str]:
    raw = re.split(r"[^a-z0-9]+", text.lower())
    out: set[str] = set()
    for token in raw:
        if len(token) < 3:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        out.add(token)
        out |= SYNONYMS.get(token, set())
    return out


def _affinity(role_name: str, scope: str, skill_name: str, skill_description: str) -> int:
    role = _tokens(role_name) | _tokens(scope)
    skill = _tokens(skill_name.replace("_", " ")) | _tokens(skill_description)
    return len(role & skill)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def build_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    register_library(registry, SKILLS, HANDLERS)
    return registry


def build_tool_registry(data_dir: str = "./data", settings=None):
    return build_default_tools(data_dir, settings)


def build_agent_registry(skills: SkillRegistry | None = None) -> AgentRegistry:
    """One template per role. Skills come from the role's department, so a new
    skill added to a department is immediately available to its 12 agents."""
    skills = skills or build_skill_registry()
    registry = AgentRegistry()
    for department, roles in ROLES.items():
        dept_skills = [s.name for s in skills.by_department(department)]
        if not dept_skills:
            raise ValueError(f"department '{department}' has no skills registered")
        scored = {
            s.name: _affinity("", "", s.name, s.description)
            for s in skills.by_department(department)
        }
        for name, scope in roles:
            ranking = sorted(
                dept_skills,
                key=lambda n: (-_affinity(name, scope, n, scored and skills.require(n).description), n),
            )
            primary = ranking[:2]
            template = AgentTemplate(
                id=f"{slug(department)}__{slug(name)}",
                name=name,
                department=department,
                description=scope,
                skills=ranking,
                primary_skills=primary,
                name_tokens=_tokens(name) - GENERIC_NAME_TOKENS,
                tools=list(DEFAULT_TOOLS),
                permissions=DEFAULT_PERMISSIONS + ROLE_PERMISSIONS.get(name, ()),
                system_prompt=(
                    f"You are the {name} in OmniForge AI Studio's "
                    f"{DEPARTMENTS[department]['name']} department. {scope} "
                    "Produce the artifact itself, with concrete specifics: names, "
                    "numbers, steps, code or copy. Never answer with a plan to make a plan."
                ),
            )
            registry.register(template.id, template)
    return registry

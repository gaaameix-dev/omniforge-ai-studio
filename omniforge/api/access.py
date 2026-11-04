"""Project-level access control.

Rules, in order:
  1. a system owner/admin may touch any project
  2. if the project has members, the user must be one, with enough role
  3. if the project has no members, any authenticated user may read it and run
     against it (the pre-ACL behaviour, so existing deployments keep working)
"""
from __future__ import annotations

from fastapi import HTTPException

from ..core.auth import ROLE_RANK, Identity, allows


def project_role(identity: Identity, store, project_id: str) -> str | None:
    if identity.role in {"owner", "admin"} and identity.kind != "session":
        return "owner"
    return store.project_role(project_id, identity.user_id or "")


def has_members(store, project_id: str) -> bool:
    return bool(store.list_project_members(project_id))


def ensure(identity: Identity, store, project_id: str, action: str) -> None:
    """Raise 404/403 unless this identity may perform `action` on the project."""
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if identity.role in {"owner", "admin"}:
        return
    if not has_members(store, project_id):
        if action == "read" or allows(identity.role, "run"):
            return
        raise HTTPException(status_code=403, detail=f"role '{identity.role}' cannot {action}")
    role = store.project_role(project_id, identity.user_id or "")
    if role is None:
        raise HTTPException(status_code=403, detail="not a member of this project")
    if ROLE_RANK.get(role, 0) < ROLE_RANK.get("viewer" if action == "read" else
                                              "member" if action == "run" else "admin", 4):
        raise HTTPException(status_code=403, detail=f"project role '{role}' cannot {action}")

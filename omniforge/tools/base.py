"""Tool contract: a named, permissioned, schema-described async function.

Every tool declares what it needs before it can run:
  permissions   - capability strings an agent must hold, e.g. "github.write"
  destructive   - True means a human approval must be on record for the call
  requires_approval - destructive calls are additionally gated per run
Inputs and outputs are described in `parameters` so the registry can publish a
schema that a model (or a human) can inspect before calling.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class ToolError(RuntimeError):
    """The tool exists but could not run (bad input, upstream failure)."""


class PermissionDenied(ToolError):
    """The calling agent does not hold the required capability."""


class ApprovalRequired(ToolError):
    """A destructive tool was called without a recorded human approval."""


class IntegrationNotConfigured(ToolError):
    """The backing service has no credentials; nothing was attempted."""


@dataclass
class Tool:
    name: str
    description: str
    run: Callable[..., Awaitable[Any]]
    parameters: dict[str, Any] = field(default_factory=dict)
    returns: str = "string"
    permissions: tuple[str, ...] = ()
    destructive: bool = False
    requires_approval: bool = False
    enabled: bool = True
    category: str = "general"
    requires_network: bool = False
    provider: str = "builtin"

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
            "permissions": list(self.permissions),
            "destructive": self.destructive,
            "requires_approval": self.requires_approval,
            "enabled": self.enabled,
            "category": self.category,
            "requires_network": self.requires_network,
            "provider": self.provider,
        }

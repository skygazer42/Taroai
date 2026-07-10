from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class PolicyRequest(BaseModel):
    tenant_id: str
    user_id: str
    action: str
    resource: str
    workspace_id: str | None = None
    run_id: str | None = None
    sensitivity_level: int = Field(default=0, ge=0)
    risk_level: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    effect: PolicyEffect
    allowed: bool = False
    approval_required: bool = False
    reason: str | None = None
    missing_permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def allow(cls, metadata: dict[str, Any] | None = None):
        return cls(effect=PolicyEffect.ALLOW, allowed=True, metadata=metadata or {})

    @classmethod
    def deny(
        cls,
        reason: str,
        missing_permissions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return cls(
            effect=PolicyEffect.DENY,
            allowed=False,
            reason=reason,
            missing_permissions=missing_permissions or [],
            metadata=metadata or {},
        )

    @classmethod
    def require_approval(cls, reason: str, metadata: dict[str, Any] | None = None):
        return cls(
            effect=PolicyEffect.APPROVAL_REQUIRED,
            allowed=False,
            approval_required=True,
            reason=reason,
            metadata=metadata or {},
        )

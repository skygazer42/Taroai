from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field

from taroai.domain import utc_now


class SecretScope(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    allowed_tool_names: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)

    def allows(
        self,
        tenant_id: str,
        workspace_id: str | None,
        tool_name: str,
        actions: list[str],
    ) -> bool:
        if self.tenant_id != tenant_id:
            return False
        if self.workspace_id is not None and self.workspace_id != workspace_id:
            return False
        if self.allowed_tool_names and not any(
            tool_name == allowed
            or (allowed.endswith("*") and tool_name.startswith(allowed[:-1]))
            for allowed in self.allowed_tool_names
        ):
            return False
        return set(actions).issubset(set(self.actions))


class SecretRef(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str | None = None
    name: str = Field(min_length=1)
    scope: SecretScope
    backend: str = "memory"
    external_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SecretLease(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    session_id: str | None = None
    secret_ref_id: str
    tool_name: str
    actions: list[str] = Field(default_factory=list)
    lease_token: str
    issued_at: datetime
    expires_at: datetime

    def to_audit_metadata(self) -> dict:
        return {
            "lease_id": self.id,
            "secret_ref_id": self.secret_ref_id,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "actions": self.actions,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class SecretLeaseResolveRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    lease_token: str = Field(min_length=1)
    action: str = Field(default="read", min_length=1)


class SecretLeaseResolution(BaseModel):
    lease_id: str
    secret_ref_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    session_id: str | None = None
    tool_name: str
    action: str
    expires_at: datetime
    value: str

    def to_audit_metadata(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "secret_ref_id": self.secret_ref_id,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "action": self.action,
            "expires_at": self.expires_at.isoformat(),
        }


class SecretCaptureRequest(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    name: str = Field(min_length=1, max_length=200)
    tool_name: str | None = None
    connector_id: str | None = None
    action_id: str | None = None
    actions: list[str] = Field(default_factory=list)
    status: Literal["pending", "resolved", "cancelled"] = "pending"
    secret_ref_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

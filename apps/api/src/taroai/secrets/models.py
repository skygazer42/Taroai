from datetime import datetime

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
        if self.allowed_tool_names and tool_name not in self.allowed_tool_names:
            return False
        return set(actions).issubset(set(self.actions))


class SecretRef(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str | None = None
    name: str = Field(min_length=1)
    scope: SecretScope
    created_at: datetime = Field(default_factory=utc_now)


class SecretLease(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str | None = None
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
            "tool_name": self.tool_name,
            "actions": self.actions,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

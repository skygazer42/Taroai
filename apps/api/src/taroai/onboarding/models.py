from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ReadinessCheckStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class TenantReadinessCheck(BaseModel):
    name: str
    status: ReadinessCheckStatus
    required: bool = True
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantReadinessReport(BaseModel):
    tenant_id: str
    user_id: str
    ready: bool
    blocking_checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[TenantReadinessCheck] = Field(default_factory=list)


class TenantBootstrapRequest(BaseModel):
    tenant_id: str | None = Field(default=None, min_length=1)
    tenant_slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    starter_workspace_id: str | None = Field(default=None, min_length=1)
    starter_workspace_name: str = Field(default="Default Workspace", min_length=1)
    starter_knowledge_base_name: str = Field(default="Company Knowledge", min_length=1)
    starter_solution_pack_ids: list[str] = Field(default_factory=list)
    owner_email: str = Field(min_length=3)
    owner_display_name: str = Field(min_length=1)
    owner_password: str = Field(min_length=8)

    @model_validator(mode="after")
    def require_tenant_identity(self):
        if self.tenant_id is None and self.tenant_slug is None:
            raise ValueError("tenant_id or tenant_slug is required")
        return self


class TenantBootstrapResult(BaseModel):
    tenant_id: str
    tenant_slug: str
    owner_user_id: str
    owner_role_id: str
    starter_workspace_id: str
    starter_knowledge_base_id: str
    starter_skill_ids: list[str] = Field(default_factory=list)
    starter_solution_pack_ids: list[str] = Field(default_factory=list)
    starter_solution_pack_skill_ids: list[str] = Field(default_factory=list)
    readiness: TenantReadinessReport

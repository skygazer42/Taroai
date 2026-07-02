from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    tenant_id: str = Field(min_length=1)
    owner_email: str = Field(min_length=3)
    owner_display_name: str = Field(min_length=1)
    owner_password: str = Field(min_length=8)


class TenantBootstrapResult(BaseModel):
    tenant_id: str
    owner_user_id: str
    owner_role_id: str
    readiness: TenantReadinessReport

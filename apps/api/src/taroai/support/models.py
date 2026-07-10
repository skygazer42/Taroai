from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SupportAccessScope(str, Enum):
    RUN_DEBUG = "run_debug"
    TENANT_DEBUG = "tenant_debug"


class SupportSessionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"
    EXPIRED = "expired"


class SupportSessionCreate(BaseModel):
    requested_by_user_id: str = Field(min_length=1)
    scope: SupportAccessScope
    reason: str = Field(min_length=1)
    expires_at: datetime


class SupportSession(BaseModel):
    id: str
    tenant_id: str
    requested_by_user_id: str
    approved_by_user_id: str | None = None
    scope: SupportAccessScope
    reason: str
    expires_at: datetime
    status: SupportSessionStatus
    break_glass: bool = False
    audit_event_id: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    revoked_at: datetime | None = None


class SupportRunMetadata(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    agent_id: str | None = None
    status: str
    mode: str
    message: str = "[REDACTED]"
    message_length: int = Field(ge=0)
    attachment_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class SupportRunEventSummary(BaseModel):
    id: str
    sequence: int
    type: str
    payload_keys: list[str] = Field(default_factory=list)
    created_at: datetime


class SupportArtifactMetadata(BaseModel):
    id: str
    name: str
    artifact_type: str
    uri: str
    created_at: datetime


class SupportBillingSummary(BaseModel):
    meter_count: int = Field(ge=0)
    quantity_by_meter_type: dict[str, float] = Field(default_factory=dict)
    cost_estimate_total: float = 0


class SupportAuditSummary(BaseModel):
    event_count: int = Field(ge=0)
    event_types: list[str] = Field(default_factory=list)
    metadata_keys: list[str] = Field(default_factory=list)


class SupportTraceSummary(BaseModel):
    trace_id: str
    span_count: int = Field(ge=0)
    trace_event_count: int = Field(ge=0)
    guardrail_finding_count: int = Field(ge=0)
    error_category: str | None = None
    span_names: list[str] = Field(default_factory=list)


class SupportRunDebugBundle(BaseModel):
    session_id: str
    run: SupportRunMetadata
    events: list[SupportRunEventSummary] = Field(default_factory=list)
    trace_summary: SupportTraceSummary
    artifacts: list[SupportArtifactMetadata] = Field(default_factory=list)
    billing_summary: SupportBillingSummary
    audit_summary: SupportAuditSummary

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    DETECTED = "detected"
    TRIAGING = "triaging"
    MITIGATING = "mitigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentSeverity(str, Enum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class IncidentCreate(BaseModel):
    severity: IncidentSeverity
    summary: str = Field(min_length=1)
    affected_components: list[str] = Field(default_factory=list)
    affected_tenant_ids: list[str] = Field(default_factory=list)
    owner_user_id: str | None = None
    linked_run_ids: list[str] = Field(default_factory=list)


class Incident(BaseModel):
    id: str
    tenant_id: str
    severity: IncidentSeverity
    status: IncidentStatus
    summary: str
    affected_components: list[str] = Field(default_factory=list)
    affected_tenant_ids: list[str] = Field(default_factory=list)
    started_at: datetime
    resolved_at: datetime | None = None
    owner_user_id: str | None = None
    linked_run_ids: list[str] = Field(default_factory=list)

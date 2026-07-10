from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from taroai.deployment_evidence import (
    RestoreDrillVerificationConfig,
    RestoreDrillVerificationResult,
)
from taroai.domain import new_id, utc_now


class JobType(str, Enum):
    RUN_EXECUTION = "runs.execute"
    TRIGGER_DUE = "triggers.due"
    RESTORE_DRILL_DUE = "restore_drill.due"
    RESTORE_DRILL_EXECUTION = "restore_drill.execute"
    RESTORE_DRILL_EVIDENCE = "restore_drill.evidence"
    CONNECTOR_SYNC = "connectors.sync"
    BILLING_AGGREGATION = "billing.aggregate"
    CLEANUP = "system.cleanup"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class TenantJob(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)


class RunExecutionJob(TenantJob):
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)


class BillingAggregationJob(TenantJob):
    billing_period: str = Field(min_length=1)


class TriggerDueJob(TenantJob):
    trigger_id: str = Field(min_length=1)
    trigger_type: str = Field(min_length=1)
    scheduled_for: datetime
    requested_by_user_id: str = Field(min_length=1)


class RestoreDrillDueJob(TenantJob):
    schedule_id: str = Field(min_length=1)
    scheduled_for: datetime
    requested_by_user_id: str = Field(min_length=1)
    runbook_ref: str = Field(min_length=1)


class RestoreDrillExecutionJob(TenantJob):
    schedule_id: str = Field(min_length=1)
    run_record_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)
    verification_config: RestoreDrillVerificationConfig
    retention_expires_at: datetime | None = None


class RestoreDrillEvidenceCollectionJob(TenantJob):
    schedule_id: str = Field(min_length=1)
    run_record_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)
    verification: RestoreDrillVerificationResult
    retention_expires_at: datetime | None = None


class CleanupJob(TenantJob):
    older_than_days: int = Field(ge=1)
    resource_types: list[str] = Field(default_factory=list)


class JobEnvelope(BaseModel):
    id: str = Field(default_factory=lambda: new_id("job"))
    type: JobType
    payload: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    available_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    worker_id: str | None = None
    attempts: int = 0
    max_attempts: int = Field(default=3, ge=1)
    error: str | None = None

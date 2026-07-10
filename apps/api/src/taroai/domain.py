from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    CLASSIFYING = "classifying"
    RETRIEVING_CONTEXT = "retrieving_context"
    PLANNING = "planning"
    AWAITING_POLICY = "awaiting_policy"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class RunMode(str, Enum):
    CHAT = "chat"
    WORKFLOW = "workflow"
    AUTONOMOUS = "autonomous"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RunCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    agent_id: str | None = None
    message: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)
    mode: RunMode = RunMode.CHAT


class Run(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    agent_id: str | None
    message: str
    attachments: list[str]
    mode: RunMode
    status: RunStatus
    created_at: datetime
    updated_at: datetime


class RunEvent(BaseModel):
    id: str
    sequence: int = Field(ge=1)
    tenant_id: str
    workspace_id: str
    run_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class IdempotencyRecord(BaseModel):
    tenant_id: str
    key: str = Field(min_length=1)
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    request_hash: str = Field(min_length=1)
    status_code: int = Field(ge=100, le=599)
    response_body: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Artifact(BaseModel):
    id: str
    tenant_id: str
    run_id: str
    name: str
    artifact_type: str
    uri: str
    created_at: datetime


class BillingMeterEvent(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    run_id: str | None
    agent_id: str | None
    skill_id: str | None = None
    meter_type: Literal[
        "model_tokens_input",
        "model_tokens_output",
        "model_tokens_cached_input",
        "model_call_count",
        "model_latency_ms",
        "embedding_call_count",
        "embedding_tokens",
        "sandbox_minutes",
        "browser_action_count",
        "tool_call_count",
        "storage_bytes",
        "artifact_bytes",
        "external_artifact_download_bytes",
        "egress_bytes",
        "run_count",
        "skill_call_count",
        "trigger_invocation_count",
        "connector_invocation_count",
        "connector_sync_document_count",
    ]
    quantity: float
    unit: str
    provider: str | None = None
    model: str | None = None
    cost_estimate: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AuditEvent(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str | None
    user_id: str | None
    run_id: str | None
    event_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ApprovalRequest(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    step_id: str
    reason: str
    status: ApprovalStatus
    requested_by_user_id: str | None = None
    resolved_by_user_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None

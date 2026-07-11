from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from taroai.domain import utc_now


class AgentObservation(BaseModel):
    action_id: str = Field(min_length=1)
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    safe_error: str | None = None
    failure_class: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AgentDecision(BaseModel):
    kind: Literal["action", "respond", "request_input", "replan"]
    rationale_summary: str = ""
    action_key: str | None = None
    tool_name: str | None = None
    skill_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
    expected_outcome: str | None = None
    response_text: str | None = None


class AgentVerificationResult(BaseModel):
    outcome: Literal["complete", "repair", "replan", "wait_user", "fail"]
    feedback: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class AgentCycle(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    iteration: int = Field(ge=1)
    plan_revision: int = Field(default=1, ge=1)
    thread_id: str | None = None
    decision: AgentDecision | None = None
    verifier_result: AgentVerificationResult | None = None
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed", "waiting"] = "running"
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class AgentAction(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    cycle_id: str
    action_key: str = Field(min_length=1)
    decision: AgentDecision
    thread_id: str | None = None
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "uncertain",
    ] = "pending"
    observation: AgentObservation | None = None
    failure_class: str | None = None
    lease_owner_id: str | None = None
    lease_expires_at: datetime | None = None
    lease_generation: int = Field(default=0, ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentCheckpoint(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    sequence: int = Field(ge=1)
    state_payload: dict[str, Any]
    checksum: str = Field(min_length=1)
    thread_id: str | None = None
    cycle_id: str | None = None
    last_committed_action_id: str | None = None
    sandbox_checkpoint_ref: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

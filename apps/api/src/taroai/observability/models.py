from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from taroai.domain import AuditEvent, BillingMeterEvent, Run, RunEvent


class TraceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: Literal["internal", "server", "client", "producer", "consumer"] = "internal"
    status: Literal["ok", "error"] = "ok"
    started_at: datetime
    ended_at: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)


TraceEventSource = Literal["run_event", "billing_meter", "audit_event"]


class TraceEvent(BaseModel):
    trace_id: str
    span_id: str
    name: str
    source: TraceEventSource
    occurred_at: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)


class GuardrailTraceFinding(BaseModel):
    trace_id: str
    source_audit_event_id: str
    event_type: str
    stage: str | None = None
    action: str | None = None
    severity: str | None = None
    message: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    detector_finding_ids: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    user_id: str | None = None


class TraceExportResult(BaseModel):
    status: Literal["exported", "disabled", "failed"]
    trace_id: str
    span_count: int
    destination: str | None = None
    error_type: str | None = None
    message: str | None = None


ErrorCategory = Literal[
    "policy_denied",
    "tool_failed",
    "sandbox_failed",
    "model_failed",
    "timeout",
    "approval_rejected",
    "unknown",
]


class ErrorClassification(BaseModel):
    category: ErrorCategory
    source_event_type: str | None = None
    message: str | None = None


class RunTrace(BaseModel):
    run: Run
    events: list[RunEvent]
    spans: list[TraceSpan] = Field(default_factory=list)
    trace_events: list[TraceEvent] = Field(default_factory=list)
    guardrail_findings: list[GuardrailTraceFinding] = Field(default_factory=list)
    error_classification: ErrorClassification | None = None
    billing_meters: list[BillingMeterEvent]
    audit_events: list[AuditEvent]

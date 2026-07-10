import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import (
    Artifact,
    AuditEvent,
    ApprovalRequest,
    ApprovalStatus,
    BillingMeterEvent,
    IdempotencyRecord,
    Run,
    RunCreate,
    RunEvent,
    RunStatus,
    new_id,
    utc_now,
)
from taroai.errors import NotFoundError, RunTransitionError, TenantAccessError
from taroai.licensing.models import LicenseValidationResult


TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}

RETRYABLE_RUN_STATUSES = {
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}


class RunStateSnapshot(BaseModel):
    tenant_id: str
    workspace_id: str
    user_id: str
    run_id: str
    goal: str
    status: RunStatus
    plan: list[dict[str, Any]] = Field(default_factory=list)
    current_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    approved_step_ids: list[str] = Field(default_factory=list)
    approved_guardrail_keys: list[str] = Field(default_factory=list)
    pending_guardrail_approval_key: str | None = None
    pending_guardrail_approval_stage: str | None = None
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_context: dict[str, Any] = Field(default_factory=dict)
    sandbox_session_id: str | None = None
    browser_session_id: str | None = None
    approval_id: str | None = None
    failure_reason: str | None = None
    updated_at: datetime

    @classmethod
    def from_runtime_state(cls, state: Any) -> "RunStateSnapshot":
        return cls(
            **state.model_dump(mode="json"),
            updated_at=utc_now(),
        )

    def to_runtime_state_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"updated_at"})


class InMemoryControlPlaneStore(BaseModel):
    runs: dict[str, Run] = Field(default_factory=dict)
    run_events: dict[str, list[RunEvent]] = Field(default_factory=dict)
    artifacts: dict[str, list[Artifact]] = Field(default_factory=dict)
    billing_meters: dict[str, list[BillingMeterEvent]] = Field(default_factory=dict)
    audit_events: dict[str, list[AuditEvent]] = Field(default_factory=dict)
    approval_requests: dict[str, list[ApprovalRequest]] = Field(default_factory=dict)
    runtime_states: dict[str, RunStateSnapshot] = Field(default_factory=dict)
    idempotency_records: dict[str, IdempotencyRecord] = Field(default_factory=dict)
    license_validations: dict[str, LicenseValidationResult] = Field(default_factory=dict)

    def create_run(self, tenant_id: str, user_id: str, payload: RunCreate) -> Run:
        now = utc_now()
        run = Run(
            id=new_id("run"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            user_id=user_id,
            agent_id=payload.agent_id,
            message=payload.message,
            attachments=payload.attachments,
            mode=payload.mode,
            status=RunStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        self.runs[run.id] = run
        self._append_run_event(
            run,
            "run.created",
            {
                "status": run.status.value,
                "mode": run.mode.value,
                "agent_id": run.agent_id,
            },
        )
        self._record_run_meter(run)
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=user_id,
            run_id=run.id,
            event_type="run.created",
            metadata={"mode": run.mode.value, "agent_id": run.agent_id},
        )
        return run

    def get_run(self, tenant_id: str, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if run.tenant_id != tenant_id:
            raise TenantAccessError(f"Run {run_id} is not in tenant {tenant_id}")
        return run

    def get_idempotency_record(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
    ) -> IdempotencyRecord | None:
        return self.idempotency_records.get(
            self._idempotency_record_key(tenant_id, key, method, path)
        )

    def save_idempotency_record(self, record: IdempotencyRecord) -> IdempotencyRecord:
        self.idempotency_records[
            self._idempotency_record_key(
                record.tenant_id,
                record.key,
                record.method,
                record.path,
            )
        ] = record
        return record

    def save_license_validation(
        self,
        validation: LicenseValidationResult,
    ) -> LicenseValidationResult:
        self.license_validations[validation.license.tenant_id] = validation.model_copy(deep=True)
        return validation

    def get_active_license_validation(
        self,
        tenant_id: str,
    ) -> LicenseValidationResult | None:
        validation = self.license_validations.get(tenant_id)
        if validation is None:
            return None
        return validation.model_copy(deep=True)

    def list_runs(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        status: RunStatus | None = None,
    ) -> list[Run]:
        return [
            run
            for run in self.runs.values()
            if run.tenant_id == tenant_id
            and (workspace_id is None or run.workspace_id == workspace_id)
            and (status is None or run.status == status)
        ]

    def list_run_events(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int | None = None,
    ) -> list[RunEvent]:
        self.get_run(tenant_id, run_id)
        events = list(self.run_events.get(run_id, []))
        if after_sequence is None:
            return events
        return [event for event in events if event.sequence > after_sequence]

    def update_run_status(
        self,
        tenant_id: str,
        run_id: str,
        status: RunStatus,
        emit_status_event: bool = True,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        updated_run = run.model_copy(update={"status": status, "updated_at": utc_now()})
        self.runs[run_id] = updated_run
        if emit_status_event:
            self.append_run_event(
                updated_run,
                "run.status_changed",
                {"status": status.value},
            )
        return updated_run

    def cancel_run(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
        reason_code: str,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            raise RunTransitionError(f"Run {run_id} cannot be cancelled from {run.status.value}")
        cancelled_run = run.model_copy(
            update={"status": RunStatus.CANCELLED, "updated_at": utc_now()}
        )
        self.runs[run_id] = cancelled_run
        metadata = {
            "cancelled_by_user_id": cancelled_by_user_id,
            "reason_code": reason_code,
            "status": RunStatus.CANCELLED.value,
        }
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=cancelled_by_user_id,
            run_id=run_id,
            event_type="run.cancelled",
            metadata=metadata,
        )
        self._append_run_event(cancelled_run, "run.cancelled", metadata)
        return cancelled_run

    def request_run_retry(
        self,
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        reason_code: str,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        if run.status not in RETRYABLE_RUN_STATUSES:
            raise RunTransitionError(f"Run {run_id} cannot be retried from {run.status.value}")
        retrying_run = run.model_copy(
            update={"status": RunStatus.RETRYING, "updated_at": utc_now()}
        )
        self.runs[run_id] = retrying_run
        metadata = {
            "requested_by_user_id": requested_by_user_id,
            "reason_code": reason_code,
            "previous_status": run.status.value,
            "status": RunStatus.RETRYING.value,
        }
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=requested_by_user_id,
            run_id=run_id,
            event_type="run.retry_requested",
            metadata=metadata,
        )
        self._append_run_event(retrying_run, "run.retry_requested", metadata)
        return retrying_run

    def append_run_event(self, run: Run, event_type: str, payload: dict) -> RunEvent:
        return self._append_run_event(run, event_type, payload)

    def create_artifact(
        self,
        tenant_id: str,
        run_id: str,
        name: str,
        artifact_type: str,
        uri: str,
    ) -> Artifact:
        run = self.get_run(tenant_id, run_id)
        artifact = Artifact(
            id=new_id("artifact"),
            tenant_id=tenant_id,
            run_id=run_id,
            name=name,
            artifact_type=artifact_type,
            uri=uri,
            created_at=utc_now(),
        )
        self.artifacts.setdefault(run_id, []).append(artifact)
        self._append_run_event(
            run,
            "artifact.created",
            {"artifact_id": artifact.id, "name": artifact.name, "type": artifact.artifact_type},
        )
        return artifact

    def list_artifacts(self, tenant_id: str, run_id: str) -> list[Artifact]:
        self.get_run(tenant_id, run_id)
        return list(self.artifacts.get(run_id, []))

    def create_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        step_id: str,
        reason: str,
    ) -> ApprovalRequest:
        run = self.get_run(tenant_id, run_id)
        approval = ApprovalRequest(
            id=new_id("approval"),
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            status=ApprovalStatus.PENDING,
            requested_by_user_id=run.user_id,
            created_at=utc_now(),
        )
        self.approval_requests.setdefault(run_id, []).append(approval)
        self._append_run_event(
            run,
            "approval.requested",
            {"approval_id": approval.id, "step_id": step_id, "reason": reason},
        )
        return approval

    def resolve_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        approved_by_user_id: str,
    ) -> ApprovalRequest:
        return self._complete_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            status=ApprovalStatus.APPROVED,
            resolved_by_user_id=approved_by_user_id,
            event_type="approval.resolved",
        )

    def reject_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        rejected_by_user_id: str,
    ) -> ApprovalRequest:
        return self._complete_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            status=ApprovalStatus.REJECTED,
            resolved_by_user_id=rejected_by_user_id,
            event_type="approval.rejected",
        )

    def cancel_pending_approval_requests(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
    ) -> list[ApprovalRequest]:
        run = self.get_run(tenant_id, run_id)
        approvals = self.approval_requests.get(run_id, [])
        cancelled: list[ApprovalRequest] = []
        for index, approval in enumerate(approvals):
            if approval.status != ApprovalStatus.PENDING:
                continue
            resolved = approval.model_copy(
                update={
                    "status": ApprovalStatus.CANCELLED,
                    "resolved_by_user_id": cancelled_by_user_id,
                    "resolved_at": utc_now(),
                }
            )
            approvals[index] = resolved
            metadata = {
                "approval_id": approval.id,
                "status": ApprovalStatus.CANCELLED.value,
                "resolved_by_user_id": cancelled_by_user_id,
            }
            self._record_audit_event(
                tenant_id=tenant_id,
                workspace_id=run.workspace_id,
                user_id=cancelled_by_user_id,
                run_id=run_id,
                event_type="approval.cancelled",
                metadata=metadata,
            )
            self._append_run_event(run, "approval.cancelled", metadata)
            cancelled.append(resolved)
        return cancelled

    def _complete_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        status: ApprovalStatus,
        resolved_by_user_id: str,
        event_type: str,
    ) -> ApprovalRequest:
        run = self.get_run(tenant_id, run_id)
        approvals = self.approval_requests.get(run_id, [])
        for index, approval in enumerate(approvals):
            if approval.id == approval_id:
                resolved = approval.model_copy(
                    update={
                        "status": status,
                        "resolved_by_user_id": resolved_by_user_id,
                        "resolved_at": utc_now(),
                    }
                )
                approvals[index] = resolved
                self._append_run_event(
                    run,
                    event_type,
                    {
                        "approval_id": approval_id,
                        "status": status.value,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                )
                self._record_audit_event(
                    tenant_id=tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=resolved_by_user_id,
                    run_id=run_id,
                    event_type=event_type,
                    metadata={
                        "approval_id": approval_id,
                        "status": status.value,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                )
                return resolved
        raise NotFoundError(f"Approval request not found: {approval_id}")

    def list_approval_requests(self, tenant_id: str, run_id: str) -> list[ApprovalRequest]:
        self.get_run(tenant_id, run_id)
        return list(self.approval_requests.get(run_id, []))

    def save_runtime_state(self, state: Any) -> RunStateSnapshot:
        self.get_run(state.tenant_id, state.run_id)
        snapshot = RunStateSnapshot.from_runtime_state(state)
        self.runtime_states[state.run_id] = snapshot
        return snapshot

    def get_runtime_state(self, tenant_id: str, run_id: str) -> RunStateSnapshot:
        self.get_run(tenant_id, run_id)
        snapshot = self.runtime_states.get(run_id)
        if snapshot is None:
            raise NotFoundError(f"Runtime state not found: {run_id}")
        return snapshot

    def list_billing_meters(self, tenant_id: str) -> list[BillingMeterEvent]:
        return list(self.billing_meters.get(tenant_id, []))

    def list_audit_events(self, tenant_id: str) -> list[AuditEvent]:
        return list(self.audit_events.get(tenant_id, []))

    def record_billing_meter(
        self,
        tenant_id: str,
        run_id: str | None,
        meter_type: str,
        quantity: float,
        unit: str,
        metadata: dict[str, Any] | None = None,
        skill_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cost_estimate: float | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> BillingMeterEvent:
        run = self.get_run(tenant_id, run_id) if run_id is not None else None
        resolved_workspace_id = run.workspace_id if run is not None else workspace_id
        resolved_user_id = run.user_id if run is not None else user_id
        resolved_agent_id = run.agent_id if run is not None else agent_id
        if resolved_workspace_id is None or resolved_user_id is None:
            raise ValueError("workspace_id and user_id are required when run_id is not provided")
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=resolved_user_id,
            run_id=run.id if run is not None else None,
            agent_id=resolved_agent_id,
            skill_id=skill_id,
            meter_type=meter_type,
            quantity=quantity,
            unit=unit,
            provider=provider,
            model=model,
            cost_estimate=cost_estimate,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        self.billing_meters.setdefault(meter.tenant_id, []).append(meter)
        if run is not None:
            self._append_run_event(
                run,
                "billing.metered",
                {"meter_id": meter.id, "type": meter.meter_type},
            )
        self._record_billing_audit_event(meter)
        return meter

    def record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        if run_id is not None:
            self.get_run(tenant_id, run_id)
        return self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata or {},
        )

    def _append_run_event(self, run: Run, event_type: str, payload: dict) -> RunEvent:
        sequence = len(self.run_events.get(run.id, [])) + 1
        event = RunEvent(
            id=new_id("event"),
            sequence=sequence,
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            type=event_type,
            payload=payload,
            created_at=utc_now(),
        )
        self.run_events.setdefault(run.id, []).append(event)
        return event

    def _idempotency_record_key(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
    ) -> str:
        return json.dumps([tenant_id, key, method, path], separators=(",", ":"))

    def _record_run_meter(self, run: Run) -> BillingMeterEvent:
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            agent_id=run.agent_id,
            meter_type="run_count",
            quantity=1,
            unit="run",
            metadata={"mode": run.mode.value},
            created_at=utc_now(),
        )
        self.billing_meters.setdefault(run.tenant_id, []).append(meter)
        self._append_run_event(run, "billing.metered", {"meter_id": meter.id, "type": meter.meter_type})
        self._record_billing_audit_event(meter)
        return meter

    def _record_billing_audit_event(self, meter: BillingMeterEvent) -> AuditEvent:
        return self._record_audit_event(
            tenant_id=meter.tenant_id,
            workspace_id=meter.workspace_id,
            user_id=meter.user_id,
            run_id=meter.run_id,
            event_type="billing.metered",
            metadata=self._billing_audit_metadata(meter),
        )

    def _billing_audit_metadata(self, meter: BillingMeterEvent) -> dict[str, Any]:
        return {
            "meter_id": meter.id,
            "meter_type": meter.meter_type,
            "quantity": meter.quantity,
            "unit": meter.unit,
            "skill_id": meter.skill_id,
            "provider": meter.provider,
            "model": meter.model,
            "cost_estimate": meter.cost_estimate,
        }

    def _record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict,
    ) -> AuditEvent:
        audit_event = AuditEvent(
            id=new_id("audit"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata,
            created_at=utc_now(),
        )
        self.audit_events.setdefault(tenant_id, []).append(audit_event)
        if run_id is not None:
            run = self.runs[run_id]
            self._append_run_event(run, "audit.recorded", {"audit_event_id": audit_event.id})
        return audit_event

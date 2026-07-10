from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from taroai.domain import AuditEvent, utc_now
from taroai.triggers.models import TriggerDefinition, TriggerStatus, TriggerType


TriggerOperationalStatus = Literal["disabled", "failing", "healthy", "stuck"]


class TriggerOperationSummary(BaseModel):
    tenant_id: str
    trigger_id: str
    workspace_id: str
    trigger_type: TriggerType
    trigger_status: TriggerStatus
    status: TriggerOperationalStatus
    status_reason: str
    recommended_action: str
    next_run_at: datetime | None = None
    last_invoked_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_failure_reason_code: str | None = None
    last_schedule_evaluated_at: datetime | None = None


class TriggerOperationsResponse(BaseModel):
    tenant_id: str
    stuck_after_seconds: int = Field(ge=1)
    counts: dict[TriggerOperationalStatus, int]
    summaries: list[TriggerOperationSummary] = Field(default_factory=list)


class TriggerOperationsService(BaseModel):
    stuck_after_seconds: int = Field(default=900, ge=1)

    def summarize(
        self,
        triggers: list[TriggerDefinition],
        audit_events: list[AuditEvent],
        now: datetime | None = None,
        tenant_id: str | None = None,
    ) -> TriggerOperationsResponse:
        resolved_now = now or utc_now()
        summaries = [
            self._summary_for_trigger(trigger, audit_events, resolved_now)
            for trigger in triggers
        ]
        return TriggerOperationsResponse(
            tenant_id=tenant_id or (triggers[0].tenant_id if triggers else ""),
            stuck_after_seconds=self.stuck_after_seconds,
            counts=self._counts(summaries),
            summaries=summaries,
        )

    def _summary_for_trigger(
        self,
        trigger: TriggerDefinition,
        audit_events: list[AuditEvent],
        now: datetime,
    ) -> TriggerOperationSummary:
        trigger_events = self._events_for_trigger(trigger.id, audit_events)
        last_invoked = self._latest_event(trigger_events, "trigger.invoked")
        last_failure = self._latest_event(trigger_events, "trigger.failed")
        last_schedule_evaluated = self._latest_event(
            trigger_events,
            "trigger.schedule.evaluated",
        )
        status, reason, action = self._status_for_trigger(
            trigger=trigger,
            now=now,
            last_invoked=last_invoked,
            last_failure=last_failure,
        )
        return TriggerOperationSummary(
            tenant_id=trigger.tenant_id,
            trigger_id=trigger.id,
            workspace_id=trigger.workspace_id,
            trigger_type=trigger.type,
            trigger_status=trigger.status,
            status=status,
            status_reason=reason,
            recommended_action=action,
            next_run_at=trigger.next_run_at,
            last_invoked_at=last_invoked.created_at if last_invoked is not None else None,
            last_failure_at=last_failure.created_at if last_failure is not None else None,
            last_failure_reason_code=(
                str(last_failure.metadata.get("reason_code"))
                if last_failure is not None and last_failure.metadata.get("reason_code")
                else None
            ),
            last_schedule_evaluated_at=(
                last_schedule_evaluated.created_at
                if last_schedule_evaluated is not None
                else None
            ),
        )

    def _status_for_trigger(
        self,
        trigger: TriggerDefinition,
        now: datetime,
        last_invoked: AuditEvent | None,
        last_failure: AuditEvent | None,
    ) -> tuple[TriggerOperationalStatus, str, str]:
        if trigger.status == TriggerStatus.DISABLED:
            return ("disabled", "trigger_disabled", "enable_trigger_when_ready")
        if last_failure is not None and (
            last_invoked is None or last_failure.created_at >= last_invoked.created_at
        ):
            return ("failing", "recent_trigger_failure", "inspect_trigger_failure_audit")
        if self._schedule_is_stuck(trigger, now):
            return (
                "stuck",
                "schedule_next_run_overdue",
                "inspect_trigger_scheduler_worker",
            )
        return ("healthy", "no_current_trigger_operations_issue", "no_action_required")

    def _schedule_is_stuck(self, trigger: TriggerDefinition, now: datetime) -> bool:
        if trigger.type != TriggerType.SCHEDULE or trigger.next_run_at is None:
            return False
        overdue_cutoff = now - timedelta(seconds=self.stuck_after_seconds)
        return trigger.next_run_at < overdue_cutoff

    def _events_for_trigger(
        self,
        trigger_id: str,
        audit_events: list[AuditEvent],
    ) -> list[AuditEvent]:
        return [
            event
            for event in audit_events
            if event.metadata.get("trigger_id") == trigger_id
        ]

    def _latest_event(
        self,
        audit_events: list[AuditEvent],
        event_type: str,
    ) -> AuditEvent | None:
        matching = [event for event in audit_events if event.event_type == event_type]
        if not matching:
            return None
        return sorted(matching, key=lambda event: (event.created_at, event.id))[-1]

    def _counts(
        self,
        summaries: list[TriggerOperationSummary],
    ) -> dict[TriggerOperationalStatus, int]:
        counts: dict[TriggerOperationalStatus, int] = {
            "disabled": 0,
            "failing": 0,
            "healthy": 0,
            "stuck": 0,
        }
        for summary in summaries:
            counts[summary.status] += 1
        return counts

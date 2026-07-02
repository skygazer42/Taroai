from typing import Any

from pydantic import BaseModel

from taroai.domain import AuditEvent, BillingMeterEvent, Run, RunEvent, RunStatus
from taroai.observability.models import (
    ErrorClassification,
    GuardrailTraceFinding,
    RunTrace,
    TraceEvent,
    TraceExportResult,
    TraceSpan,
)


TRACE_REDACTED_METADATA_VALUE = "[REDACTED]"
TRACE_SENSITIVE_METADATA_KEYS = {
    "content",
    "input",
    "messages",
    "model_input",
    "model_output",
    "output",
    "prompt",
    "raw_content",
    "raw_input",
    "raw_messages",
    "raw_output",
    "raw_prompt",
    "raw_response",
    "response",
    "tool_input",
    "tool_output",
}


class RunTraceService(BaseModel):
    exporter: Any | None = None

    def build(self, store: Any, tenant_id: str, run_id: str) -> RunTrace:
        run = store.get_run(tenant_id, run_id)
        events = store.list_run_events(tenant_id, run_id)
        billing_meters = [
            meter for meter in store.list_billing_meters(tenant_id) if meter.run_id == run_id
        ]
        audit_events = self._trace_safe_audit_events(
            [event for event in store.list_audit_events(tenant_id) if event.run_id == run_id]
        )
        return RunTrace(
            run=run,
            events=events,
            spans=self.build_spans(run, events, billing_meters, audit_events),
            trace_events=self.build_trace_events(run, events, billing_meters, audit_events),
            guardrail_findings=self.build_guardrail_findings(run, audit_events),
            error_classification=self.classify_error(run, audit_events, events),
            billing_meters=billing_meters,
            audit_events=audit_events,
        )

    def export(self, store: Any, tenant_id: str, run_id: str) -> TraceExportResult:
        trace = self.build(store=store, tenant_id=tenant_id, run_id=run_id)
        if self.exporter is None:
            return TraceExportResult(
                status="disabled",
                trace_id=trace.run.id,
                span_count=len(trace.spans),
                message="Trace exporter is disabled",
            )
        return self.exporter.export(trace)

    def _trace_safe_audit_events(self, audit_events: list[AuditEvent]) -> list[AuditEvent]:
        return [
            event.model_copy(
                update={"metadata": self._redact_trace_metadata(event.metadata)},
                deep=True,
            )
            for event in audit_events
        ]

    def _redact_trace_metadata(self, value):
        if isinstance(value, dict):
            return {
                key: (
                    TRACE_REDACTED_METADATA_VALUE
                    if self._is_trace_sensitive_metadata_key(key)
                    else self._redact_trace_metadata(nested)
                )
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [self._redact_trace_metadata(item) for item in value]
        return value

    def _is_trace_sensitive_metadata_key(self, key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        if normalized in TRACE_SENSITIVE_METADATA_KEYS:
            return True
        return normalized.startswith("raw_") or normalized.endswith(
            (
                "_content",
                "_input",
                "_messages",
                "_output",
                "_prompt",
                "_response",
            )
        )

    def build_spans(
        self,
        run: Run,
        events: list[RunEvent],
        billing_meters: list[BillingMeterEvent],
        audit_events: list[AuditEvent],
    ) -> list[TraceSpan]:
        root_span = TraceSpan(
            trace_id=run.id,
            span_id=f"run:{run.id}",
            name="run",
            started_at=run.created_at,
            ended_at=run.updated_at,
            status="error" if run.status == "failed" else "ok",
            attributes={
                "tenant_id": run.tenant_id,
                "workspace_id": run.workspace_id,
                "user_id": run.user_id,
                "run_id": run.id,
                "agent_id": run.agent_id,
                "status": run.status,
            },
        )
        return [
            root_span,
            *self.build_runtime_spans(run, events),
            *[self._event_span(run, event) for event in events],
            *[self._billing_span(run, meter) for meter in billing_meters],
            *[self._audit_span(run, event) for event in audit_events],
        ]

    def build_runtime_spans(self, run: Run, events: list[RunEvent]) -> list[TraceSpan]:
        spans: list[TraceSpan] = []
        context_event = self._first_event(events, "context.loaded")
        plan_event = self._first_event(events, "plan.created")
        if context_event is not None:
            spans.append(
                self._runtime_span(
                    run=run,
                    event=context_event,
                    name="runtime.context_load",
                    attributes={
                        "knowledge_result_count": self._number_payload(
                            context_event,
                            "knowledge_result_count",
                        ),
                        "memory_record_count": self._number_payload(
                            context_event,
                            "memory_record_count",
                        ),
                        "status": "ok",
                    },
                )
            )
        if plan_event is not None:
            start_event = context_event or plan_event
            spans.append(
                self._runtime_span(
                    run=run,
                    event=plan_event,
                    name="runtime.planning",
                    started_at=start_event.created_at,
                    attributes={
                        "planned_step_count": self._planned_step_count(plan_event),
                        "status": "ok",
                    },
                )
            )
        spans.extend(self._runtime_step_spans(run, events))
        spans.extend(self._runtime_tool_call_spans(run, events))
        spans.extend(self._runtime_artifact_spans(run, events))
        spans.extend(self._runtime_approval_spans(run, events))
        return spans

    def build_trace_events(
        self,
        run: Run,
        events: list[RunEvent],
        billing_meters: list[BillingMeterEvent],
        audit_events: list[AuditEvent],
    ) -> list[TraceEvent]:
        ordered: list[tuple[Any, int, TraceEvent]] = []
        for index, event in enumerate(events):
            ordered.append((event.created_at, index, self._run_trace_event(run, event)))
        offset = len(ordered)
        for index, meter in enumerate(billing_meters):
            ordered.append((meter.created_at, offset + index, self._billing_trace_event(run, meter)))
        offset = len(ordered)
        for index, event in enumerate(audit_events):
            ordered.append((event.created_at, offset + index, self._audit_trace_event(run, event)))
        return [item[2] for item in sorted(ordered, key=lambda item: (item[0], item[1]))]

    def build_guardrail_findings(
        self,
        run: Run,
        audit_events: list[AuditEvent],
    ) -> list[GuardrailTraceFinding]:
        return [
            self._guardrail_finding(run, event)
            for event in audit_events
            if event.event_type.startswith("guardrail.")
            or event.event_type.startswith("tool.guardrail_")
        ]

    def _runtime_step_spans(self, run: Run, events: list[RunEvent]) -> list[TraceSpan]:
        spans: list[TraceSpan] = []
        for index, event in enumerate(events):
            if event.type != "step.started":
                continue
            step_id = self._string_payload(event, "step_id")
            terminal = self._first_after(
                events,
                index,
                lambda candidate: self._payload_matches(candidate, "step_id", step_id)
                and candidate.type
                in {
                    "tool_call.completed",
                    "tool_call.failed",
                    "tool_call.approval_required",
                    "run.failed",
                },
            )
            status = "error" if terminal is not None and terminal.type in {
                "tool_call.failed",
                "run.failed",
            } else "ok"
            spans.append(
                self._runtime_span(
                    run=run,
                    event=event,
                    name="runtime.step",
                    ended_at=(terminal or event).created_at,
                    status=status,
                    attributes={
                        "step_id": step_id,
                        "title": self._string_payload(event, "title"),
                        "status": status,
                    },
                )
            )
        return spans

    def _runtime_tool_call_spans(self, run: Run, events: list[RunEvent]) -> list[TraceSpan]:
        spans: list[TraceSpan] = []
        for index, event in enumerate(events):
            if event.type != "tool_call.started":
                continue
            step_id = self._string_payload(event, "step_id")
            terminal = self._first_after(
                events,
                index,
                lambda candidate: self._payload_matches(candidate, "step_id", step_id)
                and candidate.type
                in {
                    "tool_call.completed",
                    "tool_call.failed",
                    "tool_call.approval_required",
                },
            )
            status = "error" if terminal is not None and terminal.type == "tool_call.failed" else "ok"
            spans.append(
                self._runtime_span(
                    run=run,
                    event=event,
                    name="runtime.tool_call",
                    ended_at=(terminal or event).created_at,
                    status=status,
                    attributes={
                        "step_id": step_id,
                        "tool_name": self._string_payload(event, "tool_name"),
                        "attempt": self._number_payload(event, "attempt"),
                        "status": status,
                    },
                )
            )
        return spans

    def _runtime_artifact_spans(self, run: Run, events: list[RunEvent]) -> list[TraceSpan]:
        spans: list[TraceSpan] = []
        for index, event in enumerate(events):
            if event.type != "artifact.created":
                continue
            terminal = self._first_after(
                events,
                index,
                lambda candidate: candidate.type in {"run.succeeded", "run.failed"},
            )
            status = "error" if terminal is not None and terminal.type == "run.failed" else "ok"
            spans.append(
                self._runtime_span(
                    run=run,
                    event=event,
                    name="runtime.artifact",
                    ended_at=(terminal or event).created_at,
                    status=status,
                    attributes={
                        "artifact_id": self._string_payload(event, "artifact_id"),
                        "artifact_name": self._string_payload(event, "name"),
                        "artifact_type": self._string_payload(event, "type"),
                        "status": status,
                    },
                )
            )
        return spans

    def _runtime_approval_spans(self, run: Run, events: list[RunEvent]) -> list[TraceSpan]:
        spans: list[TraceSpan] = []
        for index, event in enumerate(events):
            if event.type != "approval.requested":
                continue
            approval_id = self._string_payload(event, "approval_id")
            terminal = self._first_after(
                events,
                index,
                lambda candidate: self._payload_matches(candidate, "approval_id", approval_id)
                and candidate.type == "approval.resolved",
            )
            spans.append(
                self._runtime_span(
                    run=run,
                    event=event,
                    name="runtime.approval",
                    ended_at=(terminal or event).created_at,
                    attributes={
                        "approval_id": approval_id,
                        "step_id": self._string_payload(event, "step_id"),
                        "status": (
                            self._string_payload(terminal, "status")
                            if terminal is not None
                            else "pending"
                        ),
                    },
                )
            )
        return spans

    def _runtime_span(
        self,
        run: Run,
        event: RunEvent,
        name: str,
        attributes: dict[str, Any],
        started_at: Any | None = None,
        ended_at: Any | None = None,
        status: str = "ok",
    ) -> TraceSpan:
        return TraceSpan(
            trace_id=run.id,
            span_id=f"{name}:{event.id}",
            parent_span_id=f"run:{run.id}",
            name=name,
            status=status,
            started_at=started_at or event.created_at,
            ended_at=ended_at or event.created_at,
            attributes={key: value for key, value in attributes.items() if value is not None},
        )

    def _first_event(self, events: list[RunEvent], event_type: str) -> RunEvent | None:
        return next((event for event in events if event.type == event_type), None)

    def _first_after(self, events: list[RunEvent], index: int, predicate) -> RunEvent | None:
        return next((event for event in events[index + 1:] if predicate(event)), None)

    def _payload_matches(self, event: RunEvent, key: str, expected: str | None) -> bool:
        if expected is None:
            return key not in event.payload
        return event.payload.get(key) == expected

    def _string_payload(self, event: RunEvent | None, key: str) -> str | None:
        if event is None:
            return None
        value = event.payload.get(key)
        if isinstance(value, str):
            return value
        return None

    def _number_payload(self, event: RunEvent, key: str) -> int | float | None:
        value = event.payload.get(key)
        if isinstance(value, int | float):
            return value
        return None

    def _planned_step_count(self, event: RunEvent) -> int:
        steps = event.payload.get("steps")
        if isinstance(steps, list):
            return len(steps)
        return 0

    def _event_span(self, run: Run, event: RunEvent) -> TraceSpan:
        return TraceSpan(
            trace_id=run.id,
            span_id=f"event:{event.id}",
            parent_span_id=f"run:{run.id}",
            name=f"event.{event.type}",
            started_at=event.created_at,
            ended_at=event.created_at,
            attributes={
                "run_event_id": event.id,
                "event_type": event.type,
                "workspace_id": event.workspace_id,
            },
        )

    def _billing_span(self, run: Run, meter: BillingMeterEvent) -> TraceSpan:
        return TraceSpan(
            trace_id=run.id,
            span_id=f"billing:{meter.id}",
            parent_span_id=f"run:{run.id}",
            name=f"billing.{meter.meter_type}",
            started_at=meter.created_at,
            ended_at=meter.created_at,
            attributes={
                "meter_id": meter.id,
                "meter_type": meter.meter_type,
                "quantity": meter.quantity,
                "unit": meter.unit,
                "cost_estimate": meter.cost_estimate,
                "workspace_id": meter.workspace_id,
                "user_id": meter.user_id,
                "agent_id": meter.agent_id,
                "skill_id": meter.skill_id,
            },
        )

    def _audit_span(self, run: Run, event: AuditEvent) -> TraceSpan:
        return TraceSpan(
            trace_id=run.id,
            span_id=f"audit:{event.id}",
            parent_span_id=f"run:{run.id}",
            name=f"audit.{event.event_type}",
            started_at=event.created_at,
            ended_at=event.created_at,
            attributes={
                "audit_event_id": event.id,
                "event_type": event.event_type,
                "workspace_id": event.workspace_id,
                "user_id": event.user_id,
            },
        )

    def _run_trace_event(self, run: Run, event: RunEvent) -> TraceEvent:
        return TraceEvent(
            trace_id=run.id,
            span_id=f"event:{event.id}",
            name=event.type,
            source="run_event",
            occurred_at=event.created_at,
            attributes={
                "run_event_id": event.id,
                "event_type": event.type,
                "workspace_id": event.workspace_id,
                "payload": event.payload,
            },
        )

    def _billing_trace_event(self, run: Run, meter: BillingMeterEvent) -> TraceEvent:
        return TraceEvent(
            trace_id=run.id,
            span_id=f"billing:{meter.id}",
            name=f"billing.{meter.meter_type}",
            source="billing_meter",
            occurred_at=meter.created_at,
            attributes={
                "meter_id": meter.id,
                "meter_type": meter.meter_type,
                "quantity": meter.quantity,
                "unit": meter.unit,
                "cost_estimate": meter.cost_estimate,
                "workspace_id": meter.workspace_id,
                "user_id": meter.user_id,
                "agent_id": meter.agent_id,
                "skill_id": meter.skill_id,
                "provider": meter.provider,
                "model": meter.model,
                "metadata": meter.metadata,
            },
        )

    def _audit_trace_event(self, run: Run, event: AuditEvent) -> TraceEvent:
        return TraceEvent(
            trace_id=run.id,
            span_id=f"audit:{event.id}",
            name=f"audit.{event.event_type}",
            source="audit_event",
            occurred_at=event.created_at,
            attributes={
                "audit_event_id": event.id,
                "event_type": event.event_type,
                "workspace_id": event.workspace_id,
                "user_id": event.user_id,
                "metadata": event.metadata,
            },
        )

    def _guardrail_finding(self, run: Run, event: AuditEvent) -> GuardrailTraceFinding:
        return GuardrailTraceFinding(
            trace_id=run.id,
            source_audit_event_id=event.id,
            event_type=event.event_type,
            stage=self._string_metadata(event, "stage"),
            action=self._string_metadata(event, "guardrail_action"),
            severity=self._string_metadata(event, "severity"),
            message=self._string_metadata(event, "message"),
            rule_ids=self._list_metadata(event, "guardrail_rule_ids"),
            detector_finding_ids=self._list_metadata(
                event,
                "guardrail_detector_finding_ids",
            ),
            workspace_id=event.workspace_id,
            user_id=event.user_id,
        )

    def _string_metadata(self, event: AuditEvent, key: str) -> str | None:
        value = event.metadata.get(key)
        if isinstance(value, str):
            return value
        return None

    def _list_metadata(self, event: AuditEvent, key: str) -> list[str]:
        value = event.metadata.get(key)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    def classify_error(
        self,
        run: Run,
        audit_events: list[AuditEvent],
        run_events: list[RunEvent],
    ) -> ErrorClassification | None:
        if run.status != RunStatus.FAILED:
            return None
        for event in reversed(audit_events):
            category = self._category_for_audit_event(event.event_type)
            if category is not None:
                return ErrorClassification(
                    category=category,
                    source_event_type=event.event_type,
                    message=self._classification_message(event),
                )
        for event in reversed(run_events):
            category = self._category_for_run_event(event.type)
            if category is not None:
                return ErrorClassification(
                    category=category,
                    source_event_type=event.type,
                    message=self._run_event_message(event),
                )
        return ErrorClassification(category="unknown", message="run failed")

    def _category_for_audit_event(self, event_type: str) -> str | None:
        if event_type in {"tool.failed", "tool.execution_failed"}:
            return "tool_failed"
        if event_type in {
            "tool.blocked",
            "policy.denied",
            "guardrail.blocked",
            "model.policy_denied",
        }:
            return "policy_denied"
        if event_type.startswith("sandbox.") and "failed" in event_type:
            return "sandbox_failed"
        if event_type.startswith("model.") and (
            "failed" in event_type or event_type == "model.budget_exceeded"
        ):
            return "model_failed"
        if event_type in {"approval.rejected", "approval.denied"}:
            return "approval_rejected"
        if "timeout" in event_type or "timed_out" in event_type:
            return "timeout"
        return None

    def _category_for_run_event(self, event_type: str) -> str | None:
        if event_type == "tool_call.failed":
            return "tool_failed"
        if event_type == "run.timed_out":
            return "timeout"
        return None

    def _classification_message(self, event: AuditEvent) -> str | None:
        for key in ["error", "reason", "failure_reason", "message"]:
            value = event.metadata.get(key)
            if isinstance(value, str):
                return value
        return event.event_type

    def _run_event_message(self, event: RunEvent) -> str | None:
        for key in ["error", "reason", "failure_reason", "message"]:
            value = event.payload.get(key)
            if isinstance(value, str):
                return value
        return event.type

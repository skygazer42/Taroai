import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import AuditEvent, BillingMeterEvent, Run, utc_now, new_id
from taroai.observability import RunTraceService
from taroai.support.models import (
    SupportAccessScope,
    SupportArtifactMetadata,
    SupportAuditSummary,
    SupportBillingSummary,
    SupportRunDebugBundle,
    SupportRunEventSummary,
    SupportRunMetadata,
    SupportSession,
    SupportSessionCreate,
    SupportSessionStatus,
    SupportTraceSummary,
)


class SupportAccessDeniedError(PermissionError):
    pass


class InMemorySupportAccessService(BaseModel):
    store: Any = Field(exclude=True, repr=False)
    trace_service: RunTraceService = Field(default_factory=RunTraceService)
    sessions: dict[str, SupportSession] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def request_session(
        self,
        tenant_id: str,
        payload: SupportSessionCreate,
    ) -> SupportSession:
        session = SupportSession(
            id=new_id("support_session"),
            tenant_id=tenant_id,
            requested_by_user_id=payload.requested_by_user_id,
            scope=payload.scope,
            reason=payload.reason,
            expires_at=payload.expires_at,
            status=SupportSessionStatus.PENDING,
            created_at=utc_now(),
        )
        audit_event = self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=payload.requested_by_user_id,
            run_id=None,
            event_type="support.session.requested",
            metadata={
                "support_session_id": session.id,
                "scope": payload.scope.value,
                "requested_by_user_id": payload.requested_by_user_id,
                "reason_code": self._reason_code(payload.reason),
                "expires_at": payload.expires_at.isoformat(),
            },
        )
        session = session.model_copy(update={"audit_event_id": audit_event.id})
        self.sessions[session.id] = session
        return session

    def approve_session(
        self,
        tenant_id: str,
        session_id: str,
        approved_by_user_id: str,
    ) -> SupportSession:
        session = self._get_session(tenant_id, session_id)
        approved = session.model_copy(
            update={
                "status": SupportSessionStatus.APPROVED,
                "approved_by_user_id": approved_by_user_id,
                "approved_at": utc_now(),
            }
        )
        audit_event = self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=approved_by_user_id,
            run_id=None,
            event_type="support.session.approved",
            metadata={
                "support_session_id": approved.id,
                "scope": approved.scope.value,
                "requested_by_user_id": approved.requested_by_user_id,
                "approved_by_user_id": approved_by_user_id,
                "expires_at": approved.expires_at.isoformat(),
            },
        )
        approved = approved.model_copy(update={"audit_event_id": audit_event.id})
        self.sessions[session_id] = approved
        return approved

    def break_glass_session(
        self,
        tenant_id: str,
        requested_by_user_id: str,
        reason: str,
        expires_at,
    ) -> SupportSession:
        session = SupportSession(
            id=new_id("support_session"),
            tenant_id=tenant_id,
            requested_by_user_id=requested_by_user_id,
            approved_by_user_id=requested_by_user_id,
            scope=SupportAccessScope.TENANT_DEBUG,
            reason=reason,
            expires_at=expires_at,
            status=SupportSessionStatus.APPROVED,
            break_glass=True,
            created_at=utc_now(),
            approved_at=utc_now(),
        )
        audit_event = self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=requested_by_user_id,
            run_id=None,
            event_type="support.session.break_glass",
            metadata={
                "support_session_id": session.id,
                "scope": session.scope.value,
                "requested_by_user_id": requested_by_user_id,
                "reason_code": self._reason_code(reason),
                "expires_at": session.expires_at.isoformat(),
            },
        )
        session = session.model_copy(update={"audit_event_id": audit_event.id})
        self.sessions[session.id] = session
        return session

    def build_run_debug_bundle(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
    ) -> SupportRunDebugBundle:
        session = self._require_active_approved_session(tenant_id, session_id)
        run = self.store.get_run(tenant_id, run_id)
        events = self.store.list_run_events(tenant_id, run_id)
        artifacts = self.store.list_artifacts(tenant_id, run_id)
        meters = [
            meter
            for meter in self.store.list_billing_meters(tenant_id)
            if meter.run_id == run_id
        ]
        audit_events = [
            event
            for event in self.store.list_audit_events(tenant_id)
            if event.run_id == run_id
        ]
        trace = self.trace_service.build(self.store, tenant_id, run_id)
        bundle = SupportRunDebugBundle(
            session_id=session.id,
            run=self._run_metadata(run),
            events=[
                SupportRunEventSummary(
                    id=event.id,
                    sequence=event.sequence,
                    type=event.type,
                    payload_keys=sorted(event.payload.keys()),
                    created_at=event.created_at,
                )
                for event in events
            ],
            trace_summary=SupportTraceSummary(
                trace_id=trace.run.id,
                span_count=len(trace.spans),
                trace_event_count=len(trace.trace_events),
                guardrail_finding_count=len(trace.guardrail_findings),
                error_category=(
                    trace.error_classification.category
                    if trace.error_classification is not None
                    else None
                ),
                span_names=[span.name for span in trace.spans],
            ),
            artifacts=[
                SupportArtifactMetadata(
                    id=artifact.id,
                    name=artifact.name,
                    artifact_type=artifact.artifact_type,
                    uri=self._safe_artifact_uri(artifact.uri),
                    created_at=artifact.created_at,
                )
                for artifact in artifacts
            ],
            billing_summary=self._billing_summary(meters),
            audit_summary=self._audit_summary(audit_events),
        )
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=session.requested_by_user_id,
            run_id=run.id,
            event_type="support.run_debug.accessed",
            metadata={
                "support_session_id": session.id,
                "run_id": run.id,
                "scope": session.scope.value,
                "accessed_by_user_id": session.requested_by_user_id,
                "artifact_count": len(bundle.artifacts),
                "event_count": len(bundle.events),
            },
        )
        return bundle

    def _require_active_approved_session(
        self,
        tenant_id: str,
        session_id: str,
    ) -> SupportSession:
        session = self._get_session(tenant_id, session_id)
        if session.expires_at <= utc_now():
            expired = session.model_copy(
                update={
                    "status": SupportSessionStatus.EXPIRED,
                }
            )
            self.sessions[session_id] = expired
            raise SupportAccessDeniedError("support session is expired")
        if session.status != SupportSessionStatus.APPROVED:
            raise SupportAccessDeniedError("support session is not approved")
        return session

    def _get_session(self, tenant_id: str, session_id: str) -> SupportSession:
        session = self.sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise SupportAccessDeniedError("support session is not available")
        return session

    def _run_metadata(self, run: Run) -> SupportRunMetadata:
        return SupportRunMetadata(
            id=run.id,
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            agent_id=run.agent_id,
            status=run.status.value,
            mode=run.mode.value,
            message_length=len(run.message),
            attachment_count=len(run.attachments),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _billing_summary(
        self,
        meters: list[BillingMeterEvent],
    ) -> SupportBillingSummary:
        quantity_by_meter_type: dict[str, float] = {}
        cost_estimate_total = 0.0
        for meter in meters:
            quantity_by_meter_type[meter.meter_type] = (
                quantity_by_meter_type.get(meter.meter_type, 0.0) + meter.quantity
            )
            cost_estimate_total += meter.cost_estimate or 0
        return SupportBillingSummary(
            meter_count=len(meters),
            quantity_by_meter_type=dict(sorted(quantity_by_meter_type.items())),
            cost_estimate_total=cost_estimate_total,
        )

    def _audit_summary(self, events: list[AuditEvent]) -> SupportAuditSummary:
        metadata_keys: set[str] = set()
        for event in events:
            metadata_keys.update(event.metadata.keys())
        return SupportAuditSummary(
            event_count=len(events),
            event_types=sorted({event.event_type for event in events}),
            metadata_keys=sorted(metadata_keys),
        )

    def _safe_artifact_uri(self, uri: str) -> str:
        normalized = uri.lower()
        if "?" in uri or any(
            token in normalized
            for token in (
                "signature",
                "credential",
                "token",
                "secret",
                "password",
                "x-amz-",
            )
        ):
            return "[REDACTED]"
        return uri

    def _record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict[str, Any],
    ) -> AuditEvent:
        return self.store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata,
        )

    def _reason_code(self, reason: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_")
        return normalized or "unspecified"

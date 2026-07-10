from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.audit.models import (
    AuditCoverageFinding,
    AuditCoverageReport,
    AuditCoverageRequirement,
    AuditEvent,
    AuditEventCreate,
)
from taroai.domain import utc_now
from taroai.licensing.models import LicensedFeature


DEFAULT_SENSITIVE_METADATA_KEYS = {
    "access_key",
    "access_key_id",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "secret_access_key",
    "signed_url",
    "token",
}


class AuditService(BaseModel):
    store: Any
    retention_days: int = Field(default=365, ge=1)
    license_service: Any | None = Field(default=None, exclude=True, repr=False)
    redaction_value: str = "[REDACTED]"
    sensitive_metadata_keys: set[str] = Field(default_factory=lambda: set(DEFAULT_SENSITIVE_METADATA_KEYS))

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def record(self, event: AuditEventCreate) -> AuditEvent:
        self._enforce_retention_entitlement(event)
        metadata = self.redact_metadata(event.metadata)
        metadata["audit_retention_days"] = self.retention_days
        metadata["audit_retention_expires_at"] = (
            utc_now() + timedelta(days=self.retention_days)
        ).isoformat()
        if event.actor is not None:
            metadata["actor"] = event.actor.model_dump(mode="json", exclude_none=True)
        recorded = self.store.record_audit_event(
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            user_id=event.user_id,
            run_id=event.run_id,
            event_type=event.event_type,
            metadata=metadata,
        )
        return recorded.model_copy(deep=True)

    def _enforce_retention_entitlement(self, event: AuditEventCreate) -> None:
        if self.license_service is None:
            return
        if event.event_type.startswith("license."):
            return
        self.license_service.require_entitlement(
            tenant_id=event.tenant_id,
            feature=LicensedFeature.AUDIT_RETENTION_DAYS,
            requested_amount=self.retention_days,
        )

    def list_for_tenant(self, tenant_id: str) -> list[AuditEvent]:
        return [
            event.model_copy(deep=True)
            for event in self.store.list_audit_events(tenant_id)
        ]

    def check_coverage(
        self,
        tenant_id: str,
        requirements: list[AuditCoverageRequirement],
    ) -> AuditCoverageReport:
        events = self.list_for_tenant(tenant_id)
        covered_event_types: list[str] = []
        missing_requirements: list[AuditCoverageFinding] = []
        for requirement in requirements:
            matching_events = [
                event for event in events if event.event_type == requirement.event_type
            ]
            missing_metadata_keys = self._missing_metadata_keys(
                requirement,
                matching_events,
            )
            if missing_metadata_keys:
                missing_requirements.append(
                    AuditCoverageFinding(
                        area=requirement.area,
                        event_type=requirement.event_type,
                        missing_metadata_keys=missing_metadata_keys,
                    )
                )
                continue
            covered_event_types.append(requirement.event_type)
        return AuditCoverageReport(
            tenant_id=tenant_id,
            total_requirements=len(requirements),
            covered_event_types=covered_event_types,
            missing_requirements=missing_requirements,
            is_complete=len(missing_requirements) == 0,
        )

    def redact_metadata(self, value: dict[str, Any]) -> dict[str, Any]:
        return self._redact_value(value)

    def _redact_value(self, value):
        if isinstance(value, dict):
            return {
                key: (
                    self.redaction_value
                    if self._is_sensitive_key(key)
                    else self._redact_value(nested)
                )
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        if normalized in self.sensitive_metadata_keys:
            return True
        return normalized.endswith(
            (
                "_access_key",
                "_access_key_id",
                "_access_token",
                "_api_key",
                "_apikey",
                "_authorization",
                "_credential",
                "_credentials",
                "_password",
                "_private_key",
                "_secret",
                "_secret_access_key",
                "_signed_url",
                "_token",
            )
        )

    def _missing_metadata_keys(
        self,
        requirement: AuditCoverageRequirement,
        events: list[AuditEvent],
    ) -> list[str]:
        required_keys = sorted(requirement.required_metadata_keys)
        if not events:
            return required_keys
        if not required_keys:
            return []
        missing_by_event = [
            [
                key
                for key in required_keys
                if key not in event.metadata or event.metadata[key] is None
            ]
            for event in events
        ]
        return min(missing_by_event, key=len)

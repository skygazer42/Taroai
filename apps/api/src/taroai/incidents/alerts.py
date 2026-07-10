from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import new_id, utc_now
from taroai.errors import NotFoundError, TenantAccessError
from taroai.incidents.models import IncidentSeverity
from taroai.incidents.slo import SloTier


class AlertSource(str, Enum):
    API = "api"
    WORKER = "worker"
    SANDBOX = "sandbox"
    MODEL_GATEWAY = "model_gateway"
    CONNECTOR = "connector"
    BILLING = "billing"
    AUDIT = "audit"
    STORAGE = "storage"
    FRONTEND = "frontend"


class EscalationPolicy(BaseModel):
    primary_contact: str = Field(min_length=1)
    secondary_contact: str | None = None
    executive_contact: str | None = None

    def contacts(self) -> list[str]:
        return [
            contact
            for contact in [
                self.primary_contact,
                self.secondary_contact,
                self.executive_contact,
            ]
            if contact
        ]


class AlertCreate(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    source: AlertSource
    severity: IncidentSeverity
    component: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    tenant_tier: SloTier = SloTier.BUSINESS
    customer_impacting: bool = False
    observed_at: datetime = Field(default_factory=utc_now)


class Alert(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str | None = None
    source: AlertSource
    severity: IncidentSeverity
    component: str
    summary: str
    tenant_tier: SloTier
    customer_impacting: bool
    observed_at: datetime
    acknowledged_by_user_id: str | None = None
    acknowledged_at: datetime | None = None


class AlertRoutingRule(BaseModel):
    id: str = Field(min_length=1)
    sources: list[AlertSource] = Field(default_factory=list)
    severities: list[IncidentSeverity] = Field(default_factory=list)
    tenant_tiers: list[SloTier] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    business_hours_only: bool = False
    escalation_policy: EscalationPolicy
    priority: int = Field(default=100, ge=0)

    def matches(self, alert: Alert) -> bool:
        return (
            matches_optional(self.sources, alert.source)
            and matches_optional(self.severities, alert.severity)
            and matches_optional(self.tenant_tiers, alert.tenant_tier)
            and matches_optional(self.components, alert.component)
            and (
                not self.business_hours_only
                or is_business_hours(alert.observed_at)
            )
        )


class AlertRouteDecision(BaseModel):
    alert_id: str
    rule_id: str
    tenant_id: str
    source: AlertSource
    severity: IncidentSeverity
    component: str
    notify_contacts: list[str]
    page_immediately: bool
    customer_impacting: bool


class AlertAcknowledgement(BaseModel):
    alert_id: str
    tenant_id: str
    acknowledged_by_user_id: str
    acknowledged_at: datetime
    audit_event_id: str | None = None


class AlertRoutingService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    rules: list[AlertRoutingRule] = Field(default_factory=list)
    audit_store: Any | None = Field(default=None, exclude=True, repr=False)
    alerts: dict[str, Alert] = Field(default_factory=dict)

    def route_alert(self, payload: AlertCreate) -> AlertRouteDecision:
        alert = Alert(
            id=new_id("alert"),
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            source=payload.source,
            severity=payload.severity,
            component=payload.component,
            summary=payload.summary,
            tenant_tier=payload.tenant_tier,
            customer_impacting=payload.customer_impacting,
            observed_at=payload.observed_at,
        )
        self.alerts[alert.id] = alert
        rule = self._resolve_rule(alert)
        return AlertRouteDecision(
            alert_id=alert.id,
            rule_id=rule.id,
            tenant_id=alert.tenant_id,
            source=alert.source,
            severity=alert.severity,
            component=alert.component,
            notify_contacts=rule.escalation_policy.contacts(),
            page_immediately=alert.severity in {
                IncidentSeverity.SEV1,
                IncidentSeverity.SEV2,
            },
            customer_impacting=alert.customer_impacting,
        )

    def acknowledge_alert(
        self,
        tenant_id: str,
        alert_id: str,
        acknowledged_by_user_id: str,
    ) -> AlertAcknowledgement:
        alert = self._get_alert(tenant_id, alert_id)
        acknowledged_at = utc_now()
        updated = alert.model_copy(
            update={
                "acknowledged_by_user_id": acknowledged_by_user_id,
                "acknowledged_at": acknowledged_at,
            }
        )
        self.alerts[alert_id] = updated
        audit_event_id = None
        if updated.customer_impacting and self.audit_store is not None:
            audit = self.audit_store.record_audit_event(
                tenant_id=tenant_id,
                workspace_id=updated.workspace_id,
                user_id=acknowledged_by_user_id,
                run_id=None,
                event_type="alert.acknowledged",
                metadata={
                    "alert_id": updated.id,
                    "source": updated.source.value,
                    "severity": updated.severity.value,
                    "component": updated.component,
                    "customer_impacting": updated.customer_impacting,
                    "acknowledged_by_user_id": acknowledged_by_user_id,
                },
            )
            audit_event_id = audit.id
        return AlertAcknowledgement(
            alert_id=updated.id,
            tenant_id=tenant_id,
            acknowledged_by_user_id=acknowledged_by_user_id,
            acknowledged_at=acknowledged_at,
            audit_event_id=audit_event_id,
        )

    def _resolve_rule(self, alert: Alert) -> AlertRoutingRule:
        for rule in sorted(self.rules, key=lambda candidate: candidate.priority):
            if rule.matches(alert):
                return rule
        return AlertRoutingRule(
            id="default",
            escalation_policy=EscalationPolicy(primary_contact="platform-oncall"),
        )

    def _get_alert(self, tenant_id: str, alert_id: str) -> Alert:
        alert = self.alerts.get(alert_id)
        if alert is None:
            raise NotFoundError(f"Alert not found: {alert_id}")
        if alert.tenant_id != tenant_id:
            raise TenantAccessError(f"Alert {alert_id} is not in tenant {tenant_id}")
        return alert


def matches_optional(values: list, candidate) -> bool:
    return not values or candidate in values


def is_business_hours(observed_at: datetime) -> bool:
    return observed_at.weekday() < 5 and 9 <= observed_at.hour < 18

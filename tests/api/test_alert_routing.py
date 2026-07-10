from datetime import datetime, timezone

from taroai.incidents import IncidentSeverity, SloTier
from taroai.incidents.alerts import (
    AlertCreate,
    AlertRoutingRule,
    AlertRoutingService,
    AlertSource,
    EscalationPolicy,
)
from taroai.store import InMemoryControlPlaneStore


def test_alert_routing_escalates_customer_impacting_sev1_to_executive_contact():
    service = AlertRoutingService(
        rules=[
            AlertRoutingRule(
                id="sev1_customer_impact",
                severities=[IncidentSeverity.SEV1],
                escalation_policy=EscalationPolicy(
                    primary_contact="sre-primary",
                    secondary_contact="platform-lead",
                    executive_contact="cto",
                ),
                priority=10,
            )
        ]
    )

    decision = service.route_alert(
        AlertCreate(
            tenant_id="tenant_acme",
            source=AlertSource.SANDBOX,
            severity=IncidentSeverity.SEV1,
            component="sandbox",
            summary="Sandbox startup failure rate is above threshold.",
            tenant_tier=SloTier.ENTERPRISE,
            customer_impacting=True,
            observed_at=datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
        )
    )

    assert decision.rule_id == "sev1_customer_impact"
    assert decision.page_immediately is True
    assert decision.notify_contacts == ["sre-primary", "platform-lead", "cto"]


def test_alert_routing_prefers_business_hours_rule_when_observed_during_workday():
    service = AlertRoutingService(
        rules=[
            AlertRoutingRule(
                id="business_hours_api",
                sources=[AlertSource.API],
                business_hours_only=True,
                escalation_policy=EscalationPolicy(primary_contact="api-day-oncall"),
                priority=10,
            ),
            AlertRoutingRule(
                id="api_default",
                sources=[AlertSource.API],
                escalation_policy=EscalationPolicy(primary_contact="api-general-oncall"),
                priority=50,
            ),
        ]
    )

    business_hours = service.route_alert(
        AlertCreate(
            tenant_id="tenant_acme",
            source=AlertSource.API,
            severity=IncidentSeverity.SEV3,
            component="api",
            summary="Readiness latency is elevated.",
            observed_at=datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
        )
    )
    after_hours = service.route_alert(
        AlertCreate(
            tenant_id="tenant_acme",
            source=AlertSource.API,
            severity=IncidentSeverity.SEV3,
            component="api",
            summary="Readiness latency is elevated.",
            observed_at=datetime(2026, 7, 6, 23, 0, tzinfo=timezone.utc),
        )
    )

    assert business_hours.rule_id == "business_hours_api"
    assert business_hours.notify_contacts == ["api-day-oncall"]
    assert after_hours.rule_id == "api_default"
    assert after_hours.notify_contacts == ["api-general-oncall"]


def test_alert_routing_applies_enterprise_tenant_tier_escalation():
    service = AlertRoutingService(
        rules=[
            AlertRoutingRule(
                id="enterprise_model_gateway",
                sources=[AlertSource.MODEL_GATEWAY],
                tenant_tiers=[SloTier.ENTERPRISE],
                escalation_policy=EscalationPolicy(
                    primary_contact="model-oncall",
                    secondary_contact="enterprise-success",
                ),
                priority=10,
            ),
            AlertRoutingRule(
                id="model_gateway_default",
                sources=[AlertSource.MODEL_GATEWAY],
                escalation_policy=EscalationPolicy(primary_contact="model-oncall"),
                priority=50,
            ),
        ]
    )

    enterprise = service.route_alert(
        AlertCreate(
            tenant_id="tenant_acme",
            source=AlertSource.MODEL_GATEWAY,
            severity=IncidentSeverity.SEV2,
            component="model_gateway",
            summary="Provider fallback rate is elevated.",
            tenant_tier=SloTier.ENTERPRISE,
            observed_at=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
        )
    )
    business = service.route_alert(
        AlertCreate(
            tenant_id="tenant_beta",
            source=AlertSource.MODEL_GATEWAY,
            severity=IncidentSeverity.SEV2,
            component="model_gateway",
            summary="Provider fallback rate is elevated.",
            tenant_tier=SloTier.BUSINESS,
            observed_at=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert enterprise.rule_id == "enterprise_model_gateway"
    assert enterprise.notify_contacts == ["model-oncall", "enterprise-success"]
    assert business.rule_id == "model_gateway_default"
    assert business.notify_contacts == ["model-oncall"]


def test_acknowledging_customer_impacting_alert_records_safe_audit_event():
    store = InMemoryControlPlaneStore()
    service = AlertRoutingService(
        audit_store=store,
        rules=[
            AlertRoutingRule(
                id="storage_default",
                sources=[AlertSource.STORAGE],
                escalation_policy=EscalationPolicy(primary_contact="storage-oncall"),
            )
        ],
    )
    decision = service.route_alert(
        AlertCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            source=AlertSource.STORAGE,
            severity=IncidentSeverity.SEV2,
            component="storage",
            summary="Customer artifact downloads are failing with sensitive URL context.",
            customer_impacting=True,
            observed_at=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
        )
    )

    acknowledgement = service.acknowledge_alert(
        tenant_id="tenant_acme",
        alert_id=decision.alert_id,
        acknowledged_by_user_id="user_sre",
    )

    audits = store.list_audit_events("tenant_acme")
    assert acknowledgement.audit_event_id == audits[0].id
    assert audits[0].event_type == "alert.acknowledged"
    assert audits[0].workspace_id == "workspace_sales"
    assert audits[0].metadata == {
        "alert_id": decision.alert_id,
        "source": "storage",
        "severity": "sev2",
        "component": "storage",
        "customer_impacting": True,
        "acknowledged_by_user_id": "user_sre",
    }
    assert "sensitive URL context" not in str(audits[0].metadata)

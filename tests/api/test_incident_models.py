import pytest

from taroai.errors import NotFoundError, RunTransitionError, TenantAccessError
from taroai.incidents import (
    IncidentCreate,
    IncidentSeverity,
    IncidentStatus,
    InMemoryIncidentService,
)


def test_incident_service_creates_structured_tenant_scoped_incident():
    service = InMemoryIncidentService()

    incident = service.create_incident(
        tenant_id="tenant_acme",
        payload=IncidentCreate(
            severity=IncidentSeverity.SEV2,
            summary="API run execution latency is elevated.",
            affected_components=["api", "worker"],
            affected_tenant_ids=["tenant_acme"],
            owner_user_id="user_sre",
            linked_run_ids=["run_123"],
        ),
    )

    assert incident.id.startswith("incident_")
    assert incident.tenant_id == "tenant_acme"
    assert incident.status == IncidentStatus.DETECTED
    assert incident.severity == IncidentSeverity.SEV2
    assert incident.affected_components == ["api", "worker"]
    assert incident.affected_tenant_ids == ["tenant_acme"]
    assert incident.owner_user_id == "user_sre"
    assert incident.linked_run_ids == ["run_123"]
    assert incident.resolved_at is None
    assert service.get_incident("tenant_acme", incident.id) == incident


def test_incident_service_updates_status_through_valid_lifecycle():
    service = InMemoryIncidentService()
    incident = service.create_incident(
        tenant_id="tenant_acme",
        payload=IncidentCreate(
            severity=IncidentSeverity.SEV1,
            summary="Sandbox provider is unavailable.",
            affected_components=["sandbox"],
        ),
    )

    triaging = service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.TRIAGING,
        owner_user_id="user_sre",
    )
    mitigating = service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.MITIGATING,
    )
    monitoring = service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.MONITORING,
    )
    resolved = service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.RESOLVED,
    )
    closed = service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.CLOSED,
    )

    assert triaging.status == IncidentStatus.TRIAGING
    assert triaging.owner_user_id == "user_sre"
    assert mitigating.status == IncidentStatus.MITIGATING
    assert monitoring.status == IncidentStatus.MONITORING
    assert resolved.status == IncidentStatus.RESOLVED
    assert resolved.resolved_at is not None
    assert closed.status == IncidentStatus.CLOSED
    assert closed.resolved_at == resolved.resolved_at


def test_incident_service_rejects_invalid_status_transition():
    service = InMemoryIncidentService()
    incident = service.create_incident(
        tenant_id="tenant_acme",
        payload=IncidentCreate(
            severity=IncidentSeverity.SEV3,
            summary="Model gateway degradation.",
            affected_components=["model_gateway"],
        ),
    )

    with pytest.raises(RunTransitionError):
        service.update_status(
            tenant_id="tenant_acme",
            incident_id=incident.id,
            status=IncidentStatus.CLOSED,
        )


def test_incident_service_keeps_incidents_tenant_scoped():
    service = InMemoryIncidentService()
    acme = service.create_incident(
        tenant_id="tenant_acme",
        payload=IncidentCreate(
            severity=IncidentSeverity.SEV2,
            summary="Acme workspace event stream errors.",
            affected_components=["event_stream"],
        ),
    )
    other = service.create_incident(
        tenant_id="tenant_other",
        payload=IncidentCreate(
            severity=IncidentSeverity.SEV4,
            summary="Other tenant support ticket sync lag.",
            affected_components=["connector"],
        ),
    )

    assert service.list_incidents("tenant_acme") == [acme]
    assert service.list_incidents("tenant_other") == [other]
    with pytest.raises(TenantAccessError):
        service.get_incident("tenant_other", acme.id)
    with pytest.raises(NotFoundError):
        service.get_incident("tenant_acme", "incident_missing")

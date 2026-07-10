from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now
from taroai.errors import NotFoundError, RunTransitionError, TenantAccessError
from taroai.incidents.models import Incident, IncidentCreate, IncidentStatus


INCIDENT_STATUS_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.DETECTED: {
        IncidentStatus.TRIAGING,
        IncidentStatus.MITIGATING,
    },
    IncidentStatus.TRIAGING: {
        IncidentStatus.MITIGATING,
        IncidentStatus.MONITORING,
    },
    IncidentStatus.MITIGATING: {
        IncidentStatus.MONITORING,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.MONITORING: {IncidentStatus.RESOLVED},
    IncidentStatus.RESOLVED: {IncidentStatus.CLOSED},
    IncidentStatus.CLOSED: set(),
}


class InMemoryIncidentService(BaseModel):
    incidents: dict[str, Incident] = Field(default_factory=dict)

    def create_incident(self, tenant_id: str, payload: IncidentCreate) -> Incident:
        incident = Incident(
            id=new_id("incident"),
            tenant_id=tenant_id,
            severity=payload.severity,
            status=IncidentStatus.DETECTED,
            summary=payload.summary,
            affected_components=payload.affected_components,
            affected_tenant_ids=payload.affected_tenant_ids,
            started_at=utc_now(),
            owner_user_id=payload.owner_user_id,
            linked_run_ids=payload.linked_run_ids,
        )
        self.incidents[incident.id] = incident
        return incident

    def get_incident(self, tenant_id: str, incident_id: str) -> Incident:
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise NotFoundError(f"Incident not found: {incident_id}")
        if incident.tenant_id != tenant_id:
            raise TenantAccessError(
                f"Incident {incident_id} is not in tenant {tenant_id}"
            )
        return incident

    def list_incidents(self, tenant_id: str) -> list[Incident]:
        return [
            incident
            for incident in self.incidents.values()
            if incident.tenant_id == tenant_id
        ]

    def update_status(
        self,
        tenant_id: str,
        incident_id: str,
        status: IncidentStatus,
        owner_user_id: str | None = None,
    ) -> Incident:
        incident = self.get_incident(tenant_id, incident_id)
        allowed_statuses = INCIDENT_STATUS_TRANSITIONS[incident.status]
        if status not in allowed_statuses:
            raise RunTransitionError(
                "Incident "
                f"{incident_id} cannot transition from "
                f"{incident.status.value} to {status.value}"
            )
        resolved_at = incident.resolved_at
        if status == IncidentStatus.RESOLVED and resolved_at is None:
            resolved_at = utc_now()
        updated = incident.model_copy(
            update={
                "status": status,
                "owner_user_id": owner_user_id or incident.owner_user_id,
                "resolved_at": resolved_at,
            }
        )
        self.incidents[incident_id] = updated
        return updated

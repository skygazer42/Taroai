from datetime import timedelta

import pytest

from taroai.domain import utc_now
from taroai.incidents import (
    IncidentCreate,
    IncidentSeverity,
    IncidentStatus,
    InMemoryIncidentService,
)
from taroai.incidents.postmortem import (
    ImprovementCandidateStatus,
    ImprovementCandidateTargetType,
    IncidentImprovementCandidateCreate,
    InMemoryIncidentPostmortemService,
    PostmortemClosureError,
    PostmortemCreate,
    PostmortemReviewRequiredError,
    PostmortemStatus,
    PostmortemTimelineEvent,
    RemediationTask,
)


def create_resolved_incident():
    incident_service = InMemoryIncidentService()
    incident = incident_service.create_incident(
        tenant_id="tenant_acme",
        payload=IncidentCreate(
            severity=IncidentSeverity.SEV1,
            summary="Sandbox command isolation failed.",
            affected_components=["sandbox", "runtime"],
            affected_tenant_ids=["tenant_acme"],
            owner_user_id="user_sre",
            linked_run_ids=["run_123"],
        ),
    )
    incident_service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.MITIGATING,
    )
    resolved = incident_service.update_status(
        tenant_id="tenant_acme",
        incident_id=incident.id,
        status=IncidentStatus.RESOLVED,
    )
    return incident_service, resolved


def complete_postmortem_payload(incident_id: str) -> PostmortemCreate:
    return PostmortemCreate(
        incident_id=incident_id,
        impact_summary="Two sandbox-backed runs were paused during mitigation.",
        root_cause="The sandbox lifecycle checker did not catch a stale workspace mount.",
        contributing_factors=[
            "Sandbox verification ran only on local process provider.",
            "No alert routed for repeated sandbox startup failures.",
        ],
        timeline=[
            PostmortemTimelineEvent(
                occurred_at=utc_now() - timedelta(minutes=20),
                title="Detection",
                description="SRE detected repeated sandbox startup failures.",
            ),
            PostmortemTimelineEvent(
                occurred_at=utc_now() - timedelta(minutes=5),
                title="Mitigation",
                description="Sandbox creation kill switch was enabled.",
            ),
        ],
        remediation_tasks=[
            RemediationTask(
                title="Add Docker sandbox lifecycle verification to CI.",
                owner_user_id="user_sre",
                due_at=utc_now() + timedelta(days=7),
            )
        ],
        owner_user_id="user_sre",
        customer_summary="Sandbox-backed automation was paused while isolation checks were restored.",
        linked_run_ids=["run_123"],
    )


def test_postmortem_requires_core_fields_before_incident_closure():
    incident_service, incident = create_resolved_incident()
    service = InMemoryIncidentPostmortemService()
    postmortem = service.create_postmortem(
        tenant_id="tenant_acme",
        payload=PostmortemCreate(
            incident_id=incident.id,
            timeline=[],
            owner_user_id="user_sre",
        ),
    )

    with pytest.raises(PostmortemClosureError) as error:
        service.close_incident_with_postmortem(
            incident_service=incident_service,
            tenant_id="tenant_acme",
            incident_id=incident.id,
            postmortem_id=postmortem.id,
            reviewed_by_user_id="user_sre_lead",
        )

    assert error.value.missing_fields == [
        "impact_summary",
        "root_cause",
        "timeline",
        "remediation_tasks",
        "customer_summary",
    ]
    assert incident_service.get_incident(
        "tenant_acme",
        incident.id,
    ).status == IncidentStatus.RESOLVED


def test_reviewed_postmortem_can_close_resolved_incident():
    incident_service, incident = create_resolved_incident()
    service = InMemoryIncidentPostmortemService()
    postmortem = service.create_postmortem(
        tenant_id="tenant_acme",
        payload=complete_postmortem_payload(incident.id),
    )

    closed = service.close_incident_with_postmortem(
        incident_service=incident_service,
        tenant_id="tenant_acme",
        incident_id=incident.id,
        postmortem_id=postmortem.id,
        reviewed_by_user_id="user_sre_lead",
    )

    reviewed = service.get_postmortem("tenant_acme", postmortem.id)
    assert closed.status == IncidentStatus.CLOSED
    assert reviewed.status == PostmortemStatus.REVIEWED
    assert reviewed.reviewed_by_user_id == "user_sre_lead"
    assert reviewed.reviewed_at is not None


def test_incident_learning_candidate_requires_human_reviewed_postmortem():
    _incident_service, incident = create_resolved_incident()
    service = InMemoryIncidentPostmortemService()
    postmortem = service.create_postmortem(
        tenant_id="tenant_acme",
        payload=complete_postmortem_payload(incident.id),
    )

    with pytest.raises(
        PostmortemReviewRequiredError,
        match="postmortem must be reviewed before linking improvement candidates",
    ):
        service.record_learning_candidate(
            tenant_id="tenant_acme",
            postmortem_id=postmortem.id,
            payload=IncidentImprovementCandidateCreate(
                target_type=ImprovementCandidateTargetType.PROMPT,
                target_id="agent_support.system_prompt",
                proposed_change_summary="Add sandbox readiness check before command execution.",
                rationale="The incident showed missing readiness validation.",
                risk_level="medium",
                source_run_ids=["run_123"],
                owner_user_id="user_prompt_owner",
            ),
            reviewed_by_user_id="user_sre_lead",
        )


def test_postmortem_learning_creates_reviewable_candidate_without_production_change():
    _incident_service, incident = create_resolved_incident()
    service = InMemoryIncidentPostmortemService()
    postmortem = service.create_postmortem(
        tenant_id="tenant_acme",
        payload=complete_postmortem_payload(incident.id),
    )
    service.mark_reviewed(
        tenant_id="tenant_acme",
        postmortem_id=postmortem.id,
        reviewed_by_user_id="user_sre_lead",
    )

    candidate = service.record_learning_candidate(
        tenant_id="tenant_acme",
        postmortem_id=postmortem.id,
        payload=IncidentImprovementCandidateCreate(
            target_type=ImprovementCandidateTargetType.SKILL_MANIFEST,
            target_id="support.ticket_triage",
            proposed_change_summary="Require sandbox isolation verification evidence.",
            rationale="Prevent recurrence of unsafe sandbox assumptions.",
            risk_level="high",
            source_run_ids=["run_123"],
            owner_user_id="user_skill_owner",
        ),
        reviewed_by_user_id="user_sre_lead",
    )

    updated = service.get_postmortem("tenant_acme", postmortem.id)
    assert candidate.status == ImprovementCandidateStatus.PENDING_REVIEW
    assert candidate.incident_id == incident.id
    assert candidate.postmortem_id == postmortem.id
    assert candidate.human_reviewed_by_user_id == "user_sre_lead"
    assert candidate.production_change_applied is False
    assert candidate.production_published_at is None
    assert updated.linked_eval_candidate_ids == [candidate.id]

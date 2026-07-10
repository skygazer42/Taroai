from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now
from taroai.errors import NotFoundError, TenantAccessError
from taroai.incidents.models import IncidentStatus


class PostmortemStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class ImprovementCandidateStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED_FOR_EVAL = "approved_for_eval"
    REJECTED = "rejected"


class ImprovementCandidateTargetType(str, Enum):
    PROMPT = "prompt"
    SKILL_MANIFEST = "skill_manifest"
    WORKFLOW = "workflow"
    RETRIEVAL_CONFIG = "retrieval_config"
    POLICY_CONFIG = "policy_config"
    MEMORY_CANDIDATE = "memory_candidate"


class PostmortemTimelineEvent(BaseModel):
    occurred_at: datetime
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class RemediationTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("remediation"))
    title: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    due_at: datetime
    completed_at: datetime | None = None


class PostmortemCreate(BaseModel):
    incident_id: str = Field(min_length=1)
    timeline: list[PostmortemTimelineEvent] = Field(default_factory=list)
    impact_summary: str | None = None
    root_cause: str | None = None
    contributing_factors: list[str] = Field(default_factory=list)
    remediation_tasks: list[RemediationTask] = Field(default_factory=list)
    owner_user_id: str | None = None
    customer_summary: str | None = None
    linked_run_ids: list[str] = Field(default_factory=list)


class IncidentPostmortem(BaseModel):
    id: str
    tenant_id: str
    incident_id: str
    status: PostmortemStatus
    timeline: list[PostmortemTimelineEvent] = Field(default_factory=list)
    impact_summary: str | None = None
    root_cause: str | None = None
    contributing_factors: list[str] = Field(default_factory=list)
    remediation_tasks: list[RemediationTask] = Field(default_factory=list)
    owner_user_id: str | None = None
    customer_summary: str | None = None
    linked_run_ids: list[str] = Field(default_factory=list)
    linked_eval_candidate_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by_user_id: str | None = None


class IncidentImprovementCandidateCreate(BaseModel):
    target_type: ImprovementCandidateTargetType
    target_id: str = Field(min_length=1)
    proposed_change_summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    source_run_ids: list[str] = Field(default_factory=list)
    owner_user_id: str = Field(min_length=1)


class IncidentImprovementCandidate(BaseModel):
    id: str
    tenant_id: str
    incident_id: str
    postmortem_id: str
    target_type: ImprovementCandidateTargetType
    target_id: str
    proposed_change_summary: str
    rationale: str
    risk_level: str
    source_run_ids: list[str] = Field(default_factory=list)
    owner_user_id: str
    status: ImprovementCandidateStatus
    human_reviewed_by_user_id: str
    production_change_applied: bool = False
    production_published_at: datetime | None = None
    created_at: datetime


class PostmortemClosureError(ValueError):
    def __init__(self, missing_fields: list[str]):
        super().__init__(
            "postmortem is not ready for incident closure: "
            + ", ".join(missing_fields)
        )
        self.missing_fields = missing_fields


class PostmortemReviewRequiredError(PermissionError):
    pass


class InMemoryIncidentPostmortemService(BaseModel):
    postmortems: dict[str, IncidentPostmortem] = Field(default_factory=dict)
    candidates: dict[str, IncidentImprovementCandidate] = Field(default_factory=dict)

    def create_postmortem(
        self,
        tenant_id: str,
        payload: PostmortemCreate,
    ) -> IncidentPostmortem:
        postmortem = IncidentPostmortem(
            id=new_id("postmortem"),
            tenant_id=tenant_id,
            incident_id=payload.incident_id,
            status=PostmortemStatus.DRAFT,
            timeline=payload.timeline,
            impact_summary=payload.impact_summary,
            root_cause=payload.root_cause,
            contributing_factors=payload.contributing_factors,
            remediation_tasks=payload.remediation_tasks,
            owner_user_id=payload.owner_user_id,
            customer_summary=payload.customer_summary,
            linked_run_ids=payload.linked_run_ids,
            created_at=utc_now(),
        )
        self.postmortems[postmortem.id] = postmortem
        return postmortem

    def get_postmortem(
        self,
        tenant_id: str,
        postmortem_id: str,
    ) -> IncidentPostmortem:
        postmortem = self.postmortems.get(postmortem_id)
        if postmortem is None:
            raise NotFoundError(f"Postmortem not found: {postmortem_id}")
        if postmortem.tenant_id != tenant_id:
            raise TenantAccessError(
                f"Postmortem {postmortem_id} is not in tenant {tenant_id}"
            )
        return postmortem

    def mark_reviewed(
        self,
        tenant_id: str,
        postmortem_id: str,
        reviewed_by_user_id: str,
    ) -> IncidentPostmortem:
        postmortem = self.get_postmortem(tenant_id, postmortem_id)
        missing_fields = self._missing_closure_fields(postmortem)
        if missing_fields:
            raise PostmortemClosureError(missing_fields)
        reviewed = postmortem.model_copy(
            update={
                "status": PostmortemStatus.REVIEWED,
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
            }
        )
        self.postmortems[postmortem_id] = reviewed
        return reviewed

    def close_incident_with_postmortem(
        self,
        incident_service,
        tenant_id: str,
        incident_id: str,
        postmortem_id: str,
        reviewed_by_user_id: str,
    ):
        postmortem = self.get_postmortem(tenant_id, postmortem_id)
        if postmortem.incident_id != incident_id:
            raise TenantAccessError(
                f"Postmortem {postmortem_id} is not linked to incident {incident_id}"
            )
        self.mark_reviewed(
            tenant_id=tenant_id,
            postmortem_id=postmortem_id,
            reviewed_by_user_id=reviewed_by_user_id,
        )
        return incident_service.update_status(
            tenant_id=tenant_id,
            incident_id=incident_id,
            status=IncidentStatus.CLOSED,
        )

    def record_learning_candidate(
        self,
        tenant_id: str,
        postmortem_id: str,
        payload: IncidentImprovementCandidateCreate,
        reviewed_by_user_id: str,
    ) -> IncidentImprovementCandidate:
        postmortem = self.get_postmortem(tenant_id, postmortem_id)
        if postmortem.status != PostmortemStatus.REVIEWED:
            raise PostmortemReviewRequiredError(
                "postmortem must be reviewed before linking improvement candidates"
            )
        candidate = IncidentImprovementCandidate(
            id=new_id("improvement_candidate"),
            tenant_id=tenant_id,
            incident_id=postmortem.incident_id,
            postmortem_id=postmortem.id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            proposed_change_summary=payload.proposed_change_summary,
            rationale=payload.rationale,
            risk_level=payload.risk_level,
            source_run_ids=payload.source_run_ids,
            owner_user_id=payload.owner_user_id,
            status=ImprovementCandidateStatus.PENDING_REVIEW,
            human_reviewed_by_user_id=reviewed_by_user_id,
            created_at=utc_now(),
        )
        self.candidates[candidate.id] = candidate
        updated_postmortem = postmortem.model_copy(
            update={
                "linked_eval_candidate_ids": [
                    *postmortem.linked_eval_candidate_ids,
                    candidate.id,
                ]
            }
        )
        self.postmortems[postmortem.id] = updated_postmortem
        return candidate

    def _missing_closure_fields(
        self,
        postmortem: IncidentPostmortem,
    ) -> list[str]:
        missing_fields: list[str] = []
        if not self._has_text(postmortem.impact_summary):
            missing_fields.append("impact_summary")
        if not self._has_text(postmortem.root_cause):
            missing_fields.append("root_cause")
        if not postmortem.timeline:
            missing_fields.append("timeline")
        if not postmortem.remediation_tasks:
            missing_fields.append("remediation_tasks")
        if not self._has_text(postmortem.customer_summary):
            missing_fields.append("customer_summary")
        return missing_fields

    def _has_text(self, value: str | None) -> bool:
        return value is not None and bool(value.strip())

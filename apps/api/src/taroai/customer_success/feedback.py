from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import new_id, utc_now
from taroai.skills import SkillManifest
from taroai.store import NotFoundError


class CustomerFeedbackType(str, Enum):
    THUMBS_RATING = "thumbs_rating"
    BUG_REPORT = "bug_report"
    MISSING_SKILL = "missing_skill"
    WRONG_ANSWER = "wrong_answer"
    SLOW_RUN = "slow_run"
    COST_CONCERN = "cost_concern"
    FEATURE_REQUEST = "feature_request"


class CustomerFeedbackTargetType(str, Enum):
    RUN = "run"
    ARTIFACT = "artifact"
    SKILL = "skill"
    SOLUTION_PACK = "solution_pack"
    ONBOARDING_STEP = "onboarding_step"
    TENANT = "tenant"


class FeedbackCandidateStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CustomerFeedbackCreate(BaseModel):
    submitted_by_user_id: str = Field(min_length=1)
    feedback_type: CustomerFeedbackType
    target_type: CustomerFeedbackTargetType
    target_id: str = Field(min_length=1)
    rating: int | None = Field(default=None, ge=-1, le=1)
    comment: str | None = None
    run_id: str | None = None
    artifact_id: str | None = None
    skill_id: str | None = None
    solution_pack_id: str | None = None
    onboarding_step_id: str | None = None
    missing_skill_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CustomerFeedback(BaseModel):
    id: str
    tenant_id: str
    submitted_by_user_id: str
    feedback_type: CustomerFeedbackType
    target_type: CustomerFeedbackTargetType
    target_id: str
    rating: int | None = None
    comment: str | None = None
    run_id: str | None = None
    artifact_id: str | None = None
    skill_id: str | None = None
    solution_pack_id: str | None = None
    onboarding_step_id: str | None = None
    missing_skill_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class FeedbackEvaluationCandidate(BaseModel):
    id: str
    tenant_id: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_run_id: str
    failure_reason: str
    proposed_eval_name: str
    status: FeedbackCandidateStatus
    human_reviewed_by_user_id: str
    production_change_applied: bool = False
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    evaluation_case_id: str | None = None
    created_at: datetime


class SolutionPackFeedbackCandidate(BaseModel):
    id: str
    tenant_id: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    solution_pack_id: str
    requested_skill_name: str
    proposed_change_summary: str
    status: FeedbackCandidateStatus
    human_reviewed_by_user_id: str
    production_change_applied: bool = False
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    publication_draft_id: str | None = None
    created_at: datetime


class FeedbackEvaluationCaseRecord(BaseModel):
    id: str
    tenant_id: str
    source_candidate_id: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_run_id: str
    failure_reason: str
    proposed_eval_name: str
    status: str
    created_by_user_id: str
    production_change_applied: bool = False
    created_at: datetime


class SolutionPackPublicationDraftRecord(BaseModel):
    id: str
    tenant_id: str
    source_candidate_id: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    solution_pack_id: str
    requested_skill_name: str
    proposed_change_summary: str
    proposed_pack_version: str | None = None
    proposed_skill_manifest: SkillManifest | None = None
    proposed_skill_manifests: list[SkillManifest] = Field(default_factory=list)
    status: str
    created_by_user_id: str
    production_change_applied: bool = False
    created_at: datetime


class InMemoryCustomerFeedbackService(BaseModel):
    audit_store: Any | None = Field(default=None, exclude=True, repr=False)
    solution_pack_registry: Any | None = Field(default=None, exclude=True, repr=False)
    feedback_records: dict[str, CustomerFeedback] = Field(default_factory=dict)
    evaluation_candidates: dict[str, FeedbackEvaluationCandidate] = Field(default_factory=dict)
    solution_pack_candidates: dict[str, SolutionPackFeedbackCandidate] = Field(default_factory=dict)
    evaluation_case_records: dict[str, FeedbackEvaluationCaseRecord] = Field(default_factory=dict)
    solution_pack_publication_drafts: dict[
        str,
        SolutionPackPublicationDraftRecord,
    ] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def capture_feedback(
        self,
        tenant_id: str,
        payload: CustomerFeedbackCreate,
    ) -> CustomerFeedback:
        feedback = CustomerFeedback(
            id=new_id("feedback"),
            tenant_id=tenant_id,
            submitted_by_user_id=payload.submitted_by_user_id,
            feedback_type=payload.feedback_type,
            target_type=payload.target_type,
            target_id=payload.target_id,
            rating=payload.rating,
            comment=payload.comment,
            run_id=payload.run_id,
            artifact_id=payload.artifact_id,
            skill_id=payload.skill_id,
            solution_pack_id=payload.solution_pack_id,
            onboarding_step_id=payload.onboarding_step_id,
            missing_skill_name=payload.missing_skill_name,
            metadata=payload.metadata,
            created_at=utc_now(),
        )
        self.feedback_records[feedback.id] = feedback
        self._record_feedback_audit(feedback)
        return feedback

    def list_feedback(self, tenant_id: str) -> list[CustomerFeedback]:
        return [
            feedback
            for feedback in self.feedback_records.values()
            if feedback.tenant_id == tenant_id
        ]

    def list_evaluation_candidates(
        self,
        tenant_id: str,
    ) -> list[FeedbackEvaluationCandidate]:
        return [
            candidate
            for candidate in self.evaluation_candidates.values()
            if candidate.tenant_id == tenant_id
        ]

    def list_solution_pack_candidates(
        self,
        tenant_id: str,
    ) -> list[SolutionPackFeedbackCandidate]:
        return [
            candidate
            for candidate in self.solution_pack_candidates.values()
            if candidate.tenant_id == tenant_id
        ]

    def list_evaluation_cases(
        self,
        tenant_id: str,
    ) -> list[FeedbackEvaluationCaseRecord]:
        return [
            evaluation_case
            for evaluation_case in self.evaluation_case_records.values()
            if evaluation_case.tenant_id == tenant_id
        ]

    def list_solution_pack_publication_drafts(
        self,
        tenant_id: str,
    ) -> list[SolutionPackPublicationDraftRecord]:
        return [
            publication_draft
            for publication_draft in self.solution_pack_publication_drafts.values()
            if publication_draft.tenant_id == tenant_id
        ]

    def update_solution_pack_publication_draft(
        self,
        tenant_id: str,
        publication_draft_id: str,
        updated_by_user_id: str,
        requested_skill_name: str | None = None,
        proposed_change_summary: str | None = None,
        proposed_pack_version: str | None = None,
        proposed_skill_manifest: SkillManifest | dict[str, Any] | None = None,
        proposed_skill_manifests: list[SkillManifest | dict[str, Any]] | None = None,
    ) -> SolutionPackPublicationDraftRecord:
        draft = self._get_solution_pack_publication_draft(
            tenant_id,
            publication_draft_id,
        )
        self._require_editable_publication_draft(draft)
        update: dict[str, Any] = {}
        if requested_skill_name is not None:
            update["requested_skill_name"] = self._required_text(
                requested_skill_name,
                "requested_skill_name",
            )
        if proposed_change_summary is not None:
            update["proposed_change_summary"] = self._required_text(
                proposed_change_summary,
                "proposed_change_summary",
            )
        if proposed_pack_version is not None:
            update["proposed_pack_version"] = self._required_text(
                proposed_pack_version,
                "proposed_pack_version",
            )
        if proposed_skill_manifest is not None:
            update["proposed_skill_manifest"] = SkillManifest.model_validate(
                proposed_skill_manifest
            )
        if proposed_skill_manifests is not None:
            update["proposed_skill_manifests"] = [
                SkillManifest.model_validate(skill)
                for skill in proposed_skill_manifests
            ]
        updated = draft.model_copy(update=update)
        self._save_solution_pack_publication_draft(updated)
        self._record_publication_draft_audit(
            tenant_id=tenant_id,
            user_id=updated_by_user_id,
            event_type="customer.solution_pack_draft.updated",
            draft=updated,
        )
        return updated

    def apply_solution_pack_publication_draft(
        self,
        tenant_id: str,
        publication_draft_id: str,
        applied_by_user_id: str,
    ) -> SolutionPackPublicationDraftRecord:
        draft = self._get_solution_pack_publication_draft(
            tenant_id,
            publication_draft_id,
        )
        if draft.status != "approved":
            raise ValueError("solution pack publication draft is not approved")
        if draft.production_change_applied:
            raise ValueError("solution pack publication draft is already applied")
        if self.solution_pack_registry is None:
            raise ValueError("solution pack registry is not configured")
        if draft.proposed_pack_version is None:
            raise ValueError("solution pack publication draft requires proposed pack version")
        proposed_skills = self._proposed_skills_for_draft(draft)
        if not proposed_skills:
            raise ValueError("solution pack publication draft requires proposed skill manifest")

        current_entry = self.solution_pack_registry.get_for_tenant(
            tenant_id,
            draft.solution_pack_id,
        )
        if current_entry.manifest.version == draft.proposed_pack_version:
            raise ValueError("solution pack publication draft requires a new pack version")
        updated_manifest = current_entry.manifest.model_copy(
            update={
                "version": draft.proposed_pack_version,
                "skills": self._replace_or_append_skills(
                    current_entry.manifest.skills,
                    proposed_skills,
                ),
            }
        )
        self.solution_pack_registry.register_for_tenant(
            tenant_id=tenant_id,
            created_by_user_id=applied_by_user_id,
            manifest=updated_manifest,
        )
        self.solution_pack_registry.publish(tenant_id, draft.solution_pack_id)

        applied = draft.model_copy(
            update={
                "status": "applied",
                "production_change_applied": True,
            }
        )
        self._save_solution_pack_publication_draft(applied)
        self._mark_solution_pack_candidate_applied(
            tenant_id,
            draft.source_candidate_id,
        )
        self._record_publication_draft_audit(
            tenant_id=tenant_id,
            user_id=applied_by_user_id,
            event_type="customer.solution_pack_draft.applied",
            draft=applied,
        )
        return applied

    def submit_solution_pack_publication_draft(
        self,
        tenant_id: str,
        publication_draft_id: str,
        submitted_by_user_id: str,
    ) -> SolutionPackPublicationDraftRecord:
        draft = self._get_solution_pack_publication_draft(
            tenant_id,
            publication_draft_id,
        )
        if draft.status not in {"draft", "rejected"}:
            raise ValueError("solution pack publication draft is not editable")
        updated = draft.model_copy(update={"status": "in_review"})
        self._save_solution_pack_publication_draft(updated)
        self._record_publication_draft_audit(
            tenant_id=tenant_id,
            user_id=submitted_by_user_id,
            event_type="customer.solution_pack_draft.submitted",
            draft=updated,
        )
        return updated

    def review_solution_pack_publication_draft(
        self,
        tenant_id: str,
        publication_draft_id: str,
        reviewed_by_user_id: str,
        status: str,
        review_note: str | None = None,
    ) -> SolutionPackPublicationDraftRecord:
        if status not in {"approved", "rejected"}:
            raise ValueError("solution pack publication draft review must approve or reject")
        draft = self._get_solution_pack_publication_draft(
            tenant_id,
            publication_draft_id,
        )
        if draft.status != "in_review":
            raise ValueError("solution pack publication draft is not in review")
        updated = draft.model_copy(
            update={
                "status": status,
                "production_change_applied": False,
            }
        )
        self._save_solution_pack_publication_draft(updated)
        self._record_publication_draft_audit(
            tenant_id=tenant_id,
            user_id=reviewed_by_user_id,
            event_type="customer.solution_pack_draft.reviewed",
            draft=updated,
            review_note_present=review_note is not None,
        )
        return updated

    def create_evaluation_candidates_for_low_rated_runs(
        self,
        tenant_id: str,
        reviewed_by_user_id: str,
    ) -> list[FeedbackEvaluationCandidate]:
        candidates: list[FeedbackEvaluationCandidate] = []
        for feedback in self.list_feedback(tenant_id):
            if not self._is_low_rated_run_feedback(feedback):
                continue
            if self._has_evaluation_candidate_for_feedback(feedback.id):
                continue
            candidate = FeedbackEvaluationCandidate(
                id=new_id("eval_candidate"),
                tenant_id=tenant_id,
                source_feedback_ids=[feedback.id],
                source_run_id=feedback.run_id or feedback.target_id,
                failure_reason="low_rated_run",
                proposed_eval_name="Review low-rated customer run",
                status=FeedbackCandidateStatus.PENDING_REVIEW,
                human_reviewed_by_user_id=reviewed_by_user_id,
                created_at=utc_now(),
            )
            self.evaluation_candidates[candidate.id] = candidate
            candidates.append(candidate)
        return candidates

    def create_solution_pack_improvement_candidates(
        self,
        tenant_id: str,
        reviewed_by_user_id: str,
        minimum_repeated_feedback: int = 3,
    ) -> list[SolutionPackFeedbackCandidate]:
        grouped_feedback: dict[tuple[str, str], list[CustomerFeedback]] = defaultdict(list)
        for feedback in self.list_feedback(tenant_id):
            if feedback.feedback_type != CustomerFeedbackType.MISSING_SKILL:
                continue
            solution_pack_id = feedback.solution_pack_id
            if (
                solution_pack_id is None
                and feedback.target_type == CustomerFeedbackTargetType.SOLUTION_PACK
            ):
                solution_pack_id = feedback.target_id
            missing_skill_name = self._normalized_missing_skill_name(feedback)
            if solution_pack_id is None or missing_skill_name is None:
                continue
            grouped_feedback[(solution_pack_id, missing_skill_name)].append(feedback)

        candidates: list[SolutionPackFeedbackCandidate] = []
        for (solution_pack_id, missing_skill_name), feedback_items in grouped_feedback.items():
            if len(feedback_items) < minimum_repeated_feedback:
                continue
            if self._has_solution_pack_candidate(tenant_id, solution_pack_id, missing_skill_name):
                continue
            candidate = SolutionPackFeedbackCandidate(
                id=new_id("pack_candidate"),
                tenant_id=tenant_id,
                source_feedback_ids=[feedback.id for feedback in feedback_items],
                solution_pack_id=solution_pack_id,
                requested_skill_name=missing_skill_name,
                proposed_change_summary="Review repeated missing-skill feedback for solution pack.",
                status=FeedbackCandidateStatus.PENDING_REVIEW,
                human_reviewed_by_user_id=reviewed_by_user_id,
                created_at=utc_now(),
            )
            self.solution_pack_candidates[candidate.id] = candidate
            candidates.append(candidate)
        return candidates

    def review_evaluation_candidate(
        self,
        tenant_id: str,
        candidate_id: str,
        reviewed_by_user_id: str,
        status: FeedbackCandidateStatus,
        review_note: str | None = None,
    ) -> FeedbackEvaluationCandidate:
        if status not in {FeedbackCandidateStatus.ACCEPTED, FeedbackCandidateStatus.REJECTED}:
            raise ValueError("feedback evaluation candidate review must accept or reject")
        candidate = self._get_evaluation_candidate(tenant_id, candidate_id)
        evaluation_case_id = None
        if status == FeedbackCandidateStatus.ACCEPTED:
            evaluation_case = self._ensure_evaluation_case_record(
                candidate=candidate,
                created_by_user_id=reviewed_by_user_id,
            )
            evaluation_case_id = evaluation_case.id
        elif candidate.evaluation_case_id is not None:
            self.evaluation_case_records.pop(candidate.evaluation_case_id, None)
        updated = candidate.model_copy(
            update={
                "status": status,
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
                "review_note": review_note,
                "evaluation_case_id": evaluation_case_id,
                "production_change_applied": False,
            }
        )
        self.evaluation_candidates[candidate_id] = updated
        self._record_candidate_review_audit(
            tenant_id=tenant_id,
            user_id=reviewed_by_user_id,
            event_type="customer.feedback_eval_candidate.reviewed",
            metadata={
                "candidate_id": updated.id,
                "status": updated.status.value,
                "source_feedback_count": len(updated.source_feedback_ids),
                "evaluation_case_id": updated.evaluation_case_id,
                "reviewed_by_user_id": reviewed_by_user_id,
            },
        )
        return updated

    def review_solution_pack_candidate(
        self,
        tenant_id: str,
        candidate_id: str,
        reviewed_by_user_id: str,
        status: FeedbackCandidateStatus,
        review_note: str | None = None,
    ) -> SolutionPackFeedbackCandidate:
        if status not in {FeedbackCandidateStatus.ACCEPTED, FeedbackCandidateStatus.REJECTED}:
            raise ValueError("solution pack candidate review must accept or reject")
        candidate = self._get_solution_pack_candidate(tenant_id, candidate_id)
        publication_draft_id = None
        if status == FeedbackCandidateStatus.ACCEPTED:
            publication_draft = self._ensure_solution_pack_publication_draft(
                candidate=candidate,
                created_by_user_id=reviewed_by_user_id,
            )
            publication_draft_id = publication_draft.id
        elif candidate.publication_draft_id is not None:
            self.solution_pack_publication_drafts.pop(
                candidate.publication_draft_id,
                None,
            )
        updated = candidate.model_copy(
            update={
                "status": status,
                "reviewed_by_user_id": reviewed_by_user_id,
                "reviewed_at": utc_now(),
                "review_note": review_note,
                "publication_draft_id": publication_draft_id,
                "production_change_applied": False,
            }
        )
        self.solution_pack_candidates[candidate_id] = updated
        self._record_candidate_review_audit(
            tenant_id=tenant_id,
            user_id=reviewed_by_user_id,
            event_type="customer.solution_pack_candidate.reviewed",
            metadata={
                "candidate_id": updated.id,
                "status": updated.status.value,
                "solution_pack_id": updated.solution_pack_id,
                "source_feedback_count": len(updated.source_feedback_ids),
                "publication_draft_id": updated.publication_draft_id,
                "reviewed_by_user_id": reviewed_by_user_id,
            },
        )
        return updated

    def _is_low_rated_run_feedback(self, feedback: CustomerFeedback) -> bool:
        if feedback.target_type != CustomerFeedbackTargetType.RUN and feedback.run_id is None:
            return False
        if feedback.feedback_type in {
            CustomerFeedbackType.WRONG_ANSWER,
            CustomerFeedbackType.BUG_REPORT,
        }:
            return True
        return (
            feedback.feedback_type == CustomerFeedbackType.THUMBS_RATING
            and feedback.rating == -1
        )

    def _has_evaluation_candidate_for_feedback(self, feedback_id: str) -> bool:
        return any(
            feedback_id in candidate.source_feedback_ids
            for candidate in self.evaluation_candidates.values()
        )

    def _get_evaluation_candidate(
        self,
        tenant_id: str,
        candidate_id: str,
    ) -> FeedbackEvaluationCandidate:
        candidate = self.evaluation_candidates.get(candidate_id)
        if candidate is None or candidate.tenant_id != tenant_id:
            raise NotFoundError(f"Feedback evaluation candidate not found: {candidate_id}")
        return candidate

    def _get_solution_pack_candidate(
        self,
        tenant_id: str,
        candidate_id: str,
    ) -> SolutionPackFeedbackCandidate:
        candidate = self.solution_pack_candidates.get(candidate_id)
        if candidate is None or candidate.tenant_id != tenant_id:
            raise NotFoundError(f"Solution pack feedback candidate not found: {candidate_id}")
        return candidate

    def _has_solution_pack_candidate(
        self,
        tenant_id: str,
        solution_pack_id: str,
        missing_skill_name: str,
    ) -> bool:
        return any(
            candidate.tenant_id == tenant_id
            and candidate.solution_pack_id == solution_pack_id
            and candidate.requested_skill_name == missing_skill_name
            for candidate in self.solution_pack_candidates.values()
        )

    def _get_solution_pack_publication_draft(
        self,
        tenant_id: str,
        publication_draft_id: str,
    ) -> SolutionPackPublicationDraftRecord:
        draft = self.solution_pack_publication_drafts.get(publication_draft_id)
        if draft is None or draft.tenant_id != tenant_id:
            raise NotFoundError(
                f"Solution pack publication draft not found: {publication_draft_id}"
            )
        return draft

    def _save_solution_pack_publication_draft(
        self,
        publication_draft: SolutionPackPublicationDraftRecord,
    ) -> None:
        self.solution_pack_publication_drafts[publication_draft.id] = publication_draft

    def _require_editable_publication_draft(
        self,
        draft: SolutionPackPublicationDraftRecord,
    ) -> None:
        if draft.status not in {"draft", "rejected"}:
            raise ValueError("solution pack publication draft is not editable")

    def _required_text(self, value: str, field_name: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must not be empty")
        return cleaned

    def _proposed_skills_for_draft(
        self,
        draft: SolutionPackPublicationDraftRecord,
    ) -> list[SkillManifest]:
        if draft.proposed_skill_manifests:
            return list(draft.proposed_skill_manifests)
        if draft.proposed_skill_manifest is not None:
            return [draft.proposed_skill_manifest]
        return []

    def _replace_or_append_skills(
        self,
        skills: list[SkillManifest],
        proposed_skills: list[SkillManifest],
    ) -> list[SkillManifest]:
        updated_skills: list[SkillManifest] = []
        proposed_by_id = {skill.id: skill for skill in proposed_skills}
        replaced_skill_ids: set[str] = set()
        for skill in skills:
            proposed_skill = proposed_by_id.get(skill.id)
            if proposed_skill is not None:
                updated_skills.append(proposed_skill)
                replaced_skill_ids.add(proposed_skill.id)
                continue
            updated_skills.append(skill)
        for proposed_skill in proposed_skills:
            if proposed_skill.id not in replaced_skill_ids:
                updated_skills.append(proposed_skill)
        return updated_skills

    def _mark_solution_pack_candidate_applied(
        self,
        tenant_id: str,
        candidate_id: str,
    ) -> None:
        try:
            candidate = self._get_solution_pack_candidate(tenant_id, candidate_id)
        except NotFoundError:
            return
        self._save_solution_pack_candidate(
            candidate.model_copy(update={"production_change_applied": True})
        )

    def _save_solution_pack_candidate(
        self,
        candidate: SolutionPackFeedbackCandidate,
    ) -> None:
        self.solution_pack_candidates[candidate.id] = candidate

    def _record_publication_draft_audit(
        self,
        tenant_id: str,
        user_id: str,
        event_type: str,
        draft: SolutionPackPublicationDraftRecord,
        review_note_present: bool = False,
    ) -> None:
        if self.audit_store is None:
            return
        metadata = {
            "publication_draft_id": draft.id,
            "status": draft.status,
            "solution_pack_id": draft.solution_pack_id,
            "source_feedback_count": len(draft.source_feedback_ids),
            "actor_user_id": user_id,
            "review_note_present": review_note_present,
        }
        if event_type == "customer.solution_pack_draft.reviewed":
            metadata["reviewed_by_user_id"] = user_id
        if event_type == "customer.solution_pack_draft.applied":
            proposed_skills = self._proposed_skills_for_draft(draft)
            metadata = {
                "publication_draft_id": draft.id,
                "solution_pack_id": draft.solution_pack_id,
                "pack_version": draft.proposed_pack_version,
                "source_feedback_count": len(draft.source_feedback_ids),
                "applied_by_user_id": user_id,
            }
            if len(proposed_skills) == 1:
                metadata["skill_id"] = proposed_skills[0].id
            else:
                metadata["skill_ids"] = [skill.id for skill in proposed_skills]
                metadata["skill_count"] = len(proposed_skills)
        self.audit_store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            run_id=None,
            event_type=event_type,
            metadata=metadata,
        )

    def _ensure_evaluation_case_record(
        self,
        candidate: FeedbackEvaluationCandidate,
        created_by_user_id: str,
    ) -> FeedbackEvaluationCaseRecord:
        if candidate.evaluation_case_id is not None:
            existing = self.evaluation_case_records.get(candidate.evaluation_case_id)
            if existing is not None:
                return existing
        evaluation_case = FeedbackEvaluationCaseRecord(
            id=candidate.evaluation_case_id or new_id("eval_case"),
            tenant_id=candidate.tenant_id,
            source_candidate_id=candidate.id,
            source_feedback_ids=list(candidate.source_feedback_ids),
            source_run_id=candidate.source_run_id,
            failure_reason=candidate.failure_reason,
            proposed_eval_name=candidate.proposed_eval_name,
            status="draft",
            created_by_user_id=created_by_user_id,
            production_change_applied=False,
            created_at=utc_now(),
        )
        self.evaluation_case_records[evaluation_case.id] = evaluation_case
        return evaluation_case

    def _ensure_solution_pack_publication_draft(
        self,
        candidate: SolutionPackFeedbackCandidate,
        created_by_user_id: str,
    ) -> SolutionPackPublicationDraftRecord:
        if candidate.publication_draft_id is not None:
            existing = self.solution_pack_publication_drafts.get(candidate.publication_draft_id)
            if existing is not None:
                return existing
        publication_draft = SolutionPackPublicationDraftRecord(
            id=candidate.publication_draft_id or new_id("pack_draft"),
            tenant_id=candidate.tenant_id,
            source_candidate_id=candidate.id,
            source_feedback_ids=list(candidate.source_feedback_ids),
            solution_pack_id=candidate.solution_pack_id,
            requested_skill_name=candidate.requested_skill_name,
            proposed_change_summary=candidate.proposed_change_summary,
            status="draft",
            created_by_user_id=created_by_user_id,
            production_change_applied=False,
            created_at=utc_now(),
        )
        self.solution_pack_publication_drafts[publication_draft.id] = publication_draft
        return publication_draft

    def _normalized_missing_skill_name(
        self,
        feedback: CustomerFeedback,
    ) -> str | None:
        if feedback.missing_skill_name is None:
            return None
        value = feedback.missing_skill_name.strip()
        return value or None

    def _record_feedback_audit(self, feedback: CustomerFeedback) -> None:
        if self.audit_store is None:
            return
        self.audit_store.record_audit_event(
            tenant_id=feedback.tenant_id,
            workspace_id=None,
            user_id=feedback.submitted_by_user_id,
            run_id=feedback.run_id,
            event_type="customer.feedback.submitted",
            metadata={
                "feedback_id": feedback.id,
                "feedback_type": feedback.feedback_type.value,
                "target_type": feedback.target_type.value,
                "target_id": feedback.target_id,
                "rating": feedback.rating,
                "submitted_by_user_id": feedback.submitted_by_user_id,
            },
        )

    def _record_candidate_review_audit(
        self,
        tenant_id: str,
        user_id: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit_store is None:
            return
        self.audit_store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            run_id=None,
            event_type=event_type,
            metadata=metadata,
        )

import json
from datetime import datetime, timezone
from typing import Any

from taroai.customer_success.feedback import (
    CustomerFeedback,
    CustomerFeedbackCreate,
    CustomerFeedbackTargetType,
    CustomerFeedbackType,
    FeedbackCandidateStatus,
    FeedbackEvaluationCaseRecord,
    FeedbackEvaluationCandidate,
    InMemoryCustomerFeedbackService,
    SolutionPackFeedbackCandidate,
    SolutionPackPublicationDraftRecord,
)
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id, utc_now
from taroai.skills import SkillManifest
from taroai.store import NotFoundError


class SqlCustomerFeedbackService(InMemoryCustomerFeedbackService):
    config: DatabaseConfig

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
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            connection.execute(
                """
                INSERT INTO customer_feedback_records (
                    id, tenant_id, submitted_by_user_id, feedback_type,
                    target_type, target_id, rating, comment, run_id, artifact_id,
                    skill_id, solution_pack_id, onboarding_step_id,
                    missing_skill_name, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._feedback_values(feedback),
            )
        self._record_feedback_audit(feedback)
        return feedback

    def list_feedback(self, tenant_id: str) -> list[CustomerFeedback]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM customer_feedback_records
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._feedback_from_row(row) for row in rows]

    def list_evaluation_candidates(
        self,
        tenant_id: str,
    ) -> list[FeedbackEvaluationCandidate]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM customer_feedback_evaluation_candidates
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._evaluation_candidate_from_row(row) for row in rows]

    def list_solution_pack_candidates(
        self,
        tenant_id: str,
    ) -> list[SolutionPackFeedbackCandidate]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM customer_solution_pack_feedback_candidates
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._solution_pack_candidate_from_row(row) for row in rows]

    def list_evaluation_cases(
        self,
        tenant_id: str,
    ) -> list[FeedbackEvaluationCaseRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM customer_feedback_evaluation_cases
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._evaluation_case_from_row(row) for row in rows]

    def list_solution_pack_publication_drafts(
        self,
        tenant_id: str,
    ) -> list[SolutionPackPublicationDraftRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM customer_solution_pack_publication_drafts
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._publication_draft_from_row(row) for row in rows]

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
            self._save_evaluation_candidate(candidate)
            candidates.append(candidate)
        return candidates

    def create_solution_pack_improvement_candidates(
        self,
        tenant_id: str,
        reviewed_by_user_id: str,
        minimum_repeated_feedback: int = 3,
    ) -> list[SolutionPackFeedbackCandidate]:
        grouped_feedback: dict[tuple[str, str], list[CustomerFeedback]] = {}
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
            grouped_feedback.setdefault(
                (solution_pack_id, missing_skill_name),
                [],
            ).append(feedback)

        candidates: list[SolutionPackFeedbackCandidate] = []
        for (solution_pack_id, missing_skill_name), feedback_items in grouped_feedback.items():
            if len(feedback_items) < minimum_repeated_feedback:
                continue
            if self._has_solution_pack_candidate(
                tenant_id,
                solution_pack_id,
                missing_skill_name,
            ):
                continue
            candidate = SolutionPackFeedbackCandidate(
                id=new_id("pack_candidate"),
                tenant_id=tenant_id,
                source_feedback_ids=[feedback.id for feedback in feedback_items],
                solution_pack_id=solution_pack_id,
                requested_skill_name=missing_skill_name,
                proposed_change_summary=(
                    "Review repeated missing-skill feedback for solution pack."
                ),
                status=FeedbackCandidateStatus.PENDING_REVIEW,
                human_reviewed_by_user_id=reviewed_by_user_id,
                created_at=utc_now(),
            )
            self._save_solution_pack_candidate(candidate)
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
            self._delete_evaluation_case(candidate.tenant_id, candidate.evaluation_case_id)
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
        self._save_evaluation_candidate(updated)
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
            self._delete_publication_draft(
                candidate.tenant_id,
                candidate.publication_draft_id,
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
        self._save_solution_pack_candidate(updated)
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

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _has_evaluation_candidate_for_feedback(self, feedback_id: str) -> bool:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source_feedback_ids FROM customer_feedback_evaluation_candidates"
            ).fetchall()
        return any(feedback_id in self._loads(row["source_feedback_ids"]) for row in rows)

    def _has_solution_pack_candidate(
        self,
        tenant_id: str,
        solution_pack_id: str,
        missing_skill_name: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM customer_solution_pack_feedback_candidates
                WHERE tenant_id = ?
                  AND solution_pack_id = ?
                  AND requested_skill_name = ?
                """,
                (tenant_id, solution_pack_id, missing_skill_name),
            ).fetchone()
        return row is not None

    def _get_evaluation_candidate(
        self,
        tenant_id: str,
        candidate_id: str,
    ) -> FeedbackEvaluationCandidate:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM customer_feedback_evaluation_candidates
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, candidate_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Feedback evaluation candidate not found: {candidate_id}")
        return self._evaluation_candidate_from_row(row)

    def _get_solution_pack_candidate(
        self,
        tenant_id: str,
        candidate_id: str,
    ) -> SolutionPackFeedbackCandidate:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM customer_solution_pack_feedback_candidates
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, candidate_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Solution pack feedback candidate not found: {candidate_id}")
        return self._solution_pack_candidate_from_row(row)

    def _get_solution_pack_publication_draft(
        self,
        tenant_id: str,
        publication_draft_id: str,
    ) -> SolutionPackPublicationDraftRecord:
        draft = self._get_publication_draft_optional(tenant_id, publication_draft_id)
        if draft is None:
            raise NotFoundError(
                f"Solution pack publication draft not found: {publication_draft_id}"
            )
        return draft

    def _save_solution_pack_publication_draft(
        self,
        publication_draft: SolutionPackPublicationDraftRecord,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE customer_solution_pack_publication_drafts
                SET requested_skill_name = ?,
                    proposed_change_summary = ?,
                    proposed_pack_version = ?,
                    proposed_skill_manifest = ?,
                    proposed_skill_manifests = ?,
                    status = ?,
                    production_change_applied = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    publication_draft.requested_skill_name,
                    publication_draft.proposed_change_summary,
                    publication_draft.proposed_pack_version,
                    self._json_optional_model(publication_draft.proposed_skill_manifest),
                    self._json_model_list(publication_draft.proposed_skill_manifests),
                    publication_draft.status,
                    publication_draft.production_change_applied,
                    publication_draft.tenant_id,
                    publication_draft.id,
                ),
            )

    def _ensure_evaluation_case_record(
        self,
        candidate: FeedbackEvaluationCandidate,
        created_by_user_id: str,
    ) -> FeedbackEvaluationCaseRecord:
        if candidate.evaluation_case_id is not None:
            existing = self._get_evaluation_case_optional(
                candidate.tenant_id,
                candidate.evaluation_case_id,
            )
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
        with self._connect() as connection:
            self._ensure_tenant(connection, evaluation_case.tenant_id)
            connection.execute(
                """
                INSERT INTO customer_feedback_evaluation_cases (
                    id, tenant_id, source_candidate_id, source_feedback_ids,
                    source_run_id, failure_reason, proposed_eval_name, status,
                    created_by_user_id, production_change_applied, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._evaluation_case_values(evaluation_case),
            )
        return evaluation_case

    def _ensure_solution_pack_publication_draft(
        self,
        candidate: SolutionPackFeedbackCandidate,
        created_by_user_id: str,
    ) -> SolutionPackPublicationDraftRecord:
        if candidate.publication_draft_id is not None:
            existing = self._get_publication_draft_optional(
                candidate.tenant_id,
                candidate.publication_draft_id,
            )
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
        with self._connect() as connection:
            self._ensure_tenant(connection, publication_draft.tenant_id)
            connection.execute(
                """
                INSERT INTO customer_solution_pack_publication_drafts (
                    id, tenant_id, source_candidate_id, source_feedback_ids,
                    solution_pack_id, requested_skill_name, proposed_change_summary,
                    proposed_pack_version, proposed_skill_manifest,
                    proposed_skill_manifests, status, created_by_user_id,
                    production_change_applied, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._publication_draft_values(publication_draft),
            )
        return publication_draft

    def _save_evaluation_candidate(
        self,
        candidate: FeedbackEvaluationCandidate,
    ) -> None:
        with self._connect() as connection:
            self._ensure_tenant(connection, candidate.tenant_id)
            connection.execute(
                """
                INSERT INTO customer_feedback_evaluation_candidates (
                    id, tenant_id, source_feedback_ids, source_run_id,
                    failure_reason, proposed_eval_name, status,
                    human_reviewed_by_user_id, production_change_applied,
                    reviewed_by_user_id, reviewed_at, review_note,
                    evaluation_case_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_feedback_ids = excluded.source_feedback_ids,
                    status = excluded.status,
                    production_change_applied = excluded.production_change_applied,
                    reviewed_by_user_id = excluded.reviewed_by_user_id,
                    reviewed_at = excluded.reviewed_at,
                    review_note = excluded.review_note,
                    evaluation_case_id = excluded.evaluation_case_id
                """,
                self._evaluation_candidate_values(candidate),
            )

    def _save_solution_pack_candidate(
        self,
        candidate: SolutionPackFeedbackCandidate,
    ) -> None:
        with self._connect() as connection:
            self._ensure_tenant(connection, candidate.tenant_id)
            connection.execute(
                """
                INSERT INTO customer_solution_pack_feedback_candidates (
                    id, tenant_id, source_feedback_ids, solution_pack_id,
                    requested_skill_name, proposed_change_summary, status,
                    human_reviewed_by_user_id, production_change_applied,
                    reviewed_by_user_id, reviewed_at, review_note,
                    publication_draft_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_feedback_ids = excluded.source_feedback_ids,
                    status = excluded.status,
                    production_change_applied = excluded.production_change_applied,
                    reviewed_by_user_id = excluded.reviewed_by_user_id,
                    reviewed_at = excluded.reviewed_at,
                    review_note = excluded.review_note,
                    publication_draft_id = excluded.publication_draft_id
                """,
                self._solution_pack_candidate_values(candidate),
            )

    def _delete_evaluation_case(self, tenant_id: str, evaluation_case_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM customer_feedback_evaluation_cases
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, evaluation_case_id),
            )

    def _delete_publication_draft(self, tenant_id: str, publication_draft_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM customer_solution_pack_publication_drafts
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, publication_draft_id),
            )

    def _get_evaluation_case_optional(
        self,
        tenant_id: str,
        evaluation_case_id: str,
    ) -> FeedbackEvaluationCaseRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM customer_feedback_evaluation_cases
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, evaluation_case_id),
            ).fetchone()
        if row is None:
            return None
        return self._evaluation_case_from_row(row)

    def _get_publication_draft_optional(
        self,
        tenant_id: str,
        publication_draft_id: str,
    ) -> SolutionPackPublicationDraftRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM customer_solution_pack_publication_drafts
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, publication_draft_id),
            ).fetchone()
        if row is None:
            return None
        return self._publication_draft_from_row(row)

    def _feedback_values(self, feedback: CustomerFeedback) -> tuple[Any, ...]:
        return (
            feedback.id,
            feedback.tenant_id,
            feedback.submitted_by_user_id,
            feedback.feedback_type.value,
            feedback.target_type.value,
            feedback.target_id,
            feedback.rating,
            feedback.comment,
            feedback.run_id,
            feedback.artifact_id,
            feedback.skill_id,
            feedback.solution_pack_id,
            feedback.onboarding_step_id,
            feedback.missing_skill_name,
            self._json(feedback.metadata),
            self._dt(feedback.created_at),
        )

    def _evaluation_candidate_values(
        self,
        candidate: FeedbackEvaluationCandidate,
    ) -> tuple[Any, ...]:
        return (
            candidate.id,
            candidate.tenant_id,
            self._json(candidate.source_feedback_ids),
            candidate.source_run_id,
            candidate.failure_reason,
            candidate.proposed_eval_name,
            candidate.status.value,
            candidate.human_reviewed_by_user_id,
            candidate.production_change_applied,
            candidate.reviewed_by_user_id,
            self._dt_optional(candidate.reviewed_at),
            candidate.review_note,
            candidate.evaluation_case_id,
            self._dt(candidate.created_at),
        )

    def _solution_pack_candidate_values(
        self,
        candidate: SolutionPackFeedbackCandidate,
    ) -> tuple[Any, ...]:
        return (
            candidate.id,
            candidate.tenant_id,
            self._json(candidate.source_feedback_ids),
            candidate.solution_pack_id,
            candidate.requested_skill_name,
            candidate.proposed_change_summary,
            candidate.status.value,
            candidate.human_reviewed_by_user_id,
            candidate.production_change_applied,
            candidate.reviewed_by_user_id,
            self._dt_optional(candidate.reviewed_at),
            candidate.review_note,
            candidate.publication_draft_id,
            self._dt(candidate.created_at),
        )

    def _evaluation_case_values(
        self,
        evaluation_case: FeedbackEvaluationCaseRecord,
    ) -> tuple[Any, ...]:
        return (
            evaluation_case.id,
            evaluation_case.tenant_id,
            evaluation_case.source_candidate_id,
            self._json(evaluation_case.source_feedback_ids),
            evaluation_case.source_run_id,
            evaluation_case.failure_reason,
            evaluation_case.proposed_eval_name,
            evaluation_case.status,
            evaluation_case.created_by_user_id,
            evaluation_case.production_change_applied,
            self._dt(evaluation_case.created_at),
        )

    def _publication_draft_values(
        self,
        publication_draft: SolutionPackPublicationDraftRecord,
    ) -> tuple[Any, ...]:
        return (
            publication_draft.id,
            publication_draft.tenant_id,
            publication_draft.source_candidate_id,
            self._json(publication_draft.source_feedback_ids),
            publication_draft.solution_pack_id,
            publication_draft.requested_skill_name,
            publication_draft.proposed_change_summary,
            publication_draft.proposed_pack_version,
            self._json_optional_model(publication_draft.proposed_skill_manifest),
            self._json_model_list(publication_draft.proposed_skill_manifests),
            publication_draft.status,
            publication_draft.created_by_user_id,
            publication_draft.production_change_applied,
            self._dt(publication_draft.created_at),
        )

    def _feedback_from_row(self, row) -> CustomerFeedback:
        return CustomerFeedback(
            id=row["id"],
            tenant_id=row["tenant_id"],
            submitted_by_user_id=row["submitted_by_user_id"],
            feedback_type=CustomerFeedbackType(row["feedback_type"]),
            target_type=CustomerFeedbackTargetType(row["target_type"]),
            target_id=row["target_id"],
            rating=row["rating"],
            comment=row["comment"],
            run_id=row["run_id"],
            artifact_id=row["artifact_id"],
            skill_id=row["skill_id"],
            solution_pack_id=row["solution_pack_id"],
            onboarding_step_id=row["onboarding_step_id"],
            missing_skill_name=row["missing_skill_name"],
            metadata=self._loads(row["metadata"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _evaluation_candidate_from_row(self, row) -> FeedbackEvaluationCandidate:
        return FeedbackEvaluationCandidate(
            id=row["id"],
            tenant_id=row["tenant_id"],
            source_feedback_ids=self._loads(row["source_feedback_ids"]),
            source_run_id=row["source_run_id"],
            failure_reason=row["failure_reason"],
            proposed_eval_name=row["proposed_eval_name"],
            status=FeedbackCandidateStatus(row["status"]),
            human_reviewed_by_user_id=row["human_reviewed_by_user_id"],
            production_change_applied=self._bool(row["production_change_applied"]),
            reviewed_by_user_id=row["reviewed_by_user_id"],
            reviewed_at=self._parse_dt_optional(row["reviewed_at"]),
            review_note=row["review_note"],
            evaluation_case_id=row["evaluation_case_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _solution_pack_candidate_from_row(self, row) -> SolutionPackFeedbackCandidate:
        return SolutionPackFeedbackCandidate(
            id=row["id"],
            tenant_id=row["tenant_id"],
            source_feedback_ids=self._loads(row["source_feedback_ids"]),
            solution_pack_id=row["solution_pack_id"],
            requested_skill_name=row["requested_skill_name"],
            proposed_change_summary=row["proposed_change_summary"],
            status=FeedbackCandidateStatus(row["status"]),
            human_reviewed_by_user_id=row["human_reviewed_by_user_id"],
            production_change_applied=self._bool(row["production_change_applied"]),
            reviewed_by_user_id=row["reviewed_by_user_id"],
            reviewed_at=self._parse_dt_optional(row["reviewed_at"]),
            review_note=row["review_note"],
            publication_draft_id=row["publication_draft_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _evaluation_case_from_row(self, row) -> FeedbackEvaluationCaseRecord:
        return FeedbackEvaluationCaseRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            source_candidate_id=row["source_candidate_id"],
            source_feedback_ids=self._loads(row["source_feedback_ids"]),
            source_run_id=row["source_run_id"],
            failure_reason=row["failure_reason"],
            proposed_eval_name=row["proposed_eval_name"],
            status=row["status"],
            created_by_user_id=row["created_by_user_id"],
            production_change_applied=self._bool(row["production_change_applied"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _publication_draft_from_row(self, row) -> SolutionPackPublicationDraftRecord:
        return SolutionPackPublicationDraftRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            source_candidate_id=row["source_candidate_id"],
            source_feedback_ids=self._loads(row["source_feedback_ids"]),
            solution_pack_id=row["solution_pack_id"],
            requested_skill_name=row["requested_skill_name"],
            proposed_change_summary=row["proposed_change_summary"],
            proposed_pack_version=row["proposed_pack_version"],
            proposed_skill_manifest=self._loads_optional(row["proposed_skill_manifest"]),
            proposed_skill_manifests=[
                SkillManifest.model_validate(skill)
                for skill in self._loads(row["proposed_skill_manifests"])
            ],
            status=row["status"],
            created_by_user_id=row["created_by_user_id"],
            production_change_applied=self._bool(row["production_change_applied"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _json_optional_model(self, value: Any | None) -> str | None:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return self._json(value.model_dump(mode="json"))
        return self._json(value)

    def _json_model_list(self, values: list[Any]) -> str:
        return self._json(
            [
                value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else value
                for value in values
            ]
        )

    def _loads(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)

    def _loads_optional(self, value: Any) -> Any | None:
        if value is None:
            return None
        return self._loads(value)

    def _dt_optional(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _dt(self, value: datetime) -> str:
        resolved = value
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc).isoformat()

    def _parse_dt_optional(self, value: Any) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)

    def _parse_dt(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            resolved = value
        else:
            resolved = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if resolved.tzinfo is None:
            return resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc)

    def _bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "t", "yes"}

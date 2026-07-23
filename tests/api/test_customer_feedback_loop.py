from pathlib import Path

import pytest

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.customer_success import (
    CustomerFeedbackCreate,
    CustomerFeedbackTargetType,
    CustomerFeedbackType,
    FeedbackCandidateStatus,
    InMemoryCustomerFeedbackService,
    SqlCustomerFeedbackService,
)
from taroai.domain import RunCreate, RunStatus
from taroai.skills import SkillManifest
from taroai.solution_packs import InMemorySolutionPackRegistry, SolutionPackManifest
from taroai.store import InMemoryControlPlaneStore
from tests.api.test_solution_packs import skill_manifest_payload, solution_pack_payload


def create_customer_run(store: InMemoryControlPlaneStore):
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_customer",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_renewal",
            message="Private customer renewal details.",
            mode="autonomous",
        ),
    )
    return store.update_run_status("tenant_acme", run.id, RunStatus.SUCCEEDED)


def test_customer_feedback_capture_is_tenant_scoped_and_audited_without_raw_comment():
    store = InMemoryControlPlaneStore()
    run = create_customer_run(store)
    service = InMemoryCustomerFeedbackService(audit_store=store)

    feedback = service.capture_feedback(
        tenant_id="tenant_acme",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_customer",
            feedback_type=CustomerFeedbackType.THUMBS_RATING,
            target_type=CustomerFeedbackTargetType.RUN,
            target_id=run.id,
            run_id=run.id,
            rating=-1,
            comment="The answer exposed private renewal context.",
            metadata={"raw_response": "private renewal context"},
        ),
    )

    assert feedback.id.startswith("feedback_")
    assert service.list_feedback("tenant_acme") == [feedback]
    assert service.list_feedback("tenant_other") == []
    audits = store.list_audit_events("tenant_acme")
    assert audits[-1].event_type == "customer.feedback.submitted"
    assert audits[-1].metadata == {
        "feedback_id": feedback.id,
        "feedback_type": "thumbs_rating",
        "target_type": "run",
        "target_id": run.id,
        "rating": -1,
        "submitted_by_user_id": "user_customer",
    }
    assert "private renewal context" not in str(audits[-1].metadata)


def test_low_rated_run_feedback_creates_reviewable_evaluation_candidate_only():
    store = InMemoryControlPlaneStore()
    run = create_customer_run(store)
    service = InMemoryCustomerFeedbackService(audit_store=store)
    feedback = service.capture_feedback(
        tenant_id="tenant_acme",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_customer",
            feedback_type=CustomerFeedbackType.WRONG_ANSWER,
            target_type=CustomerFeedbackTargetType.RUN,
            target_id=run.id,
            run_id=run.id,
            rating=-1,
            comment="Wrong answer with private context.",
        ),
    )

    candidates = service.create_evaluation_candidates_for_low_rated_runs(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_cs_lead",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status == FeedbackCandidateStatus.PENDING_REVIEW
    assert candidate.source_feedback_ids == [feedback.id]
    assert candidate.source_run_id == run.id
    assert candidate.failure_reason == "low_rated_run"
    assert candidate.human_reviewed_by_user_id == "user_cs_lead"
    assert candidate.production_change_applied is False
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert "private context" not in candidate.model_dump_json()


def test_repeated_missing_skill_feedback_creates_solution_pack_candidate_without_mutation():
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    service = InMemoryCustomerFeedbackService(solution_pack_registry=pack_registry)
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice reconciliation",
                comment="Need this for private finance workflow.",
            ),
        )
    service.capture_feedback(
        tenant_id="tenant_other",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_other",
            feedback_type=CustomerFeedbackType.MISSING_SKILL,
            target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
            target_id="sales.renewal_ops",
            solution_pack_id="sales.renewal_ops",
            missing_skill_name="ERP invoice reconciliation",
        ),
    )

    candidates = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status == FeedbackCandidateStatus.PENDING_REVIEW
    assert candidate.solution_pack_id == "sales.renewal_ops"
    assert candidate.requested_skill_name == "ERP invoice reconciliation"
    assert len(candidate.source_feedback_ids) == 3
    assert candidate.human_reviewed_by_user_id == "user_solution_lead"
    assert candidate.production_change_applied is False
    assert pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops").manifest.skills == manifest.skills
    assert "private finance" not in candidate.model_dump_json()


def test_feedback_evaluation_candidate_review_creates_eval_artifact_without_mutating_run():
    store = InMemoryControlPlaneStore()
    run = create_customer_run(store)
    service = InMemoryCustomerFeedbackService(audit_store=store)
    feedback = service.capture_feedback(
        tenant_id="tenant_acme",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_customer",
            feedback_type=CustomerFeedbackType.WRONG_ANSWER,
            target_type=CustomerFeedbackTargetType.RUN,
            target_id=run.id,
            run_id=run.id,
            rating=-1,
            comment="Wrong answer with private account context.",
        ),
    )
    candidate = service.create_evaluation_candidates_for_low_rated_runs(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_cs_lead",
    )[0]

    accepted = service.review_evaluation_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_eval_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
        review_note="Create eval case for renewal-answer correctness.",
    )

    assert accepted.status == FeedbackCandidateStatus.ACCEPTED
    assert accepted.reviewed_by_user_id == "user_eval_owner"
    assert accepted.review_note == "Create eval case for renewal-answer correctness."
    assert accepted.production_change_applied is False
    assert accepted.evaluation_case_id.startswith("eval_case_")
    assert accepted.source_feedback_ids == [feedback.id]
    evaluation_cases = service.list_evaluation_cases("tenant_acme")
    assert len(evaluation_cases) == 1
    evaluation_case = evaluation_cases[0]
    assert evaluation_case.id == accepted.evaluation_case_id
    assert evaluation_case.source_candidate_id == candidate.id
    assert evaluation_case.source_feedback_ids == [feedback.id]
    assert evaluation_case.source_run_id == run.id
    assert evaluation_case.failure_reason == "low_rated_run"
    assert evaluation_case.proposed_eval_name == "Review low-rated customer run"
    assert evaluation_case.status == "draft"
    assert evaluation_case.created_by_user_id == "user_eval_owner"
    assert evaluation_case.production_change_applied is False
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert "private account" not in accepted.model_dump_json()
    assert "private account" not in evaluation_case.model_dump_json()
    audit = store.list_audit_events("tenant_acme")[-1]
    assert audit.event_type == "customer.feedback_eval_candidate.reviewed"
    assert audit.metadata == {
        "candidate_id": candidate.id,
        "status": "accepted",
        "source_feedback_count": 1,
        "evaluation_case_id": accepted.evaluation_case_id,
        "reviewed_by_user_id": "user_eval_owner",
    }


def test_solution_pack_candidate_review_creates_publication_draft_without_pack_mutation():
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    service = InMemoryCustomerFeedbackService(
        audit_store=InMemoryControlPlaneStore(),
        solution_pack_registry=pack_registry,
    )
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice reconciliation",
                comment="Need this for private finance workflow.",
            ),
        )
    candidate = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )[0]

    accepted = service.review_solution_pack_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_pack_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
        review_note="Draft a new skill for invoice reconciliation.",
    )

    assert accepted.status == FeedbackCandidateStatus.ACCEPTED
    assert accepted.reviewed_by_user_id == "user_pack_owner"
    assert accepted.review_note == "Draft a new skill for invoice reconciliation."
    assert accepted.production_change_applied is False
    assert accepted.publication_draft_id.startswith("pack_draft_")
    assert accepted.source_feedback_ids == candidate.source_feedback_ids
    publication_drafts = service.list_solution_pack_publication_drafts("tenant_acme")
    assert len(publication_drafts) == 1
    publication_draft = publication_drafts[0]
    assert publication_draft.id == accepted.publication_draft_id
    assert publication_draft.source_candidate_id == candidate.id
    assert publication_draft.source_feedback_ids == candidate.source_feedback_ids
    assert publication_draft.solution_pack_id == "sales.renewal_ops"
    assert publication_draft.requested_skill_name == "ERP invoice reconciliation"
    assert publication_draft.proposed_change_summary == (
        "Review repeated missing-skill feedback for solution pack."
    )
    assert publication_draft.status == "draft"
    assert publication_draft.created_by_user_id == "user_pack_owner"
    assert publication_draft.production_change_applied is False
    assert pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops").manifest.skills == manifest.skills
    assert "private finance" not in accepted.model_dump_json()
    assert "private finance" not in publication_draft.model_dump_json()


def test_solution_pack_publication_draft_edit_and_approval_workflow_is_governed():
    store = InMemoryControlPlaneStore()
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    service = InMemoryCustomerFeedbackService(
        audit_store=store,
        solution_pack_registry=pack_registry,
    )
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice reconciliation",
                comment="Need this for private finance workflow.",
            ),
        )
    candidate = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )[0]
    accepted = service.review_solution_pack_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_pack_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
    )

    updated = service.update_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=accepted.publication_draft_id,
        updated_by_user_id="user_solution_editor",
        requested_skill_name="ERP invoice matching",
        proposed_change_summary="Add governed invoice matching skill draft.",
        proposed_pack_version="1.0.1",
        proposed_skill_manifests=[
            SkillManifest.model_validate(
                {
                    **skill_manifest_payload("sales.erp_invoice_matching"),
                    "name": "ERP Invoice Matching",
                    "description": "Match ERP invoices against renewal account data.",
                    "required_scopes": ["erp.invoice.read"],
                    "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
                }
            )
        ],
    )
    submitted = service.submit_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=updated.id,
        submitted_by_user_id="user_solution_editor",
    )
    approved = service.review_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=updated.id,
        reviewed_by_user_id="user_solution_approver",
        status="approved",
        review_note="Approved without exposing private finance notes.",
    )

    assert updated.requested_skill_name == "ERP invoice matching"
    assert updated.proposed_change_summary == "Add governed invoice matching skill draft."
    assert updated.status == "draft"
    assert submitted.status == "in_review"
    assert approved.status == "approved"
    assert approved.production_change_applied is False
    assert service.list_solution_pack_publication_drafts("tenant_acme") == [approved]
    assert pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops").manifest.skills == manifest.skills
    audit_events = store.list_audit_events("tenant_acme")
    assert [event.event_type for event in audit_events[-3:]] == [
        "customer.solution_pack_draft.updated",
        "customer.solution_pack_draft.submitted",
        "customer.solution_pack_draft.reviewed",
    ]
    assert audit_events[-1].metadata == {
        "publication_draft_id": updated.id,
        "status": "approved",
        "solution_pack_id": "sales.renewal_ops",
        "source_feedback_count": 3,
        "actor_user_id": "user_solution_approver",
        "reviewed_by_user_id": "user_solution_approver",
        "review_note_present": True,
    }
    assert "private finance" not in approved.model_dump_json()
    assert "private finance" not in str(audit_events[-1].metadata)


def test_solution_pack_publication_draft_application_publishes_new_pack_version():
    store = InMemoryControlPlaneStore()
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_solution_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    service = InMemoryCustomerFeedbackService(
        audit_store=store,
        solution_pack_registry=pack_registry,
    )
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice reconciliation",
            ),
        )
    candidate = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )[0]
    accepted = service.review_solution_pack_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_pack_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
    )
    proposed_skill = SkillManifest.model_validate(
        {
            **skill_manifest_payload("sales.erp_invoice_matching"),
            "name": "ERP Invoice Matching",
            "description": "Match ERP invoices against renewal account data.",
            "required_scopes": ["erp.invoice.read"],
            "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
        }
    )
    updated = service.update_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=accepted.publication_draft_id,
        updated_by_user_id="user_solution_editor",
        requested_skill_name=proposed_skill.name,
        proposed_change_summary="Add governed ERP invoice matching skill.",
        proposed_pack_version="1.0.1",
        proposed_skill_manifest=proposed_skill,
    )
    submitted = service.submit_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=updated.id,
        submitted_by_user_id="user_solution_editor",
    )
    approved = service.review_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=submitted.id,
        reviewed_by_user_id="user_solution_approver",
        status="approved",
    )

    applied = service.apply_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=approved.id,
        applied_by_user_id="user_solution_publisher",
    )

    current_pack = pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops")
    versions = pack_registry.list_versions("tenant_acme", "sales.renewal_ops")
    assert applied.status == "applied"
    assert applied.production_change_applied is True
    assert current_pack.status.value == "published"
    assert current_pack.manifest.version == "1.0.1"
    assert [skill.id for skill in current_pack.manifest.skills] == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
        "sales.erp_invoice_matching",
    ]
    assert [entry.manifest.version for entry in versions] == ["1.0.0", "1.0.1"]
    assert pack_registry.list_versions("tenant_acme", "sales.renewal_ops")[0].manifest.skills == (
        manifest.skills
    )
    assert service.list_solution_pack_candidates("tenant_acme")[0].production_change_applied is True
    audit_metadata = store.list_audit_events("tenant_acme")[-1].metadata
    assert audit_metadata == {
        "publication_draft_id": applied.id,
        "solution_pack_id": "sales.renewal_ops",
        "pack_version": "1.0.1",
        "skill_id": "sales.erp_invoice_matching",
        "source_feedback_count": 3,
        "applied_by_user_id": "user_solution_publisher",
    }
    assert "input_schema" not in str(audit_metadata)


def test_solution_pack_publication_draft_application_can_publish_multiple_skills():
    store = InMemoryControlPlaneStore()
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_solution_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    service = InMemoryCustomerFeedbackService(
        audit_store=store,
        solution_pack_registry=pack_registry,
    )
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice automation",
            ),
        )
    candidate = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )[0]
    accepted = service.review_solution_pack_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_pack_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
    )
    proposed_skills = [
        SkillManifest.model_validate(
            {
                **skill_manifest_payload("sales.erp_invoice_matching"),
                "name": "ERP Invoice Matching",
                "description": "Match ERP invoices against renewal account data.",
                "required_scopes": ["erp.invoice.read"],
                "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
            }
        ),
        SkillManifest.model_validate(
            {
                **skill_manifest_payload("sales.erp_payment_terms"),
                "name": "ERP Payment Terms",
                "description": "Extract payment terms for renewal invoice reviews.",
                "required_scopes": ["erp.invoice.read"],
                "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
            }
        ),
    ]
    updated = service.update_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=accepted.publication_draft_id,
        updated_by_user_id="user_solution_editor",
        requested_skill_name="ERP invoice automation",
        proposed_change_summary="Add governed invoice matching and payment-term skills.",
        proposed_pack_version="1.0.1",
        proposed_skill_manifests=proposed_skills,
    )
    submitted = service.submit_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=updated.id,
        submitted_by_user_id="user_solution_editor",
    )
    approved = service.review_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=submitted.id,
        reviewed_by_user_id="user_solution_approver",
        status="approved",
    )

    applied = service.apply_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=approved.id,
        applied_by_user_id="user_solution_publisher",
    )

    current_pack = pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops")
    assert applied.status == "applied"
    assert [skill.id for skill in current_pack.manifest.skills] == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
        "sales.erp_invoice_matching",
        "sales.erp_payment_terms",
    ]
    assert applied.proposed_skill_manifests == proposed_skills
    audit_metadata = store.list_audit_events("tenant_acme")[-1].metadata
    assert audit_metadata["skill_ids"] == [
        "sales.erp_invoice_matching",
        "sales.erp_payment_terms",
    ]
    assert audit_metadata["skill_count"] == 2
    assert "input_schema" not in str(audit_metadata)


def test_solution_pack_publication_draft_application_rejects_duplicate_skill_ids():
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_solution_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    service = InMemoryCustomerFeedbackService(solution_pack_registry=pack_registry)
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice automation",
            ),
        )
    candidate = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )[0]
    accepted = service.review_solution_pack_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_pack_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
    )
    proposed_skills = [
        SkillManifest.model_validate(
            {
                **skill_manifest_payload("sales.erp_invoice_matching"),
                "name": "ERP Invoice Matching",
                "description": "Match ERP invoices against renewal account data.",
                "required_scopes": ["erp.invoice.read"],
            }
        ),
        SkillManifest.model_validate(
            {
                **skill_manifest_payload("sales.erp_invoice_matching"),
                "name": "ERP Invoice Matching Duplicate",
                "description": "Duplicate should not be publishable.",
                "required_scopes": ["erp.invoice.read"],
            }
        ),
    ]
    updated = service.update_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=accepted.publication_draft_id,
        updated_by_user_id="user_solution_editor",
        proposed_pack_version="1.0.1",
        proposed_skill_manifests=proposed_skills,
    )
    submitted = service.submit_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=updated.id,
        submitted_by_user_id="user_solution_editor",
    )
    approved = service.review_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=submitted.id,
        reviewed_by_user_id="user_solution_approver",
        status="approved",
    )

    with pytest.raises(ValueError, match="solution pack skill ids must be unique"):
        service.apply_solution_pack_publication_draft(
            tenant_id="tenant_acme",
            publication_draft_id=approved.id,
            applied_by_user_id="user_solution_publisher",
        )

    current_pack = pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops")
    assert current_pack.manifest.version == "1.0.0"
    assert service.list_solution_pack_candidates(
        "tenant_acme"
    )[0].production_change_applied is False


def test_feedback_candidate_review_rejects_without_downstream_artifact():
    store = InMemoryControlPlaneStore()
    run = create_customer_run(store)
    service = InMemoryCustomerFeedbackService(audit_store=store)
    service.capture_feedback(
        tenant_id="tenant_acme",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_customer",
            feedback_type=CustomerFeedbackType.THUMBS_RATING,
            target_type=CustomerFeedbackTargetType.RUN,
            target_id=run.id,
            run_id=run.id,
            rating=-1,
        ),
    )
    candidate = service.create_evaluation_candidates_for_low_rated_runs(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_cs_lead",
    )[0]

    rejected = service.review_evaluation_candidate(
        tenant_id="tenant_acme",
        candidate_id=candidate.id,
        reviewed_by_user_id="user_eval_owner",
        status=FeedbackCandidateStatus.REJECTED,
        review_note="Duplicate of existing eval case.",
    )

    assert rejected.status == FeedbackCandidateStatus.REJECTED
    assert rejected.evaluation_case_id is None
    assert rejected.production_change_applied is False
    assert service.list_evaluation_cases("tenant_acme") == []


def test_sql_customer_feedback_service_persists_review_records_after_restart(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'customer-feedback.sqlite3'}"
    config = DatabaseConfig(url=database_url)
    MigrationRunner(
        config=config,
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_solution_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    service = SqlCustomerFeedbackService(
        config=config,
        solution_pack_registry=pack_registry,
    )
    run_feedback = service.capture_feedback(
        tenant_id="tenant_acme",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_customer",
            feedback_type=CustomerFeedbackType.WRONG_ANSWER,
            target_type=CustomerFeedbackTargetType.RUN,
            target_id="run_customer_issue",
            run_id="run_customer_issue",
            rating=-1,
            comment="Wrong answer with private account context.",
        ),
    )
    for user_id in ["user_1", "user_2", "user_3"]:
        service.capture_feedback(
            tenant_id="tenant_acme",
            payload=CustomerFeedbackCreate(
                submitted_by_user_id=user_id,
                feedback_type=CustomerFeedbackType.MISSING_SKILL,
                target_type=CustomerFeedbackTargetType.SOLUTION_PACK,
                target_id="sales.renewal_ops",
                solution_pack_id="sales.renewal_ops",
                missing_skill_name="ERP invoice reconciliation",
                comment="Need this for private finance workflow.",
            ),
        )
    service.capture_feedback(
        tenant_id="tenant_other",
        payload=CustomerFeedbackCreate(
            submitted_by_user_id="user_other",
            feedback_type=CustomerFeedbackType.WRONG_ANSWER,
            target_type=CustomerFeedbackTargetType.RUN,
            target_id="run_other",
            run_id="run_other",
            rating=-1,
        ),
    )
    evaluation_candidate = service.create_evaluation_candidates_for_low_rated_runs(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_cs_lead",
    )[0]
    assert service.create_evaluation_candidates_for_low_rated_runs(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_cs_lead",
    ) == []
    pack_candidate = service.create_solution_pack_improvement_candidates(
        tenant_id="tenant_acme",
        reviewed_by_user_id="user_solution_lead",
        minimum_repeated_feedback=3,
    )[0]

    accepted_evaluation = service.review_evaluation_candidate(
        tenant_id="tenant_acme",
        candidate_id=evaluation_candidate.id,
        reviewed_by_user_id="user_eval_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
        review_note="Create eval case for renewal-answer correctness.",
    )
    accepted_pack = service.review_solution_pack_candidate(
        tenant_id="tenant_acme",
        candidate_id=pack_candidate.id,
        reviewed_by_user_id="user_pack_owner",
        status=FeedbackCandidateStatus.ACCEPTED,
        review_note="Draft a new skill for invoice reconciliation.",
    )
    updated_pack_draft = service.update_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=accepted_pack.publication_draft_id,
        updated_by_user_id="user_solution_editor",
        requested_skill_name="ERP invoice matching",
        proposed_change_summary="Add governed invoice matching skill draft.",
        proposed_pack_version="1.0.1",
        proposed_skill_manifests=[
            SkillManifest.model_validate(
                {
                    **skill_manifest_payload("sales.erp_invoice_matching"),
                    "name": "ERP Invoice Matching",
                    "description": "Match ERP invoices against renewal account data.",
                    "required_scopes": ["erp.invoice.read"],
                    "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
                }
            )
        ],
    )
    submitted_pack_draft = service.submit_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=updated_pack_draft.id,
        submitted_by_user_id="user_solution_editor",
    )
    approved_pack_draft = service.review_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=submitted_pack_draft.id,
        reviewed_by_user_id="user_solution_approver",
        status="approved",
        review_note="Approved without exposing private finance notes.",
    )
    applied_pack_draft = service.apply_solution_pack_publication_draft(
        tenant_id="tenant_acme",
        publication_draft_id=approved_pack_draft.id,
        applied_by_user_id="user_solution_publisher",
    )
    restarted = SqlCustomerFeedbackService(config=config)

    feedback_records = restarted.list_feedback("tenant_acme")
    evaluation_candidates = restarted.list_evaluation_candidates("tenant_acme")
    pack_candidates = restarted.list_solution_pack_candidates("tenant_acme")
    evaluation_cases = restarted.list_evaluation_cases("tenant_acme")
    publication_drafts = restarted.list_solution_pack_publication_drafts(
        "tenant_acme"
    )

    assert [feedback.id for feedback in feedback_records] == [
        run_feedback.id,
        *pack_candidate.source_feedback_ids,
    ]
    assert evaluation_candidates[0].id == evaluation_candidate.id
    assert evaluation_candidates[0].status == FeedbackCandidateStatus.ACCEPTED
    assert evaluation_candidates[0].evaluation_case_id == accepted_evaluation.evaluation_case_id
    assert pack_candidates[0].id == pack_candidate.id
    assert pack_candidates[0].status == FeedbackCandidateStatus.ACCEPTED
    assert pack_candidates[0].publication_draft_id == accepted_pack.publication_draft_id
    assert evaluation_cases[0].id == accepted_evaluation.evaluation_case_id
    assert evaluation_cases[0].source_candidate_id == evaluation_candidate.id
    assert evaluation_cases[0].source_feedback_ids == [run_feedback.id]
    assert evaluation_cases[0].created_by_user_id == "user_eval_owner"
    assert publication_drafts[0].id == accepted_pack.publication_draft_id
    assert publication_drafts[0].source_candidate_id == pack_candidate.id
    assert publication_drafts[0].requested_skill_name == "ERP invoice matching"
    assert publication_drafts[0].proposed_change_summary == (
        "Add governed invoice matching skill draft."
    )
    assert publication_drafts[0].proposed_pack_version == "1.0.1"
    assert publication_drafts[0].proposed_skill_manifest is None
    assert publication_drafts[0].proposed_skill_manifests[0].id == (
        "sales.erp_invoice_matching"
    )
    assert publication_drafts[0].status == "applied"
    assert publication_drafts[0].created_by_user_id == "user_pack_owner"
    assert applied_pack_draft.production_change_applied is True
    assert pack_candidates[0].production_change_applied is True
    assert pack_registry.get_for_tenant(
        "tenant_acme",
        "sales.renewal_ops",
    ).manifest.version == "1.0.1"
    assert restarted.list_evaluation_cases("tenant_other") == []
    assert restarted.list_solution_pack_publication_drafts("tenant_other") == []
    assert "private account" not in evaluation_cases[0].model_dump_json()
    assert "private finance" not in publication_drafts[0].model_dump_json()

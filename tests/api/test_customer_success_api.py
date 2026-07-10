from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.customer_success import InMemoryCustomerFeedbackService
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.solution_packs import InMemorySolutionPackRegistry, SolutionPackManifest
from tests.api.test_customer_feedback_loop import create_customer_run
from tests.api.test_customer_success_metrics import seed_success_inputs
from tests.api.test_solution_packs import skill_manifest_payload, solution_pack_payload


def create_customer_success_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    admin = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="success-admin@example.com",
            display_name="Success Admin",
            password="correct horse battery staple",
        )
    )
    employee = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="employee@example.com",
            display_name="Employee",
            password="correct horse battery staple",
        )
    )
    viewer = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_other",
            email="other-viewer@example.com",
            display_name="Other Viewer",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_customer_success_admin",
            name="Customer Success Admin",
            permissions=[
                Permission(action="customer_success.read", resource="tenant:tenant_acme"),
                Permission(action="customer_success.feedback", resource="tenant:tenant_acme"),
                Permission(action="customer_success.manage", resource="tenant:tenant_acme"),
                Permission(action="solution_packs.read", resource="tenant:tenant_acme"),
                Permission(action="solution_packs.manage", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_customer_success_employee",
            name="Customer Success Employee",
            permissions=[
                Permission(action="customer_success.feedback", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_other",
            id="role_other_customer_success_viewer",
            name="Other Customer Success Viewer",
            permissions=[
                Permission(action="customer_success.read", resource="tenant:tenant_other"),
            ],
        )
    )
    identity.assign_role("tenant_acme", admin.id, "role_customer_success_admin")
    identity.assign_role("tenant_acme", employee.id, "role_customer_success_employee")
    identity.assign_role("tenant_other", viewer.id, "role_other_customer_success_viewer")
    return identity, admin, employee, viewer


def test_customer_success_summary_endpoint_returns_tenant_scoped_dashboard_metrics():
    store, pack_registry = seed_success_inputs()
    identity, admin, _, other_viewer = create_customer_success_identity()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            solution_pack_registry=pack_registry,
        )
    )

    response = client.get(
        "/api/customer-success/summary",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant_acme"
    assert body["adoption"]["active_users"] == 2
    assert body["adoption"]["runs_created"] == 3
    assert body["adoption"]["artifact_downloads"] == 1
    assert body["solution_pack_outcomes"][0]["pack_id"] == "sales.renewal_ops"
    assert "Customer prompt" not in response.text
    assert "private-renewal-report" not in response.text

    other_response = client.get(
        "/api/customer-success/summary",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": other_viewer.id},
    )
    assert other_response.status_code == 200
    assert other_response.json()["tenant_id"] == "tenant_other"
    assert other_response.json()["adoption"]["runs_created"] == 1


def test_customer_success_summary_endpoint_requires_customer_success_read_permission():
    store, pack_registry = seed_success_inputs()
    identity, _, employee, _ = create_customer_success_identity()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            solution_pack_registry=pack_registry,
        )
    )

    response = client.get(
        "/api/customer-success/summary",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": employee.id},
    )

    assert response.status_code == 403


def test_customer_feedback_endpoint_uses_authenticated_user_and_returns_safe_payload():
    store, _ = seed_success_inputs()
    run = create_customer_run(store)
    identity, admin, employee, _ = create_customer_success_identity()
    feedback_service = InMemoryCustomerFeedbackService(audit_store=store)
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            customer_feedback_service=feedback_service,
        )
    )

    response = client.post(
        "/api/customer-success/feedback",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": employee.id},
        json={
            "submitted_by_user_id": "attacker_user",
            "feedback_type": "wrong_answer",
            "target_type": "run",
            "target_id": run.id,
            "run_id": run.id,
            "rating": -1,
            "comment": "Private renewal context should not be returned.",
            "metadata": {"raw_response": "private renewal context"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["submitted_by_user_id"] == employee.id
    assert body["comment_present"] is True
    assert body["metadata_present"] is True
    assert "comment" not in body
    assert "metadata" not in body
    assert "Private renewal context" not in response.text

    list_response = client.get(
        "/api/customer-success/feedback",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert list_response.status_code == 200
    assert list_response.json()[0]["submitted_by_user_id"] == employee.id
    assert "Private renewal context" not in list_response.text
    assert "private renewal context" not in str(store.list_audit_events("tenant_acme")[-1].metadata)


def test_customer_success_candidate_endpoints_require_manage_permission_and_create_reviews():
    store, _ = seed_success_inputs()
    run = create_customer_run(store)
    identity, admin, employee, _ = create_customer_success_identity()
    feedback_service = InMemoryCustomerFeedbackService(audit_store=store)
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            customer_feedback_service=feedback_service,
        )
    )
    employee_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": employee.id}
    admin_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    client.post(
        "/api/customer-success/feedback",
        headers=employee_headers,
        json={
            "submitted_by_user_id": employee.id,
            "feedback_type": "wrong_answer",
            "target_type": "run",
            "target_id": run.id,
            "run_id": run.id,
            "rating": -1,
            "comment": "Wrong answer with private context.",
        },
    )
    for user_id in ["user_1", "user_2", "user_3"]:
        client.post(
            "/api/customer-success/feedback",
            headers=employee_headers,
            json={
                "submitted_by_user_id": user_id,
                "feedback_type": "missing_skill",
                "target_type": "solution_pack",
                "target_id": "sales.renewal_ops",
                "solution_pack_id": "sales.renewal_ops",
                "missing_skill_name": "ERP invoice reconciliation",
                "comment": "Need this for private finance workflow.",
            },
        )

    forbidden = client.post(
        "/api/customer-success/evaluation-candidates",
        headers=employee_headers,
    )
    assert forbidden.status_code == 403

    evaluation = client.post(
        "/api/customer-success/evaluation-candidates",
        headers=admin_headers,
    )
    pack = client.post(
        "/api/customer-success/solution-pack-candidates",
        headers=admin_headers,
        json={"minimum_repeated_feedback": 3},
    )

    assert evaluation.status_code == 201
    assert evaluation.json()[0]["human_reviewed_by_user_id"] == admin.id
    assert evaluation.json()[0]["production_change_applied"] is False
    assert pack.status_code == 201
    assert pack.json()[0]["requested_skill_name"] == "ERP invoice reconciliation"
    assert pack.json()[0]["human_reviewed_by_user_id"] == admin.id
    assert "private finance" not in pack.text

    review_eval = client.post(
        f"/api/customer-success/evaluation-candidates/{evaluation.json()[0]['id']}/review",
        headers=admin_headers,
        json={
            "status": "accepted",
            "review_note": "Create eval case for renewal-answer correctness.",
        },
    )
    review_pack = client.post(
        f"/api/customer-success/solution-pack-candidates/{pack.json()[0]['id']}/review",
        headers=admin_headers,
        json={
            "status": "accepted",
            "review_note": "Draft a new skill for invoice reconciliation.",
        },
    )

    assert review_eval.status_code == 200
    assert review_eval.json()["status"] == "accepted"
    assert review_eval.json()["reviewed_by_user_id"] == admin.id
    assert review_eval.json()["evaluation_case_id"].startswith("eval_case_")
    assert review_eval.json()["production_change_applied"] is False
    assert "renewal-answer" not in str(store.list_audit_events("tenant_acme")[-2].metadata)
    assert review_pack.status_code == 200
    assert review_pack.json()["status"] == "accepted"
    assert review_pack.json()["reviewed_by_user_id"] == admin.id
    assert review_pack.json()["publication_draft_id"].startswith("pack_draft_")
    assert review_pack.json()["production_change_applied"] is False
    assert "private finance" not in review_pack.text

    listed_evaluation = client.get(
        "/api/customer-success/evaluation-candidates",
        headers=admin_headers,
    )
    listed_pack = client.get(
        "/api/customer-success/solution-pack-candidates",
        headers=admin_headers,
    )
    listed_evaluation_cases = client.get(
        "/api/customer-success/evaluation-cases",
        headers=admin_headers,
    )
    listed_pack_drafts = client.get(
        "/api/customer-success/solution-pack-drafts",
        headers=admin_headers,
    )
    forbidden_cases = client.get(
        "/api/customer-success/evaluation-cases",
        headers=employee_headers,
    )
    forbidden_drafts = client.get(
        "/api/customer-success/solution-pack-drafts",
        headers=employee_headers,
    )
    forbidden_list = client.get(
        "/api/customer-success/evaluation-candidates",
        headers=employee_headers,
    )

    assert listed_evaluation.status_code == 200
    assert [item["id"] for item in listed_evaluation.json()] == [
        evaluation.json()[0]["id"]
    ]
    assert listed_evaluation.json()[0]["status"] == "accepted"
    assert "Wrong answer with private context" not in listed_evaluation.text
    assert listed_pack.status_code == 200
    assert [item["id"] for item in listed_pack.json()] == [pack.json()[0]["id"]]
    assert listed_pack.json()[0]["status"] == "accepted"
    assert "private finance" not in listed_pack.text
    assert listed_evaluation_cases.status_code == 200
    assert [item["id"] for item in listed_evaluation_cases.json()] == [
        review_eval.json()["evaluation_case_id"]
    ]
    assert listed_evaluation_cases.json()[0]["source_candidate_id"] == evaluation.json()[0]["id"]
    assert listed_evaluation_cases.json()[0]["status"] == "draft"
    assert listed_evaluation_cases.json()[0]["created_by_user_id"] == admin.id
    assert "Wrong answer with private context" not in listed_evaluation_cases.text
    assert listed_pack_drafts.status_code == 200
    assert [item["id"] for item in listed_pack_drafts.json()] == [
        review_pack.json()["publication_draft_id"]
    ]
    assert listed_pack_drafts.json()[0]["source_candidate_id"] == pack.json()[0]["id"]
    assert listed_pack_drafts.json()[0]["status"] == "draft"
    assert listed_pack_drafts.json()[0]["created_by_user_id"] == admin.id
    assert "private finance" not in listed_pack_drafts.text
    assert forbidden_cases.status_code == 403
    assert forbidden_drafts.status_code == 403
    assert forbidden_list.status_code == 403

    draft_id = review_pack.json()["publication_draft_id"]
    forbidden_update = client.patch(
        f"/api/customer-success/solution-pack-drafts/{draft_id}",
        headers=employee_headers,
        json={
            "requested_skill_name": "ERP invoice matching",
            "proposed_change_summary": "Employee should not edit draft.",
        },
    )
    updated_draft = client.patch(
        f"/api/customer-success/solution-pack-drafts/{draft_id}",
        headers=admin_headers,
        json={
            "requested_skill_name": "ERP invoice matching",
            "proposed_change_summary": "Add governed invoice matching skill draft.",
        },
    )
    submitted_draft = client.post(
        f"/api/customer-success/solution-pack-drafts/{draft_id}/submit",
        headers=admin_headers,
    )
    approved_draft = client.post(
        f"/api/customer-success/solution-pack-drafts/{draft_id}/review",
        headers=admin_headers,
        json={
            "status": "approved",
            "review_note": "Approved without exposing private finance notes.",
        },
    )

    assert forbidden_update.status_code == 403
    assert updated_draft.status_code == 200
    assert updated_draft.json()["requested_skill_name"] == "ERP invoice matching"
    assert updated_draft.json()["status"] == "draft"
    assert submitted_draft.status_code == 200
    assert submitted_draft.json()["status"] == "in_review"
    assert approved_draft.status_code == 200
    assert approved_draft.json()["status"] == "approved"
    assert approved_draft.json()["production_change_applied"] is False
    assert "Approved without exposing" not in approved_draft.text
    reviewed_metadata = store.list_audit_events("tenant_acme")[-1].metadata
    assert reviewed_metadata["actor_user_id"] == admin.id
    assert reviewed_metadata["reviewed_by_user_id"] == admin.id
    assert reviewed_metadata["review_note_present"] is True
    assert "Approved without exposing" not in str(reviewed_metadata)
    assert "private finance" not in str(reviewed_metadata)


def test_customer_success_solution_pack_draft_apply_publishes_pack_version():
    store, _ = seed_success_inputs()
    identity, admin, employee, _ = create_customer_success_identity()
    pack_registry = InMemorySolutionPackRegistry()
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id=admin.id,
        manifest=SolutionPackManifest.model_validate(solution_pack_payload()),
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    feedback_service = InMemoryCustomerFeedbackService(
        audit_store=store,
        solution_pack_registry=pack_registry,
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            solution_pack_registry=pack_registry,
            customer_feedback_service=feedback_service,
        )
    )
    admin_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    employee_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": employee.id}
    for user_id in ["user_1", "user_2", "user_3"]:
        client.post(
            "/api/customer-success/feedback",
            headers=employee_headers,
            json={
                "submitted_by_user_id": user_id,
                "feedback_type": "missing_skill",
                "target_type": "solution_pack",
                "target_id": "sales.renewal_ops",
                "solution_pack_id": "sales.renewal_ops",
                "missing_skill_name": "ERP invoice reconciliation",
            },
        )
    candidate = client.post(
        "/api/customer-success/solution-pack-candidates",
        headers=admin_headers,
        json={"minimum_repeated_feedback": 3},
    ).json()[0]
    accepted = client.post(
        f"/api/customer-success/solution-pack-candidates/{candidate['id']}/review",
        headers=admin_headers,
        json={"status": "accepted"},
    ).json()
    draft_id = accepted["publication_draft_id"]
    update = client.patch(
        f"/api/customer-success/solution-pack-drafts/{draft_id}",
        headers=admin_headers,
        json={
            "requested_skill_name": "ERP Invoice Automation",
            "proposed_change_summary": "Add governed invoice matching and terms skills.",
            "proposed_pack_version": "1.0.1",
            "proposed_skill_manifests": [
                {
                    **skill_manifest_payload("sales.erp_invoice_matching"),
                    "name": "ERP Invoice Matching",
                    "description": "Match ERP invoices against renewal account data.",
                    "required_scopes": ["erp.invoice.read"],
                    "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
                },
                {
                    **skill_manifest_payload("sales.erp_payment_terms"),
                    "name": "ERP Payment Terms",
                    "description": "Extract payment terms for renewal invoice reviews.",
                    "required_scopes": ["erp.invoice.read"],
                    "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
                },
            ],
        },
    )
    client.post(
        f"/api/customer-success/solution-pack-drafts/{draft_id}/submit",
        headers=admin_headers,
    )
    client.post(
        f"/api/customer-success/solution-pack-drafts/{draft_id}/review",
        headers=admin_headers,
        json={"status": "approved"},
    )
    forbidden = client.post(
        f"/api/customer-success/solution-pack-drafts/{draft_id}/apply",
        headers=employee_headers,
    )
    applied = client.post(
        f"/api/customer-success/solution-pack-drafts/{draft_id}/apply",
        headers=admin_headers,
    )

    assert update.status_code == 200
    assert forbidden.status_code == 403
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert applied.json()["production_change_applied"] is True
    current_pack = pack_registry.get_for_tenant("tenant_acme", "sales.renewal_ops")
    assert current_pack.status.value == "published"
    assert current_pack.manifest.version == "1.0.1"
    assert [skill.id for skill in current_pack.manifest.skills[-2:]] == [
        "sales.erp_invoice_matching",
        "sales.erp_payment_terms",
    ]
    audit_metadata = store.list_audit_events("tenant_acme")[-1].metadata
    assert audit_metadata["skill_count"] == 2
    assert "input_schema" not in str(audit_metadata)

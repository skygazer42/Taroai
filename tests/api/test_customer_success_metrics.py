from taroai.customer_success import InMemoryCustomerSuccessService, SuccessHealthBand
from taroai.domain import RunCreate, RunStatus
from taroai.skills import InMemorySkillRegistry
from taroai.solution_packs import InMemorySolutionPackRegistry, SolutionPackManifest
from taroai.store import InMemoryControlPlaneStore
from tests.api.test_solution_packs import solution_pack_payload


def create_run(
    store: InMemoryControlPlaneStore,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str,
    status: RunStatus,
):
    run = store.create_run(
        tenant_id=tenant_id,
        user_id=user_id,
        payload=RunCreate(
            workspace_id=workspace_id,
            agent_id=agent_id,
            message="Customer prompt with private renewal details.",
            attachments=["s3://tenant/private.csv"],
            mode="autonomous",
        ),
    )
    store.update_run_status(tenant_id, run.id, status)
    return store.get_run(tenant_id, run.id)


def seed_success_inputs():
    store = InMemoryControlPlaneStore()
    first = create_run(
        store,
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        agent_id="agent_renewal",
        status=RunStatus.SUCCEEDED,
    )
    second = create_run(
        store,
        tenant_id="tenant_acme",
        workspace_id="workspace_success",
        user_id="user_2",
        agent_id="agent_renewal",
        status=RunStatus.SUCCEEDED,
    )
    failed = create_run(
        store,
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        agent_id="agent_support",
        status=RunStatus.FAILED,
    )
    other = create_run(
        store,
        tenant_id="tenant_other",
        workspace_id="workspace_other",
        user_id="user_other",
        agent_id="agent_other",
        status=RunStatus.SUCCEEDED,
    )
    store.create_artifact(
        tenant_id="tenant_acme",
        run_id=first.id,
        name="renewal-report.md",
        artifact_type="markdown",
        uri="s3://tenant_acme/private-renewal-report.md",
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=first.id,
        meter_type="skill_call_count",
        quantity=1,
        unit="call",
        skill_id="sales.crm_lookup",
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=second.id,
        meter_type="skill_call_count",
        quantity=2,
        unit="call",
        skill_id="sales.renewal_checklist",
    )
    store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=first.workspace_id,
        user_id="user_1",
        run_id=first.id,
        event_type="storage.downloaded",
        metadata={"object_id": "obj_123", "uri": "s3://tenant_acme/private-renewal-report.md"},
    )
    store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=first.workspace_id,
        user_id="manager_1",
        run_id=first.id,
        event_type="approval.resolved",
        metadata={"approval_id": "approval_1", "status": "approved"},
    )
    store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=failed.workspace_id,
        user_id="user_1",
        run_id=failed.id,
        event_type="customer.feedback.submitted",
        metadata={"rating": "thumbs_down", "raw_comment": "private customer context"},
    )
    store.record_audit_event(
        tenant_id="tenant_other",
        workspace_id=other.workspace_id,
        user_id="user_other",
        run_id=other.id,
        event_type="storage.downloaded",
        metadata={"object_id": "obj_other"},
    )

    pack_registry = InMemorySolutionPackRegistry()
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    pack_registry.record_installation(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        version="1.0.0",
        workspace_ids=["workspace_sales", "workspace_success"],
        installed_skill_ids=["sales.crm_lookup", "sales.renewal_checklist"],
        installed_by_user_id="user_admin",
    )
    return store, pack_registry


def test_customer_success_summary_aggregates_adoption_metrics_without_private_content():
    store, pack_registry = seed_success_inputs()
    service = InMemoryCustomerSuccessService(
        store=store,
        solution_pack_registry=pack_registry,
        skill_registry=InMemorySkillRegistry(),
    )

    summary = service.build_tenant_summary("tenant_acme")

    assert summary.tenant_id == "tenant_acme"
    assert summary.adoption.active_users == 2
    assert summary.adoption.active_workspaces == 2
    assert summary.adoption.runs_created == 3
    assert summary.adoption.runs_completed == 2
    assert summary.adoption.artifact_downloads == 1
    assert summary.adoption.skills_used == 2
    assert summary.adoption.approvals_resolved == 1
    assert summary.adoption.feedback_submitted == 1
    assert summary.adoption.repeated_workflows == 1
    assert summary.health.band in {SuccessHealthBand.HEALTHY, SuccessHealthBand.WATCH}
    assert 0 <= summary.health.adoption_score <= 100
    assert 0 <= summary.health.reliability_score <= 100
    assert 0 <= summary.health.value_score <= 100
    assert 0 <= summary.health.risk_score <= 100

    safe_payload = summary.model_dump_json()
    assert "Customer prompt" not in safe_payload
    assert "private-renewal-report" not in safe_payload
    assert "private customer context" not in safe_payload


def test_customer_success_metrics_are_tenant_scoped():
    store, pack_registry = seed_success_inputs()
    service = InMemoryCustomerSuccessService(
        store=store,
        solution_pack_registry=pack_registry,
    )

    acme = service.build_tenant_summary("tenant_acme")
    other = service.build_tenant_summary("tenant_other")

    assert acme.adoption.runs_created == 3
    assert acme.adoption.artifact_downloads == 1
    assert other.adoption.runs_created == 1
    assert other.adoption.artifact_downloads == 1
    assert other.solution_pack_outcomes == []


def test_customer_success_solution_pack_outcome_metrics_use_manifest_targets():
    store, pack_registry = seed_success_inputs()
    service = InMemoryCustomerSuccessService(
        store=store,
        solution_pack_registry=pack_registry,
    )

    summary = service.build_tenant_summary("tenant_acme")

    assert len(summary.solution_pack_outcomes) == 1
    outcome = summary.solution_pack_outcomes[0]
    assert outcome.pack_id == "sales.renewal_ops"
    assert outcome.version == "1.0.0"
    assert outcome.workspace_count == 2
    assert outcome.installed_skill_count == 2
    assert outcome.metric_values == {
        "active_workspaces": 2,
        "skills_installed": 2,
    }

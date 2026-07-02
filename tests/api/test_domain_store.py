import pytest

from taroai.agent import AgentRuntimeState, PlanStep, ToolResult
from taroai.domain import ApprovalStatus, RunCreate, RunStatus
from taroai.store import InMemoryControlPlaneStore, TenantAccessError


def test_create_run_records_initial_events_and_meter():
    store = InMemoryControlPlaneStore()

    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Research this prospect and prepare an outreach brief.",
            attachments=["file_123"],
            mode="autonomous",
        ),
    )

    assert run.tenant_id == "tenant_acme"
    assert run.workspace_id == "workspace_sales"
    assert run.user_id == "user_1"
    assert run.status == RunStatus.CREATED
    assert run.message == "Research this prospect and prepare an outreach brief."

    events = store.list_run_events("tenant_acme", run.id)
    assert [event.type for event in events] == [
        "run.created",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
    ]

    meters = store.list_billing_meters("tenant_acme")
    assert len(meters) == 1
    assert meters[0].run_id == run.id
    assert meters[0].meter_type == "run_count"
    assert meters[0].quantity == 1

    audit_events = store.list_audit_events("tenant_acme")
    assert [event.event_type for event in audit_events] == [
        "billing.metered",
        "run.created",
    ]
    assert audit_events[0].run_id == run.id
    assert audit_events[0].metadata["meter_type"] == "run_count"
    assert audit_events[0].metadata["meter_id"] == meters[0].id
    assert audit_events[1].run_id == run.id


def test_approval_resolution_records_audit_event():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Send an approval-gated customer update.",
            mode="autonomous",
        ),
    )
    approval = store.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    store.resolve_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    approval_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "approval.resolved"
    ]
    assert len(approval_audits) == 1
    assert approval_audits[0].workspace_id == "workspace_sales"
    assert approval_audits[0].user_id == "manager_1"
    assert approval_audits[0].run_id == run.id
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "approved",
    }


def test_approval_rejection_records_audit_event():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Send an approval-gated customer update.",
            mode="autonomous",
        ),
    )
    approval = store.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    rejected = store.reject_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        rejected_by_user_id="manager_1",
    )

    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.resolved_by_user_id == "manager_1"
    approval_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "approval.rejected"
    ]
    assert len(approval_audits) == 1
    assert approval_audits[0].workspace_id == "workspace_sales"
    assert approval_audits[0].user_id == "manager_1"
    assert approval_audits[0].run_id == run.id
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "rejected",
    }


def test_run_cancellation_records_audit_and_cancels_pending_approvals():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Send an approval-gated customer update.",
            mode="autonomous",
        ),
    )
    approval = store.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    cancelled_run = store.cancel_run(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
        reason_code="user_requested",
    )
    cancelled_approvals = store.cancel_pending_approval_requests(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
    )

    assert cancelled_run.status == RunStatus.CANCELLED
    assert cancelled_approvals[0].id == approval.id
    assert cancelled_approvals[0].status == ApprovalStatus.CANCELLED
    audits = store.list_audit_events("tenant_acme")
    run_audits = [event for event in audits if event.event_type == "run.cancelled"]
    approval_audits = [event for event in audits if event.event_type == "approval.cancelled"]
    assert run_audits[0].metadata == {
        "cancelled_by_user_id": "manager_1",
        "reason_code": "user_requested",
        "status": "cancelled",
    }
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "cancelled",
    }


def test_get_run_rejects_cross_tenant_access():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Research this prospect.",
            mode="autonomous",
        ),
    )

    with pytest.raises(TenantAccessError):
        store.get_run("tenant_other", run.id)


def test_artifact_metadata_is_tenant_scoped():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Create a summary report.",
            mode="workflow",
        ),
    )

    artifact = store.create_artifact(
        tenant_id="tenant_acme",
        run_id=run.id,
        name="prospect-brief.md",
        artifact_type="document",
        uri="s3://tenant_acme/runs/run_1/prospect-brief.md",
    )

    assert artifact.tenant_id == "tenant_acme"
    assert artifact.run_id == run.id
    assert artifact.name == "prospect-brief.md"
    assert store.list_artifacts("tenant_acme", run.id) == [artifact]

    with pytest.raises(TenantAccessError):
        store.list_artifacts("tenant_other", run.id)


def test_runtime_state_snapshot_is_tenant_scoped():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Research this prospect.",
            mode="autonomous",
        ),
    )
    state = AgentRuntimeState(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id=run.id,
        goal="Research this prospect.",
        status=RunStatus.RUNNING,
        plan=[
            PlanStep(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
        current_step_id="step_research",
        tool_results=[
            ToolResult(
                tool_name="research.lookup",
                output={"ok": True},
            )
        ],
    )

    snapshot = store.save_runtime_state(state)

    assert snapshot.run_id == run.id
    assert snapshot.plan[0]["id"] == "step_research"
    assert snapshot.tool_results[0]["output"] == {"ok": True}
    assert store.get_runtime_state("tenant_acme", run.id) == snapshot

    with pytest.raises(TenantAccessError):
        store.get_runtime_state("tenant_other", run.id)

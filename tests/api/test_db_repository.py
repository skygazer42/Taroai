from pathlib import Path

import pytest

from taroai.agent import AgentRuntimeState, PlanStep
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.domain import ApprovalStatus, RunCreate, RunStatus
from taroai.store import TenantAccessError


def test_migration_runner_applies_pending_schema_and_records_versions(tmp_path: Path):
    database_path = tmp_path / "taroai.sqlite3"
    runner = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=Path("apps/api/migrations"),
    )

    result = runner.apply()

    assert result.applied_versions == [
        "001_initial.sql",
        "002_short_term_memory_reviews.sql",
        "003_model_policy_scopes.sql",
    ]
    with runner.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    assert "runs" in tables
    assert "runtime_states" in tables
    assert "short_term_memory_reviews" in tables
    assert "model_policy_scopes" in tables
    assert versions == [
        "001_initial.sql",
        "002_short_term_memory_reviews.sql",
        "003_model_policy_scopes.sql",
    ]


def test_sql_repository_persists_run_events_and_runtime_state_across_instances(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))

    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Research this prospect.",
            attachments=["file_123"],
            mode="autonomous",
        ),
    )
    runtime_state = AgentRuntimeState(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id=run.id,
        goal=run.message,
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
        approved_guardrail_keys=["model_request:rule_1"],
        pending_guardrail_approval_key="model_request:rule_2",
        pending_guardrail_approval_stage="model_request",
    )
    repository.save_runtime_state(runtime_state)

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    events = restarted.list_run_events("tenant_acme", run.id)
    snapshot = restarted.get_runtime_state("tenant_acme", run.id)

    assert persisted_run.message == "Research this prospect."
    assert persisted_run.attachments == ["file_123"]
    assert [event.type for event in events] == [
        "run.created",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
    ]
    assert snapshot.status == RunStatus.RUNNING
    assert snapshot.plan[0]["tool_name"] == "research.lookup"
    assert snapshot.approved_guardrail_keys == ["model_request:rule_1"]
    assert snapshot.pending_guardrail_approval_key == "model_request:rule_2"
    assert snapshot.pending_guardrail_approval_stage == "model_request"

    with pytest.raises(TenantAccessError):
        restarted.get_run("tenant_other", run.id)


def test_sql_repository_persists_status_artifacts_approvals_and_reads(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )

    repository.update_run_status("tenant_acme", run.id, RunStatus.QUEUED)
    artifact = repository.create_artifact(
        tenant_id="tenant_acme",
        run_id=run.id,
        name="agent-result.md",
        artifact_type="markdown",
        uri="s3://taroai-artifacts/tenant_acme/workspace_sales/runs/run_123/artifacts/agent-result.md",
    )
    approval = repository.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    artifacts = restarted.list_artifacts("tenant_acme", run.id)
    approvals = restarted.list_approval_requests("tenant_acme", run.id)
    meters = restarted.list_billing_meters("tenant_acme")
    audits = restarted.list_audit_events("tenant_acme")

    assert persisted_run.status == RunStatus.QUEUED
    assert artifacts == [artifact]
    assert approvals == [approval]
    assert [meter.meter_type for meter in meters] == ["run_count"]
    assert [event.event_type for event in audits] == [
        "billing.metered",
        "run.created",
    ]

    resolved = restarted.resolve_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )
    assert resolved.status == ApprovalStatus.APPROVED
    assert restarted.list_approval_requests("tenant_acme", run.id)[0].status == ApprovalStatus.APPROVED
    approval_audits = [
        event
        for event in restarted.list_audit_events("tenant_acme")
        if event.event_type == "approval.resolved"
    ]
    assert len(approval_audits) == 1
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "approved",
    }


def test_sql_repository_persists_approval_rejection(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )
    approval = repository.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    rejected = repository.reject_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        rejected_by_user_id="manager_1",
    )
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted = restarted.list_approval_requests("tenant_acme", run.id)[0]

    assert rejected.status == ApprovalStatus.REJECTED
    assert persisted.status == ApprovalStatus.REJECTED
    assert persisted.resolved_by_user_id == "manager_1"
    approval_audits = [
        event
        for event in restarted.list_audit_events("tenant_acme")
        if event.event_type == "approval.rejected"
    ]
    assert len(approval_audits) == 1
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "rejected",
    }


def test_sql_repository_persists_run_cancellation_and_pending_approval_cancellation(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )
    approval = repository.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    cancelled_run = repository.cancel_run(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
        reason_code="user_requested",
    )
    cancelled_approvals = repository.cancel_pending_approval_requests(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
    )
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    persisted_approval = restarted.list_approval_requests("tenant_acme", run.id)[0]

    assert cancelled_run.status == RunStatus.CANCELLED
    assert persisted_run.status == RunStatus.CANCELLED
    assert cancelled_approvals[0].id == approval.id
    assert persisted_approval.status == ApprovalStatus.CANCELLED
    assert persisted_approval.resolved_by_user_id == "manager_1"
    audits = restarted.list_audit_events("tenant_acme")
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


def test_sql_repository_records_tool_call_audit_and_billing(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )

    meter = repository.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="tool_call_count",
        quantity=1,
        unit="call",
        metadata={"step_id": "step_research", "tool_name": "research.lookup"},
    )
    audit = repository.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        event_type="tool.executed",
        metadata={"step_id": "step_research", "tool_name": "research.lookup"},
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    meters = restarted.list_billing_meters("tenant_acme")
    audits = restarted.list_audit_events("tenant_acme")
    event_types = [event.type for event in restarted.list_run_events("tenant_acme", run.id)]

    assert meter in meters
    assert audit in audits
    assert event_types.count("billing.metered") == 2
    assert event_types.count("audit.recorded") == 4
    assert [
        event.event_type
        for event in audits
        if event.event_type == "billing.metered"
    ] == ["billing.metered", "billing.metered"]

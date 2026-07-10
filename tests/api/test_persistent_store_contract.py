from pathlib import Path
from typing import Callable

import pytest

from taroai.agent import AgentRuntimeState, PlanStep
from taroai.db import DatabaseConfig, SqlControlPlaneRepository
from taroai.domain import ApprovalStatus, RunCreate, RunMode, RunStatus
from taroai.store import InMemoryControlPlaneStore, NotFoundError, TenantAccessError


ControlPlaneStore = InMemoryControlPlaneStore | SqlControlPlaneRepository


def build_in_memory_store(
    _tmp_path: Path,
) -> tuple[ControlPlaneStore, Callable[[], ControlPlaneStore]]:
    store = InMemoryControlPlaneStore()
    return store, lambda: store


def build_sql_store(
    tmp_path: Path,
) -> tuple[ControlPlaneStore, Callable[[], ControlPlaneStore]]:
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    store = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    store.initialize_schema(Path("apps/api/migrations"))

    def reopen() -> ControlPlaneStore:
        return SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))

    return store, reopen


@pytest.mark.parametrize(
    "store_builder",
    [build_in_memory_store, build_sql_store],
    ids=["in_memory", "sql"],
)
def test_control_plane_store_lifecycle_contract_matches_persistent_behavior(
    tmp_path: Path,
    store_builder: Callable[
        [Path],
        tuple[ControlPlaneStore, Callable[[], ControlPlaneStore]],
    ],
):
    store, reopen_store = store_builder(tmp_path)
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_owner",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_research",
            message="Create a prospect brief.",
            attachments=["file_input"],
            mode=RunMode.AUTONOMOUS,
        ),
    )
    store.update_run_status("tenant_acme", run.id, RunStatus.RUNNING)
    artifact = store.create_artifact(
        tenant_id="tenant_acme",
        run_id=run.id,
        name="brief.md",
        artifact_type="markdown",
        uri=f"s3://tenant_acme/runs/{run.id}/artifacts/brief.md",
    )
    approval = store.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )
    resolved_approval = store.resolve_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="user_manager",
    )
    runtime_state = AgentRuntimeState(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_owner",
        run_id=run.id,
        goal=run.message,
        status=RunStatus.RUNNING,
        plan=[
            PlanStep(
                id="step_send",
                title="Send summary",
                tool_name="email.send",
                tool_input={"to": "customer@example.com"},
            )
        ],
        current_step_id="step_send",
        sandbox_session_id="sandbox_session_1",
        browser_session_id="browser_session_1",
    )
    store.save_runtime_state(runtime_state)
    tool_meter = store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="tool_call_count",
        quantity=1,
        unit="call",
        metadata={"tool_name": "email.send"},
    )
    tool_audit = store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        event_type="tool.executed",
        metadata={"tool_name": "email.send"},
    )

    reopened = reopen_store()
    persisted_run = reopened.get_run("tenant_acme", run.id)
    events = reopened.list_run_events("tenant_acme", run.id)
    persisted_artifacts = reopened.list_artifacts("tenant_acme", run.id)
    persisted_approvals = reopened.list_approval_requests("tenant_acme", run.id)
    persisted_state = reopened.get_runtime_state("tenant_acme", run.id)
    persisted_meters = reopened.list_billing_meters("tenant_acme")
    persisted_audits = reopened.list_audit_events("tenant_acme")

    assert persisted_run.status == RunStatus.RUNNING
    assert persisted_run.attachments == ["file_input"]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    created_payload = events[0].payload
    status_payload = next(
        event.payload for event in events if event.type == "run.status_changed"
    )
    assert type(created_payload["status"]) is str
    assert type(created_payload["mode"]) is str
    assert type(status_payload["status"]) is str
    assert created_payload == {
        "status": RunStatus.CREATED.value,
        "mode": RunMode.AUTONOMOUS.value,
        "agent_id": "agent_research",
    }
    assert status_payload == {"status": RunStatus.RUNNING.value}

    assert persisted_artifacts == [artifact]
    assert persisted_approvals == [resolved_approval]
    assert persisted_approvals[0].status == ApprovalStatus.APPROVED
    assert persisted_state.plan[0]["tool_name"] == "email.send"
    assert persisted_state.sandbox_session_id == "sandbox_session_1"
    assert persisted_state.browser_session_id == "browser_session_1"
    assert tool_meter in persisted_meters
    assert tool_audit in persisted_audits
    run_meter = next(
        meter for meter in persisted_meters if meter.meter_type == "run_count"
    )
    run_audit = next(
        event for event in persisted_audits if event.event_type == "run.created"
    )
    assert type(run_meter.metadata["mode"]) is str
    assert type(run_audit.metadata["mode"]) is str
    assert run_meter.metadata["mode"] == RunMode.AUTONOMOUS.value
    assert run_audit.metadata["mode"] == RunMode.AUTONOMOUS.value

    with pytest.raises((NotFoundError, TenantAccessError)):
        reopened.get_run("tenant_other", run.id)

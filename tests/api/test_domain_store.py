from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from taroai.agent import AgentRuntimeState, PlanStep, ToolResult
from taroai.domain import ApprovalStatus, IdempotencyRecord, RunCreate, RunStatus, utc_now
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
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert [event.sequence for event in store.list_run_events("tenant_acme", run.id, after_sequence=2)] == [3, 4]

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


def test_operation_level_billing_meter_does_not_require_run_event():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="workflow",
        ),
    )
    original_run_events = store.list_run_events("tenant_acme", run.id)

    meter = store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=None,
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        meter_type="embedding_call_count",
        quantity=1,
        unit="call",
        provider="openai_compatible",
        model="text-embedding-3-small",
        metadata={"purpose": "knowledge_index", "input_count": 2},
    )

    billing_audit = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.metadata.get("meter_id") == meter.id
    ][0]

    assert meter.run_id is None
    assert meter.workspace_id == run.workspace_id
    assert meter.user_id == run.user_id
    assert store.list_run_events("tenant_acme", run.id) == original_run_events
    assert billing_audit.run_id is None
    assert billing_audit.workspace_id == run.workspace_id
    assert billing_audit.user_id == run.user_id
    assert billing_audit.event_type == "billing.metered"


def test_store_persists_idempotency_records_by_tenant_method_path_and_key():
    store = InMemoryControlPlaneStore()
    record = IdempotencyRecord(
        tenant_id="tenant_acme",
        key="run-create-001",
        method="POST",
        path="/api/runs",
        request_hash="hash_1",
        status_code=201,
        response_body={"run_id": "run_1", "status": "created"},
        created_at=utc_now(),
    )

    saved = store.save_idempotency_record(record)

    assert saved == record
    assert store.get_idempotency_record(
        tenant_id="tenant_acme",
        key="run-create-001",
        method="POST",
        path="/api/runs",
    ) == record
    assert store.get_idempotency_record(
        tenant_id="tenant_other",
        key="run-create-001",
        method="POST",
        path="/api/runs",
    ) is None


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


def test_agent_loop_v2_domain_models_pin_thread_and_model_snapshot():
    from taroai.domain import (
        ChatMessage,
        ChatMessageCreate,
        ChatMessageDeliveryStatus,
        ChatMessageDispatchStatus,
        ChatMessageRole,
        ChatThread,
        ChatThreadCreate,
        ChatThreadStatus,
        ResourceReference,
    )

    resource_ref = ResourceReference(
        type="skill",
        id="skill_report_repair",
        version="1.2.0",
    )
    thread_payload = ChatThreadCreate(
        workspace_id="workspace_sales",
        title="Repair the sales report",
        provider_id="deepseek",
        model_id="deepseek-chat",
        reasoning_effort="medium",
    )
    now = utc_now()
    thread = ChatThread(
        id="thread_1",
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        status=ChatThreadStatus.ACTIVE,
        pinned=False,
        created_at=now,
        updated_at=now,
        **thread_payload.model_dump(),
    )
    message_payload = ChatMessageCreate(
        role=ChatMessageRole.USER,
        content="Fix the failing report.",
        dispatch_status=ChatMessageDispatchStatus.QUEUED,
        delivery_status=ChatMessageDeliveryStatus.PENDING,
        attachments=["file_123"],
        resource_refs=[resource_ref],
    )
    message = ChatMessage(
        id="message_1",
        tenant_id=thread.tenant_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        sequence=1,
        created_by_user_id="user_1",
        created_at=now,
        updated_at=now,
        **message_payload.model_dump(),
    )
    run_payload = RunCreate(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        trigger_message_id=message.id,
        provider_id=thread.provider_id,
        model_id=thread.model_id,
        reasoning_effort=thread.reasoning_effort,
        resource_refs=message.resource_refs,
        message=message.content,
    )

    store = InMemoryControlPlaneStore()
    store.chat_threads[thread.id] = thread
    store.chat_messages[message.id] = message
    store.workspace_tenants[thread.workspace_id] = thread.tenant_id
    store.user_tenants["user_1"] = thread.tenant_id
    run = store.create_run("tenant_acme", "user_1", run_payload)

    assert thread.status == ChatThreadStatus.ACTIVE
    assert message.role == ChatMessageRole.USER
    assert message.dispatch_status == ChatMessageDispatchStatus.QUEUED
    assert message.delivery_status == ChatMessageDeliveryStatus.PENDING
    assert run.thread_id == "thread_1"
    assert run.trigger_message_id == "message_1"
    assert run.provider_id == "deepseek"
    assert run.model_id == "deepseek-chat"
    assert run.reasoning_effort == "medium"
    assert run.resource_refs == [resource_ref]

    legacy_payload = RunCreate(
        workspace_id="workspace_sales",
        message="Legacy payload remains valid.",
    )
    assert legacy_payload.thread_id is None
    assert legacy_payload.model_id is None
    assert legacy_payload.resource_refs == []


def test_agent_loop_v2_persistence_models_and_runtime_state_are_serializable():
    from taroai.agent.models import (
        AgentAction,
        AgentCheckpoint,
        AgentCycle,
        AgentDecision,
        AgentObservation,
        AgentVerificationResult,
    )
    from taroai.store import RunStateSnapshot

    now = utc_now()
    observation = AgentObservation(
        action_id="action_1",
        success=False,
        output={"exit_code": 1},
        error="report generator failed",
        failure_class="tool_error",
        created_at=now,
    )
    decision = AgentDecision(
        kind="action",
        rationale_summary="Repair the failing report generator.",
        tool_name="sandbox.command",
        tool_input={"command": "python repair.py"},
    )
    verification = AgentVerificationResult(
        outcome="repair",
        feedback="The report is still missing its summary.",
    )
    cycle = AgentCycle(
        id="cycle_1",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        thread_id="thread_1",
        run_id="run_1",
        iteration=2,
        plan_revision=3,
        decision=decision,
        verifier_result=verification,
        budget_snapshot={"model_calls": 2},
        started_at=now,
    )
    action = AgentAction(
        id="action_1",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        thread_id="thread_1",
        run_id="run_1",
        cycle_id=cycle.id,
        action_key="run_1:cycle_1:action_1",
        decision=decision,
        status="failed",
        observation=observation,
        usage={"sandbox_seconds": 1.5},
        started_at=now,
        completed_at=now,
    )
    checkpoint = AgentCheckpoint(
        id="checkpoint_1",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        thread_id="thread_1",
        run_id="run_1",
        cycle_id=cycle.id,
        sequence=4,
        last_committed_action_id=action.id,
        state_payload={"iteration": 2, "repair_attempts": 1},
        checksum="sha256:checkpoint",
        created_at=now,
    )
    state = AgentRuntimeState(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id="run_1",
        goal="Fix the failing report.",
        status=RunStatus.RUNNING,
        iteration=2,
        max_iterations=8,
        observations=[observation],
        active_plan_revision=3,
        pending_actions=[decision],
        verifier_result=verification,
        repair_attempts=1,
        replan_count=2,
        steering_messages=["Keep the executive summary concise."],
        started_at=now,
        deadline_at=now,
        checkpoint_sequence=4,
    )

    snapshot = RunStateSnapshot.from_runtime_state(state)
    restored_payload = snapshot.to_runtime_state_payload()

    assert cycle.decision == decision
    assert action.observation == observation
    assert checkpoint.state_payload["repair_attempts"] == 1
    assert state.iteration == 2
    assert state.max_iterations == 8
    assert state.observations == [observation]
    assert state.active_plan_revision == 3
    assert state.pending_actions == [decision]
    assert state.verifier_result == verification
    assert state.repair_attempts == 1
    assert state.replan_count == 2
    assert state.steering_messages == ["Keep the executive summary concise."]
    assert state.checkpoint_sequence == 4
    assert snapshot.state_payload["iteration"] == 2
    assert snapshot.state_payload["observations"][0]["action_id"] == "action_1"
    assert restored_payload["pending_actions"][0]["tool_name"] == "sandbox.command"


def test_legacy_runtime_snapshot_without_state_payload_remains_readable():
    from taroai.store import RunStateSnapshot

    snapshot = RunStateSnapshot(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id="run_legacy",
        goal="Continue a legacy run.",
        status=RunStatus.RUNNING,
        updated_at=utc_now(),
    )

    payload = snapshot.to_runtime_state_payload()

    assert payload["run_id"] == "run_legacy"
    assert payload["goal"] == "Continue a legacy run."
    assert "state_payload" not in payload


def test_in_memory_chat_message_sequences_are_scoped_to_each_thread():
    from taroai.domain import ChatMessageCreate, ChatThreadCreate

    store = InMemoryControlPlaneStore()
    first_thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales", title="First"),
    )
    second_thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales", title="Second"),
    )

    first_message = store.append_chat_message(
        "tenant_acme",
        first_thread.id,
        "user_1",
        ChatMessageCreate(content="First message"),
    )
    second_message = store.append_chat_message(
        "tenant_acme",
        first_thread.id,
        "user_1",
        ChatMessageCreate(content="Second message"),
    )
    other_thread_message = store.append_chat_message(
        "tenant_acme",
        second_thread.id,
        "user_1",
        ChatMessageCreate(content="Other thread message"),
    )

    assert [first_message.sequence, second_message.sequence] == [1, 2]
    assert other_thread_message.sequence == 1
    with pytest.raises(TenantAccessError):
        store.list_chat_messages("tenant_other", first_thread.id)


def test_in_memory_observation_commit_restores_action_when_checkpoint_append_fails():
    from taroai.agent.models import AgentAction, AgentCycle, AgentDecision, AgentObservation

    class FailingCheckpointList(list):
        def append(self, _checkpoint):
            raise RuntimeError("checkpoint append failed")

    store = InMemoryControlPlaneStore()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="Run atomically."),
    )
    cycle = store.create_agent_cycle(
        AgentCycle(
            id="cycle_append_failure",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            iteration=1,
        )
    )
    action = store.create_agent_action(
        AgentAction(
            id="action_append_failure",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            cycle_id=cycle.id,
            action_key="append-failure",
            decision=AgentDecision(kind="action", tool_name="sandbox.command"),
        )
    )
    store.agent_checkpoints[run.id] = FailingCheckpointList()
    claimed = store.claim_agent_action(
        "tenant_acme",
        action.id,
        lease_owner_id="worker_append_failure",
        lease_seconds=30,
    )
    assert claimed is not None

    with pytest.raises(RuntimeError, match="checkpoint append failed"):
        store.commit_agent_action_observation(
            "tenant_acme",
            action.id,
            AgentObservation(action_id=action.id, success=True),
            lease_owner_id="worker_append_failure",
            lease_generation=claimed.lease_generation,
            usage={"tool_calls": 1},
            state_payload={"iteration": 1},
            checksum="sha256:append-failure",
        )

    persisted = store.get_agent_action("tenant_acme", action.id)
    assert persisted.status == "running"
    assert persisted.observation is None
    assert persisted.lease_owner_id == "worker_append_failure"
    assert persisted.lease_generation == claimed.lease_generation
    assert persisted.lease_expires_at == claimed.lease_expires_at
    assert store.get_latest_agent_checkpoint("tenant_acme", run.id) is None


def test_in_memory_concurrent_message_reads_return_consistent_snapshots():
    from taroai.domain import ChatMessageCreate, ChatThreadCreate

    store = InMemoryControlPlaneStore()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    barrier = Barrier(3)

    def write_messages(offset: int) -> None:
        barrier.wait()
        for index in range(250):
            store.append_chat_message(
                "tenant_acme",
                thread.id,
                "user_1",
                ChatMessageCreate(content=f"Message {offset + index}"),
            )

    def read_snapshots() -> list[list[int]]:
        barrier.wait()
        snapshots: list[list[int]] = []
        for _ in range(500):
            snapshots.append(
                [
                    message.sequence
                    for message in store.list_chat_messages("tenant_acme", thread.id)
                ]
            )
        return snapshots

    with ThreadPoolExecutor(max_workers=3) as executor:
        writer_a = executor.submit(write_messages, 0)
        writer_b = executor.submit(write_messages, 250)
        reader = executor.submit(read_snapshots)
        writer_a.result()
        writer_b.result()
        snapshots = reader.result()

    for sequence_snapshot in snapshots:
        assert sequence_snapshot == list(range(1, len(sequence_snapshot) + 1))
    assert len(store.list_chat_messages("tenant_acme", thread.id)) == 500

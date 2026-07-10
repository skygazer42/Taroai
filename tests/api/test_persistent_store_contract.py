from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

import pytest

from taroai.agent import AgentRuntimeState, PlanStep
from taroai.agent.models import (
    AgentAction,
    AgentCheckpoint,
    AgentCycle,
    AgentDecision,
    AgentObservation,
    AgentVerificationResult,
)
from taroai.db import DatabaseConfig, SqlControlPlaneRepository
from taroai.domain import (
    ApprovalStatus,
    ChatMessageCreate,
    ChatMessageDeliveryStatus,
    ChatMessageDispatchStatus,
    ChatThreadCreate,
    ChatThreadStatus,
    ResourceReference,
    RunCreate,
    RunMode,
    RunStatus,
    utc_now,
)
from taroai.store import InMemoryControlPlaneStore, NotFoundError, TenantAccessError


ControlPlaneStore = InMemoryControlPlaneStore | SqlControlPlaneRepository


def test_store_modules_import_cleanly_in_a_fresh_interpreter():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path("apps/api/src").resolve())

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import taroai.store; import taroai.db.repository",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


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


@pytest.mark.parametrize(
    "store_builder",
    [build_in_memory_store, build_sql_store],
    ids=["in_memory", "sql"],
)
def test_chat_thread_and_message_repository_contract_matches_persistent_behavior(
    tmp_path: Path,
    store_builder: Callable[
        [Path],
        tuple[ControlPlaneStore, Callable[[], ControlPlaneStore]],
    ],
):
    store, reopen_store = store_builder(tmp_path)
    thread = store.create_chat_thread(
        tenant_id="tenant_acme",
        user_id="user_owner",
        payload=ChatThreadCreate(
            workspace_id="workspace_sales",
            title="Initial title",
            provider_id="openai",
            model_id="gpt-5",
        ),
    )

    updated_thread = store.update_chat_thread(
        "tenant_acme",
        thread.id,
        title="Prospect research",
        pinned=True,
        status=ChatThreadStatus.ARCHIVED,
        model_id="gpt-5-mini",
    )
    ready = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_owner",
        ChatMessageCreate(
            content="Start the research.",
            dispatch_status=ChatMessageDispatchStatus.READY,
            resource_refs=[ResourceReference(type="skill", id="skill_research", version="1.0.0")],
        ),
    )
    queued = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_owner",
        ChatMessageCreate(
            content="Then draft the brief.",
            dispatch_status=ChatMessageDispatchStatus.QUEUED,
        ),
    )
    steering = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_owner",
        ChatMessageCreate(
            content="Keep it under one page.",
            dispatch_status=ChatMessageDispatchStatus.STEERING,
        ),
    )

    assert [ready.sequence, queued.sequence, steering.sequence] == [1, 2, 3]
    assert store.get_chat_thread("tenant_acme", thread.id) == updated_thread
    assert store.list_chat_threads("tenant_acme", "workspace_sales") == [updated_thread]
    assert store.get_chat_message("tenant_acme", queued.id) == queued
    assert store.list_chat_messages("tenant_acme", thread.id) == [ready, queued, steering]

    first_claim = store.claim_next_queued_message("tenant_acme", thread.id)
    second_claim = store.claim_next_queued_message("tenant_acme", thread.id)
    assert first_claim is not None and first_claim.id == ready.id
    assert second_claim is not None and second_claim.id == queued.id
    assert first_claim.dispatch_status == ChatMessageDispatchStatus.INFLIGHT
    assert second_claim.dispatch_status == ChatMessageDispatchStatus.INFLIGHT
    assert store.claim_next_queued_message("tenant_acme", thread.id) is None

    assert store.list_pending_steering_messages("tenant_acme", thread.id) == [steering]
    applied = store.mark_steering_applied("tenant_acme", steering.id)
    assert applied.dispatch_status == ChatMessageDispatchStatus.COMPLETED
    assert store.list_pending_steering_messages("tenant_acme", thread.id) == []

    delivered = store.update_chat_message(
        "tenant_acme",
        queued.id,
        delivery_status=ChatMessageDeliveryStatus.DELIVERED,
    )
    assert delivered.delivery_status == ChatMessageDeliveryStatus.DELIVERED

    with pytest.raises((NotFoundError, TenantAccessError)):
        store.get_chat_thread("tenant_other", thread.id)
    with pytest.raises((NotFoundError, TenantAccessError)):
        store.get_chat_message("tenant_other", queued.id)

    reopened = reopen_store()
    assert reopened.get_chat_thread("tenant_acme", thread.id).title == "Prospect research"
    persisted_messages = reopened.list_chat_messages("tenant_acme", thread.id)
    assert [message.sequence for message in persisted_messages] == [1, 2, 3]
    assert persisted_messages[1].delivery_status == ChatMessageDeliveryStatus.DELIVERED


@pytest.mark.parametrize(
    "store_builder",
    [build_in_memory_store, build_sql_store],
    ids=["in_memory", "sql"],
)
def test_agent_cycle_action_and_checkpoint_repository_contract_is_atomic_and_durable(
    tmp_path: Path,
    store_builder: Callable[
        [Path],
        tuple[ControlPlaneStore, Callable[[], ControlPlaneStore]],
    ],
):
    store, reopen_store = store_builder(tmp_path)
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_owner",
        ChatThreadCreate(workspace_id="workspace_sales", title="Repair report"),
    )
    trigger = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_owner",
        ChatMessageCreate(content="Repair the report."),
    )
    run = store.create_run(
        "tenant_acme",
        "user_owner",
        RunCreate(
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            trigger_message_id=trigger.id,
            provider_id="openai",
            model_id="gpt-5",
            resource_refs=[ResourceReference(type="skill", id="skill_repair", version="2.0.0")],
            message=trigger.content,
        ),
    )
    decision = AgentDecision(
        kind="action",
        tool_name="sandbox.command",
        tool_input={"command": "python repair.py"},
    )
    cycle = store.create_agent_cycle(
        AgentCycle(
            id="cycle_1",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            thread_id=thread.id,
            run_id=run.id,
            iteration=1,
            decision=decision,
        )
    )
    action = store.create_agent_action(
        AgentAction(
            id="action_1",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            thread_id=thread.id,
            run_id=run.id,
            cycle_id=cycle.id,
            action_key="repair-report",
            decision=decision,
        )
    )

    with pytest.raises(ValueError, match="Duplicate action_key"):
        store.create_agent_action(
            action.model_copy(update={"id": "action_duplicate"})
        )

    observation = AgentObservation(
        action_id=action.id,
        success=True,
        output={"exit_code": 0},
    )
    committed_action, first_checkpoint = store.commit_agent_action_observation(
        "tenant_acme",
        action.id,
        observation,
        usage={"sandbox_seconds": 0.25},
        state_payload={"iteration": 1, "phase": "verify"},
        checksum="sha256:first",
    )
    assert committed_action.status == "succeeded"
    assert committed_action.observation == observation
    assert committed_action.usage == {"sandbox_seconds": 0.25}
    assert first_checkpoint.sequence == 1
    assert first_checkpoint.last_committed_action_id == action.id

    second_checkpoint = store.create_agent_checkpoint(
        AgentCheckpoint(
            id="checkpoint_2",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            thread_id=thread.id,
            run_id=run.id,
            cycle_id=cycle.id,
            sequence=2,
            last_committed_action_id=action.id,
            state_payload={"iteration": 2},
            checksum="sha256:second",
        )
    )
    second_checkpoint.state_payload["iteration"] = 999
    latest = store.get_latest_agent_checkpoint("tenant_acme", run.id)
    assert latest is not None
    assert latest.sequence == 2
    assert latest.state_payload == {"iteration": 2}

    with pytest.raises(ValueError, match="checkpoint sequence"):
        store.create_agent_checkpoint(
            second_checkpoint.model_copy(
                update={"id": "checkpoint_gap", "sequence": 4}
            )
        )

    completed_cycle = store.complete_agent_cycle(
        "tenant_acme",
        cycle.id,
        status="completed",
        verifier_result=AgentVerificationResult(outcome="complete"),
    )
    assert completed_cycle.status == "completed"
    assert completed_cycle.completed_at is not None

    reopened = reopen_store()
    persisted_run = reopened.get_run("tenant_acme", run.id)
    persisted_events = reopened.list_run_events("tenant_acme", run.id)
    persisted_action = reopened.get_agent_action("tenant_acme", action.id)
    persisted_checkpoint = reopened.get_latest_agent_checkpoint("tenant_acme", run.id)
    assert persisted_run.thread_id == thread.id
    assert persisted_run.trigger_message_id == trigger.id
    assert persisted_run.model_id == "gpt-5"
    assert persisted_run.resource_refs[0].id == "skill_repair"
    assert {event.thread_id for event in persisted_events} == {thread.id}
    assert [event.thread_sequence for event in persisted_events] == list(
        range(1, len(persisted_events) + 1)
    )
    assert persisted_action.status == "succeeded"
    assert persisted_action.observation == observation
    assert persisted_checkpoint is not None and persisted_checkpoint.sequence == 2

    with pytest.raises((NotFoundError, TenantAccessError)):
        reopened.get_agent_action("tenant_other", action.id)


@pytest.mark.parametrize(
    "store_builder",
    [build_in_memory_store, build_sql_store],
    ids=["in_memory", "sql"],
)
def test_action_observation_commit_rolls_back_when_checkpoint_cannot_be_persisted(
    tmp_path: Path,
    store_builder: Callable[
        [Path],
        tuple[ControlPlaneStore, Callable[[], ControlPlaneStore]],
    ],
):
    store, _ = store_builder(tmp_path)
    run = store.create_run(
        "tenant_acme",
        "user_owner",
        RunCreate(workspace_id="workspace_sales", message="Run an atomic action."),
    )
    cycle = store.create_agent_cycle(
        AgentCycle(
            id="cycle_atomic",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            iteration=1,
        )
    )
    action = store.create_agent_action(
        AgentAction(
            id="action_atomic",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            cycle_id=cycle.id,
            action_key="atomic-action",
            decision=AgentDecision(kind="action", tool_name="sandbox.command"),
        )
    )

    with pytest.raises(TypeError):
        store.commit_agent_action_observation(
            "tenant_acme",
            action.id,
            AgentObservation(action_id=action.id, success=False, error="failed"),
            usage={"tool_calls": 1},
            state_payload={"not_json_serializable": object()},
            checksum="sha256:rollback",
        )

    persisted_action = store.get_agent_action("tenant_acme", action.id)
    assert persisted_action.status == "pending"
    assert persisted_action.observation is None
    assert store.get_latest_agent_checkpoint("tenant_acme", run.id) is None


def test_sql_queue_claim_compare_and_set_prevents_duplicate_claims(tmp_path: Path):
    store, reopen_store = build_sql_store(tmp_path)
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_owner",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    message = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_owner",
        ChatMessageCreate(
            content="Claim me once.",
            dispatch_status=ChatMessageDispatchStatus.QUEUED,
        ),
    )
    repositories = [reopen_store(), reopen_store()]

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda repository: repository.claim_next_queued_message(
                    "tenant_acme", thread.id
                ),
                repositories,
            )
        )

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].id == message.id
    assert claimed[0].dispatch_status == ChatMessageDispatchStatus.INFLIGHT


def test_sql_restart_marks_interrupted_running_action_uncertain(tmp_path: Path):
    store, reopen_store = build_sql_store(tmp_path)
    run = store.create_run(
        "tenant_acme",
        "user_owner",
        RunCreate(workspace_id="workspace_sales", message="Run a long action."),
    )
    cycle = store.create_agent_cycle(
        AgentCycle(
            id="cycle_restart",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            iteration=1,
        )
    )
    action = store.create_agent_action(
        AgentAction(
            id="action_restart",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            cycle_id=cycle.id,
            action_key="long-action",
            decision=AgentDecision(kind="action", tool_name="sandbox.command"),
            status="running",
            started_at=utc_now(),
        )
    )
    assert store.get_agent_action("tenant_acme", action.id).status == "running"

    recovered = reopen_store().get_agent_action("tenant_acme", action.id)

    assert recovered.status == "uncertain"
    assert reopen_store().get_agent_action("tenant_acme", action.id).status == "uncertain"


def test_sql_action_observation_rolls_back_when_checkpoint_insert_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store, _ = build_sql_store(tmp_path)
    run = store.create_run(
        "tenant_acme",
        "user_owner",
        RunCreate(workspace_id="workspace_sales", message="Commit atomically."),
    )
    cycle = store.create_agent_cycle(
        AgentCycle(
            id="cycle_insert_failure",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            iteration=1,
        )
    )
    action = store.create_agent_action(
        AgentAction(
            id="action_insert_failure",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            cycle_id=cycle.id,
            action_key="insert-failure",
            decision=AgentDecision(kind="action", tool_name="sandbox.command"),
        )
    )

    def fail_checkpoint_insert(*_args, **_kwargs):
        raise RuntimeError("checkpoint insert failed")

    with monkeypatch.context() as patch:
        patch.setattr(
            SqlControlPlaneRepository,
            "_insert_agent_checkpoint",
            fail_checkpoint_insert,
        )
        with pytest.raises(RuntimeError, match="checkpoint insert failed"):
            store.commit_agent_action_observation(
                "tenant_acme",
                action.id,
                AgentObservation(action_id=action.id, success=True),
                usage={"tool_calls": 1},
                state_payload={"iteration": 1},
                checksum="sha256:insert-failure",
            )

    persisted = store.get_agent_action("tenant_acme", action.id)
    assert persisted.status == "pending"
    assert persisted.observation is None
    assert store.get_latest_agent_checkpoint("tenant_acme", run.id) is None


def test_sql_concurrent_message_appends_allocate_strict_thread_sequences(tmp_path: Path):
    store, reopen_store = build_sql_store(tmp_path)
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_owner",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    repositories = [reopen_store() for _ in range(6)]

    with ThreadPoolExecutor(max_workers=6) as executor:
        messages = list(
            executor.map(
                lambda indexed: indexed[1].append_chat_message(
                    "tenant_acme",
                    thread.id,
                    "user_owner",
                    ChatMessageCreate(content=f"Concurrent {indexed[0]}"),
                ),
                enumerate(repositories),
            )
        )

    assert sorted(message.sequence for message in messages) == [1, 2, 3, 4, 5, 6]
    assert [
        message.sequence
        for message in reopen_store().list_chat_messages("tenant_acme", thread.id)
    ] == [1, 2, 3, 4, 5, 6]


def test_sql_concurrent_observation_commits_allocate_strict_checkpoint_sequences(
    tmp_path: Path,
):
    store, reopen_store = build_sql_store(tmp_path)
    run = store.create_run(
        "tenant_acme",
        "user_owner",
        RunCreate(workspace_id="workspace_sales", message="Commit two actions."),
    )
    actions: list[AgentAction] = []
    for iteration in (1, 2):
        cycle = store.create_agent_cycle(
            AgentCycle(
                id=f"cycle_concurrent_{iteration}",
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                iteration=iteration,
            )
        )
        actions.append(
            store.create_agent_action(
                AgentAction(
                    id=f"action_concurrent_{iteration}",
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    cycle_id=cycle.id,
                    action_key=f"concurrent-{iteration}",
                    decision=AgentDecision(kind="action", tool_name="sandbox.command"),
                )
            )
        )
    repositories = [reopen_store(), reopen_store()]

    def commit(index: int) -> AgentCheckpoint:
        action = actions[index]
        _, checkpoint = repositories[index].commit_agent_action_observation(
            "tenant_acme",
            action.id,
            AgentObservation(action_id=action.id, success=True),
            usage={"tool_calls": 1},
            state_payload={"iteration": index + 1},
            checksum=f"sha256:concurrent-{index + 1}",
        )
        return checkpoint

    with ThreadPoolExecutor(max_workers=2) as executor:
        checkpoints = list(executor.map(commit, (0, 1)))

    assert sorted(checkpoint.sequence for checkpoint in checkpoints) == [1, 2]
    latest = reopen_store().get_latest_agent_checkpoint("tenant_acme", run.id)
    assert latest is not None and latest.sequence == 2

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from taroai.agent import AgentRuntime
from taroai.app import create_app
from taroai.config import Settings
from taroai.domain import (
    ApprovalStatus,
    ChatMessageCreate,
    ChatMessageRole,
    ChatThreadCreate,
    RunCreate,
    RunMode,
    RunStatus,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.workflow import (
    WorkflowCoordinator,
    WorkflowPhaseSpec,
    WorkflowSpec,
    WorkflowTaskSpec,
    workflow_goal,
)


def _workflow_spec() -> WorkflowSpec:
    return WorkflowSpec(
        name="Research and compare",
        phases=[
            WorkflowPhaseSpec(
                id="research",
                title="Research",
                tasks=[
                    WorkflowTaskSpec(id="source_a", title="Source A"),
                    WorkflowTaskSpec(id="source_b", title="Source B"),
                ],
            ),
            WorkflowPhaseSpec(
                id="compare",
                title="Compare",
                tasks=[
                    WorkflowTaskSpec(
                        id="compare",
                        title="Compare findings",
                        dependsOn=["source_a", "source_b"],
                    )
                ],
            ),
        ],
        maxConcurrency=2,
        finalSynthesisPrompt="Return one concise comparison.",
    )


def test_workflow_agent_plans_from_published_instructions():
    store = InMemoryControlPlaneStore()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    trigger = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_1",
        ChatMessageCreate(content="Run this workflow"),
    )
    agent_run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            trigger_message_id=trigger.id,
            agent_id="agent_report",
            message="Published Agent instructions\n\nStructured input:\n{}",
            mode=RunMode.WORKFLOW,
        ),
    )
    chat_run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            trigger_message_id=trigger.id,
            message="Hidden execution context",
            mode=RunMode.WORKFLOW,
        ),
    )

    assert workflow_goal(store, agent_run) == agent_run.message
    assert workflow_goal(store, chat_run) == "Run this workflow"


def test_pending_workflow_preview_can_be_edited_before_a_new_approval():
    store = InMemoryControlPlaneStore()
    parent = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Research two sources",
            mode=RunMode.WORKFLOW,
        ),
    )
    runtime = AgentRuntime(store=store)
    state = runtime._initial_state(parent)
    state.status = RunStatus.AWAITING_APPROVAL
    workflow = store.create_workflow(parent, _workflow_spec())
    approval = store.create_approval_request(
        parent.tenant_id,
        parent.id,
        f"workflow:{workflow.id}",
        "Approve workflow: 3 steps",
        kind="workflow",
        subject_type="workflow",
        subject_id=workflow.id,
        preview_payload=workflow.spec.model_dump(mode="json", by_alias=True),
        validation_payload={"valid": True},
    )
    store.update_workflow(parent.tenant_id, workflow.id, approval_id=approval.id)
    store.update_run_status(parent.tenant_id, parent.id, RunStatus.AWAITING_APPROVAL)
    state.approval_id = approval.id
    state.runtime_metadata["workflow_id"] = workflow.id
    runtime._save_state(state)
    revised = _workflow_spec().model_dump(mode="json", by_alias=True)
    revised["phases"][0]["tasks"][0]["title"] = "Review official source A"
    revised["maxConcurrency"] = 1
    client = TestClient(create_app(store=store, settings=Settings(_env_file=None)))

    response = client.patch(
        f"/api/workflows/{workflow.id}/preview",
        headers={"X-Tenant-ID": parent.tenant_id, "X-User-ID": parent.user_id},
        json={"spec": revised},
    )

    assert response.status_code == 200, response.text
    updated = store.get_workflow(parent.tenant_id, workflow.id)
    assert updated.spec.task("source_a").title == "Review official source A"
    assert updated.spec.max_concurrency == 1
    approvals = store.list_approval_requests(parent.tenant_id, parent.id)
    assert approvals[0].status == ApprovalStatus.CANCELLED
    assert approvals[-1].status == ApprovalStatus.PENDING
    assert response.json()["approval_id"] == approvals[-1].id
    assert client.app.state.runtime._load_state(
        parent.tenant_id, parent.id
    ).approval_id == approvals[-1].id
    assert {task.task_id for task in store.list_workflow_tasks(parent.tenant_id, workflow.id)} == {
        "source_a",
        "source_b",
        "compare",
    }


def test_workflow_runs_independent_tasks_then_injects_dependency_summaries():
    store = InMemoryControlPlaneStore()
    parent = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Research two sources and compare them",
            mode=RunMode.WORKFLOW,
        ),
    )
    runtime = AgentRuntime(store=store)
    runtime._save_state(runtime._initial_state(parent))
    workflow = store.create_workflow(parent, _workflow_spec())
    store.update_workflow("tenant_acme", workflow.id, status="running")
    coordinator = WorkflowCoordinator(store=store, runtime=runtime)

    roots = coordinator.ready_runs("tenant_acme", workflow.id)

    assert len(roots) == 2
    assert all(
        "Execute only the task below. Do not solve the whole workflow." in run.message
        for run in roots
    )
    assert all("Completed dependency task summaries:\n{}" in run.message for run in roots)
    assert {
        task.status for task in store.list_workflow_tasks("tenant_acme", workflow.id)
    } == {"queued", "pending"}

    summaries = ["A supports the claim.", "B disputes one detail."]
    for child, summary in zip(roots, summaries):
        coordinator.mark_running(child)
        child = store.update_run_status("tenant_acme", child.id, RunStatus.SUCCEEDED)
        next_runs = coordinator.complete_child(
            child, SimpleNamespace(final_response_text=summary)
        )

    assert len(next_runs) == 1
    join = next_runs[0]
    assert "Completed dependency task summaries:" in join.message
    assert summaries[0] in join.message
    assert summaries[1] in join.message

    coordinator.mark_running(join)
    join = store.update_run_status("tenant_acme", join.id, RunStatus.SUCCEEDED)
    coordinator.complete_child(
        join, SimpleNamespace(final_response_text="A and B differ on one detail.")
    )

    assert store.get_workflow("tenant_acme", workflow.id).status == "succeeded"
    assert store.get_run("tenant_acme", parent.id).status == RunStatus.SUCCEEDED
    event_types = [
        event.type for event in store.list_run_events("tenant_acme", parent.id)
    ]
    assert "workflow.phase.updated" in event_types
    assert "workflow.completed" in event_types
    assert "workflow.synthesis_fallback" in event_types


def test_workflow_spec_rejects_dependency_cycles():
    with pytest.raises(ValidationError, match="cycle"):
        WorkflowSpec(
            name="Invalid",
            phases=[
                WorkflowPhaseSpec(
                    id="phase",
                    title="Phase",
                    tasks=[
                        WorkflowTaskSpec(id="a", title="A", dependsOn=["b"]),
                        WorkflowTaskSpec(id="b", title="B", dependsOn=["a"]),
                    ],
                )
            ],
            finalSynthesisPrompt="Return a result.",
        )


def test_workflow_task_messages_expose_worker_transcript_without_internal_prompt():
    store = InMemoryControlPlaneStore()
    parent = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Research two sources",
            mode=RunMode.WORKFLOW,
        ),
    )
    runtime = AgentRuntime(store=store)
    runtime._save_state(runtime._initial_state(parent))
    workflow = store.create_workflow(parent, _workflow_spec())
    store.update_workflow("tenant_acme", workflow.id, status="running")
    WorkflowCoordinator(store=store, runtime=runtime).ready_runs(
        "tenant_acme", workflow.id
    )
    task = next(
        item
        for item in store.list_workflow_tasks("tenant_acme", workflow.id)
        if item.task_id == "source_a"
    )
    assert task.child_thread_id is not None
    store.append_chat_message(
        "tenant_acme",
        task.child_thread_id,
        None,
        ChatMessageCreate(
            role=ChatMessageRole.ASSISTANT,
            content="Source A summary",
            execution_content="internal worker prompt",
        ),
    )
    client = TestClient(
        create_app(store=store, settings=Settings(_env_file=None))
    )

    response = client.get(
        f"/api/workflows/{workflow.id}/tasks/source_a/messages",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["workerThreadId"] == task.child_thread_id
    assert response.json()["messages"][-1]["content"] == "Source A summary"
    assert "execution_content" not in response.json()["messages"][-1]
    assert "internal worker prompt" not in response.text


def test_paused_workflow_synthesizes_only_after_resume():
    store = InMemoryControlPlaneStore()
    parent = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Research two sources and compare them",
            mode=RunMode.WORKFLOW,
        ),
    )
    runtime = AgentRuntime(store=store)
    runtime._save_state(runtime._initial_state(parent))
    workflow = store.create_workflow(parent, _workflow_spec())
    store.update_workflow("tenant_acme", workflow.id, status="running")
    coordinator = WorkflowCoordinator(store=store, runtime=runtime)
    roots = coordinator.ready_runs("tenant_acme", workflow.id)
    join_runs = []
    for child in roots:
        coordinator.mark_running(child)
        child = store.update_run_status("tenant_acme", child.id, RunStatus.SUCCEEDED)
        join_runs.extend(
            coordinator.complete_child(
                child, SimpleNamespace(final_response_text=f"Summary for {child.id}")
            )
        )
    join = join_runs[0]
    coordinator.mark_running(join)
    coordinator.pause("tenant_acme", workflow.id)
    join = store.update_run_status("tenant_acme", join.id, RunStatus.SUCCEEDED)
    coordinator.complete_child(
        join, SimpleNamespace(final_response_text="Comparison complete")
    )

    assert store.get_workflow("tenant_acme", workflow.id).status == "paused"
    assert store.get_run("tenant_acme", parent.id).status == RunStatus.WAITING_FOR_USER

    resumed, ready = coordinator.resume("tenant_acme", workflow.id)

    assert ready == []
    assert resumed.status == "succeeded"
    assert store.get_run("tenant_acme", parent.id).status == RunStatus.SUCCEEDED


def test_cancelling_workflow_supersedes_its_pending_approval():
    store = InMemoryControlPlaneStore()
    parent = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Research two sources",
            mode=RunMode.WORKFLOW,
        ),
    )
    runtime = AgentRuntime(store=store)
    runtime._save_state(runtime._initial_state(parent))
    workflow = store.create_workflow(parent, _workflow_spec())
    approval = store.create_approval_request(
        "tenant_acme",
        parent.id,
        f"workflow:{workflow.id}",
        "Approve workflow",
        kind="workflow",
    )
    store.update_workflow("tenant_acme", workflow.id, approval_id=approval.id)

    cancelled = WorkflowCoordinator(store=store, runtime=runtime).cancel(
        "tenant_acme", workflow.id, "user_1"
    )
    resolved = store.list_approval_requests("tenant_acme", parent.id)[0]

    assert cancelled.status == "cancelled"
    assert resolved.status == ApprovalStatus.CANCELLED
    assert resolved.execution_status == "superseded"

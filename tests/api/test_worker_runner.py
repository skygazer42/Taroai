from pathlib import Path

import pytest

from taroai.agent import AgentRuntime
from taroai.audit import AuditService
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.domain import RunCreate, RunStatus
from taroai.guardrails import InMemoryGuardrailService
from taroai.lifecycle import SqlLifecyclePolicyStore
from taroai.model_gateway import (
    ModelGatewayRequest,
    ModelMessage,
    ModelPolicy,
    ModelPolicyDeniedError,
    ModelPolicyScopeUpsert,
    PlannedToolCall,
    SqlModelPolicyStore,
)
from taroai.storage import SqlStorageCatalog
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import AgentWorker, InMemoryJobQueue, JobStatus, JobType, RunExecutionJob
from taroai.workers.runner import (
    AgentWorkerRunner,
    build_agent_worker_runner,
    build_cleanup_worker_runner,
)
from tests.api.adapters import DeterministicModelGateway, DeterministicToolGateway


def test_agent_worker_runner_processes_one_queued_run():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a worker processed brief.",
            mode="autonomous",
        ),
    )
    queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=run.id,
            requested_by_user_id="user_1",
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect"},
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    runner = AgentWorkerRunner(
        worker=AgentWorker(runtime=runtime, queue=queue),
        stop_after_empty_polls=1,
    )

    result = runner.run_once()

    job = queue.jobs[0]
    assert result.processed_jobs == 1
    assert result.last_job_id == job.id
    assert job.status == JobStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def test_agent_worker_records_job_audit_with_worker_actor():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="run_owner_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a worker audited brief.",
            mode="autonomous",
        ),
    )
    queued_job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="run_owner_1",
            run_id=run.id,
            requested_by_user_id="operator_1",
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        tool_gateway=DeterministicToolGateway(),
    )
    worker = AgentWorker(
        runtime=runtime,
        queue=queue,
        worker_id="agent_worker_1",
        audit_service=AuditService(store=store),
    )

    worker.process_next()

    worker_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type.startswith("worker.job.")
    ]
    assert [event.event_type for event in worker_events] == [
        "worker.job.started",
        "worker.job.succeeded",
    ]
    assert [event.metadata["job_id"] for event in worker_events] == [queued_job.id, queued_job.id]
    assert [event.metadata["worker_id"] for event in worker_events] == [
        "agent_worker_1",
        "agent_worker_1",
    ]
    assert [event.metadata["requested_by_user_id"] for event in worker_events] == [
        "operator_1",
        "operator_1",
    ]
    assert [event.metadata["actor"]["actor_type"] for event in worker_events] == [
        "worker",
        "worker",
    ]
    assert [event.metadata["actor"]["user_id"] for event in worker_events] == [
        "operator_1",
        "operator_1",
    ]


def test_agent_worker_records_failed_job_audit_with_worker_actor():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="run_owner_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a worker audited brief.",
            mode="autonomous",
        ),
    )
    queued_job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="run_owner_1",
            run_id=run.id,
            requested_by_user_id="operator_1",
        ),
        max_attempts=1,
    )
    worker = AgentWorker(
        runtime=AgentRuntime(
            store=store,
            model_gateway=DeterministicModelGateway(),
            model_policy=ModelPolicy(
                default_model="gpt-denied",
                denied_models=["gpt-denied"],
            ),
        ),
        queue=queue,
        worker_id="agent_worker_1",
        audit_service=AuditService(store=store),
    )

    rejected = worker.process_next()

    assert rejected is not None
    assert rejected.status == JobStatus.DEAD_LETTER
    worker_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type.startswith("worker.job.")
    ]
    assert [event.event_type for event in worker_events] == [
        "worker.job.started",
        "worker.job.failed",
    ]
    failed_event = worker_events[-1]
    assert failed_event.metadata["job_id"] == queued_job.id
    assert failed_event.metadata["worker_id"] == "agent_worker_1"
    assert failed_event.metadata["run_id"] == run.id
    assert failed_event.metadata["requested_by_user_id"] == "operator_1"
    assert failed_event.metadata["final_job_status"] == "dead_letter"
    assert failed_event.metadata["actor"]["actor_type"] == "worker"
    assert failed_event.metadata["actor"]["user_id"] == "operator_1"


def test_agent_worker_runner_stops_after_idle_poll_limit():
    runner = AgentWorkerRunner(
        worker=AgentWorker(
            runtime=AgentRuntime(store=InMemoryControlPlaneStore()),
            queue=InMemoryJobQueue(),
        ),
        stop_after_empty_polls=2,
    )

    result = runner.run_until_idle()

    assert result.processed_jobs == 0
    assert result.idle_polls == 2


def test_build_agent_worker_runner_uses_sql_control_plane_store_from_settings(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'worker.sqlite3'}",
        control_plane_store_backend="sql",
        _env_file=None,
    )

    runner = build_agent_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.runtime.store, SqlControlPlaneRepository)


def test_build_agent_worker_runner_loads_sql_model_policy_store(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'worker-policy.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    policy_store = SqlModelPolicyStore(config=DatabaseConfig(url=database_url))
    policy_store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            default_model="consumer-free",
            allowed_models=["enterprise-approved"],
            denied_models=["consumer-free"],
            updated_by_user_id="admin_1",
        )
    )
    settings = Settings(
        database_url=database_url,
        model_gateway_policy_store_backend="sql",
        _env_file=None,
    )

    runner = build_agent_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    with pytest.raises(ModelPolicyDeniedError):
        runner.worker.runtime.model_policy.assert_request_allowed(
            ModelGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                messages=[ModelMessage(role="user", content="Plan this run.")],
            )
        )


def test_build_agent_worker_runner_registers_runtime_tool_handlers():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    tool_names = set(runner.worker.runtime.tool_gateway.policies)

    assert "sandbox.command" in tool_names
    assert "browser.action" in tool_names


def test_build_agent_worker_runner_injects_worker_audit_service():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    assert runner.worker.audit_service is not None


def test_build_agent_worker_runner_injects_worker_guardrail_service():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    assert isinstance(
        runner.worker.runtime.tool_gateway.guardrail_service,
        InMemoryGuardrailService,
    )
    assert runner.worker.runtime.guardrail_service is runner.worker.runtime.tool_gateway.guardrail_service


def test_build_cleanup_worker_runner_uses_sql_storage_catalog_from_settings(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'cleanup-worker.sqlite3'}",
        control_plane_store_backend="sql",
        lifecycle_policy_backend="sql",
        storage_catalog_backend="sql",
        _env_file=None,
    )

    runner = build_cleanup_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.storage_lifecycle_service.storage_catalog, SqlStorageCatalog)
    assert isinstance(runner.worker.storage_lifecycle_service.lifecycle_policy_store, SqlLifecyclePolicyStore)
    assert runner.worker.storage_lifecycle_service.audit_service is not None
    assert runner.worker.audit_service is not None

from pathlib import Path

import pytest

import taroai.workers.runner as runner_module
from taroai.agent import AgentRuntime
from taroai.audit import AuditService
from taroai.billing import BillingPricingRuleUpsert, SqlBillingPricingRuleStore
from taroai.config import Settings
from taroai.connectors import (
    ConnectorAclMapping,
    ConnectorAclMappingRule,
    ConnectorSyncDocument,
    ConnectorSyncJob,
    SqlConnectorRegistry,
    SourceAclPrincipal,
)
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.domain import ChatThreadCreate, RunCreate, RunMode, RunStatus
from taroai.guardrails import InMemoryGuardrailService
from taroai.knowledge import (
    InMemoryKnowledgeService,
    KnowledgeBaseCreate,
    SqlKnowledgeService,
)
from taroai.lifecycle import (
    InMemoryRestoreDrillScheduleStore,
    RestoreDrillRunRecord,
    RestoreDrillRunStatus,
    RestoreDrillScheduleCreate,
    SqlLifecyclePolicyStore,
    SqlRestoreDrillScheduleStore,
)
from taroai.deployment import RestoreDrillVerificationConfig
from taroai.deployment_evidence import RestoreDrillVerificationResult
from taroai.model_gateway import (
    ModelGatewayRouter,
    ModelGatewayRequest,
    ModelMessage,
    ModelPolicy,
    ModelPolicyDeniedError,
    ModelProviderConfig,
    ModelPolicyScopeUpsert,
    PlannedToolCall,
    ModelProviderUpsert,
    SqlModelProviderStore,
    SqlModelPolicyStore,
)
from taroai.identity import InMemoryIdentityService
from taroai.policy import IdentityPolicyService
from taroai.sandbox import BrowserController, HttpBrowserController
from taroai.storage import SqlStorageCatalog
from taroai.storage import InMemoryStorageCatalog, StorageDownloadResult
from taroai.store import InMemoryControlPlaneStore
from taroai.triggers import (
    InMemoryTriggerStore,
    SqlTriggerStore,
    TriggerDefinitionCreate,
    TriggerScheduleConfig,
    TriggerService,
    TriggerType,
)
from taroai.workers import (
    AgentWorker,
    ConnectorSyncWorker,
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RestoreDrillDueJob,
    RestoreDrillDueWorker,
    RestoreDrillDueWorkerRunner,
    RestoreDrillEvidenceCollectionJob,
    RestoreDrillEvidenceWorker,
    RestoreDrillEvidenceWorkerRunner,
    RestoreDrillExecutionJob,
    RestoreDrillExecutionWorker,
    RestoreDrillExecutionWorkerRunner,
    RunExecutionJob,
    TriggerDueJob,
    TriggerDueWorker,
    TriggerSchedulerWorker,
)
from taroai.workers.runner import (
    AgentWorkerRunner,
    TriggerDueWorkerRunner,
    TriggerSchedulerWorkerRunner,
    build_restore_drill_due_worker_runner,
    build_restore_drill_evidence_worker_runner,
    build_restore_drill_execution_worker_runner,
    build_agent_worker_runner,
    build_cleanup_worker_runner,
    build_connector_sync_worker_runner,
    build_trigger_scheduler_worker_runner,
    build_trigger_due_worker_runner,
    build_restore_drill_scheduler_worker_runner,
    WorkerProcessConfig,
)
from taroai.workflow import (
    WorkflowCoordinator,
    WorkflowPhaseSpec,
    WorkflowSpec,
    WorkflowTaskSpec,
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


def test_agent_worker_refreshes_model_runtime_before_execution():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Use the newly approved model.",
            mode="autonomous",
            model_id="fresh-model",
        ),
    )
    queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            requested_by_user_id=run.user_id,
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        model_policy=ModelPolicy(
            default_model="old-model",
            allowed_models=["old-model"],
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    refreshes = []

    def refresh_model_runtime():
        refreshes.append(True)
        runtime.model_policy = ModelPolicy(
            default_model="fresh-model",
            allowed_models=["fresh-model"],
        )

    worker = AgentWorker(
        runtime=runtime,
        queue=queue,
        refresh_model_runtime=refresh_model_runtime,
    )

    worker.process_next()

    assert refreshes == [True]
    assert store.get_run(run.tenant_id, run.id).status == RunStatus.SUCCEEDED


def test_agent_worker_dispatches_next_workflow_task_without_continuing_hidden_thread():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    parent = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Complete two steps.",
            mode=RunMode.WORKFLOW,
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="worker_step",
                    title="Complete worker task",
                    tool_name="planning.record",
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    runtime._save_state(runtime._initial_state(parent))
    workflow = store.create_workflow(
        parent,
        WorkflowSpec(
            name="Two steps",
            phases=[
                WorkflowPhaseSpec(
                    id="phase",
                    title="Phase",
                    tasks=[
                        WorkflowTaskSpec(id="first", title="First"),
                        WorkflowTaskSpec(
                            id="second",
                            title="Second",
                            dependsOn=["first"],
                        ),
                    ],
                )
            ],
            finalSynthesisPrompt="Return the result.",
        ),
    )
    store.update_workflow("tenant_acme", workflow.id, status="running")
    coordinator = WorkflowCoordinator(store=store, runtime=runtime)
    first = coordinator.ready_runs("tenant_acme", workflow.id)[0]
    queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id=first.tenant_id,
            workspace_id=first.workspace_id,
            user_id=first.user_id,
            run_id=first.id,
            requested_by_user_id=first.user_id,
        ),
    )

    worker = AgentWorker(
        runtime=runtime,
        queue=queue,
        workflow_coordinator=coordinator,
        chat_service=type(
            "UnexpectedChatContinuation",
            (),
            {
                "continue_thread": lambda *_: pytest.fail(
                    "workflow worker threads are not chat continuations"
                )
            },
        )(),
    )

    completed_job = worker.process_next()

    assert completed_job is not None
    assert completed_job.status == JobStatus.SUCCEEDED
    assert len(queue.jobs) == 2
    assert queue.jobs[1].status == JobStatus.PENDING
    second = next(
        task
        for task in store.list_workflow_tasks("tenant_acme", workflow.id)
        if task.task_id == "second"
    )
    assert second.child_run_id == queue.jobs[1].payload["run_id"]


def test_trigger_due_worker_runner_processes_one_queued_trigger():
    store = InMemoryControlPlaneStore()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    trigger = trigger_service.create_trigger(
        TriggerDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            agent_id="agent_sla",
            created_by_user_id=None,
            service_account_id="svc_scheduler",
            type=TriggerType.SCHEDULE,
            name="Daily SLA sweep",
            input_template={"message": "Check open SLA risk."},
            schedule=TriggerScheduleConfig(
                cron_expression="0 9 * * *",
                timezone="UTC",
            ),
        )
    )
    queue = InMemoryJobQueue()
    queue.enqueue(
        JobType.TRIGGER_DUE,
        TriggerDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            trigger_id=trigger.id,
            trigger_type="schedule",
            scheduled_for=trigger.created_at,
            requested_by_user_id="svc_scheduler",
        ),
    )
    runner = TriggerDueWorkerRunner(
        worker=TriggerDueWorker(
            store=store,
            trigger_service=trigger_service,
            queue=queue,
        ),
        stop_after_empty_polls=1,
    )

    result = runner.run_once()

    assert result.processed_jobs == 1
    assert queue.jobs[0].status == JobStatus.SUCCEEDED
    assert store.list_runs("tenant_acme")[0].status == RunStatus.QUEUED


def test_trigger_scheduler_worker_runner_enqueues_due_schedule_trigger():
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    trigger = trigger_service.create_trigger(
        TriggerDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            agent_id="agent_sla",
            created_by_user_id=None,
            service_account_id="svc_scheduler",
            type=TriggerType.SCHEDULE,
            name="Daily SLA sweep",
            input_template={"message": "Check open SLA risk."},
            schedule=TriggerScheduleConfig(
                cron_expression="0 9 * * *",
                timezone="UTC",
            ),
            next_run_at=trigger_scheduler_now(),
        )
    )
    queue = InMemoryJobQueue()
    runner = TriggerSchedulerWorkerRunner(
        worker=TriggerSchedulerWorker(
            trigger_service=trigger_service,
            queue=queue,
        ),
        stop_after_empty_polls=1,
    )

    result = runner.run_once(now=trigger_scheduler_now())

    assert result.processed_jobs == 1
    assert queue.jobs[0].type == JobType.TRIGGER_DUE
    assert queue.jobs[0].payload["trigger_id"] == trigger.id
    assert (
        trigger_service.get_trigger("tenant_acme", trigger.id).next_run_at is not None
    )


def test_restore_drill_due_worker_runner_processes_one_queued_drill():
    from datetime import datetime, timezone

    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            service_account_id="svc_restore_drill",
            interval_days=30,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    queue = InMemoryJobQueue()
    queued_job = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        RestoreDrillDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref="docs/operations/disaster-recovery.md",
        ),
    )
    runner = RestoreDrillDueWorkerRunner(
        worker=RestoreDrillDueWorker(
            schedule_store=schedule_store,
            queue=queue,
        ),
        stop_after_empty_polls=1,
    )

    result = runner.run_once()

    assert result.processed_jobs == 1
    assert result.last_job_id == queued_job.id
    assert queue.jobs[0].status == JobStatus.SUCCEEDED
    records = schedule_store.list_run_records("tenant_acme", schedule.id)
    assert len(records) == 1
    assert records[0].runbook_ref == "docs/operations/disaster-recovery.md"


class RecordingRestoreDrillObjectStorage:
    def __init__(self):
        self.contents: dict[str, bytes] = {}

    def upload(self, storage_object, content: bytes):
        self.contents[storage_object.id] = content
        return {
            "storage_object_id": storage_object.id,
            "uri": storage_object.uri,
            "etag": "etag_restore_drill",
        }

    def download(self, storage_object) -> StorageDownloadResult:
        return StorageDownloadResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            content=self.contents[storage_object.id],
            content_type=storage_object.content_type,
        )


def restore_drill_verification_result() -> RestoreDrillVerificationResult:
    return RestoreDrillVerificationResult(
        drill_id="restore_drill_runner",
        backup_manifest_generated=True,
        restore_order_executed=True,
        database_restore_verified=True,
        object_storage_restore_verified=True,
        redis_restore_or_rebuild_verified=True,
        config_restore_verified=True,
        post_restore_validation_passed=True,
        rpo_minutes=4,
        rto_minutes=18,
    )


def restore_drill_verification_config() -> RestoreDrillVerificationConfig:
    return RestoreDrillVerificationConfig(
        drill_id="restore_drill_runner",
        backup_manifest_path=Path("/restore/evidence/backup-manifest.json"),
        executed_restore_order=["postgres", "object_storage", "redis"],
        migration_plan_path=Path("/restore/evidence/migration-plan.json"),
        object_storage_verification_path=Path(
            "/restore/evidence/object-storage-verification.json"
        ),
        redis_queue_verification_path=Path("/restore/evidence/redis-verification.json"),
        config_restored=True,
        post_restore_checks_passed=True,
        rpo_minutes=4,
        rto_minutes=18,
    )


def test_restore_drill_execution_worker_runner_processes_one_queued_execution():
    from datetime import datetime, timezone

    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            service_account_id="svc_restore_drill",
            interval_days=30,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    run_record = schedule_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref=schedule.runbook_ref,
        )
    )
    queue = InMemoryJobQueue()
    queued_job = queue.enqueue(
        JobType.RESTORE_DRILL_EXECUTION,
        RestoreDrillExecutionJob(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            run_record_id=run_record.id,
            requested_by_user_id="svc_restore_drill",
            verification_config=restore_drill_verification_config(),
        ),
        now=datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc),
    )
    runner = RestoreDrillExecutionWorkerRunner(
        worker=RestoreDrillExecutionWorker(
            schedule_store=schedule_store,
            queue=queue,
            verifier=lambda config: restore_drill_verification_result(),
        ),
        stop_after_empty_polls=1,
    )

    result = runner.run_once()

    assert result.processed_jobs == 1
    assert result.last_job_id == queued_job.id
    assert queue.jobs[0].status == JobStatus.SUCCEEDED
    assert queue.jobs[1].type == JobType.RESTORE_DRILL_EVIDENCE
    evidence_payload = RestoreDrillEvidenceCollectionJob.model_validate(
        queue.jobs[1].payload
    )
    assert evidence_payload.run_record_id == run_record.id


def test_restore_drill_evidence_worker_runner_processes_one_queued_evidence():
    from datetime import datetime, timezone

    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            service_account_id="svc_restore_drill",
            interval_days=30,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    run_record = schedule_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref=schedule.runbook_ref,
        )
    )
    queue = InMemoryJobQueue()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingRestoreDrillObjectStorage()
    queued_job = queue.enqueue(
        JobType.RESTORE_DRILL_EVIDENCE,
        RestoreDrillEvidenceCollectionJob(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            run_record_id=run_record.id,
            requested_by_user_id="svc_restore_drill",
            verification=restore_drill_verification_result(),
        ),
        now=datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc),
    )
    runner = RestoreDrillEvidenceWorkerRunner(
        worker=RestoreDrillEvidenceWorker(
            schedule_store=schedule_store,
            queue=queue,
            storage_catalog=storage_catalog,
            object_storage=object_storage,
        ),
        stop_after_empty_polls=1,
    )

    result = runner.run_once()

    assert result.processed_jobs == 1
    assert result.last_job_id == queued_job.id
    assert queue.jobs[0].status == JobStatus.SUCCEEDED
    updated = schedule_store.get_run_record(schedule.tenant_id, run_record.id)
    assert updated.status == RestoreDrillRunStatus.EVIDENCE_READY
    assert updated.evidence_object_id is not None


def test_connector_sync_worker_runner_processes_one_queued_sync_job():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    knowledge_service = InMemoryKnowledgeService()
    knowledge_base = knowledge_service.create_base(
        tenant_id="tenant_acme",
        user_id="svc_connector_sync",
        request=KnowledgeBaseCreate(
            workspace_id="workspace_sales",
            name="Sales Knowledge",
        ),
    )
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="svc_connector_sync",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="connector_sync",
            message="Sync connector connector_crm into knowledge base.",
            mode=RunMode.AUTONOMOUS,
        ),
    )
    queued_job = queue.enqueue(
        JobType.CONNECTOR_SYNC,
        ConnectorSyncJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            connector_id="connector_crm",
            run_id=run.id,
            knowledge_base_id=knowledge_base.id,
            requested_by_user_id="svc_connector_sync",
            cursor="cursor_001",
            acl_mapping=ConnectorAclMapping(
                rules=[
                    ConnectorAclMappingRule(
                        source_principal_id="group:sales",
                        acl_subject="team:sales",
                    )
                ]
            ),
            documents=[
                ConnectorSyncDocument(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    connector_id="connector_crm",
                    source_uri="crm://accounts/acme",
                    source_document_id="crm_account_123",
                    title="Acme Account",
                    document_version="v3",
                    content_hash="sha256:connector-sync-runner-acme",
                    sensitivity_level=2,
                    source_acl=[
                        SourceAclPrincipal(
                            source_principal_id="group:sales",
                            principal_type="group",
                        )
                    ],
                    chunks=[
                        {
                            "content": "Renewal is in legal review.",
                            "citation": {"source": "crm"},
                        }
                    ],
                )
            ],
        ),
    )
    runner = runner_module.ConnectorSyncWorkerRunner(
        worker=ConnectorSyncWorker(
            queue=queue,
            knowledge_service=knowledge_service,
            store=store,
            audit_service=AuditService(store=store),
        ),
        stop_after_empty_polls=1,
    )

    result = runner.run_once()

    assert result.processed_jobs == 1
    assert result.last_job_id == queued_job.id
    assert queue.jobs[0].status == JobStatus.SUCCEEDED
    documents = knowledge_service.list_documents("tenant_acme", knowledge_base.id)
    assert len(documents) == 1
    assert documents[0].acl_subjects == ["team:sales"]
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def trigger_scheduler_now():
    from datetime import datetime, timezone

    return datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc)


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
    assert [event.metadata["job_id"] for event in worker_events] == [
        queued_job.id,
        queued_job.id,
    ]
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


def test_agent_worker_acks_thread_run_without_a_queued_continuation():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales", title="Scheduled run"),
    )
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Complete the scheduled task.",
            mode=RunMode.AUTONOMOUS,
            thread_id=thread.id,
        ),
    )
    queued_job = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=run.id,
            requested_by_user_id="user_1",
        ),
    )
    worker = AgentWorker(
        runtime=AgentRuntime(
            store=store,
            model_gateway=DeterministicModelGateway(),
            tool_gateway=DeterministicToolGateway(),
        ),
        queue=queue,
        chat_service=type(
            "IdleChatService",
            (),
            {"continue_thread": lambda *_: None},
        )(),
    )

    completed = worker.process_next()

    assert completed is not None
    assert completed.id == queued_job.id
    assert completed.status == JobStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def test_agent_worker_acks_a_handled_run_failure():
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
    queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="run_owner_1",
            run_id=run.id,
            requested_by_user_id="run_owner_1",
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
    )

    completed = worker.process_next()

    assert completed is not None
    assert completed.status == JobStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED


def test_agent_worker_records_dead_letter_audit_and_terminates_run():
    class ExplodingRuntime(AgentRuntime):
        def execute_run(self, tenant_id: str, run_id: str):
            self.store.update_run_status(tenant_id, run_id, RunStatus.RUNNING)
            raise RuntimeError("model client crashed")

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
        runtime=ExplodingRuntime(store=store),
        queue=queue,
        worker_id="agent_worker_1",
        audit_service=AuditService(store=store),
    )

    rejected = worker.process_next()

    assert rejected is not None
    assert rejected.status == JobStatus.DEAD_LETTER
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED
    assert store.get_runtime_state("tenant_acme", run.id).failure_reason == (
        "worker_retries_exhausted"
    )
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


def test_agent_worker_marks_run_retrying_after_temporary_failure():
    class ExplodingRuntime(AgentRuntime):
        def execute_run(self, tenant_id: str, run_id: str):
            self.store.update_run_status(tenant_id, run_id, RunStatus.RUNNING)
            raise RuntimeError("temporary model failure")

    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Retry this run.",
            mode="workflow",
        ),
    )
    queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            requested_by_user_id=run.user_id,
        ),
        max_attempts=2,
    )

    rejected = AgentWorker(runtime=ExplodingRuntime(store=store), queue=queue).process_next()

    assert rejected is not None
    assert rejected.status == JobStatus.PENDING
    assert store.get_run(run.tenant_id, run.id).status == RunStatus.RETRYING
    assert store.list_run_events(run.tenant_id, run.id)[-1].payload == {
        "status": "retrying"
    }


def test_agent_worker_terminates_run_after_final_lease_expires():
    from datetime import datetime, timezone

    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Recover an abandoned worker run.",
            mode="autonomous",
        ),
    )
    old = datetime(2026, 7, 1, tzinfo=timezone.utc)
    queued = queue.enqueue(
        JobType.RUN_EXECUTION,
        RunExecutionJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=run.id,
            requested_by_user_id="user_1",
        ),
        now=old,
        max_attempts=1,
    )
    queue.claim(JobType.RUN_EXECUTION, "dead_worker", now=old, lease_seconds=1)
    store.update_run_status("tenant_acme", run.id, RunStatus.RUNNING)

    AgentWorker(runtime=AgentRuntime(store=store), queue=queue).process_next()

    assert queue.get(queued.id).status == JobStatus.DEAD_LETTER
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED


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


def test_build_agent_worker_runner_uses_sql_control_plane_store_from_settings(
    tmp_path: Path,
):
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


def test_build_agent_worker_runner_wires_model_budget_window_from_settings():
    settings = Settings(
        model_gateway_workspace_call_limit=3,
        model_gateway_budget_window_seconds=3600,
        _env_file=None,
    )

    runner = build_agent_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    policy = runner.worker.runtime.model_budget_guard.policy
    assert policy.max_model_calls_per_workspace == 3
    assert policy.budget_window_seconds == 3600


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
            model_sensitivity_limits={"enterprise-approved": 3},
            updated_by_user_id="admin_1",
        )
    )
    settings = Settings(
        database_url=database_url,
        model_gateway_sensitivity_limits={"global-default": 1},
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
    assert runner.worker.runtime.model_policy.model_sensitivity_limits == {
        "global-default": 1
    }
    assert runner.worker.runtime.model_policy.scoped_policies[
        0
    ].model_sensitivity_limits == {"enterprise-approved": 3}


def test_build_agent_worker_runner_refreshes_sql_model_configuration(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'worker-model-refresh.sqlite3'}"
    config = DatabaseConfig(url=database_url)
    MigrationRunner(
        config=config,
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    provider_store = SqlModelProviderStore(config=config)
    policy_store = SqlModelPolicyStore(config=config)
    provider_store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="tenant-model",
            workspace_id="workspace_sales",
            api_key_secret_ref_id="secret_model_key",
            default_model="old-model",
            model_ids=["old-model"],
        )
    )
    policy_store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            default_model="old-model",
            allowed_models=["old-model"],
        )
    )
    settings = Settings(
        database_url=database_url,
        model_gateway_provider_store_backend="sql",
        model_gateway_policy_store_backend="sql",
        _env_file=None,
    )
    runner = build_agent_worker_runner(settings, queue=InMemoryJobQueue())

    provider_store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="tenant-model",
            workspace_id="workspace_sales",
            api_key_secret_ref_id="secret_model_key",
            default_model="fresh-model",
            model_ids=["fresh-model"],
        )
    )
    policy_store.upsert_scope(
        ModelPolicyScopeUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            default_model="fresh-model",
            allowed_models=["fresh-model"],
        )
    )

    assert runner.worker.refresh_model_runtime is not None
    runner.worker.refresh_model_runtime()

    gateway = runner.worker.runtime.model_gateway
    assert isinstance(gateway, ModelGatewayRouter)
    assert gateway.provider_registry.providers[0].default_model == "fresh-model"
    assert runner.worker.runtime.model_policy.scoped_policies[0].default_model == "fresh-model"


def test_build_agent_worker_runner_loads_sql_billing_pricing_rule_store(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'worker-billing-pricing.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    pricing_store = SqlBillingPricingRuleStore(config=DatabaseConfig(url=database_url))
    pricing_store.upsert_rule(
        BillingPricingRuleUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            meter_type="model_tokens_input",
            unit="token",
            provider="openai_compatible",
            model="gpt-enterprise",
            price_per_unit=0.003,
            pricing_unit_quantity=1000,
            updated_by_user_id="admin_1",
        )
    )
    settings = Settings(
        database_url=database_url,
        billing_pricing_rule_store_backend="sql",
        _env_file=None,
    )

    runner = build_agent_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    assert (
        runner.worker.runtime.billing_pricing_service.estimate_cost(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            meter_type="model_tokens_input",
            quantity=2000,
            unit="token",
            provider="openai_compatible",
            model="gpt-enterprise",
        )
        == 0.006
    )


def test_build_agent_worker_runner_wires_model_gateway_secret_ref_from_settings():
    settings = Settings(
        model_gateway_api_key_secret_ref_id="secret_model_key",
        model_gateway_secret_lease_ttl_seconds=45,
        model_gateway_model="gpt-enterprise",
        model_gateway_chat_request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
        _env_file=None,
    )

    runner = build_agent_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    gateway = runner.worker.runtime.model_gateway
    assert gateway.api_key_secret_ref_id == "secret_model_key"
    assert gateway.secret_lease_ttl_seconds == 45
    assert gateway.chat_request_options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    assert gateway.secret_service is not None


def test_build_agent_worker_runner_resolves_chat_model_providers():
    runner = build_agent_worker_runner(
        Settings(
            model_gateway_allowed_models=["gpt-worker"],
            model_gateway_providers=[
                ModelProviderConfig(
                    id="worker-provider",
                    default_model="gpt-worker",
                )
            ],
            _env_file=None,
        ),
        queue=InMemoryJobQueue(),
    )

    catalog = runner.worker.chat_service.model_catalog(
        "tenant_acme",
        "workspace_sales",
        "user_1",
    )

    assert [(item.provider_id, item.model_id) for item in catalog] == [
        ("worker-provider", "gpt-worker")
    ]


def test_build_agent_worker_runner_omits_disabled_runtime_tool_handlers():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    tool_names = set(runner.worker.runtime.tool_gateway.policies)

    assert "sandbox.command" not in tool_names
    assert "browser.action" not in tool_names


def test_build_agent_worker_runner_wires_runtime_policy_service():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    policy_service = runner.worker.runtime.policy_service

    assert isinstance(policy_service, IdentityPolicyService)
    assert isinstance(policy_service.identity_service, InMemoryIdentityService)


def test_build_agent_worker_runner_injects_worker_audit_service():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    assert runner.worker.audit_service is not None


def test_build_agent_worker_runner_wires_license_service_into_worker_audit_service():
    store = InMemoryControlPlaneStore()
    runner = build_agent_worker_runner(
        Settings(license_runtime_enforcement_enabled=True, _env_file=None),
        store=store,
        queue=InMemoryJobQueue(),
    )

    assert runner.worker.audit_service.license_service is not None
    assert (
        runner.worker.audit_service.license_service.runtime_enforcement_enabled is True
    )
    assert runner.worker.audit_service.license_service.validation_store is store
    assert runner.worker.runtime.license_service is runner.worker.audit_service.license_service


def test_build_agent_worker_runner_injects_worker_guardrail_service():
    runner = build_agent_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
    )

    assert isinstance(
        runner.worker.runtime.tool_gateway.guardrail_service,
        InMemoryGuardrailService,
    )
    assert (
        runner.worker.runtime.guardrail_service
        is runner.worker.runtime.tool_gateway.guardrail_service
    )


@pytest.mark.parametrize("provider", ["playwright", "browserbase"])
def test_build_worker_browser_controller_uses_http_adapter_for_remote_providers(
    provider: str,
):
    controller = runner_module.build_worker_browser_controller(
        Settings(
            browser_provider=provider,
            browser_controller_base_url="http://browser-controller.internal",
            browser_controller_api_key="browser_key",
            browser_controller_timeout_seconds=12,
            _env_file=None,
        )
    )

    assert isinstance(controller, HttpBrowserController)
    assert controller.provider == provider
    assert controller.base_url == "http://browser-controller.internal"
    assert controller.api_key == "browser_key"
    assert controller.timeout_seconds == 12


def test_build_worker_browser_controller_keeps_disabled_provider_local():
    controller = runner_module.build_worker_browser_controller(Settings(_env_file=None))

    assert isinstance(controller, BrowserController)
    assert not isinstance(controller, HttpBrowserController)
    assert controller.provider == "disabled"


def test_build_cleanup_worker_runner_uses_sql_storage_catalog_from_settings(
    tmp_path: Path,
):
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

    assert isinstance(
        runner.worker.storage_lifecycle_service.storage_catalog, SqlStorageCatalog
    )
    assert isinstance(
        runner.worker.storage_lifecycle_service.lifecycle_policy_store,
        SqlLifecyclePolicyStore,
    )
    assert runner.worker.storage_lifecycle_service.audit_service is not None
    assert runner.worker.audit_service is not None


def test_build_trigger_due_worker_runner_uses_sql_trigger_store_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'trigger-worker.sqlite3'}",
        trigger_store_backend="sql",
        _env_file=None,
    )

    runner = build_trigger_due_worker_runner(
        settings,
        store=InMemoryControlPlaneStore(),
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.trigger_service.store, SqlTriggerStore)


def test_build_trigger_scheduler_worker_runner_uses_sql_trigger_store_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'trigger-scheduler.sqlite3'}",
        trigger_store_backend="sql",
        _env_file=None,
    )

    runner = build_trigger_scheduler_worker_runner(
        settings,
        store=InMemoryControlPlaneStore(),
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.trigger_service.store, SqlTriggerStore)


def test_worker_process_config_accepts_connector_sync_kind():
    config = WorkerProcessConfig(worker_kind="connector_sync")

    assert config.worker_kind == "connector_sync"


@pytest.mark.parametrize(
    "worker_kind",
    [
        "restore_drill_due",
        "restore_drill_execution",
        "restore_drill_scheduler",
        "restore_drill_evidence",
    ],
)
def test_worker_process_config_accepts_restore_drill_kinds(worker_kind: str):
    config = WorkerProcessConfig(worker_kind=worker_kind)

    assert config.worker_kind == worker_kind


def test_build_restore_drill_due_worker_runner_uses_provided_schedule_store():
    schedule_store = InMemoryRestoreDrillScheduleStore()

    runner = build_restore_drill_due_worker_runner(
        Settings(_env_file=None),
        queue=InMemoryJobQueue(),
        schedule_store=schedule_store,
    )

    assert runner.worker.schedule_store is schedule_store


def test_build_restore_drill_evidence_worker_runner_uses_provided_dependencies():
    queue = InMemoryJobQueue()
    schedule_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingRestoreDrillObjectStorage()

    runner = build_restore_drill_evidence_worker_runner(
        Settings(job_queue_backend="redis", _env_file=None),
        queue=queue,
        schedule_store=schedule_store,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    assert runner.worker.queue is queue
    assert runner.worker.schedule_store is schedule_store
    assert runner.worker.storage_catalog is storage_catalog
    assert runner.worker.object_storage is object_storage


def test_build_restore_drill_execution_worker_runner_uses_provided_dependencies():
    queue = InMemoryJobQueue()
    schedule_store = InMemoryRestoreDrillScheduleStore()

    runner = build_restore_drill_execution_worker_runner(
        Settings(job_queue_backend="redis", _env_file=None),
        queue=queue,
        schedule_store=schedule_store,
        verifier=lambda config: restore_drill_verification_result(),
    )

    assert runner.worker.queue is queue
    assert runner.worker.schedule_store is schedule_store


def test_build_restore_drill_due_worker_runner_uses_sql_schedule_store_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'restore-drill-due.sqlite3'}",
        restore_drill_schedule_backend="sql",
        _env_file=None,
    )

    runner = build_restore_drill_due_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.schedule_store, SqlRestoreDrillScheduleStore)


def test_build_restore_drill_execution_worker_runner_uses_sql_schedule_store_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'restore-drill-execution.sqlite3'}",
        restore_drill_schedule_backend="sql",
        _env_file=None,
    )

    runner = build_restore_drill_execution_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.schedule_store, SqlRestoreDrillScheduleStore)


def test_build_restore_drill_scheduler_worker_runner_uses_sql_schedule_store_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'restore-drill-scheduler.sqlite3'}",
        restore_drill_schedule_backend="sql",
        _env_file=None,
    )

    runner = build_restore_drill_scheduler_worker_runner(
        settings,
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.schedule_store, SqlRestoreDrillScheduleStore)


def test_build_connector_sync_worker_runner_uses_sql_knowledge_service_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'connector-sync-worker.sqlite3'}",
        knowledge_service_backend="sql",
        _env_file=None,
    )

    runner = build_connector_sync_worker_runner(
        settings,
        store=InMemoryControlPlaneStore(),
        queue=InMemoryJobQueue(),
    )

    assert isinstance(runner.worker.knowledge_service, SqlKnowledgeService)


def test_build_connector_sync_worker_runner_uses_sql_connector_registry_from_settings(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'connector-sync-registry.sqlite3'}",
        connector_registry_backend="sql",
        _env_file=None,
    )

    runner = build_connector_sync_worker_runner(
        settings,
        store=InMemoryControlPlaneStore(),
        queue=InMemoryJobQueue(),
        knowledge_service=InMemoryKnowledgeService(),
    )

    assert isinstance(runner.worker.connector_registry, SqlConnectorRegistry)

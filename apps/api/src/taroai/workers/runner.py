import argparse
from pathlib import Path
from time import sleep
from typing import Literal

from pydantic import BaseModel, Field

from taroai.agent import AgentRuntime, apply_agent_runtime_settings
from taroai.audit import AuditService
from taroai.chat import ChatService
from taroai.billing import (
    BillingPricingRule,
    BillingPricingRuleStore,
    BillingPricingService,
    InMemoryBillingPricingRuleStore,
    SqlBillingPricingRuleStore,
)
from taroai.config import Settings, load_settings
from taroai.connectors import (
    ConnectorDispatchService,
    ConnectorInvocationService,
    InMemoryConnectorRegistry,
    SqlConnectorRegistry,
)
from taroai.db import MigrationRunner, SqlControlPlaneRepository
from taroai.embeddings import EmbeddingGateway, OpenAICompatibleEmbeddingGateway
from taroai.guardrails import InMemoryGuardrailService
from taroai.identity import InMemoryIdentityService, SqlIdentityService
from taroai.knowledge import InMemoryKnowledgeService, SqlKnowledgeService
from taroai.lifecycle import (
    InMemoryLifecyclePolicyStore,
    InMemoryRestoreDrillScheduleStore,
    RestoreDrillScheduleStore,
    SqlLifecyclePolicyStore,
    SqlRestoreDrillScheduleStore,
)
from taroai.licensing import LicenseService
from taroai.model_gateway import (
    InMemoryModelProviderStore,
    InMemoryModelPolicyStore,
    ModelBudgetGuard,
    ModelBudgetPolicy,
    ModelGateway,
    ModelGatewayRouter,
    ModelPolicy,
    ModelPolicyStore,
    ModelProviderConfig,
    ModelProviderRateLimiter,
    ModelProviderRegistry,
    ModelProviderStore,
    OpenAICompatibleModelGateway,
    RedisModelProviderRateLimitStore,
    SqlModelProviderRateLimitStore,
    SqlModelProviderStore,
    SqlModelPolicyStore,
)
from taroai.sandbox import (
    BrowserController,
    HttpBrowserController,
    SandboxAdapter,
    SandboxNetworkMode,
    build_sandbox_adapter,
)
from taroai.sandbox.tools import register_browser_tool_handlers, register_sandbox_tool_handlers
from taroai.policy import IdentityPolicyService
from taroai.secrets import build_secret_service_from_settings
from taroai.skills import InMemorySkillRegistry, SqlSkillRegistry
from taroai.skills.import_service import HttpsGithubArchiveFetcher
from taroai.skills.service import SkillService
from taroai.store import InMemoryControlPlaneStore
from taroai.storage import (
    InMemoryStorageCatalog,
    ObjectStorageAdapter,
    S3CompatibleObjectStorage,
    SqlStorageCatalog,
    StorageContentScanner,
    StorageLifecycleService,
)
from taroai.tool_gateway import ToolGateway
from taroai.triggers.repository import SqlTriggerStore
from taroai.triggers.service import InMemoryTriggerStore, TriggerService
from taroai.workers.agent_worker import AgentWorker
from taroai.workers.cleanup_worker import CleanupWorker
from taroai.workers.connector_sync_worker import ConnectorSyncWorker
from taroai.workers.models import JobEnvelope
from taroai.workers.queue import JobQueue, RedisJobQueue, RedisQueueConfigurationError
from taroai.workers.restore_drill_scheduler_worker import RestoreDrillSchedulerWorker
from taroai.workers.restore_drill_evidence_worker import RestoreDrillEvidenceWorker
from taroai.workers.restore_drill_execution_worker import (
    RestoreDrillExecutionWorker,
    RestoreDrillVerifier,
)
from taroai.workers.restore_drill_worker import RestoreDrillDueWorker
from taroai.workers.scheduler_worker import TriggerSchedulerWorker
from taroai.workers.trigger_worker import TriggerDueWorker


class WorkerLoopResult(BaseModel):
    processed_jobs: int = 0
    idle_polls: int = 0
    last_job_id: str | None = None


class WorkerProcessConfig(BaseModel):
    worker_kind: Literal[
        "agent",
        "cleanup",
        "connector_sync",
        "trigger_due",
        "trigger_scheduler",
        "restore_drill_due",
        "restore_drill_execution",
        "restore_drill_evidence",
        "restore_drill_scheduler",
    ] = "agent"
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)
    max_jobs: int | None = Field(default=None, ge=1)
    loop_forever: bool = False


class AgentWorkerRunner(BaseModel):
    worker: AgentWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class CleanupWorkerRunner(BaseModel):
    worker: CleanupWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class ConnectorSyncWorkerRunner(BaseModel):
    worker: ConnectorSyncWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class TriggerDueWorkerRunner(BaseModel):
    worker: TriggerDueWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class TriggerSchedulerWorkerRunner(BaseModel):
    worker: TriggerSchedulerWorker
    poll_interval_seconds: float = Field(default=30.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self, now=None) -> WorkerLoopResult:
        result = self.worker.process_due(now=now)
        if result.enqueued_jobs == 0 and result.updated_triggers == 0:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(
            processed_jobs=result.enqueued_jobs,
            last_job_id=result.last_trigger_id,
        )

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            current = self.worker.process_due()
            if current.enqueued_jobs == 0 and current.updated_triggers == 0:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += current.enqueued_jobs
            result.last_job_id = current.last_trigger_id or result.last_job_id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class RestoreDrillDueWorkerRunner(BaseModel):
    worker: RestoreDrillDueWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class RestoreDrillEvidenceWorkerRunner(BaseModel):
    worker: RestoreDrillEvidenceWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class RestoreDrillExecutionWorkerRunner(BaseModel):
    worker: RestoreDrillExecutionWorker
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self) -> WorkerLoopResult:
        job = self.worker.process_next()
        if job is None:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(processed_jobs=1, last_job_id=job.id)

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            job = self.worker.process_next()
            if job is None:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


class RestoreDrillSchedulerWorkerRunner(BaseModel):
    worker: RestoreDrillSchedulerWorker
    poll_interval_seconds: float = Field(default=30.0, ge=0)
    stop_after_empty_polls: int = Field(default=1, ge=1)

    def run_once(self, now=None) -> WorkerLoopResult:
        result = self.worker.process_due(now=now)
        if result.enqueued_jobs == 0 and result.updated_schedules == 0:
            return WorkerLoopResult(idle_polls=1)
        return WorkerLoopResult(
            processed_jobs=result.enqueued_jobs,
            last_job_id=result.last_schedule_id,
        )

    def run_until_idle(self, max_jobs: int | None = None) -> WorkerLoopResult:
        result = WorkerLoopResult()
        while result.idle_polls < self.stop_after_empty_polls:
            current = self.worker.process_due()
            if current.enqueued_jobs == 0 and current.updated_schedules == 0:
                result.idle_polls += 1
                if (
                    self.poll_interval_seconds > 0
                    and result.idle_polls < self.stop_after_empty_polls
                ):
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += current.enqueued_jobs
            result.last_job_id = current.last_schedule_id or result.last_job_id
            result.idle_polls = 0
            if max_jobs is not None and result.processed_jobs >= max_jobs:
                break
        return result


def build_agent_worker_runner(
    settings: Settings,
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    runtime: AgentRuntime | None = None,
    queue: JobQueue | None = None,
) -> AgentWorkerRunner:
    resolved_store = store or build_worker_control_plane_store(settings)
    resolved_queue = queue or build_worker_queue(settings)
    audit_service = build_worker_audit_service(settings, resolved_store)
    guardrail_service = InMemoryGuardrailService()
    secret_service = build_secret_service_from_settings(settings)
    sandbox_adapter = build_sandbox_adapter(settings)
    browser_controller = build_worker_browser_controller(settings)
    policy_service = IdentityPolicyService(
        identity_service=build_worker_identity_service(settings, audit_service)
    )
    connector_registry = build_worker_connector_registry(settings)
    connector_dispatcher = ConnectorDispatchService(secret_service=secret_service)
    connector_invocation_service = ConnectorInvocationService()
    resolved_runtime = apply_agent_runtime_settings(runtime or AgentRuntime(
        store=resolved_store,
        model_gateway=build_worker_model_gateway(settings, secret_service),
        model_policy=build_model_policy(settings, build_model_policy_store(settings)),
        model_budget_guard=build_model_budget_guard(settings),
        tool_gateway=build_worker_tool_gateway(
            settings,
            audit_service,
            guardrail_service,
            secret_service,
            sandbox_adapter,
            browser_controller,
        ),
        policy_service=policy_service,
        audit_service=audit_service,
        license_service=getattr(audit_service, "license_service", None),
        knowledge_service=build_worker_knowledge_service(settings),
        sandbox_adapter=sandbox_adapter,
        browser_controller=browser_controller,
        storage_catalog=build_worker_storage_catalog(settings),
        object_storage=S3CompatibleObjectStorage.from_settings(settings),
        storage_content_scanner=build_worker_storage_content_scanner(settings),
        sandbox_runtime_image=settings.sandbox_runtime_image,
        sandbox_network_mode=SandboxNetworkMode(settings.sandbox_network_mode),
        sandbox_timeout_seconds=settings.sandbox_timeout_seconds,
        embedding_gateway=build_worker_embedding_gateway(settings, secret_service),
        billing_pricing_service=build_billing_pricing_service(
            settings,
            build_billing_pricing_rule_store(settings),
        ),
        guardrail_service=guardrail_service,
        skill_service=build_worker_skill_service(settings),
        connector_registry=connector_registry,
        connector_dispatcher=connector_dispatcher,
        connector_invocation_service=connector_invocation_service,
    ), settings)
    if resolved_runtime.skill_service is None:
        resolved_runtime.skill_service = build_worker_skill_service(settings)
    if resolved_runtime.connector_registry is None:
        resolved_runtime.connector_registry = connector_registry
    if resolved_runtime.connector_dispatcher is None:
        resolved_runtime.connector_dispatcher = connector_dispatcher
    if resolved_runtime.connector_invocation_service is None:
        resolved_runtime.connector_invocation_service = connector_invocation_service
    chat_service = ChatService(
        store=resolved_store,
        model_policy_resolver=lambda: resolved_runtime.model_policy,
        steering_available_resolver=lambda: resolved_runtime.runtime_mode == "loop_v2",
        provider_registry_resolver=lambda: ModelProviderRegistry(
            providers=effective_model_gateway_providers(
                settings,
                build_model_provider_store(settings),
            )
        ),
    )
    return AgentWorkerRunner(
        worker=AgentWorker(
            runtime=resolved_runtime,
            queue=resolved_queue,
            audit_service=audit_service,
            chat_service=chat_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
            continuation_max_attempts=settings.worker_job_max_attempts,
        ),
    )


def build_cleanup_worker_runner(
    settings: Settings,
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    queue: JobQueue | None = None,
    storage_catalog: InMemoryStorageCatalog | SqlStorageCatalog | None = None,
    object_storage: ObjectStorageAdapter | None = None,
) -> CleanupWorkerRunner:
    resolved_store = store or build_worker_control_plane_store(settings)
    resolved_queue = queue or build_worker_queue(settings)
    audit_service = build_worker_audit_service(settings, resolved_store)
    resolved_storage_catalog = storage_catalog or build_worker_storage_catalog(settings)
    resolved_object_storage = object_storage or S3CompatibleObjectStorage.from_settings(
        settings
    )
    lifecycle_policy_store = build_worker_lifecycle_policy_store(settings)
    return CleanupWorkerRunner(
        worker=CleanupWorker(
            queue=resolved_queue,
            storage_lifecycle_service=StorageLifecycleService(
                storage_catalog=resolved_storage_catalog,
                object_storage=resolved_object_storage,
                audit_service=audit_service,
                lifecycle_policy_store=lifecycle_policy_store,
            ),
            audit_service=audit_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
        ),
    )


def build_connector_sync_worker_runner(
    settings: Settings,
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    queue: JobQueue | None = None,
    knowledge_service: InMemoryKnowledgeService | SqlKnowledgeService | None = None,
    connector_registry: InMemoryConnectorRegistry | SqlConnectorRegistry | None = None,
) -> ConnectorSyncWorkerRunner:
    resolved_store = store or build_worker_control_plane_store(settings)
    resolved_queue = queue or build_worker_queue(settings)
    audit_service = build_worker_audit_service(settings, resolved_store)
    return ConnectorSyncWorkerRunner(
        worker=ConnectorSyncWorker(
            queue=resolved_queue,
            knowledge_service=knowledge_service
            or build_worker_knowledge_service(settings),
            connector_registry=connector_registry
            or build_worker_connector_registry(settings),
            store=resolved_store,
            audit_service=audit_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
            max_attempts=settings.worker_job_max_attempts,
        )
    )


def build_trigger_due_worker_runner(
    settings: Settings,
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    queue: JobQueue | None = None,
    trigger_service: TriggerService | None = None,
) -> TriggerDueWorkerRunner:
    resolved_store = store or build_worker_control_plane_store(settings)
    resolved_queue = queue or build_worker_queue(settings)
    audit_service = build_worker_audit_service(settings, resolved_store)
    return TriggerDueWorkerRunner(
        worker=TriggerDueWorker(
            store=resolved_store,
            trigger_service=trigger_service or build_worker_trigger_service(settings),
            queue=resolved_queue,
            audit_service=audit_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
            max_attempts=settings.worker_job_max_attempts,
            run_execution_queue_name=settings.run_execution_queue_name,
        ),
    )


def build_trigger_scheduler_worker_runner(
    settings: Settings,
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    queue: JobQueue | None = None,
    trigger_service: TriggerService | None = None,
) -> TriggerSchedulerWorkerRunner:
    resolved_store = store or build_worker_control_plane_store(settings)
    resolved_queue = queue or build_worker_queue(settings)
    audit_service = build_worker_audit_service(settings, resolved_store)
    return TriggerSchedulerWorkerRunner(
        worker=TriggerSchedulerWorker(
            trigger_service=trigger_service or build_worker_trigger_service(settings),
            queue=resolved_queue,
            audit_service=audit_service,
            max_attempts=settings.worker_job_max_attempts,
        ),
    )


def build_restore_drill_due_worker_runner(
    settings: Settings,
    queue: JobQueue | None = None,
    schedule_store: RestoreDrillScheduleStore | None = None,
) -> RestoreDrillDueWorkerRunner:
    resolved_queue = queue or build_worker_queue(settings)
    resolved_schedule_store = schedule_store or build_worker_restore_drill_schedule_store(
        settings
    )
    store = build_worker_control_plane_store(settings)
    audit_service = build_worker_audit_service(settings, store)
    return RestoreDrillDueWorkerRunner(
        worker=RestoreDrillDueWorker(
            schedule_store=resolved_schedule_store,
            queue=resolved_queue,
            audit_service=audit_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
            max_attempts=settings.worker_job_max_attempts,
        ),
    )


def build_restore_drill_evidence_worker_runner(
    settings: Settings,
    queue: JobQueue | None = None,
    schedule_store: RestoreDrillScheduleStore | None = None,
    storage_catalog: InMemoryStorageCatalog | SqlStorageCatalog | None = None,
    object_storage: ObjectStorageAdapter | None = None,
) -> RestoreDrillEvidenceWorkerRunner:
    resolved_queue = queue or build_worker_queue(settings)
    resolved_schedule_store = schedule_store or build_worker_restore_drill_schedule_store(
        settings
    )
    resolved_storage_catalog = storage_catalog or build_worker_storage_catalog(settings)
    resolved_object_storage = object_storage or S3CompatibleObjectStorage.from_settings(
        settings
    )
    store = build_worker_control_plane_store(settings)
    audit_service = build_worker_audit_service(settings, store)
    return RestoreDrillEvidenceWorkerRunner(
        worker=RestoreDrillEvidenceWorker(
            schedule_store=resolved_schedule_store,
            queue=resolved_queue,
            storage_catalog=resolved_storage_catalog,
            object_storage=resolved_object_storage,
            audit_service=audit_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
            max_attempts=settings.worker_job_max_attempts,
        ),
    )


def build_restore_drill_execution_worker_runner(
    settings: Settings,
    queue: JobQueue | None = None,
    schedule_store: RestoreDrillScheduleStore | None = None,
    verifier: RestoreDrillVerifier | None = None,
) -> RestoreDrillExecutionWorkerRunner:
    resolved_queue = queue or build_worker_queue(settings)
    resolved_schedule_store = schedule_store or build_worker_restore_drill_schedule_store(
        settings
    )
    store = build_worker_control_plane_store(settings)
    audit_service = build_worker_audit_service(settings, store)
    worker_data = {
        "schedule_store": resolved_schedule_store,
        "queue": resolved_queue,
        "audit_service": audit_service,
        "lease_seconds": settings.worker_job_lease_seconds,
        "retry_delay_seconds": settings.worker_job_retry_delay_seconds,
        "max_attempts": settings.worker_job_max_attempts,
    }
    if verifier is not None:
        worker_data["verifier"] = verifier
    return RestoreDrillExecutionWorkerRunner(
        worker=RestoreDrillExecutionWorker(**worker_data),
    )


def build_restore_drill_scheduler_worker_runner(
    settings: Settings,
    queue: JobQueue | None = None,
    schedule_store: RestoreDrillScheduleStore | None = None,
) -> RestoreDrillSchedulerWorkerRunner:
    resolved_queue = queue or build_worker_queue(settings)
    resolved_schedule_store = schedule_store or build_worker_restore_drill_schedule_store(
        settings
    )
    store = build_worker_control_plane_store(settings)
    audit_service = build_worker_audit_service(settings, store)
    return RestoreDrillSchedulerWorkerRunner(
        worker=RestoreDrillSchedulerWorker(
            schedule_store=resolved_schedule_store,
            queue=resolved_queue,
            audit_service=audit_service,
            max_attempts=settings.worker_job_max_attempts,
        ),
    )


def parse_worker_process_args(argv: list[str] | None = None) -> WorkerProcessConfig:
    parser = argparse.ArgumentParser(description="Run a Taroai worker process.")
    parser.add_argument(
        "--worker-kind",
        choices=[
            "agent",
            "cleanup",
            "connector_sync",
            "trigger_due",
            "trigger_scheduler",
            "restore_drill_due",
            "restore_drill_execution",
            "restore_drill_evidence",
            "restore_drill_scheduler",
        ],
        default="agent",
    )
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--stop-after-empty-polls", type=int, default=1)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--loop-forever", action="store_true")
    parsed = parser.parse_args(argv)
    return WorkerProcessConfig(
        worker_kind=parsed.worker_kind,
        poll_interval_seconds=parsed.poll_interval_seconds,
        stop_after_empty_polls=parsed.stop_after_empty_polls,
        max_jobs=parsed.max_jobs,
        loop_forever=parsed.loop_forever,
    )


def build_worker_process_runner(
    config: WorkerProcessConfig,
    settings: Settings,
) -> (
    AgentWorkerRunner
    | CleanupWorkerRunner
    | ConnectorSyncWorkerRunner
    | TriggerDueWorkerRunner
    | TriggerSchedulerWorkerRunner
    | RestoreDrillDueWorkerRunner
    | RestoreDrillExecutionWorkerRunner
    | RestoreDrillEvidenceWorkerRunner
    | RestoreDrillSchedulerWorkerRunner
):
    if config.worker_kind == "cleanup":
        runner = build_cleanup_worker_runner(settings)
    elif config.worker_kind == "connector_sync":
        runner = build_connector_sync_worker_runner(settings)
    elif config.worker_kind == "trigger_due":
        runner = build_trigger_due_worker_runner(settings)
    elif config.worker_kind == "trigger_scheduler":
        runner = build_trigger_scheduler_worker_runner(settings)
    elif config.worker_kind == "restore_drill_due":
        runner = build_restore_drill_due_worker_runner(settings)
    elif config.worker_kind == "restore_drill_execution":
        runner = build_restore_drill_execution_worker_runner(settings)
    elif config.worker_kind == "restore_drill_evidence":
        runner = build_restore_drill_evidence_worker_runner(settings)
    elif config.worker_kind == "restore_drill_scheduler":
        runner = build_restore_drill_scheduler_worker_runner(settings)
    else:
        runner = build_agent_worker_runner(settings)
    return runner.model_copy(
        update={
            "poll_interval_seconds": config.poll_interval_seconds,
            "stop_after_empty_polls": config.stop_after_empty_polls,
        }
    )


def run_worker_process(
    config: WorkerProcessConfig,
    settings: Settings | None = None,
) -> WorkerLoopResult:
    runner = build_worker_process_runner(config, settings or load_settings())
    if not config.loop_forever:
        return runner.run_until_idle(max_jobs=config.max_jobs)

    result = WorkerLoopResult()
    while True:
        current = runner.run_until_idle(max_jobs=config.max_jobs)
        result.processed_jobs += current.processed_jobs
        result.idle_polls = current.idle_polls
        result.last_job_id = current.last_job_id or result.last_job_id
        if config.max_jobs is not None and result.processed_jobs >= config.max_jobs:
            return result
        if config.poll_interval_seconds > 0:
            sleep(config.poll_interval_seconds)


def build_worker_control_plane_store(
    settings: Settings,
) -> InMemoryControlPlaneStore | SqlControlPlaneRepository:
    if settings.control_plane_store_backend == "sql":
        repository = SqlControlPlaneRepository(config=settings.database_config())
        repository.initialize_schema(Path("apps/api/migrations"))
        return repository
    return InMemoryControlPlaneStore()


def build_worker_skill_service(settings: Settings) -> SkillService:
    registry = (
        SqlSkillRegistry(config=settings.database_config())
        if settings.skill_registry_backend == "sql"
        else InMemorySkillRegistry()
    )
    return SkillService(
        registry=registry,
        github_fetcher=HttpsGithubArchiveFetcher(),
    )


def build_worker_audit_service(
    settings: Settings,
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository,
) -> AuditService:
    audit_service = AuditService(
        store=store,
        retention_days=settings.audit_retention_days,
    )
    audit_service.license_service = LicenseService(
        audit_service=audit_service,
        signature_verifier=settings.license_signature_verifier(),
        runtime_enforcement_enabled=settings.license_runtime_enforcement_enabled,
        validation_store=store,
    )
    return audit_service


def build_worker_identity_service(
    settings: Settings,
    audit_service: AuditService | None = None,
) -> InMemoryIdentityService | SqlIdentityService:
    if settings.identity_service_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlIdentityService(config=config, audit_service=audit_service)
    return InMemoryIdentityService(audit_service=audit_service)


def build_worker_storage_catalog(
    settings: Settings,
) -> InMemoryStorageCatalog | SqlStorageCatalog:
    if settings.storage_catalog_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlStorageCatalog(
            config=config,
            bucket=settings.object_storage_bucket,
        )
    return InMemoryStorageCatalog(bucket=settings.object_storage_bucket)


def build_worker_storage_content_scanner(settings: Settings) -> StorageContentScanner:
    return StorageContentScanner(
        blocked_terms=settings.object_storage_content_scan_blocked_terms,
    )


def build_worker_knowledge_service(
    settings: Settings,
) -> InMemoryKnowledgeService | SqlKnowledgeService:
    if settings.knowledge_service_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlKnowledgeService(config=config)
    return InMemoryKnowledgeService()


def build_worker_embedding_gateway(
    settings: Settings,
    secret_service,
) -> EmbeddingGateway | None:
    if not settings.embedding_gateway_enabled:
        return None
    return OpenAICompatibleEmbeddingGateway(
        base_url=settings.embedding_gateway_base_url,
        api_key=settings.embedding_gateway_api_key,
        api_key_secret_ref_id=settings.embedding_gateway_api_key_secret_ref_id or None,
        secret_service=secret_service,
        secret_lease_ttl_seconds=settings.embedding_gateway_secret_lease_ttl_seconds,
        default_model=settings.embedding_gateway_model,
        dimensions=settings.embedding_gateway_dimensions,
        timeout_seconds=settings.embedding_gateway_timeout_seconds,
    )


def build_worker_connector_registry(
    settings: Settings,
) -> InMemoryConnectorRegistry | SqlConnectorRegistry:
    if settings.connector_registry_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlConnectorRegistry(config=config)
    return InMemoryConnectorRegistry()


def build_worker_lifecycle_policy_store(
    settings: Settings,
) -> InMemoryLifecyclePolicyStore | SqlLifecyclePolicyStore:
    if settings.lifecycle_policy_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlLifecyclePolicyStore(config=config)
    return InMemoryLifecyclePolicyStore()


def build_worker_restore_drill_schedule_store(
    settings: Settings,
) -> RestoreDrillScheduleStore:
    if settings.restore_drill_schedule_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlRestoreDrillScheduleStore(config=config)
    return InMemoryRestoreDrillScheduleStore()


def build_worker_trigger_service(settings: Settings) -> TriggerService:
    if settings.trigger_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return TriggerService(store=SqlTriggerStore(config=config))
    return TriggerService(store=InMemoryTriggerStore())


def build_worker_tool_gateway(
    settings: Settings,
    audit_service: AuditService,
    guardrail_service: InMemoryGuardrailService | None = None,
    secret_service=None,
    sandbox_adapter: SandboxAdapter | None = None,
    browser_controller: BrowserController | None = None,
) -> ToolGateway:
    gateway = ToolGateway(
        audit_service=audit_service,
        secret_service=secret_service or build_secret_service_from_settings(settings),
        guardrail_service=guardrail_service or InMemoryGuardrailService(),
    )
    register_sandbox_tool_handlers(
        gateway,
        sandbox_adapter or build_sandbox_adapter(settings),
    )
    register_browser_tool_handlers(
        gateway,
        browser_controller or build_worker_browser_controller(settings),
    )
    return gateway


def build_worker_browser_controller(settings: Settings) -> BrowserController:
    if settings.browser_provider in {"playwright", "browserbase"}:
        return HttpBrowserController(
            provider=settings.browser_provider,
            base_url=settings.browser_controller_base_url,
            api_key=settings.browser_controller_api_key,
            timeout_seconds=settings.browser_controller_timeout_seconds,
        )
    return BrowserController(provider=settings.browser_provider)


def build_worker_queue(settings: Settings) -> JobQueue:
    if settings.job_queue_backend == "redis":
        return RedisJobQueue(url=settings.redis_url)
    raise RedisQueueConfigurationError("worker job queue backend must be redis")


def build_model_budget_guard(settings: Settings) -> ModelBudgetGuard:
    return ModelBudgetGuard(
        policy=ModelBudgetPolicy(
            budget_window_seconds=settings.model_gateway_budget_window_seconds,
            max_model_calls_per_run=settings.model_gateway_run_call_limit,
            max_model_tokens_per_run=settings.model_gateway_run_token_limit,
            max_model_calls_per_tenant=settings.model_gateway_tenant_call_limit,
            max_model_tokens_per_tenant=settings.model_gateway_tenant_token_limit,
            max_model_calls_per_workspace=settings.model_gateway_workspace_call_limit,
            max_model_tokens_per_workspace=settings.model_gateway_workspace_token_limit,
            max_model_calls_per_user=settings.model_gateway_user_call_limit,
            max_model_tokens_per_user=settings.model_gateway_user_token_limit,
            max_model_calls_per_agent=settings.model_gateway_agent_call_limit,
            max_model_tokens_per_agent=settings.model_gateway_agent_token_limit,
        )
    )


def build_worker_model_gateway(
    settings: Settings,
    secret_service,
) -> ModelGateway:
    providers = effective_worker_model_gateway_providers(
        settings,
        build_model_provider_store(settings),
    )
    if providers:
        return ModelGatewayRouter(
            provider_registry=ModelProviderRegistry(
                providers=providers
            ),
            secret_service=secret_service,
            rate_limiter=build_model_provider_rate_limiter(settings),
        )
    return OpenAICompatibleModelGateway(
        base_url=settings.model_gateway_base_url,
        api_key=settings.model_gateway_api_key,
        api_key_secret_ref_id=settings.model_gateway_api_key_secret_ref_id or None,
        secret_service=secret_service,
        secret_lease_ttl_seconds=settings.model_gateway_secret_lease_ttl_seconds,
        default_model=settings.model_gateway_model,
        timeout_seconds=settings.model_gateway_timeout_seconds,
        chat_request_options=settings.model_gateway_chat_request_options,
    )


def effective_worker_model_gateway_providers(
    settings: Settings,
    model_provider_store: ModelProviderStore | None = None,
) -> list[ModelProviderConfig]:
    providers = list(settings.model_gateway_providers)
    if model_provider_store is not None:
        providers.extend(
            record.to_provider_config()
            for record in model_provider_store.list_all_providers()
            if record.status == "active"
        )
    return providers


def build_model_provider_rate_limiter(settings: Settings) -> ModelProviderRateLimiter:
    if settings.model_gateway_provider_rate_limit_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return ModelProviderRateLimiter(
            store=SqlModelProviderRateLimitStore(config=config)
        )
    if settings.model_gateway_provider_rate_limit_backend == "redis":
        return ModelProviderRateLimiter(
            store=RedisModelProviderRateLimitStore(url=settings.redis_url)
        )
    return ModelProviderRateLimiter()


def build_model_policy_store(settings: Settings) -> ModelPolicyStore:
    if settings.model_gateway_policy_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlModelPolicyStore(config=config)
    return InMemoryModelPolicyStore()


def build_model_provider_store(settings: Settings) -> ModelProviderStore:
    if settings.model_gateway_provider_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlModelProviderStore(config=config)
    return InMemoryModelProviderStore()


def build_billing_pricing_rule_store(settings: Settings) -> BillingPricingRuleStore:
    if settings.billing_pricing_rule_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlBillingPricingRuleStore(config=config)
    return InMemoryBillingPricingRuleStore()


def build_billing_pricing_service(
    settings: Settings,
    billing_pricing_rule_store: BillingPricingRuleStore | None = None,
) -> BillingPricingService:
    effective_rules: dict[tuple, BillingPricingRule] = {
        billing_pricing_rule_key(rule): rule for rule in settings.billing_pricing_rules
    }
    if billing_pricing_rule_store is not None:
        for record in billing_pricing_rule_store.list_all_rules():
            rule = record.to_pricing_rule()
            effective_rules[billing_pricing_rule_key(rule)] = rule
    return BillingPricingService(rules=list(effective_rules.values()))


def billing_pricing_rule_key(rule: BillingPricingRule) -> tuple:
    return (
        rule.tenant_id,
        rule.workspace_id,
        rule.meter_type,
        rule.unit,
        rule.provider,
        rule.model,
        rule.currency,
    )


def build_model_policy(
    settings: Settings,
    model_policy_store: ModelPolicyStore | None = None,
) -> ModelPolicy:
    scoped_policies = {
        (scope.tenant_id, scope.workspace_id): scope
        for scope in settings.model_gateway_policy_scopes
    }
    if model_policy_store is not None:
        for record in model_policy_store.list_all_scopes():
            scope = record.to_policy_scope()
            scoped_policies[(scope.tenant_id, scope.workspace_id)] = scope
    return ModelPolicy(
        default_model=settings.model_gateway_model,
        allowed_models=settings.model_gateway_allowed_models,
        denied_models=settings.model_gateway_denied_models,
        model_sensitivity_limits=settings.model_gateway_sensitivity_limits,
        scoped_policies=list(scoped_policies.values()),
    )


def main(argv: list[str] | None = None) -> None:
    run_worker_process(parse_worker_process_args(argv))


if __name__ == "__main__":
    main()

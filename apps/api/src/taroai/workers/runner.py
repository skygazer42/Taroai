from pathlib import Path
from time import sleep

from pydantic import BaseModel, Field

from taroai.agent import AgentRuntime
from taroai.audit import AuditService
from taroai.config import Settings, load_settings
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.guardrails import InMemoryGuardrailService
from taroai.lifecycle import InMemoryLifecyclePolicyStore, SqlLifecyclePolicyStore
from taroai.model_gateway import (
    InMemoryModelPolicyStore,
    ModelBudgetGuard,
    ModelBudgetPolicy,
    ModelPolicy,
    ModelPolicyStore,
    OpenAICompatibleModelGateway,
    SqlModelPolicyStore,
)
from taroai.sandbox import (
    BrowserController,
    SandboxAdapter,
    register_browser_tool_handlers,
    register_sandbox_tool_handlers,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.storage import (
    InMemoryStorageCatalog,
    ObjectStorageAdapter,
    S3CompatibleObjectStorage,
    SqlStorageCatalog,
    StorageLifecycleService,
)
from taroai.tool_gateway import ToolGateway
from taroai.workers.agent_worker import AgentWorker
from taroai.workers.cleanup_worker import CleanupWorker
from taroai.workers.models import JobEnvelope
from taroai.workers.queue import JobQueue, RedisJobQueue, RedisQueueConfigurationError


class WorkerLoopResult(BaseModel):
    processed_jobs: int = 0
    idle_polls: int = 0
    last_job_id: str | None = None


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
                if self.poll_interval_seconds > 0 and result.idle_polls < self.stop_after_empty_polls:
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
                if self.poll_interval_seconds > 0 and result.idle_polls < self.stop_after_empty_polls:
                    sleep(self.poll_interval_seconds)
                continue
            result.processed_jobs += 1
            result.last_job_id = job.id
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
    audit_service = AuditService(
        store=resolved_store,
        retention_days=settings.audit_retention_days,
    )
    guardrail_service = InMemoryGuardrailService()
    resolved_runtime = runtime or AgentRuntime(
        store=resolved_store,
        model_gateway=OpenAICompatibleModelGateway(
            base_url=settings.model_gateway_base_url,
            api_key=settings.model_gateway_api_key,
            default_model=settings.model_gateway_model,
            timeout_seconds=settings.model_gateway_timeout_seconds,
        ),
        model_policy=build_model_policy(settings, build_model_policy_store(settings)),
        model_budget_guard=build_model_budget_guard(settings),
        tool_gateway=build_worker_tool_gateway(settings, audit_service, guardrail_service),
        audit_service=audit_service,
        guardrail_service=guardrail_service,
    )
    return AgentWorkerRunner(
        worker=AgentWorker(
            runtime=resolved_runtime,
            queue=resolved_queue,
            audit_service=audit_service,
            lease_seconds=settings.worker_job_lease_seconds,
            retry_delay_seconds=settings.worker_job_retry_delay_seconds,
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
    audit_service = AuditService(
        store=resolved_store,
        retention_days=settings.audit_retention_days,
    )
    resolved_storage_catalog = storage_catalog or build_worker_storage_catalog(settings)
    resolved_object_storage = object_storage or S3CompatibleObjectStorage.from_settings(settings)
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


def build_worker_control_plane_store(settings: Settings) -> InMemoryControlPlaneStore | SqlControlPlaneRepository:
    if settings.control_plane_store_backend == "sql":
        repository = SqlControlPlaneRepository(config=DatabaseConfig(url=settings.database_url))
        repository.initialize_schema(Path("apps/api/migrations"))
        return repository
    return InMemoryControlPlaneStore()


def build_worker_storage_catalog(settings: Settings) -> InMemoryStorageCatalog | SqlStorageCatalog:
    if settings.storage_catalog_backend == "sql":
        config = DatabaseConfig(url=settings.database_url)
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlStorageCatalog(
            config=config,
            bucket=settings.object_storage_bucket,
        )
    return InMemoryStorageCatalog(bucket=settings.object_storage_bucket)


def build_worker_lifecycle_policy_store(settings: Settings) -> InMemoryLifecyclePolicyStore | SqlLifecyclePolicyStore:
    if settings.lifecycle_policy_backend == "sql":
        config = DatabaseConfig(url=settings.database_url)
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlLifecyclePolicyStore(config=config)
    return InMemoryLifecyclePolicyStore()


def build_worker_tool_gateway(
    settings: Settings,
    audit_service: AuditService,
    guardrail_service: InMemoryGuardrailService | None = None,
) -> ToolGateway:
    gateway = ToolGateway(
        audit_service=audit_service,
        guardrail_service=guardrail_service or InMemoryGuardrailService(),
    )
    register_sandbox_tool_handlers(
        gateway,
        SandboxAdapter(provider=settings.sandbox_provider),
    )
    register_browser_tool_handlers(
        gateway,
        BrowserController(provider=settings.browser_provider),
    )
    return gateway


def build_worker_queue(settings: Settings) -> JobQueue:
    if settings.job_queue_backend == "redis":
        return RedisJobQueue(url=settings.redis_url)
    raise RedisQueueConfigurationError("worker job queue backend must be redis")


def build_model_budget_guard(settings: Settings) -> ModelBudgetGuard:
    return ModelBudgetGuard(
        policy=ModelBudgetPolicy(
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


def build_model_policy_store(settings: Settings) -> ModelPolicyStore:
    if settings.model_gateway_policy_store_backend == "sql":
        config = DatabaseConfig(url=settings.database_url)
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlModelPolicyStore(config=config)
    return InMemoryModelPolicyStore()


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
        scoped_policies=list(scoped_policies.values()),
    )


def main() -> None:
    runner = build_agent_worker_runner(load_settings())
    runner.run_until_idle()


if __name__ == "__main__":
    main()

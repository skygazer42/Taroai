import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from taroai.agent import AgentRuntime
from taroai.api import ApiExceptionManager
from taroai.auth import (
    AuthLoginRequest,
    AuthRequiredError,
    AuthService,
    AuthSessionStore,
    InMemoryAuthSessionStore,
    SqlAuthSessionStore,
)
from taroai.audit import (
    AuditActor,
    AuditEventCreate,
    AuditService,
    DEFAULT_AUDIT_COVERAGE_REQUIREMENTS,
)
from taroai.billing import (
    BillingAnalyticsService,
    BillingMeterQuery,
    BillingSummaryGroupBy,
    BillingSummaryQuery,
)
from taroai.config import Settings, load_settings
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.domain import RunCreate, RunStatus, utc_now
from taroai.guardrails import (
    GuardrailAction,
    GuardrailHttpDetector,
    GuardrailPromptThreatDetector,
    GuardrailSecretPatternDetector,
    GuardrailStage,
    InMemoryGuardrailService,
)
from taroai.knowledge import (
    InMemoryKnowledgeService,
    KnowledgeBaseCreate,
    KnowledgeDocumentApiCreate,
    KnowledgeDocumentCreate,
    KnowledgeQueryRequest,
    RetrievalRequest,
    SqlKnowledgeService,
)
from taroai.lifecycle import (
    BackupManifestRequest,
    BackupManifestService,
    DataCategory,
    DataExportApiRequest,
    DataExportBundleApiRequest,
    DataExportBundleRequest,
    DataExportRequest,
    DataExportService,
    DataResidencyReportRequest,
    DataResidencyService,
    InMemoryLifecyclePolicyStore,
    InMemoryTenantOffboardingStore,
    LegalHoldApiCreate,
    LegalHoldCreate,
    LegalHoldScopeType,
    LifecyclePolicyApiUpsert,
    LifecyclePolicyCreate,
    SqlLifecyclePolicyStore,
    SqlTenantOffboardingStore,
    TenantOffboardingApprovalRequest,
    TenantOffboardingApiRequest,
    TenantOffboardingDeletionRequest,
    TenantOffboardingDeletionService,
    TenantOffboardingExportCompletionRequest,
    TenantOffboardingRequest,
    TenantOffboardingService,
    TenantOffboardingState,
    TenantOffboardingTransitionError,
)
from taroai.memory import (
    GuardedLongTermMemoryService,
    GuardedShortTermMemoryService,
    InMemoryLongTermMemoryService,
    InMemoryShortTermMemoryReviewStore,
    InMemoryShortTermMemoryService,
    MemoryCandidateApiCreate,
    MemoryScopeType,
    MemoryWriteRequest,
    RedisShortTermMemoryService,
    ShortTermMemoryApiCreate,
    ShortTermMemoryReview,
    ShortTermMemoryReviewStatus,
    ShortTermMemoryWrite,
    SqlLongTermMemoryService,
    SqlShortTermMemoryReviewStore,
)
from taroai.model_gateway import (
    InMemoryModelPolicyStore,
    ModelBudgetGuard,
    ModelBudgetPolicy,
    ModelPolicyScopeApiUpsert,
    ModelPolicyStore,
    ModelPolicy,
    OpenAICompatibleModelGateway,
    SqlModelPolicyStore,
)
from taroai.observability import OtlpHttpTraceExporter, RunTraceService
from taroai.identity import InMemoryIdentityService, PasswordHasher, SqlIdentityService
from taroai.onboarding import (
    TenantBootstrapRequest,
    TenantBootstrapService,
    TenantReadinessService,
)
from taroai.policy import IdentityPolicyService, PolicyRequest, PolicyService
from taroai.sandbox import (
    BrowserAction,
    BrowserActionRequest,
    BrowserController,
    SandboxAdapter,
    SandboxCommand,
    SandboxCommandRequest,
    SandboxCreateRequest,
    SandboxExecutionError,
    SandboxFileWrite,
    SandboxFileWriteRequest,
    SandboxNetworkMode,
    SandboxSessionCreateRequest,
    SandboxSessionStatus,
    register_browser_tool_handlers,
    register_sandbox_tool_handlers,
)
from taroai.skills import InMemorySkillRegistry, SkillManifest, SqlSkillRegistry
from taroai.storage import (
    InMemoryStorageCatalog,
    ObjectStorageAdapter,
    S3CompatibleObjectStorage,
    SqlStorageCatalog,
    StorageContentRejectedError,
    StorageContentScanner,
    StorageContentScanRequest,
    StorageObjectApiCreate,
    StorageObjectCreate,
    StorageLifecycleCleanupPreviewRequest,
    StorageLifecycleCleanupRequest,
    StorageLifecycleService,
    StoragePurpose,
    StorageSignedUrlCreate,
    storage_object_audit_metadata,
)
from taroai.tool_gateway import ToolGateway
from taroai.store import InMemoryControlPlaneStore, NotFoundError, TenantAccessError
from taroai.workers import (
    JobQueue,
    JobType,
    RedisJobQueue,
    RedisQueueConfigurationError,
    RunExecutionJob,
)


class RequestContext(BaseModel):
    tenant_id: str
    user_id: str


class RunExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunQueuedResponse(BaseModel):
    run_id: str
    job_id: str
    status: str = "queued"
    queue: str


class RunCancelRequest(BaseModel):
    reason_code: str = Field(
        default="user_requested",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_.-]+$",
    )


class ApprovalResolveRequest(BaseModel):
    approval_id: str = Field(min_length=1)


class ApprovalRejectRequest(BaseModel):
    approval_id: str = Field(min_length=1)


class AuditEventQuery(BaseModel):
    run_id: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    event_type: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None

    def apply(self, events: list) -> list:
        return [event for event in events if self.matches(event)]

    def matches(self, event) -> bool:
        created_after = normalize_query_datetime(self.created_after)
        created_before = normalize_query_datetime(self.created_before)
        return all(
            [
                self.run_id is None or event.run_id == self.run_id,
                self.workspace_id is None or event.workspace_id == self.workspace_id,
                self.user_id is None or event.user_id == self.user_id,
                self.event_type is None or event.event_type == self.event_type,
                created_after is None or event.created_at >= created_after,
                created_before is None or event.created_at <= created_before,
            ]
        )


def normalize_query_datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def get_billing_meter_query(
    run_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    skill_id: str | None = None,
    meter_type: str | None = None,
) -> BillingMeterQuery:
    return BillingMeterQuery(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        agent_id=agent_id,
        skill_id=skill_id,
        meter_type=meter_type,
    )


def get_billing_summary_query(
    group_by: BillingSummaryGroupBy = "workspace_id",
    run_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    skill_id: str | None = None,
    meter_type: str | None = None,
) -> BillingSummaryQuery:
    return BillingSummaryQuery(
        group_by=group_by,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        agent_id=agent_id,
        skill_id=skill_id,
        meter_type=meter_type,
    )


def get_audit_event_query(
    run_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> AuditEventQuery:
    return AuditEventQuery(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=event_type,
        created_after=created_after,
        created_before=created_before,
    )


def record_audit_event(
    app: FastAPI,
    tenant_id: str,
    workspace_id: str | None,
    user_id: str | None,
    run_id: str | None,
    event_type: str,
    metadata: dict,
    request: Request | None = None,
) -> None:
    app.state.audit_service.record(
        AuditEventCreate(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata,
            actor=audit_actor_from_request(
                tenant_id=tenant_id,
                user_id=user_id,
                request=request,
            ),
        )
    )


def audit_actor_from_request(
    tenant_id: str,
    user_id: str | None,
    request: Request | None,
) -> AuditActor | None:
    if request is None:
        return None
    return AuditActor(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_type="user" if user_id is not None else "system",
        ip_address=audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def audit_request_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None
    if request.client is None:
        return None
    return request.client.host


def get_request_context(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> RequestContext:
    if authorization is not None:
        claims = request.app.state.auth_service.authenticate_authorization_header(authorization)
        return RequestContext(tenant_id=claims.tenant_id, user_id=claims.user_id)
    if request.app.state.settings.dev_request_headers_enabled and tenant_id is not None and user_id is not None:
        return RequestContext(tenant_id=tenant_id, user_id=user_id)
    raise AuthRequiredError("authentication required")


def require_permission(request: Request, context: RequestContext, action: str) -> None:
    resource = f"tenant:{context.tenant_id}"
    decision = request.app.state.policy_service.decide(
        PolicyRequest(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            action=action,
            resource=resource,
        )
    )
    if not decision.allowed:
        raise TenantAccessError(decision.reason or f"Permission denied: {action} on {resource}")


def require_storage_read_access(
    request: Request,
    context: RequestContext,
    storage_object,
) -> None:
    clearance_level = storage_clearance_level(request)
    if clearance_level < storage_object.sensitivity_level:
        raise TenantAccessError("Storage object sensitivity exceeds requester clearance")
    if not storage_object.acl_subjects:
        return
    request_subjects = set(storage_acl_subjects(request, context))
    allowed_subjects = set(storage_object.acl_subjects)
    if request_subjects.isdisjoint(allowed_subjects):
        raise TenantAccessError("Storage object ACL denied")


def storage_acl_subjects(request: Request, context: RequestContext) -> list[str]:
    raw_subjects = request.headers.get("X-ACL-Subjects", "")
    subjects = [
        subject.strip()
        for subject in raw_subjects.split(",")
        if subject.strip()
    ]
    subjects.extend([f"user:{context.user_id}", f"tenant:{context.tenant_id}"])
    return subjects


def storage_clearance_level(request: Request) -> int:
    raw_clearance = request.headers.get("X-Clearance-Level")
    if raw_clearance is None:
        return 0
    try:
        return int(raw_clearance)
    except ValueError as error:
        raise TenantAccessError("Invalid storage clearance level") from error


def create_app(
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
    knowledge_service: InMemoryKnowledgeService | SqlKnowledgeService | None = None,
    sandbox_adapter: SandboxAdapter | None = None,
    browser_controller: BrowserController | None = None,
    job_queue: JobQueue | None = None,
    storage_catalog: InMemoryStorageCatalog | SqlStorageCatalog | None = None,
    object_storage: ObjectStorageAdapter | None = None,
    identity_service: InMemoryIdentityService | SqlIdentityService | None = None,
    policy_service: PolicyService | None = None,
    audit_service: Any | None = None,
    auth_service: AuthService | None = None,
    billing_analytics_service: BillingAnalyticsService | None = None,
    run_trace_service: RunTraceService | None = None,
    model_policy_store: ModelPolicyStore | None = None,
    tenant_bootstrap_service: TenantBootstrapService | None = None,
    tenant_readiness_service: TenantReadinessService | None = None,
    skill_registry: InMemorySkillRegistry | SqlSkillRegistry | None = None,
    lifecycle_policy_store: InMemoryLifecyclePolicyStore | SqlLifecyclePolicyStore | None = None,
    tenant_offboarding_store: InMemoryTenantOffboardingStore | SqlTenantOffboardingStore | None = None,
    long_term_memory_service: InMemoryLongTermMemoryService | SqlLongTermMemoryService | GuardedLongTermMemoryService | None = None,
    short_term_memory_service: InMemoryShortTermMemoryService | RedisShortTermMemoryService | GuardedShortTermMemoryService | None = None,
    guardrail_service: InMemoryGuardrailService | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    app = FastAPI(title=resolved_settings.api_title)
    app.state.store = store or build_control_plane_store(resolved_settings)
    app.state.knowledge_service = knowledge_service or build_knowledge_service(resolved_settings)
    app.state.sandbox_adapter = sandbox_adapter or SandboxAdapter(provider=resolved_settings.sandbox_provider)
    app.state.browser_controller = browser_controller or build_browser_controller(resolved_settings)
    app.state.job_queue = job_queue or build_job_queue(resolved_settings)
    app.state.storage_catalog = storage_catalog or build_storage_catalog(resolved_settings)
    app.state.object_storage = object_storage or S3CompatibleObjectStorage.from_settings(resolved_settings)
    app.state.storage_content_scanner = build_storage_content_scanner(resolved_settings)
    app.state.audit_service = audit_service or AuditService(
        store=app.state.store,
        retention_days=resolved_settings.audit_retention_days,
    )
    app.state.guardrail_service = guardrail_service or build_guardrail_service(resolved_settings)
    app.state.skill_registry = skill_registry or build_skill_registry(resolved_settings)
    app.state.lifecycle_policy_store = lifecycle_policy_store or build_lifecycle_policy_store(
        resolved_settings
    )
    app.state.tenant_offboarding_store = (
        tenant_offboarding_store or build_tenant_offboarding_store(resolved_settings)
    )
    app.state.long_term_memory_service = guard_long_term_memory_service(
        long_term_memory_service or build_long_term_memory_service(resolved_settings),
        app.state.guardrail_service,
        app.state.audit_service,
    )
    app.state.short_term_memory_service = guard_short_term_memory_service(
        short_term_memory_service or build_short_term_memory_service(resolved_settings),
        app.state.guardrail_service,
        app.state.audit_service,
        build_short_term_memory_review_store(resolved_settings),
    )
    app.state.identity_service = identity_service or build_identity_service(
        resolved_settings,
        app.state.audit_service,
    )
    app.state.policy_service = policy_service or IdentityPolicyService(
        identity_service=app.state.identity_service
    )
    app.state.auth_service = auth_service or AuthService(
        identity_service=app.state.identity_service,
        session_store=build_auth_session_store(resolved_settings),
        access_token_secret=resolved_settings.access_token_secret,
        access_token_ttl_seconds=resolved_settings.access_token_ttl_seconds,
    )
    app.state.billing_analytics_service = billing_analytics_service or BillingAnalyticsService()
    app.state.run_trace_service = run_trace_service or build_run_trace_service(resolved_settings)
    app.state.model_policy_store = model_policy_store or build_model_policy_store(resolved_settings)
    app.state.tenant_readiness_service = tenant_readiness_service or TenantReadinessService(
        identity_service=app.state.identity_service,
        store=app.state.store,
        settings=resolved_settings,
        job_queue=app.state.job_queue,
    )
    app.state.tenant_bootstrap_service = tenant_bootstrap_service or TenantBootstrapService(
        identity_service=app.state.identity_service,
        store=app.state.store,
        settings=resolved_settings,
        readiness_service=app.state.tenant_readiness_service,
        audit_service=app.state.audit_service,
    )
    app.state.settings = resolved_settings
    tool_gateway = ToolGateway(
        audit_service=app.state.audit_service,
        guardrail_service=app.state.guardrail_service,
    )
    register_sandbox_tool_handlers(tool_gateway, app.state.sandbox_adapter)
    register_browser_tool_handlers(tool_gateway, app.state.browser_controller)
    app.state.runtime = runtime or AgentRuntime(
        store=app.state.store,
        model_gateway=OpenAICompatibleModelGateway(
            base_url=resolved_settings.model_gateway_base_url,
            api_key=resolved_settings.model_gateway_api_key,
            default_model=resolved_settings.model_gateway_model,
            timeout_seconds=resolved_settings.model_gateway_timeout_seconds,
        ),
        model_policy=build_model_policy(resolved_settings, app.state.model_policy_store),
        model_budget_guard=build_model_budget_guard(resolved_settings),
        tool_gateway=tool_gateway,
        audit_service=app.state.audit_service,
        knowledge_service=app.state.knowledge_service,
        long_term_memory_service=app.state.long_term_memory_service,
        guardrail_service=app.state.guardrail_service,
    )
    app.state.exception_manager = ApiExceptionManager()
    app.state.exception_manager.register(app)

    @app.post("/api/auth/login")
    def login(payload: AuthLoginRequest) -> dict:
        result = app.state.auth_service.login(
            tenant_id=payload.tenant_id,
            email=payload.email,
            password=payload.password,
        )
        return result.model_dump(mode="json")

    @app.post("/api/auth/logout")
    def logout(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        result = app.state.auth_service.logout_authorization_header(authorization)
        return result.model_dump(mode="json")

    @app.post("/api/tenants/bootstrap", status_code=status.HTTP_201_CREATED)
    def bootstrap_tenant(
        payload: TenantBootstrapRequest,
        bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
    ) -> dict:
        result = app.state.tenant_bootstrap_service.bootstrap(
            request=payload,
            bootstrap_token=bootstrap_token,
        )
        return result.model_dump(mode="json")

    @app.post("/api/runs", status_code=status.HTTP_201_CREATED)
    def create_run(
        payload: RunCreate,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, str]:
        run = app.state.store.create_run(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            payload=payload,
        )
        return {
            "run_id": run.id,
            "status": run.status,
            "events_url": f"/api/runs/{run.id}/events",
        }

    @app.get("/api/runs/{run_id}")
    def get_run(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        return app.state.store.get_run(context.tenant_id, run_id).model_dump(mode="json")

    @app.get("/api/runs/{run_id}/events")
    def get_run_events(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> StreamingResponse:
        events = app.state.store.list_run_events(context.tenant_id, run_id)

        def stream() -> Iterator[str]:
            for event in events:
                payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
                yield f"event: {event.type}\n"
                yield f"data: {payload}\n\n"

        return StreamingResponse(stream(), media_type=app.state.settings.event_stream_media_type)

    @app.post("/api/runs/{run_id}/execute")
    def execute_run(
        run_id: str,
        response: Response,
        payload: RunExecutionRequest | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        if app.state.settings.run_execution_dispatch_mode == "queue":
            queue = app.state.job_queue
            if queue is None:
                raise RedisQueueConfigurationError("job queue backend is disabled")
            run = app.state.store.get_run(context.tenant_id, run_id)
            job = queue.enqueue(
                JobType.RUN_EXECUTION,
                RunExecutionJob(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=run.user_id,
                    run_id=run.id,
                    requested_by_user_id=context.user_id,
                ),
                max_attempts=app.state.settings.worker_job_max_attempts,
            )
            queued_run = app.state.store.update_run_status(
                context.tenant_id,
                run_id,
                RunStatus.QUEUED,
            )
            app.state.store.append_run_event(
                queued_run,
                "run.execution_queued",
                {"job_id": job.id, "queue": app.state.settings.run_execution_queue_name},
            )
            response.status_code = status.HTTP_202_ACCEPTED
            return RunQueuedResponse(
                run_id=run_id,
                job_id=job.id,
                queue=app.state.settings.run_execution_queue_name,
            ).model_dump(mode="json")
        state = app.state.runtime.execute_run(context.tenant_id, run_id)
        return state.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        payload: RunCancelRequest | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        resolved_payload = payload or RunCancelRequest()
        run = app.state.runtime.cancel_run(
            tenant_id=context.tenant_id,
            run_id=run_id,
            cancelled_by_user_id=context.user_id,
            reason_code=resolved_payload.reason_code,
        )
        return run.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/approvals")
    def resolve_approval(
        run_id: str,
        payload: ApprovalResolveRequest,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        state = app.state.runtime.resume_after_approval(
            tenant_id=context.tenant_id,
            run_id=run_id,
            approval_id=payload.approval_id,
            approved_by_user_id=context.user_id,
        )
        return state.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/approvals/reject")
    def reject_approval(
        run_id: str,
        payload: ApprovalRejectRequest,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        state = app.state.runtime.reject_approval(
            tenant_id=context.tenant_id,
            run_id=run_id,
            approval_id=payload.approval_id,
            rejected_by_user_id=context.user_id,
        )
        return state.model_dump(mode="json")

    @app.get("/api/runs/{run_id}/artifacts")
    def list_artifacts(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        return [
            artifact.model_dump(mode="json")
            for artifact in app.state.store.list_artifacts(context.tenant_id, run_id)
        ]

    @app.get("/api/runs/{run_id}/trace")
    def get_run_trace(
        run_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "audit.read")
        return app.state.run_trace_service.build(
            store=app.state.store,
            tenant_id=context.tenant_id,
            run_id=run_id,
        ).model_dump(mode="json")

    @app.post("/api/runs/{run_id}/trace/export")
    def export_run_trace(
        run_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "audit.read")
        return app.state.run_trace_service.export(
            store=app.state.store,
            tenant_id=context.tenant_id,
            run_id=run_id,
        ).model_dump(mode="json")

    @app.get("/api/model-policies/scopes")
    def list_model_policy_scopes(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "model_policy.read")
        return [
            scope.model_dump(mode="json")
            for scope in app.state.model_policy_store.list_scopes(context.tenant_id)
        ]

    @app.put("/api/model-policies/scopes")
    def upsert_model_policy_scope(
        payload: ModelPolicyScopeApiUpsert,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_policy.manage")
        record = app.state.model_policy_store.upsert_scope(
            payload.to_upsert(
                tenant_id=context.tenant_id,
                updated_by_user_id=context.user_id,
            )
        )
        app.state.runtime.model_policy = build_model_policy(
            app.state.settings,
            app.state.model_policy_store,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_policy.scope.upserted",
            metadata={
                "workspace_id": record.workspace_id,
                "default_model": record.default_model,
                "allowed_model_count": len(record.allowed_models),
                "denied_model_count": len(record.denied_models),
            },
            request=request,
        )
        return record.model_dump(mode="json")

    @app.get("/api/billing/meters")
    def list_billing_meters(
        request: Request,
        query: BillingMeterQuery = Depends(get_billing_meter_query),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "billing.read")
        return [
            meter.model_dump(mode="json")
            for meter in query.apply(app.state.store.list_billing_meters(context.tenant_id))
        ]

    @app.get("/api/billing/summary")
    def summarize_billing_meters(
        request: Request,
        query: BillingSummaryQuery = Depends(get_billing_summary_query),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "billing.read")
        return [
            bucket.model_dump(mode="json")
            for bucket in app.state.billing_analytics_service.summarize(
                app.state.store.list_billing_meters(context.tenant_id),
                query,
            )
        ]

    @app.get("/api/audit-events")
    def list_audit_events(
        request: Request,
        query: AuditEventQuery = Depends(get_audit_event_query),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "audit.read")
        return [
            event.model_dump(mode="json")
            for event in query.apply(app.state.audit_service.list_for_tenant(context.tenant_id))
        ]

    @app.get("/api/audit-events/coverage")
    def get_audit_coverage(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "audit.read")
        return app.state.audit_service.check_coverage(
            context.tenant_id,
            DEFAULT_AUDIT_COVERAGE_REQUIREMENTS,
        ).model_dump(mode="json")

    @app.put("/api/lifecycle/policies/{category}")
    def upsert_lifecycle_policy(
        category: DataCategory,
        payload: LifecyclePolicyApiUpsert,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        policy = app.state.lifecycle_policy_store.upsert_policy(
            LifecyclePolicyCreate(
                tenant_id=context.tenant_id,
                category=category,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=policy.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.policy.upserted",
            metadata=lifecycle_policy_audit_metadata(policy),
            request=request,
        )
        return policy.model_dump(mode="json")

    @app.get("/api/lifecycle/policies/{category}")
    def get_lifecycle_policy(
        category: DataCategory,
        request: Request,
        workspace_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        return app.state.lifecycle_policy_store.get_policy(
            context.tenant_id,
            category,
            workspace_id=workspace_id,
        ).model_dump(mode="json")

    @app.get("/api/lifecycle/policies/{category}/effective")
    def get_effective_lifecycle_policy(
        category: DataCategory,
        request: Request,
        workspace_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        return app.state.lifecycle_policy_store.resolve_policy(
            context.tenant_id,
            category,
            workspace_id=workspace_id,
        ).model_dump(mode="json")

    @app.post("/api/lifecycle/exports", status_code=status.HTTP_201_CREATED)
    def create_data_export_manifest(
        payload: DataExportApiRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        manifest = build_data_export_service(app).create_manifest(
            DataExportRequest(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=payload.run_id,
            event_type="lifecycle.export.manifest_created",
            metadata=data_export_manifest_audit_metadata(manifest),
            request=request,
        )
        return manifest.model_dump(mode="json")

    @app.post("/api/lifecycle/export-bundles", status_code=status.HTTP_201_CREATED)
    def create_data_export_bundle(
        payload: DataExportBundleApiRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        bundle = build_data_export_service(app).create_bundle(
            DataExportBundleRequest(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=payload.run_id,
            event_type="lifecycle.export.bundle_created",
            metadata=data_export_bundle_audit_metadata(bundle),
            request=request,
        )
        return bundle.model_dump(mode="json")

    @app.post("/api/lifecycle/backup-manifests", status_code=status.HTTP_201_CREATED)
    def create_backup_manifest(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        manifest = build_backup_manifest_service(app).create_manifest(
            BackupManifestRequest(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.backup_manifest.created",
            metadata=backup_manifest_audit_metadata(manifest),
            request=request,
        )
        return manifest.model_dump(mode="json")

    @app.post("/api/lifecycle/data-residency/reports", status_code=status.HTTP_201_CREATED)
    def create_data_residency_report(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        report = build_data_residency_service(app).create_report(
            DataResidencyReportRequest(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.data_residency.report_created",
            metadata=data_residency_report_audit_metadata(report),
            request=request,
        )
        return report.model_dump(mode="json")

    @app.post("/api/lifecycle/tenant-offboarding-requests", status_code=status.HTTP_201_CREATED)
    def create_tenant_offboarding_request(
        payload: TenantOffboardingApiRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        plan = build_tenant_offboarding_service(app).create_plan(
            TenantOffboardingRequest(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.offboarding.requested",
            metadata=tenant_offboarding_audit_metadata(plan),
            request=request,
        )
        return plan.model_dump(mode="json")

    @app.get("/api/lifecycle/tenant-offboarding-requests/{plan_id}")
    def get_tenant_offboarding_request(
        plan_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        return build_tenant_offboarding_service(app).get_plan(
            context.tenant_id,
            plan_id,
        ).model_dump(mode="json")

    @app.post("/api/lifecycle/tenant-offboarding-requests/{plan_id}/approve")
    def approve_tenant_offboarding_request(
        plan_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        plan = build_tenant_offboarding_service(app).approve_plan(
            TenantOffboardingApprovalRequest(
                tenant_id=context.tenant_id,
                plan_id=plan_id,
                approved_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.offboarding.approved",
            metadata=tenant_offboarding_audit_metadata(plan),
            request=request,
        )
        return plan.model_dump(mode="json")

    @app.post(
        "/api/lifecycle/tenant-offboarding-requests/{plan_id}/export-bundles",
        status_code=status.HTTP_201_CREATED,
    )
    def create_tenant_offboarding_export_bundle(
        plan_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        offboarding_service = build_tenant_offboarding_service(app)
        plan = offboarding_service.get_plan(context.tenant_id, plan_id)
        if plan.state != TenantOffboardingState.EXPORT_PENDING:
            raise TenantOffboardingTransitionError(
                f"Tenant offboarding export cannot be started from state {plan.state.value}"
            )
        bundle = build_data_export_service(app).create_bundle(
            DataExportBundleRequest(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
                workspace_id=None,
                run_id=None,
                categories=plan.categories,
            )
        )
        completed = offboarding_service.complete_export(
            TenantOffboardingExportCompletionRequest(
                tenant_id=context.tenant_id,
                plan_id=plan_id,
                completed_by_user_id=context.user_id,
                export_bundle_id=bundle.id,
                export_storage_object_id=bundle.storage_object_id,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.offboarding.export_completed",
            metadata=tenant_offboarding_export_audit_metadata(completed, bundle),
            request=request,
        )
        return completed.model_dump(mode="json")

    @app.post("/api/lifecycle/tenant-offboarding-requests/{plan_id}/delete")
    def execute_tenant_offboarding_deletion(
        plan_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        result = build_tenant_offboarding_deletion_service(app).execute(
            TenantOffboardingDeletionRequest(
                tenant_id=context.tenant_id,
                plan_id=plan_id,
                deleted_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type=tenant_offboarding_deletion_event_type(result),
            metadata=tenant_offboarding_deletion_audit_metadata(result),
            request=request,
        )
        return result.model_dump(mode="json")

    @app.post("/api/lifecycle/legal-holds", status_code=status.HTTP_201_CREATED)
    def create_legal_hold(
        payload: LegalHoldApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        hold = app.state.lifecycle_policy_store.create_legal_hold(
            LegalHoldCreate(
                tenant_id=context.tenant_id,
                created_by_user_id=context.user_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.legal_hold.created",
            metadata=legal_hold_audit_metadata(hold),
            request=request,
        )
        return hold.model_dump(mode="json")

    @app.get("/api/lifecycle/legal-holds")
    def list_legal_holds(
        category: DataCategory,
        scope_type: LegalHoldScopeType,
        scope_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "lifecycle.read")
        return [
            hold.model_dump(mode="json")
            for hold in app.state.lifecycle_policy_store.list_active_legal_holds(
                tenant_id=context.tenant_id,
                category=category,
                scope_type=scope_type,
                scope_id=scope_id,
                now=utc_now(),
            )
        ]

    @app.post("/api/lifecycle/legal-holds/{legal_hold_id}/release")
    def release_legal_hold(
        legal_hold_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        hold = app.state.lifecycle_policy_store.release_legal_hold(
            tenant_id=context.tenant_id,
            legal_hold_id=legal_hold_id,
            released_at=utc_now(),
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.legal_hold.released",
            metadata=legal_hold_audit_metadata(hold),
            request=request,
        )
        return hold.model_dump(mode="json")

    @app.post("/api/lifecycle/storage-cleanup/preview")
    def preview_storage_cleanup(
        payload: StorageLifecycleCleanupPreviewRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.read")
        result = build_storage_lifecycle_service(app).cleanup_expired_objects(
            StorageLifecycleCleanupRequest(
                tenant_id=context.tenant_id,
                workspace_id=payload.workspace_id,
                now=payload.now or utc_now(),
                dry_run=True,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="lifecycle.cleanup.previewed",
            metadata=storage_cleanup_preview_audit_metadata(result),
            request=request,
        )
        return result.model_dump(mode="json")

    @app.get("/api/tenants/current/readiness")
    def get_tenant_readiness(
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        return app.state.tenant_readiness_service.check_tenant_readiness(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
        ).model_dump(mode="json")

    @app.post("/api/skills", status_code=status.HTTP_201_CREATED)
    def register_skill(
        payload: SkillManifest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        entry = app.state.skill_registry.register_for_tenant(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user_id,
            manifest=payload,
        )
        return entry.model_dump(mode="json")

    @app.get("/api/skills")
    def list_skills(
        request: Request,
        workspace_id: str | None = None,
        department_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        return [
            entry.model_dump(mode="json")
            for entry in app.state.skill_registry.list_visible_for_tenant(
                context.tenant_id,
                user_id=context.user_id,
                workspace_id=workspace_id,
                department_id=department_id,
            )
        ]

    @app.get("/api/skills/analytics")
    def get_skill_marketplace_analytics(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        return app.state.skill_registry.get_marketplace_analytics(
            context.tenant_id
        ).model_dump(mode="json")

    @app.get("/api/skills/{skill_id}")
    def get_skill(
        skill_id: str,
        request: Request,
        workspace_id: str | None = None,
        department_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        return app.state.skill_registry.get_visible_for_tenant(
            context.tenant_id,
            skill_id,
            user_id=context.user_id,
            workspace_id=workspace_id,
            department_id=department_id,
        ).model_dump(mode="json")

    @app.get("/api/skills/{skill_id}/versions")
    def list_skill_versions(
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        return [
            entry.model_dump(mode="json")
            for entry in app.state.skill_registry.list_versions(
                context.tenant_id,
                skill_id,
            )
        ]

    @app.post("/api/skills/{skill_id}/publish")
    def publish_skill(
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        entry = app.state.skill_registry.publish(
            context.tenant_id,
            skill_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.published",
            metadata={
                "skill_id": entry.manifest.id,
                "version": entry.manifest.version,
                "status": entry.status.value,
            },
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.post("/api/skills/{skill_id}/disable")
    def disable_skill(
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        return app.state.skill_registry.disable(
            context.tenant_id,
            skill_id,
        ).model_dump(mode="json")

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/install", status_code=status.HTTP_201_CREATED)
    def install_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        return app.state.skill_registry.install_for_workspace(
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            installed_by_user_id=context.user_id,
        ).model_dump(mode="json")

    @app.get("/api/workspaces/{workspace_id}/skills")
    def list_workspace_skills(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        return [
            installation.model_dump(mode="json")
            for installation in app.state.skill_registry.list_for_workspace(
                context.tenant_id,
                workspace_id,
            )
        ]

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/enable")
    def enable_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        return app.state.skill_registry.enable_for_workspace(
            context.tenant_id,
            workspace_id,
            skill_id,
        ).model_dump(mode="json")

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/disable")
    def disable_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        return app.state.skill_registry.disable_for_workspace(
            context.tenant_id,
            workspace_id,
            skill_id,
        ).model_dump(mode="json")

    @app.post("/api/storage/objects", status_code=status.HTTP_201_CREATED)
    def register_storage_object(
        payload: StorageObjectApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "storage.write")
        storage_object = app.state.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=context.tenant_id,
                **payload.model_dump(),
            )
        )
        return storage_object.model_dump(mode="json")

    @app.get("/api/runs/{run_id}/storage-objects")
    def list_storage_objects(
        run_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "storage.read")
        return [
            storage_object.model_dump(mode="json")
            for storage_object in app.state.storage_catalog.list_for_run(context.tenant_id, run_id)
        ]

    @app.post("/api/storage/objects/{storage_object_id}/signed-url")
    def create_storage_signed_url(
        storage_object_id: str,
        payload: StorageSignedUrlCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        storage_object = app.state.storage_catalog.get(context.tenant_id, storage_object_id)
        if payload.operation == "read":
            require_permission(request, context, "storage.read")
            require_storage_read_access(request, context, storage_object)
        else:
            require_permission(request, context, "storage.write")
        expires_in_seconds = (
            payload.expires_in_seconds
            or app.state.settings.object_storage_signed_url_ttl_seconds
        )
        signed_url = app.state.object_storage.create_signed_url(
            storage_object,
            operation=payload.operation,
            expires_in_seconds=expires_in_seconds,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=storage_object.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="storage.signed_url.created",
            metadata={
                **storage_audit_metadata(storage_object),
                "operation": payload.operation,
                "method": signed_url.method,
                "expires_at": signed_url.expires_at.isoformat(),
            },
            request=request,
        )
        return signed_url.model_dump(mode="json")

    @app.put("/api/storage/objects/{storage_object_id}/content")
    def upload_storage_object_content(
        storage_object_id: str,
        request: Request,
        content: bytes = Body(...),
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "storage.write")
        storage_object = app.state.storage_catalog.get(context.tenant_id, storage_object_id)
        if len(content) != storage_object.size_bytes:
            raise ValueError(
                f"Uploaded content length {len(content)} does not match declared size {storage_object.size_bytes}"
            )
        result = upload_storage_object(
            app=app,
            storage_object=storage_object,
            content=content,
            request=request,
            context=context,
        )
        metadata = storage_audit_metadata(storage_object)
        if storage_object.run_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=storage_object.run_id,
                meter_type="storage_bytes",
                quantity=storage_object.size_bytes,
                unit="byte",
                metadata=metadata,
            )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=storage_object.workspace_id,
            user_id=context.user_id,
            run_id=storage_object.run_id,
            event_type="storage.uploaded",
            metadata=metadata,
            request=request,
        )
        return result.model_dump(mode="json")

    @app.get("/api/storage/objects/{storage_object_id}/content")
    def download_storage_object_content(
        storage_object_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> Response:
        require_permission(request, context, "storage.read")
        storage_object = app.state.storage_catalog.get(context.tenant_id, storage_object_id)
        require_storage_read_access(request, context, storage_object)
        result = app.state.object_storage.download(storage_object)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=storage_object.workspace_id,
            user_id=context.user_id,
            run_id=storage_object.run_id,
            event_type="storage.downloaded",
            metadata=storage_audit_metadata(storage_object),
            request=request,
        )
        return Response(content=result.content, media_type=result.content_type)

    @app.delete("/api/storage/objects/{storage_object_id}")
    def delete_storage_object(
        storage_object_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "storage.write")
        storage_object = app.state.storage_catalog.get(context.tenant_id, storage_object_id)
        now = utc_now()
        if (
            storage_object.retention_expires_at is not None
            and storage_object.retention_expires_at > now
        ):
            raise ValueError(
                "Storage object cannot be deleted before retention_expires_at"
            )
        app.state.object_storage.delete(storage_object)
        deleted = app.state.storage_catalog.mark_deleted(
            tenant_id=context.tenant_id,
            storage_object_id=storage_object_id,
            deleted_at=now,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=deleted.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="storage.deleted",
            metadata=storage_audit_metadata(deleted),
            request=request,
        )
        return deleted.model_dump(mode="json")

    @app.post("/api/memory/candidates", status_code=status.HTTP_201_CREATED)
    def create_memory_candidate(
        payload: MemoryCandidateApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.write")
        memory = app.state.long_term_memory_service.propose_candidate(
            MemoryWriteRequest(
                tenant_id=context.tenant_id,
                created_by=context.user_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=memory.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="memory.candidate_created",
            metadata=memory_audit_metadata(memory),
            request=request,
        )
        return memory.model_dump(mode="json")

    @app.post("/api/memory/short-term", status_code=status.HTTP_201_CREATED)
    def put_short_term_memory(
        payload: ShortTermMemoryApiCreate,
        response: Response,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.write")
        result = app.state.short_term_memory_service.put(
            ShortTermMemoryWrite(
                tenant_id=context.tenant_id,
                created_by=context.user_id,
                **payload.model_dump(),
            )
        )
        if isinstance(result, ShortTermMemoryReview):
            response.status_code = status.HTTP_202_ACCEPTED
            record_audit_event(
                app,
                tenant_id=context.tenant_id,
                workspace_id=result.workspace_id,
                user_id=context.user_id,
                run_id=None,
                event_type="memory.short_term_review_created",
                metadata=short_term_memory_review_audit_metadata(result),
                request=request,
            )
        return result.model_dump(mode="json")

    @app.get("/api/memory/short-term")
    def list_short_term_memory(
        run_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "memory.read")
        return [
            entry.model_dump(mode="json")
            for entry in app.state.short_term_memory_service.list_for_run(
                context.tenant_id,
                run_id,
            )
        ]

    @app.delete("/api/memory/short-term")
    def delete_short_term_memory(
        run_id: str,
        key: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.write")
        return {
            "deleted": app.state.short_term_memory_service.delete(
                context.tenant_id,
                run_id,
                key,
            )
        }

    @app.get("/api/memory/short-term/reviews")
    def list_short_term_memory_reviews(
        request: Request,
        run_id: str | None = None,
        review_status: ShortTermMemoryReviewStatus | None = Query(
            default=ShortTermMemoryReviewStatus.PENDING,
            alias="status",
        ),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "memory.review")
        return [
            review.model_dump(mode="json")
            for review in app.state.short_term_memory_service.list_reviews(
                context.tenant_id,
                run_id=run_id,
                status=review_status,
            )
        ]

    @app.post("/api/memory/short-term/reviews/{review_id}/approve")
    def approve_short_term_memory_review(
        review_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.review")
        review = app.state.short_term_memory_service.approve_review(
            context.tenant_id,
            review_id,
            reviewed_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=review.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="memory.short_term_approved",
            metadata=short_term_memory_review_audit_metadata(review),
            request=request,
        )
        return review.model_dump(mode="json")

    @app.post("/api/memory/short-term/reviews/{review_id}/reject")
    def reject_short_term_memory_review(
        review_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.review")
        review = app.state.short_term_memory_service.reject_review(
            context.tenant_id,
            review_id,
            reviewed_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=review.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="memory.short_term_rejected",
            metadata=short_term_memory_review_audit_metadata(review),
            request=request,
        )
        return review.model_dump(mode="json")

    @app.get("/api/memory")
    def list_memory_by_scope(
        scope_type: MemoryScopeType,
        scope_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "memory.read")
        return [
            memory.model_dump(mode="json")
            for memory in app.state.long_term_memory_service.list_by_scope(
                context.tenant_id,
                scope_type,
                scope_id,
            )
        ]

    @app.post("/api/memory/{memory_id}/approve")
    def approve_memory_candidate(
        memory_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.review")
        memory = app.state.long_term_memory_service.approve(
            context.tenant_id,
            memory_id,
            reviewed_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=memory.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="memory.approved",
            metadata=memory_audit_metadata(memory),
            request=request,
        )
        return memory.model_dump(mode="json")

    @app.post("/api/memory/{memory_id}/reject")
    def reject_memory_candidate(
        memory_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.review")
        memory = app.state.long_term_memory_service.reject(
            context.tenant_id,
            memory_id,
            reviewed_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=memory.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="memory.rejected",
            metadata=memory_audit_metadata(memory),
            request=request,
        )
        return memory.model_dump(mode="json")

    @app.post("/api/knowledge-bases", status_code=status.HTTP_201_CREATED)
    def create_knowledge_base(
        payload: KnowledgeBaseCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "knowledge.write")
        knowledge_base = app.state.knowledge_service.create_base(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            request=payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=knowledge_base.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="knowledge.base.created",
            metadata=knowledge_base_audit_metadata(knowledge_base),
            request=request,
        )
        return knowledge_base.model_dump(mode="json")

    @app.post("/api/knowledge-documents", status_code=status.HTTP_201_CREATED)
    def register_knowledge_document(
        payload: KnowledgeDocumentApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "knowledge.write")
        document_content = knowledge_document_storage_content(payload)
        storage_object = app.state.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=context.tenant_id,
                workspace_id=payload.workspace_id,
                purpose=StoragePurpose.KNOWLEDGE_DOCUMENT,
                filename=knowledge_document_storage_filename(payload),
                content_type=payload.content_type,
                size_bytes=len(document_content),
                acl_subjects=payload.acl_subjects,
                sensitivity_level=payload.sensitivity_level,
            )
        )
        upload_storage_object(
            app=app,
            storage_object=storage_object,
            content=document_content,
            request=request,
            context=context,
        )
        document = app.state.knowledge_service.register_document(
            KnowledgeDocumentCreate(
                tenant_id=context.tenant_id,
                uploaded_by_user_id=context.user_id,
                storage_object_id=storage_object.id,
                **payload.model_dump(exclude={"content", "content_type"}),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=document.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="knowledge.document.registered",
            metadata=knowledge_document_audit_metadata(document, len(payload.chunks)),
            request=request,
        )
        return document.model_dump(mode="json")

    @app.post("/api/knowledge/query")
    def query_knowledge(
        payload: KnowledgeQueryRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "knowledge.read")
        results = app.state.knowledge_service.retrieve(
            RetrievalRequest(
                tenant_id=context.tenant_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="knowledge.query.executed",
            metadata=knowledge_query_audit_metadata(payload, results),
            request=request,
        )
        return [result.model_dump(mode="json") for result in results]

    @app.post("/api/sandbox/sessions", status_code=status.HTTP_201_CREATED)
    def create_sandbox_session(
        payload: SandboxSessionCreateRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.create")
        session = app.state.sandbox_adapter.create(
            SandboxCreateRequest(
                tenant_id=context.tenant_id,
                workspace_id=payload.workspace_id,
                run_id=payload.run_id,
                image=payload.image or app.state.settings.sandbox_runtime_image,
                network_mode=payload.network_mode
                or SandboxNetworkMode(app.state.settings.sandbox_network_mode),
                timeout_seconds=payload.timeout_seconds
                or app.state.settings.sandbox_timeout_seconds,
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=existing_run_id_or_none(app, context.tenant_id, session.run_id),
            event_type="sandbox.session.created",
            metadata=sandbox_session_audit_metadata(session),
            request=request,
        )
        return session.model_dump(mode="json")

    @app.post("/api/sandbox/sessions/{session_id}/commands")
    def execute_sandbox_command(
        session_id: str,
        payload: SandboxCommandRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        session = app.state.sandbox_adapter.get_session(context.tenant_id, session_id)
        timeout_seconds = payload.timeout_seconds or app.state.settings.sandbox_timeout_seconds
        result = app.state.sandbox_adapter.execute(
            SandboxCommand(
                tenant_id=context.tenant_id,
                workspace_id=session.workspace_id,
                run_id=session.run_id,
                session_id=session_id,
                command=payload.command,
                cwd=payload.cwd,
                timeout_seconds=timeout_seconds,
                env=payload.env,
            )
        )
        run_id = existing_run_id_or_none(app, context.tenant_id, session.run_id)
        command_output_storage_object = None
        if run_id is not None:
            command_output_content = sandbox_command_output_content(result)
            command_output_storage_object = app.state.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=context.tenant_id,
                    workspace_id=session.workspace_id,
                    run_id=run_id,
                    purpose=StoragePurpose.SANDBOX_COMMAND_OUTPUT,
                    filename=sandbox_command_output_filename(result),
                    content_type="application/json",
                    size_bytes=len(command_output_content),
                )
            )
            upload_storage_object(
                app=app,
                storage_object=command_output_storage_object,
                content=command_output_content,
                request=request,
                context=context,
            )
            result = result.model_copy(
                update={"output_uri": command_output_storage_object.uri}
            )
        metadata = sandbox_command_audit_metadata(
            session=session,
            payload=payload,
            timeout_seconds=timeout_seconds,
            exit_code=result.exit_code,
        )
        if command_output_storage_object is not None:
            metadata["storage_object_id"] = command_output_storage_object.id
        if run_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=run_id,
                meter_type="sandbox_minutes",
                quantity=timeout_seconds / 60,
                unit="minute",
                metadata=metadata,
            )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=run_id,
            event_type="sandbox.command.executed",
            metadata=metadata,
            request=request,
        )
        return result.model_dump(mode="json")

    @app.post("/api/sandbox/sessions/{session_id}/files", status_code=status.HTTP_201_CREATED)
    def upload_sandbox_file(
        session_id: str,
        payload: SandboxFileWriteRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        session = app.state.sandbox_adapter.get_session(context.tenant_id, session_id)
        file_ref = app.state.sandbox_adapter.upload_file(
            SandboxFileWrite(
                tenant_id=context.tenant_id,
                workspace_id=session.workspace_id,
                run_id=session.run_id,
                session_id=session.id,
                path=payload.path,
                content=payload.content,
                content_type=payload.content_type,
            )
        )
        run_id = existing_run_id_or_none(app, context.tenant_id, session.run_id)
        file_storage_object = None
        if run_id is not None:
            file_storage_object = app.state.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=context.tenant_id,
                    workspace_id=session.workspace_id,
                    run_id=run_id,
                    purpose=StoragePurpose.SANDBOX_FILE,
                    filename=sandbox_file_storage_filename(file_ref.path),
                    content_type=file_ref.content_type,
                    size_bytes=file_ref.size_bytes,
                )
            )
            upload_storage_object(
                app=app,
                storage_object=file_storage_object,
                content=sandbox_file_content(file_ref),
                request=request,
                context=context,
            )
        metadata = sandbox_file_audit_metadata(file_ref)
        if file_storage_object is not None:
            metadata["storage_object_id"] = file_storage_object.id
        if run_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=run_id,
                meter_type="artifact_bytes",
                quantity=file_ref.size_bytes,
                unit="byte",
                metadata=metadata,
            )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=run_id,
            event_type="sandbox.file.uploaded",
            metadata=metadata,
            request=request,
        )
        return file_ref.model_dump(mode="json")

    @app.get("/api/sandbox/sessions/{session_id}/files")
    def download_sandbox_file(
        session_id: str,
        path: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        session = app.state.sandbox_adapter.get_session(context.tenant_id, session_id)
        file_ref = app.state.sandbox_adapter.download_file(
            tenant_id=context.tenant_id,
            session_id=session_id,
            path=path,
        )
        run_id = existing_run_id_or_none(app, context.tenant_id, session.run_id)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=run_id,
            event_type="sandbox.file.downloaded",
            metadata=sandbox_file_audit_metadata(file_ref),
            request=request,
        )
        return file_ref.model_dump(mode="json")

    @app.post("/api/sandbox/sessions/{session_id}/snapshot")
    def create_sandbox_snapshot(
        session_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        snapshot = app.state.sandbox_adapter.snapshot(context.tenant_id, session_id)
        run_id = existing_run_id_or_none(app, context.tenant_id, snapshot.run_id)
        snapshot_storage_object = None
        if run_id is not None:
            snapshot_storage_object = app.state.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=context.tenant_id,
                    workspace_id=snapshot.workspace_id,
                    run_id=run_id,
                    purpose=StoragePurpose.SANDBOX_SNAPSHOT,
                    filename="snapshot.json",
                    content_type="application/json",
                    size_bytes=0,
                )
            )
            snapshot = snapshot.model_copy(
                update={"uri": snapshot_storage_object.uri}
            )
            snapshot_content = sandbox_snapshot_content(snapshot)
            snapshot_storage_object = app.state.storage_catalog.mark_uploaded(
                context.tenant_id,
                snapshot_storage_object.id,
                len(snapshot_content),
            )
            upload_storage_object(
                app=app,
                storage_object=snapshot_storage_object,
                content=snapshot_content,
                request=request,
                context=context,
            )
        metadata = sandbox_snapshot_audit_metadata(snapshot)
        if snapshot_storage_object is not None:
            metadata["storage_object_id"] = snapshot_storage_object.id
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=snapshot.workspace_id,
            user_id=context.user_id,
            run_id=run_id,
            event_type="sandbox.snapshot.created",
            metadata=metadata,
            request=request,
        )
        return snapshot.model_dump(mode="json")

    @app.delete("/api/sandbox/sessions/{session_id}")
    def destroy_sandbox_session(
        session_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        destroyed = app.state.sandbox_adapter.destroy(context.tenant_id, session_id)
        run_id = existing_run_id_or_none(app, context.tenant_id, destroyed.run_id)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=destroyed.workspace_id,
            user_id=context.user_id,
            run_id=run_id,
            event_type="sandbox.session.destroyed",
            metadata=sandbox_session_audit_metadata(destroyed),
            request=request,
        )
        return destroyed.model_dump(mode="json")

    @app.post("/api/browser/sessions/{session_id}/actions")
    def apply_browser_action(
        session_id: str,
        payload: BrowserActionRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "browser.act")
        session = app.state.sandbox_adapter.get_session(context.tenant_id, session_id)
        if session.status != SandboxSessionStatus.ACTIVE:
            raise SandboxExecutionError(f"Sandbox session is not active: {session_id}")
        ensure_browser_session(app, session)
        action = BrowserAction(
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            action_type=payload.action_type,
            url=payload.url,
            selector=payload.selector,
            text=payload.text,
            metadata=payload.metadata,
        )
        observation = app.state.browser_controller.apply(action)
        run_id = existing_run_id_or_none(app, context.tenant_id, session.run_id)
        screenshot_storage_object = None
        if run_id is not None and observation.screenshot_uri is not None:
            screenshot_storage_object = app.state.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=context.tenant_id,
                    workspace_id=session.workspace_id,
                    run_id=run_id,
                    purpose=StoragePurpose.BROWSER_SCREENSHOT,
                    filename=f"{session.id}.png",
                    content_type="image/png",
                    size_bytes=0,
                )
            )
            observation = observation.model_copy(
                update={"screenshot_uri": screenshot_storage_object.uri}
            )
            if observation.screenshot_content is not None:
                screenshot_storage_object = app.state.storage_catalog.mark_uploaded(
                    context.tenant_id,
                    screenshot_storage_object.id,
                    len(observation.screenshot_content),
                )
                upload_storage_object(
                    app=app,
                    storage_object=screenshot_storage_object,
                    content=observation.screenshot_content,
                    request=request,
                    context=context,
                )
        metadata = browser_action_audit_metadata(action, observation)
        if screenshot_storage_object is not None:
            metadata["storage_object_id"] = screenshot_storage_object.id
        if run_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=run_id,
                meter_type="browser_action_count",
                quantity=1,
                unit="action",
                metadata=metadata,
            )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=run_id,
            event_type="browser.action.performed",
            metadata=metadata,
            request=request,
        )
        return observation.model_dump(mode="json")

    return app


def build_job_queue(settings: Settings) -> JobQueue | None:
    if settings.job_queue_backend == "redis":
        return RedisJobQueue(url=settings.redis_url)
    return None


def build_browser_controller(settings: Settings) -> BrowserController:
    return BrowserController(provider=settings.browser_provider)


def build_run_trace_service(settings: Settings) -> RunTraceService:
    if settings.trace_exporter_backend == "otlp_http":
        return RunTraceService(
            exporter=OtlpHttpTraceExporter(
                endpoint_url=settings.trace_exporter_endpoint_url,
                api_key=settings.trace_exporter_api_key,
                timeout_seconds=settings.trace_exporter_timeout_seconds,
                service_name=settings.trace_exporter_service_name,
                deployment_environment=settings.environment,
            )
        )
    return RunTraceService()


def build_model_policy_store(settings: Settings) -> ModelPolicyStore:
    if settings.model_gateway_policy_store_backend == "sql":
        config = DatabaseConfig(url=settings.database_url)
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlModelPolicyStore(config=config)
    return InMemoryModelPolicyStore()


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


def build_control_plane_store(settings: Settings) -> InMemoryControlPlaneStore | SqlControlPlaneRepository:
    if settings.control_plane_store_backend == "sql":
        repository = SqlControlPlaneRepository(config=DatabaseConfig(url=settings.database_url))
        repository.initialize_schema(Path("apps/api/migrations"))
        return repository
    return InMemoryControlPlaneStore()


def build_identity_service(
    settings: Settings,
    audit_service: AuditService | None = None,
) -> InMemoryIdentityService | SqlIdentityService:
    password_hasher = PasswordHasher(
        iterations=settings.password_hash_iterations,
        salt=settings.password_hash_salt,
    )
    if settings.identity_service_backend == "sql":
        return SqlIdentityService(
            config=DatabaseConfig(url=settings.database_url),
            password_hasher=password_hasher,
            audit_service=audit_service,
        )
    return InMemoryIdentityService(
        password_hasher=password_hasher,
        audit_service=audit_service,
    )


def build_auth_session_store(settings: Settings) -> AuthSessionStore:
    if settings.auth_session_backend == "sql" or (
        settings.auth_session_backend == "auto" and settings.identity_service_backend == "sql"
    ):
        return SqlAuthSessionStore(config=DatabaseConfig(url=settings.database_url))
    return InMemoryAuthSessionStore()


def build_storage_catalog(settings: Settings) -> InMemoryStorageCatalog | SqlStorageCatalog:
    if settings.storage_catalog_backend == "sql":
        return SqlStorageCatalog(
            config=DatabaseConfig(url=settings.database_url),
            bucket=settings.object_storage_bucket,
        )
    return InMemoryStorageCatalog(bucket=settings.object_storage_bucket)


def build_storage_content_scanner(settings: Settings) -> StorageContentScanner:
    return StorageContentScanner(
        blocked_terms=settings.object_storage_content_scan_blocked_terms,
    )


def build_lifecycle_policy_store(
    settings: Settings,
) -> InMemoryLifecyclePolicyStore | SqlLifecyclePolicyStore:
    if settings.lifecycle_policy_backend == "sql":
        MigrationRunner(
            config=DatabaseConfig(url=settings.database_url),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlLifecyclePolicyStore(config=DatabaseConfig(url=settings.database_url))
    return InMemoryLifecyclePolicyStore()


def build_tenant_offboarding_store(
    settings: Settings,
) -> InMemoryTenantOffboardingStore | SqlTenantOffboardingStore:
    if settings.lifecycle_policy_backend == "sql":
        MigrationRunner(
            config=DatabaseConfig(url=settings.database_url),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlTenantOffboardingStore(config=DatabaseConfig(url=settings.database_url))
    return InMemoryTenantOffboardingStore()


def build_storage_lifecycle_service(app: FastAPI) -> StorageLifecycleService:
    return StorageLifecycleService(
        storage_catalog=app.state.storage_catalog,
        object_storage=app.state.object_storage,
        audit_service=app.state.audit_service,
        lifecycle_policy_store=app.state.lifecycle_policy_store,
    )


def build_data_export_service(app: FastAPI) -> DataExportService:
    return DataExportService(
        storage_catalog=app.state.storage_catalog,
        object_storage=app.state.object_storage,
        lifecycle_policy_store=app.state.lifecycle_policy_store,
    )


def build_backup_manifest_service(app: FastAPI) -> BackupManifestService:
    return BackupManifestService(settings=app.state.settings)


def build_data_residency_service(app: FastAPI) -> DataResidencyService:
    return DataResidencyService(settings=app.state.settings)


def build_tenant_offboarding_service(app: FastAPI) -> TenantOffboardingService:
    return TenantOffboardingService(
        lifecycle_policy_store=app.state.lifecycle_policy_store,
        offboarding_store=app.state.tenant_offboarding_store,
    )


def build_tenant_offboarding_deletion_service(app: FastAPI) -> TenantOffboardingDeletionService:
    return TenantOffboardingDeletionService(
        lifecycle_policy_store=app.state.lifecycle_policy_store,
        offboarding_store=app.state.tenant_offboarding_store,
        storage_catalog=app.state.storage_catalog,
        object_storage=app.state.object_storage,
        long_term_memory_service=app.state.long_term_memory_service,
        short_term_memory_service=app.state.short_term_memory_service,
        knowledge_service=app.state.knowledge_service,
    )


def build_knowledge_service(settings: Settings) -> InMemoryKnowledgeService | SqlKnowledgeService:
    if settings.knowledge_service_backend == "sql":
        MigrationRunner(
            config=DatabaseConfig(url=settings.database_url),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlKnowledgeService(config=DatabaseConfig(url=settings.database_url))
    return InMemoryKnowledgeService()


def build_skill_registry(settings: Settings) -> InMemorySkillRegistry | SqlSkillRegistry:
    if settings.skill_registry_backend == "sql":
        repository = SqlSkillRegistry(config=DatabaseConfig(url=settings.database_url))
        MigrationRunner(
            config=DatabaseConfig(url=settings.database_url),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return repository
    return InMemorySkillRegistry()


def build_long_term_memory_service(
    settings: Settings,
) -> InMemoryLongTermMemoryService | SqlLongTermMemoryService:
    if settings.long_term_memory_backend == "sql":
        MigrationRunner(
            config=DatabaseConfig(url=settings.database_url),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlLongTermMemoryService(config=DatabaseConfig(url=settings.database_url))
    return InMemoryLongTermMemoryService()


def build_guardrail_service(settings: Settings) -> InMemoryGuardrailService:
    detectors = []
    if settings.guardrail_secret_detector_enabled:
        detectors.append(
            GuardrailSecretPatternDetector(
                action=GuardrailAction(settings.guardrail_secret_detector_action),
                stages=[
                    GuardrailStage(stage)
                    for stage in settings.guardrail_secret_detector_stages
                ],
            )
        )
    if settings.guardrail_prompt_threat_detector_enabled:
        detectors.append(
            GuardrailPromptThreatDetector(
                action=GuardrailAction(settings.guardrail_prompt_threat_detector_action),
                stages=[
                    GuardrailStage(stage)
                    for stage in settings.guardrail_prompt_threat_detector_stages
                ],
            )
        )
    if settings.guardrail_http_detector_enabled:
        detectors.append(
            GuardrailHttpDetector(
                endpoint_url=settings.guardrail_http_detector_url,
                api_key=settings.guardrail_http_detector_api_key,
                timeout_seconds=settings.guardrail_http_detector_timeout_seconds,
                failure_action=GuardrailAction(
                    settings.guardrail_http_detector_failure_action
                ),
                stages=[
                    GuardrailStage(stage)
                    for stage in settings.guardrail_http_detector_stages
                ],
            )
        )
    return InMemoryGuardrailService(detectors=detectors)


def guard_long_term_memory_service(
    service: InMemoryLongTermMemoryService | SqlLongTermMemoryService | GuardedLongTermMemoryService,
    guardrail_service: InMemoryGuardrailService,
    audit_service: Any | None,
) -> GuardedLongTermMemoryService:
    if isinstance(service, GuardedLongTermMemoryService):
        return service
    return GuardedLongTermMemoryService(
        service=service,
        guardrail_service=guardrail_service,
        audit_service=audit_service,
    )


def build_short_term_memory_service(
    settings: Settings,
) -> InMemoryShortTermMemoryService | RedisShortTermMemoryService:
    if settings.short_term_memory_backend == "redis":
        return RedisShortTermMemoryService(url=settings.redis_url)
    return InMemoryShortTermMemoryService()


def guard_short_term_memory_service(
    service: InMemoryShortTermMemoryService | RedisShortTermMemoryService | GuardedShortTermMemoryService,
    guardrail_service: InMemoryGuardrailService,
    audit_service: Any | None,
    review_store: Any,
) -> GuardedShortTermMemoryService:
    if isinstance(service, GuardedShortTermMemoryService):
        return service
    return GuardedShortTermMemoryService(
        service=service,
        guardrail_service=guardrail_service,
        audit_service=audit_service,
        review_store=review_store,
    )


def build_short_term_memory_review_store(settings: Settings):
    if settings.control_plane_store_backend == "sql":
        MigrationRunner(
            config=DatabaseConfig(url=settings.database_url),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlShortTermMemoryReviewStore(config=DatabaseConfig(url=settings.database_url))
    return InMemoryShortTermMemoryReviewStore()


def memory_audit_metadata(memory) -> dict:
    return {
        "memory_id": memory.id,
        "workspace_id": memory.workspace_id,
        "scope_type": memory.scope_type.value,
        "scope_id": memory.scope_id,
        "source_run_id": memory.source_run_id,
        "status": memory.status.value,
        "sensitivity_level": memory.sensitivity_level,
    }


def short_term_memory_review_audit_metadata(review) -> dict:
    guardrail_metadata = review.guardrail_metadata
    return {
        "short_term_memory_review_id": review.id,
        "workspace_id": review.workspace_id,
        "run_id": review.run_id,
        "key_length": len(review.key),
        "ttl_seconds": review.ttl_seconds,
        "status": review.status.value,
        "guardrail_action": guardrail_metadata.get("guardrail_action"),
        "guardrail_rule_ids": guardrail_metadata.get("guardrail_rule_ids", []),
        "guardrail_detector_finding_ids": guardrail_metadata.get(
            "guardrail_detector_finding_ids",
            [],
        ),
        "approved_by_user_id": review.approved_by_user_id,
        "rejected_by_user_id": review.rejected_by_user_id,
    }


def knowledge_base_audit_metadata(knowledge_base) -> dict:
    return {
        "knowledge_base_id": knowledge_base.id,
        "workspace_id": knowledge_base.workspace_id,
        "name": knowledge_base.name,
        "created_by_user_id": knowledge_base.created_by_user_id,
    }


def knowledge_document_audit_metadata(document, chunk_count: int) -> dict:
    metadata = {
        "document_id": document.id,
        "workspace_id": document.workspace_id,
        "knowledge_base_id": document.knowledge_base_id,
        "source_document_id": document.source_document_id,
        "source_uri": document.source_uri,
        "uploaded_by_user_id": document.uploaded_by_user_id,
        "title": document.title,
        "acl_subjects": document.acl_subjects,
        "sensitivity_level": document.sensitivity_level,
        "document_version": document.document_version,
        "content_hash": document.content_hash,
        "chunk_count": chunk_count,
    }
    if document.storage_object_id is not None:
        metadata["storage_object_id"] = document.storage_object_id
    return metadata


def knowledge_document_storage_content(payload: KnowledgeDocumentApiCreate) -> bytes:
    content = payload.content
    if content is None:
        content = "\n\n".join(chunk.content for chunk in payload.chunks)
    return content.encode("utf-8")


def knowledge_document_storage_filename(payload: KnowledgeDocumentApiCreate) -> str:
    filename = payload.source_uri.rstrip("/").rsplit("/", 1)[-1]
    return filename or f"{payload.source_document_id}.txt"


def knowledge_query_audit_metadata(payload: KnowledgeQueryRequest, results) -> dict:
    return {
        "query_length": len(payload.query),
        "allowed_workspace_ids": payload.allowed_workspace_ids,
        "acl_subjects": payload.acl_subjects,
        "clearance_level": payload.clearance_level,
        "limit": payload.limit,
        "result_count": len(results),
        "document_ids": [result.document_id for result in results],
        "chunk_ids": [result.chunk_id for result in results],
        "source_document_ids": [result.source_document_id for result in results],
    }


def storage_audit_metadata(storage_object) -> dict:
    return storage_object_audit_metadata(storage_object)


def lifecycle_policy_audit_metadata(policy) -> dict:
    return {
        "lifecycle_policy_id": policy.id,
        "workspace_id": policy.workspace_id,
        "category": policy.category.value,
        "retention_days": policy.retention_days,
        "deletion_behavior": policy.deletion_behavior.value,
        "exportable": policy.exportable,
        "residency_region": policy.residency_region,
        "backup_class": policy.backup_class,
        "legal_hold_supported": policy.legal_hold_supported,
    }


def storage_cleanup_preview_audit_metadata(result) -> dict:
    return {
        "workspace_id": result.workspace_id,
        "dry_run": True,
        "deleted_count": result.deleted_count,
        "storage_object_ids": result.storage_object_ids,
        "skipped_count": result.skipped_count,
        "skipped_storage_object_ids": result.skipped_storage_object_ids,
        "would_delete_count": result.would_delete_count,
        "would_delete_storage_object_ids": result.would_delete_storage_object_ids,
    }


def data_export_manifest_audit_metadata(manifest) -> dict:
    return {
        "data_export_id": manifest.id,
        "workspace_id": manifest.workspace_id,
        "run_id": manifest.run_id,
        "categories": [category.value for category in manifest.categories],
        "item_count": manifest.item_count,
        "total_size_bytes": manifest.total_size_bytes,
    }


def data_export_bundle_audit_metadata(bundle) -> dict:
    return {
        "data_export_bundle_id": bundle.id,
        "data_export_id": bundle.manifest.id,
        "workspace_id": bundle.workspace_id,
        "run_id": bundle.run_id,
        "storage_object_id": bundle.storage_object_id,
        "uri": bundle.uri,
        "categories": [category.value for category in bundle.manifest.categories],
        "item_count": bundle.manifest.item_count,
        "total_size_bytes": bundle.manifest.total_size_bytes,
        "bundle_size_bytes": bundle.size_bytes,
    }


def backup_manifest_audit_metadata(manifest) -> dict:
    return {
        "backup_manifest_id": manifest.id,
        "environment": manifest.environment,
        "component_count": len(manifest.components),
        "component_types": [component.type.value for component in manifest.components],
        "restore_order": manifest.restore_order,
    }


def data_residency_report_audit_metadata(report) -> dict:
    disallowed_checks = [check for check in report.checks if not check.allowed]
    return {
        "data_residency_report_id": report.id,
        "environment": report.environment,
        "primary_region": report.primary_region,
        "allowed_regions": report.allowed_regions,
        "cross_region_replication_mode": report.cross_region_replication_mode,
        "compliant": report.compliant,
        "check_count": len(report.checks),
        "disallowed_count": len(disallowed_checks),
        "checked_resource_types": [check.resource_type.value for check in report.checks],
        "disallowed_resource_types": [
            check.resource_type.value for check in disallowed_checks
        ],
        "checked_regions": sorted({check.region for check in report.checks}),
    }


def tenant_offboarding_audit_metadata(plan) -> dict:
    return {
        "tenant_offboarding_plan_id": plan.id,
        "state": plan.state.value,
        "approval_required": plan.approval_required,
        "approval_status": plan.approval_status.value,
        "next_state_after_approval": (
            plan.next_state_after_approval.value
            if plan.next_state_after_approval is not None
            else None
        ),
        "export_before_delete": plan.export_before_delete,
        "category_count": len(plan.categories),
        "categories": [category.value for category in plan.categories],
        "reason_length": plan.reason_length,
        "blocked_reason": plan.blocked_reason,
        "blocking_legal_hold_count": len(plan.blocking_legal_hold_ids),
        "deletion_scope": plan.deletion_scope,
        "approved_by_user_id": plan.approved_by_user_id,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at is not None else None,
        "export_bundle_id": plan.export_bundle_id,
        "export_storage_object_id": plan.export_storage_object_id,
        "export_completed_by_user_id": plan.export_completed_by_user_id,
        "export_completed_at": (
            plan.export_completed_at.isoformat()
            if plan.export_completed_at is not None
            else None
        ),
        "deleted_by_user_id": plan.deleted_by_user_id,
        "deleted_at": plan.deleted_at.isoformat() if plan.deleted_at is not None else None,
    }


def tenant_offboarding_export_audit_metadata(plan, bundle) -> dict:
    metadata = tenant_offboarding_audit_metadata(plan)
    metadata.update(
        {
            "data_export_bundle_id": bundle.id,
            "filename": bundle.filename,
            "content_type": bundle.content_type,
            "size_bytes": bundle.size_bytes,
            "item_count": bundle.manifest.item_count,
            "total_size_bytes": bundle.manifest.total_size_bytes,
        }
    )
    return metadata


def tenant_offboarding_deletion_event_type(result) -> str:
    if result.plan.state == TenantOffboardingState.BLOCKED:
        return "lifecycle.offboarding.deletion_blocked"
    return "lifecycle.offboarding.deleted"


def tenant_offboarding_deletion_audit_metadata(result) -> dict:
    metadata = tenant_offboarding_audit_metadata(result.plan)
    metadata.update(
        {
            "deleted_storage_object_count": result.deleted_count,
            "skipped_storage_object_count": result.skipped_count,
            "legal_hold_count": result.legal_hold_count,
            "preserved_storage_object_count": len(result.preserved_storage_object_ids),
            "deleted_memory_record_count": result.deleted_memory_record_count,
            "deleted_short_term_memory_count": result.deleted_short_term_memory_count,
            "deleted_knowledge_base_count": result.deleted_knowledge_base_count,
            "deleted_knowledge_document_count": result.deleted_knowledge_document_count,
            "deleted_knowledge_chunk_count": result.deleted_knowledge_chunk_count,
        }
    )
    return metadata


def legal_hold_audit_metadata(hold) -> dict:
    return {
        "legal_hold_id": hold.id,
        "category": hold.category.value,
        "scope_type": hold.scope_type.value,
        "scope_id": hold.scope_id,
        "created_by_user_id": hold.created_by_user_id,
        "reason_length": len(hold.reason),
        "expires_at": hold.expires_at.isoformat() if hold.expires_at is not None else None,
        "released_at": hold.released_at.isoformat() if hold.released_at is not None else None,
    }


def upload_storage_object(
    app: FastAPI,
    storage_object,
    content: bytes,
    request: Request,
    context: RequestContext,
):
    scan_result = app.state.storage_content_scanner.scan(
        StorageContentScanRequest(
            storage_object=storage_object,
            content=content,
        )
    )
    if not scan_result.allowed:
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=storage_object.workspace_id,
            user_id=context.user_id,
            run_id=storage_object.run_id,
            event_type="storage.content_rejected",
            metadata={
                **storage_audit_metadata(storage_object),
                "matched_term_count": scan_result.matched_term_count,
            },
            request=request,
        )
        raise StorageContentRejectedError("storage content rejected by scan policy")
    return app.state.object_storage.upload(storage_object, content)


def sandbox_snapshot_content(snapshot) -> bytes:
    return json.dumps(
        snapshot.model_dump(mode="json"),
        sort_keys=True,
    ).encode("utf-8")


def existing_run_id_or_none(app: FastAPI, tenant_id: str, run_id: str) -> str | None:
    try:
        app.state.store.get_run(tenant_id, run_id)
    except NotFoundError:
        return None
    return run_id


def ensure_browser_session(app: FastAPI, session) -> None:
    try:
        app.state.browser_controller.get_session(session.tenant_id, session.id)
    except NotFoundError:
        app.state.browser_controller.open_session(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
        )


def sandbox_session_audit_metadata(session) -> dict:
    return {
        "session_id": session.id,
        "workspace_id": session.workspace_id,
        "run_id": session.run_id,
        "provider": session.provider,
        "image": session.image,
        "network_mode": session.network_mode.value,
        "timeout_seconds": session.timeout_seconds,
        "status": session.status.value,
    }


def sandbox_command_audit_metadata(
    session,
    payload: SandboxCommandRequest,
    timeout_seconds: int,
    exit_code: int,
) -> dict:
    return {
        "session_id": session.id,
        "workspace_id": session.workspace_id,
        "run_id": session.run_id,
        "provider": session.provider,
        "command": payload.command,
        "cwd": payload.cwd,
        "timeout_seconds": timeout_seconds,
        "env_keys": sorted(payload.env.keys()),
        "exit_code": exit_code,
    }


def sandbox_command_output_filename(result) -> str:
    created_at = result.created_at.strftime("%Y%m%d%H%M%S%f")
    return f"{result.session_id}-{created_at}.json"


def sandbox_command_output_content(result) -> bytes:
    return json.dumps(
        {
            "session_id": result.session_id,
            "workspace_id": result.workspace_id,
            "run_id": result.run_id,
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "created_at": result.created_at.isoformat(),
        },
        sort_keys=True,
    ).encode("utf-8")


def sandbox_file_audit_metadata(file_ref) -> dict:
    return {
        "session_id": file_ref.session_id,
        "workspace_id": file_ref.workspace_id,
        "run_id": file_ref.run_id,
        "path": file_ref.path,
        "content_type": file_ref.content_type,
        "size_bytes": file_ref.size_bytes,
    }


def sandbox_file_storage_filename(path: str) -> str:
    filename = path.rstrip("/").rsplit("/", 1)[-1]
    return filename or "sandbox-file"


def sandbox_file_content(file_ref) -> bytes:
    return (file_ref.content or "").encode("utf-8")


def sandbox_snapshot_audit_metadata(snapshot) -> dict:
    return {
        "snapshot_id": snapshot.id,
        "session_id": snapshot.session_id,
        "workspace_id": snapshot.workspace_id,
        "run_id": snapshot.run_id,
        "uri": snapshot.uri,
    }


def browser_action_audit_metadata(action: BrowserAction, observation) -> dict:
    text = action.text or ""
    return {
        "session_id": action.session_id,
        "workspace_id": action.workspace_id,
        "run_id": action.run_id,
        "action_type": action.action_type.value,
        "url": action.url,
        "selector": action.selector,
        "has_text": action.text is not None,
        "text_length": len(text),
        "current_url": observation.current_url,
        "screenshot_uri": observation.screenshot_uri,
        "metadata_keys": sorted(action.metadata.keys()),
    }


app = create_app()

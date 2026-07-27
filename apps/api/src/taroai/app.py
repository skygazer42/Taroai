import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import datetime, timedelta, timezone
from functools import partial
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlencode

import anyio.to_thread

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from taroai.agent import AgentRuntime, apply_agent_runtime_settings
from taroai.agent_engines import (
    AgentEngineApprovalDecision,
    AgentEngineConnectionCreate,
    AgentEngineConnectionPatch,
    AgentEngineRegistry,
    AgentEngineService,
    AgentEngineSessionCreate,
    AgentEngineTurn,
    InMemoryAgentEngineRegistry,
    SqlAgentEngineRegistry,
)
from taroai.agents import (
    AgentApiKey,
    AgentApiKeyCreate,
    AgentApiKeyService,
    AgentDefinitionCreate,
    AgentDefinitionPatch,
    AgentExtractRequest,
    AgentImportRequest,
    AgentRegistryService,
    AgentRunRequest,
    AgentVersionCreate,
    AgentVersionSpec,
    InMemoryAgentRegistry,
    InMemoryAgentApiKeyStore,
    PublicAgentRunCreated,
    PublicAgentRunRequest,
    PublicAgentRunResult,
    SqlAgentApiKeyStore,
    SqlAgentRegistry,
    register_agent_tool_handlers,
)
from taroai.api import ApiExceptionManager
from taroai.api.idempotency import (
    build_idempotency_request,
    find_idempotent_replay,
    save_idempotent_response,
)
from taroai.api.pagination import (
    PageRequest,
    SortDirection,
    paginate_created_at_records,
)
from taroai.auth import (
    AuthEmailVerificationRequest,
    AuthEmailVerificationSendRequest,
    AuthLoginRequest,
    AuthPasswordForgotRequest,
    AuthPasswordResetRequest,
    AuthRegisterRequest,
    AuthRequiredError,
    AuthService,
    AuthSessionStore,
    InMemoryAuthSessionStore,
    SqlAuthSessionStore,
    send_auth_email,
)
from taroai.audit import (
    AuditActor,
    AuditEventCreate,
    AuditService,
    DEFAULT_AUDIT_COVERAGE_REQUIREMENTS,
)
from taroai.billing import (
    BillingAnalyticsService,
    BillingInvoiceGroupBy,
    BillingInvoiceQuery,
    BillingInvoiceRecord,
    BillingInvoiceService,
    BillingInvoiceStore,
    BillingMeterQuery,
    BillingPricingRule,
    BillingPricingRuleApiUpsert,
    BillingPricingRuleStore,
    BillingPricingService,
    BillingSummaryGroupBy,
    BillingSummaryQuery,
    InMemoryBillingInvoiceStore,
    InMemoryBillingPricingRuleStore,
    SqlBillingInvoiceStore,
    SqlBillingPricingRuleStore,
)
from taroai.browser_profiles import (
    BrowserProfileCreate,
    BrowserProfilePatch,
    BrowserProfileRegistry,
    BrowserProfileService,
    BrowserProfileSessionCreate,
    InMemoryBrowserProfileRegistry,
    SqlBrowserProfileRegistry,
)
from taroai.chat import (
    ChatMessageEdit,
    ChatMessageSubmit,
    ChatService,
    ChatSteerSubmit,
    ChatThreadApiCreate,
    ChatThreadPatch,
    MessageDispatch,
)
from taroai.coding_workspaces import (
    CodingActionRequest,
    CodingChangesSubmit,
    CodingCheckpointCreate,
    CodingDeliveryCreate,
    CodingTestResultCreate,
    CodingWorkspaceCreate,
    CodingWorkspaceRegistry,
    CodingWorkspaceService,
    SqlCodingWorkspaceRegistry,
    RepositoryBindingCreate,
    RepositoryBindingPatch,
)
from taroai.artifacts import ArtifactService, ArtifactShareCreate, RichArtifactCreate
from taroai.config import ENTERPRISE_SANDBOX_PROVIDERS, Settings, load_settings
from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCreateRequest,
    ConnectorCredentialExpiredError,
    ConnectorCredentialRef,
    ConnectorDefinition,
    ConnectorDispatchError,
    ConnectorDispatchResult,
    ConnectorDispatchService,
    ConnectorInvocationCreate,
    ConnectorInvocationDecision,
    ConnectorInvocationService,
    ConnectorInvocationStatus,
    ConnectorOAuthCallbackRequest,
    ConnectorOAuthService,
    ConnectorStatus,
    ConnectorSyncJob,
    ConnectorSyncJobCreate,
    ConnectorSyncStateUpdate,
    ConnectorSyncStatus,
    ConnectorType,
    ConnectorUpdateRequest,
    InMemoryConnectorRegistry,
    RedisOAuthAuthorizationStateStore,
    SqlConnectorRegistry,
)
from taroai.customer_success import (
    CustomerFeedbackCreate,
    FeedbackCandidateStatus,
    InMemoryCustomerFeedbackService,
    InMemoryCustomerSuccessService,
    SqlCustomerFeedbackService,
)
from taroai.db import MigrationRunner, SqlControlPlaneRepository, close_database_pools
from taroai.domain import (
    ApprovalRequest,
    ApprovalStatus,
    ChatMessageDispatchStatus,
    ResourceReference,
    RunCreate,
    RunMode,
    RunStatus,
    new_id,
    utc_now,
)
from taroai.event_stream import ThreadEventHub
from taroai.embeddings import (
    EmbeddingGateway,
    EmbeddingGatewayRequest,
    EmbeddingUsageRecord,
    EmbeddingUsageRecorder,
    OpenAICompatibleEmbeddingGateway,
)
from taroai.evaluation import (
    AgentEvaluationExecutor,
    EvaluationRepository,
    EvaluationService,
    EvaluationSuite,
    EvaluationTargetKind,
    InMemoryEvaluationRepository,
    SqlEvaluationRepository,
    canonical_digest,
)
from taroai.guardrails import (
    GuardrailAction,
    GuardrailHttpDetector,
    GuardrailPromptThreatDetector,
    GuardrailSecretPatternDetector,
    GuardrailStage,
    InMemoryGuardrailService,
)
from taroai.knowledge import (
    DocumentChunkCreate,
    InMemoryKnowledgeService,
    KnowledgeBaseCreate,
    chunk_text_content,
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
    InMemoryRestoreDrillScheduleStore,
    InMemoryTenantOffboardingStore,
    LegalHoldApiCreate,
    LegalHoldCreate,
    LegalHoldScopeType,
    LifecyclePolicyApiUpsert,
    LifecyclePolicyCreate,
    RestoreDrillEvidenceValidationRequest,
    RestoreDrillRunEvidenceApiCreate,
    RestoreDrillRunExecutionApiCreate,
    RestoreDrillRunRecordApiUpdate,
    RestoreDrillRunStatus,
    RestoreDrillScheduleApiCreate,
    RestoreDrillScheduleApiUpdate,
    RestoreDrillScheduleCreate,
    RestoreDrillScheduleStore,
    SqlRestoreDrillScheduleStore,
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
    require_restore_drill_verification_result_ready,
    restore_drill_evidence_content,
    restore_drill_evidence_filename,
    validate_restore_drill_evidence_object,
)
from taroai.licensing import (
    LicenseImportRequest,
    LicenseImportResponse,
    LicenseService,
    LicenseStatus,
    LicensedFeature,
)
from taroai.memory import (
    GuardedLongTermMemoryService,
    GuardedShortTermMemoryService,
    InMemoryLongTermMemoryService,
    InMemoryShortTermMemoryReviewStore,
    InMemoryShortTermMemoryService,
    MemoryCandidateApiCreate,
    MemoryScopeType,
    MemoryStatus,
    MemoryWriteRequest,
    RedisShortTermMemoryService,
    ShortTermMemoryApiCreate,
    ShortTermMemoryReview,
    ShortTermMemoryReviewStatus,
    ShortTermMemoryWrite,
    SqlLongTermMemoryService,
    SqlShortTermMemoryReviewStore,
)
from taroai.memory.tools import register_memory_tool_handler
from taroai.model_gateway import (
    InMemoryModelProviderStore,
    InMemoryModelPolicyStore,
    ModelBudgetGuard,
    ModelBudgetPolicy,
    ModelGateway,
    ModelGatewayRouter,
    ModelProviderApiUpsert,
    ModelProviderChangeRequestApiCreate,
    ModelProviderChangeRequestRecord,
    ModelPolicyChangeRequestApiCreate,
    ModelPolicyChangeRequestRecord,
    ModelPolicyScopeApiUpsert,
    ModelPolicyStore,
    ModelPolicy,
    ModelPolicyVersionRecord,
    ModelProviderCredentialRotateRequest,
    ModelProviderConfig,
    ModelProviderRateLimiter,
    ModelProviderRecord,
    ModelProviderRegistry,
    ModelProviderStore,
    ModelProviderVersionRecord,
    OpenAICompatibleModelGateway,
    ModelPolicyDeniedError,
    RedisModelProviderRateLimitStore,
    SqlModelProviderRateLimitStore,
    SqlModelProviderStore,
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
    HttpBrowserController,
    LocalProcessSandboxAdapter,
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
    build_sandbox_adapter,
)
from taroai.sandbox.tools import (
    register_browser_tool_handlers,
    register_sandbox_tool_handlers,
)
from taroai.web_search import register_web_search_tool_handler
from taroai.observation_read import (
    OBSERVATION_READ_TOOL,
    register_observation_read_tool_handler,
)
from taroai.ui_render import UI_RENDER_TOOL, register_ui_render_tool_handler
from taroai.scim import (
    InMemoryScimProvisioningStore,
    ScimGroupRoleMapping,
    ScimImportRequest,
    ScimProviderCreate,
    ScimProvisioningService,
    SqlScimProvisioningStore,
)
from taroai.secrets import (
    AwsSecretsManagerSecretService,
    LocalEncryptedSecretService,
    SecretLeaseResolveRequest,
    SecretScope,
    SecretService,
    build_secret_service_from_settings,
)
from taroai.sharing import (
    InMemoryShareGrantStore,
    ShareGrantApiCreate,
    ShareGrantRevokeRequest,
    ShareGrantStore,
    ShareResourceType,
    ShareSubjectType,
    SqlShareGrantStore,
    share_grant_audit_metadata,
    InMemoryThreadShareStore,
    SqlThreadShareStore,
    ThreadShareCreate,
    ThreadShareService,
    ThreadShareStore,
)
from taroai.speech import (
    SpeechGateway,
    SpeechSummaryRequest,
    TextToSpeechRequest,
    TranscriptionRequest,
)
from taroai.skills import (
    InMemorySkillRegistry,
    SkillInstallationStatus,
    SkillManifest,
    SkillPackageKind,
    SkillStatus,
    SkillType,
    SqlSkillRegistry,
    register_skill_tool_handlers,
)
from taroai.skills.import_service import GithubSkillSource, HttpsGithubArchiveFetcher
from taroai.skills.evaluation import SkillEvaluationSuite
from taroai.skills.service import SkillService
from taroai.solution_packs import (
    InMemorySolutionPackRegistry,
    SolutionPackInstallRequest,
    SolutionPackManifest,
    SolutionPackService,
    SqlSolutionPackRegistry,
)
from taroai.sso import (
    InMemorySsoProviderRegistry,
    SqlSsoProviderRegistry,
    SsoProviderCreate,
)
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
    StorageObjectPatch,
    StorageLifecycleCleanupPreviewRequest,
    StorageLifecycleCleanupRequest,
    StorageLifecycleService,
    StoragePurpose,
    StorageSignedUrlCreate,
    storage_object_audit_metadata,
)
from taroai.store_catalog import (
    BuiltinStoreCatalog,
    StoreInstallConflictError,
    install_builtin_store_item,
)
from taroai.tool_gateway import ToolExecutionError, ToolGateway, ToolGatewayRequest
from taroai.tool_gateway.schema import JsonSchemaValidator
from taroai.triggers import (
    AgentHandoffRequest,
    AgentHandoffResponse,
    ConnectorEvent,
    ConnectorEventIngestRequest,
    ConnectorEventIngestResponse,
    ConnectorEventIngestRun,
    InMemoryTriggerStore,
    SqlTriggerStore,
    TriggerCreateRequest,
    TriggerDefinitionCreate,
    TriggerDisabledError,
    TriggerInvokeRequest,
    TriggerInvokeResponse,
    TriggerOperationsService,
    TriggerService,
    TriggerType,
    TriggerWebhookVerifier,
    assert_agent_handoff_allowed,
    match_connector_event_triggers,
)
from taroai.store import InMemoryControlPlaneStore, NotFoundError, TenantAccessError
from taroai.tenancy import (
    TenantInvitationAccept,
    TenantInvitationCreate,
    TenantOrganizationService,
    TenantPatch,
    WorkspaceCreate,
    WorkspacePatch,
)
from taroai.workers import (
    JobQueue,
    JobType,
    RedisJobQueue,
    RedisQueueConfigurationError,
    RestoreDrillExecutionJob,
    RunExecutionJob,
)
from taroai.workflow import WorkflowCoordinator, WorkflowPreviewUpdate


SANDBOX_SECRET_RESOLVER_TOKEN_HEADER = "X-Sandbox-Resolver-Token"
AGENT_RUN_SKILL_TYPES = {SkillType.WORKFLOW, SkillType.AGENT_TEMPLATE}


class RequestContext(BaseModel):
    tenant_id: str
    user_id: str


class RunExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillInvokeRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RunQueuedResponse(BaseModel):
    run_id: str
    job_id: str
    status: str = "queued"
    queue: str


def is_agent_run_skill(manifest: SkillManifest) -> bool:
    return manifest.type in AGENT_RUN_SKILL_TYPES


def build_agent_run_skill_message(
    manifest: SkillManifest,
    tool_input: dict[str, Any],
) -> str:
    return (
        "Run the installed enterprise skill below through the agent runtime.\n\n"
        f"Skill ID: {manifest.id}\n"
        f"Skill name: {manifest.name}\n"
        f"Skill description: {manifest.description}\n"
        f"Expected output schema: {json.dumps(manifest.output_schema, sort_keys=True)}\n"
        f"Input JSON: {json.dumps(tool_input, sort_keys=True)}"
    )


def skill_invocation_mode(manifest: SkillManifest) -> str:
    if is_agent_run_skill(manifest):
        return "agent_workflow"
    return "tool_gateway"


class RestoreDrillExecutionQueuedResponse(BaseModel):
    schedule_id: str
    run_record_id: str
    job_id: str
    status: str = "queued"
    queue: str


class ModelGatewayReadiness(BaseModel):
    configured: bool
    gateway_type: str
    base_url: str | None = None
    model: str | None = None
    provider_count: int = Field(default=0, ge=0)
    configured_provider_count: int = Field(default=0, ge=0)
    provider_ids: list[str] = Field(default_factory=list)
    configured_provider_ids: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    model_source: str = "none"
    credential_source: str = "none"


class SandboxReadiness(BaseModel):
    configured: bool
    provider: str
    controller_required: bool
    controller_configured: bool
    controller_endpoint_configured: bool
    controller_auth_configured: bool
    missing: list[str] = Field(default_factory=list)
    capabilities_checked: bool | None = None
    network_isolation_declared: bool | None = None
    filesystem_isolation_declared: bool | None = None
    resource_limits_declared: bool | None = None
    destroy_supported_declared: bool | None = None
    session_ttl_enforced_declared: bool | None = None
    runtime_isolation_declared: bool | None = None
    image_policy_enforced_declared: bool | None = None
    allowed_image_count: int | None = None
    max_session_ttl_seconds: int | None = None
    max_sessions: int | None = None
    max_sessions_per_tenant: int | None = None
    max_sessions_per_run: int | None = None


class BrowserReadiness(BaseModel):
    configured: bool
    provider: str
    controller_required: bool
    controller_configured: bool
    controller_endpoint_configured: bool
    controller_auth_configured: bool
    missing: list[str] = Field(default_factory=list)
    capabilities_checked: bool | None = None
    auth_required_declared: bool | None = None
    session_ttl_enforced_declared: bool | None = None
    max_session_ttl_seconds: int | None = None
    max_sessions: int | None = None
    max_sessions_per_tenant: int | None = None
    max_sessions_per_run: int | None = None
    navigation_allowlist_enforced_declared: bool | None = None
    navigation_allowed_host_count: int | None = None


RUN_CREATE_METHOD = "POST"
RUN_CREATE_PATH = "/api/runs"
RUN_APPROVAL_METHOD = "POST"
TRIGGER_WEBHOOK_METHOD = "POST"
RESTORE_DRILL_EXECUTION_METHOD = "POST"
PAGE_QUERY_PARAMETERS = {"limit", "cursor", "sort_direction"}


def run_approval_path(run_id: str) -> str:
    return f"/api/runs/{run_id}/approvals"


def run_approval_reject_path(run_id: str) -> str:
    return f"/api/runs/{run_id}/approvals/reject"


def trigger_webhook_path(trigger_id: str) -> str:
    return f"/api/triggers/{trigger_id}/webhook"


def restore_drill_execution_path(schedule_id: str, run_record_id: str) -> str:
    return (
        f"/api/lifecycle/restore-drill-schedules/{schedule_id}"
        f"/runs/{run_record_id}/execute"
    )


class RunCancelRequest(BaseModel):
    reason_code: str = Field(
        default="user_requested",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_.-]+$",
    )


class RunRetryRequest(BaseModel):
    reason_code: str = Field(
        default="operator_retry",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_.-]+$",
    )


class UncertainActionResolutionRequest(BaseModel):
    resolution: str = Field(pattern=r"^(succeeded|failed|retry)$")
    note: str = Field(default="", max_length=1000)


class ConnectorReconnectRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class AtomicUploadRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1, max_length=34_000_000)
    acl_subjects: list[str] = Field(default_factory=list)
    sensitivity_level: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")


class ThreadSuggestionRequest(BaseModel):
    limit: int = Field(default=3, ge=1, le=6)


class SkillZipImportRequest(BaseModel):
    archive_base64: str = Field(min_length=1, max_length=45_000_000)
    workspace_id: str | None = Field(default=None, min_length=1)
    manifest: SkillManifest | None = None
    source_url: str | None = Field(default=None, max_length=2000)
    source_ref: str | None = Field(default=None, max_length=500)
    subdirectory: str | None = Field(default=None, max_length=500)


class SkillGithubImportRequest(BaseModel):
    source: GithubSkillSource
    manifest: SkillManifest | None = None
    workspace_id: str | None = Field(default=None, min_length=1)


class SkillPackagePublishRequest(BaseModel):
    evaluation_run_id: str | None = None


class SkillExactInstallRequest(BaseModel):
    version: str = Field(min_length=1)
    package_digest: str = Field(min_length=64, max_length=64)


class StoreItemInstallRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    expected_digest: str | None = Field(default=None, min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid")


class SkillVersionMoveRequest(BaseModel):
    target_version: str = Field(min_length=1)
    expected_package_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )


class SkillEvaluateRequest(BaseModel):
    workspace_id: str | None = Field(default=None, min_length=1)
    suite: SkillEvaluationSuite | None = None


class AgentEvaluationRunRequest(BaseModel):
    suite_id: str = Field(min_length=1)
    suite_version: str = Field(min_length=1)


class ApprovalResolveRequest(BaseModel):
    approval_id: str = Field(min_length=1)


class ApprovalRejectRequest(BaseModel):
    approval_id: str = Field(min_length=1)


class SecretCaptureResolveRequest(BaseModel):
    value: SecretStr = Field(min_length=1, max_length=100_000)

    model_config = ConfigDict(extra="forbid")


class CustomerSuccessCandidateCreate(BaseModel):
    minimum_repeated_feedback: int = Field(default=3, ge=1)


class CustomerSuccessCandidateReview(BaseModel):
    status: FeedbackCandidateStatus
    review_note: str | None = None


class CustomerSuccessSolutionPackDraftUpdate(BaseModel):
    requested_skill_name: str | None = Field(default=None, min_length=1)
    proposed_change_summary: str | None = Field(default=None, min_length=1)
    proposed_pack_version: str | None = Field(default=None, min_length=1)
    proposed_skill_manifest: SkillManifest | None = None
    proposed_skill_manifests: list[SkillManifest] | None = None

    model_config = ConfigDict(extra="forbid")


class CustomerSuccessSolutionPackDraftReview(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    review_note: str | None = None

    model_config = ConfigDict(extra="forbid")


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


def get_billing_invoice_query(
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    group_by: BillingInvoiceGroupBy = "meter_type",
    run_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    skill_id: str | None = None,
    meter_type: str | None = None,
    currency: str = "USD",
) -> BillingInvoiceQuery:
    return BillingInvoiceQuery(
        period_start=period_start,
        period_end=period_end,
        group_by=group_by,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        agent_id=agent_id,
        skill_id=skill_id,
        meter_type=meter_type,
        currency=currency,
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


def get_page_request(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    sort_direction: SortDirection = Query(default=SortDirection.DESC),
) -> PageRequest:
    return PageRequest(
        limit=limit,
        cursor=cursor,
        sort_direction=sort_direction,
    )


def wants_page_response(request: Request) -> bool:
    return any(parameter in request.query_params for parameter in PAGE_QUERY_PARAMETERS)


def list_or_page_created_at_records(
    records: list[Any],
    request: Request,
    page: PageRequest,
) -> list[dict[str, Any]] | dict[str, Any]:
    if wants_page_response(request):
        return paginate_created_at_records(records, page).model_dump(mode="json")
    return [record.model_dump(mode="json") for record in records]


def public_thread_html(payload: dict[str, Any]) -> str:
    thread = payload["thread"]
    messages = (
        "".join(
            f"""<article class="message {escape(message["role"])}">
<header>{"You" if message["role"] == "user" else "Taroai"}<time>{escape(message["created_at"])}</time></header>
<div>{escape(message["content"])}</div></article>"""
            for message in payload["messages"]
        )
        or '<p class="empty">This conversation has no shared messages.</p>'
    )
    artifacts = "".join(
        f"<li><span>{escape(artifact['name'])}</span><small>{escape(artifact['artifact_type'])}</small></li>"
        for artifact in payload["artifacts"]
    )
    artifact_section = (
        f"<section class=artifacts><h2>Artifacts</h2><ul>{artifacts}</ul></section>"
        if artifacts
        else ""
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(thread["title"] or "Shared conversation")}</title><style>
:root{{color-scheme:light;background:#f8f6f2;color:#25221f;font:15px/1.65 Inter,ui-sans-serif,system-ui,sans-serif}}*{{box-sizing:border-box}}body{{margin:0}}main{{width:min(760px,calc(100% - 32px));margin:0 auto;padding:56px 0 96px}}.brand{{font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#8a8178}}h1{{margin:12px 0 6px;font:600 clamp(28px,5vw,42px)/1.15 Georgia,serif}}.meta{{margin:0 0 48px;color:#8a8178;font-size:12px}}.messages{{display:grid;gap:28px}}.message{{max-width:86%}}.message.user{{justify-self:end;background:#ebe7e1;border-radius:20px 20px 6px 20px;padding:14px 17px}}.message.assistant{{justify-self:start}}.message header{{display:flex;gap:10px;align-items:center;margin-bottom:7px;color:#69625c;font-size:11px;font-weight:700}}time{{color:#aaa199;font-weight:400}}.message div{{white-space:pre-wrap;overflow-wrap:anywhere}}.artifacts{{margin-top:52px;padding-top:22px;border-top:1px solid #ddd7cf}}h2{{font-size:13px}}ul{{display:grid;gap:8px;padding:0;list-style:none}}li{{display:flex;justify-content:space-between;padding:10px 12px;border:1px solid #ddd7cf;border-radius:10px;background:#fff}}li small,.empty{{color:#8a8178}}@media(max-width:600px){{main{{padding-top:32px}}.message{{max-width:94%}}}}
</style></head><body><main><div class="brand">Taroai · Shared conversation</div><h1>{escape(thread["title"] or "Shared conversation")}</h1><p class="meta">Read-only · Expires {escape(payload["expires_at"])}</p><section class="messages">{messages}</section>{artifact_section}</main></body></html>"""


def parse_webhook_json_payload(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("webhook body must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("webhook body must be a JSON object")
    return parsed


def select_webhook_idempotency_key(
    delivery_id: str | None,
    idempotency_key: str | None,
) -> str | None:
    return delivery_id or idempotency_key


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


def record_embedding_gateway_usage(
    app: FastAPI,
    request: Request | None,
    record: EmbeddingUsageRecord,
) -> None:
    EmbeddingUsageRecorder(
        store=app.state.store,
        audit_service=app.state.audit_service,
        billing_pricing_service=app.state.billing_pricing_service,
    ).record(
        record=record,
        actor=audit_actor_from_request(
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            request=request,
        ),
    )


def sso_provider_audit_metadata(entry) -> dict[str, Any]:
    provider = entry.provider
    oidc = provider.oidc
    saml = provider.saml
    return {
        "provider_id": provider.id,
        "protocol": provider.protocol.value,
        "status": entry.status.value,
        "domain_count": len(provider.domains),
        "password_fallback_enabled": provider.password_fallback_enabled,
        "jit_provisioning_enabled": provider.jit_provisioning_enabled,
        "default_role_count": len(provider.default_role_ids),
        "oidc_secret_ref_present": (
            oidc is not None and oidc.client_secret_ref_id is not None
        ),
        "saml_certificate_ref_present": saml is not None,
    }


def scim_provider_audit_metadata(entry) -> dict[str, Any]:
    provider = entry.provider
    return {
        "provider_id": provider.id,
        "status": entry.status.value,
        "default_role_count": len(provider.default_role_ids),
        "jit_create_users": provider.jit_create_users,
        "scim_token_ref_present": provider.bearer_token_secret_ref_id is not None,
    }


def scim_group_role_mapping_audit_metadata(entry) -> dict[str, Any]:
    return {
        "provider_id": entry.provider_id,
        "group_external_id": entry.mapping.group_external_id,
        "role_count": len(entry.mapping.role_ids),
    }


def scim_import_audit_metadata(result) -> dict[str, Any]:
    return {
        "provider_id": result.provider_id,
        "import_id": result.import_id,
        "users_seen": result.users_seen,
        "users_created": result.users_created,
        "users_linked": result.users_linked,
        "users_disabled": result.users_disabled,
        "roles_assigned": result.roles_assigned,
    }


def model_provider_audit_metadata(record: ModelProviderRecord) -> dict[str, Any]:
    provider = record.provider
    return {
        "provider_id": record.id,
        "provider_type": provider.provider_type,
        "status": record.status,
        "current_version": record.current_version,
        "workspace_id": provider.workspace_id,
        "default_model": provider.default_model,
        "model_count": len(provider.model_ids),
        "credential_source": _model_provider_credential_source(provider),
        "rate_limit_enabled": provider.rate_limit.enabled(),
        "fallback_enabled": provider.fallback_enabled,
    }


def model_provider_change_audit_metadata(
    record: ModelProviderChangeRequestRecord,
) -> dict[str, Any]:
    return {
        "change_request_id": record.id,
        "provider_id": record.provider_id,
        "operation": record.operation,
        "status": record.status,
        "requested_by_user_id": record.requested_by_user_id,
        "reviewed_by_user_id": record.reviewed_by_user_id,
        "has_provider_payload": record.provider_upsert is not None,
        "credential_source": "secret_ref" if record.api_key_secret_ref_id else "none",
        "rollback_version": record.rollback_version,
    }


def model_policy_change_audit_metadata(
    record: ModelPolicyChangeRequestRecord,
) -> dict[str, Any]:
    scope = record.scope_upsert
    return {
        "change_request_id": record.id,
        "operation": record.operation,
        "status": record.status,
        "workspace_id": scope.workspace_id,
        "default_model": scope.default_model,
        "allowed_model_count": len(scope.allowed_models),
        "denied_model_count": len(scope.denied_models),
        "model_sensitivity_limit_count": len(scope.model_sensitivity_limits),
        "requested_by_user_id": record.requested_by_user_id,
        "reviewed_by_user_id": record.reviewed_by_user_id,
    }


def refresh_runtime_model_gateway(app: FastAPI) -> None:
    app.state.runtime.model_gateway = build_model_gateway(
        app.state.settings,
        app.state.secret_service,
        app.state.model_provider_store,
    )


def refresh_runtime_model_policy(app: FastAPI) -> None:
    app.state.runtime.model_policy = build_model_policy(
        app.state.settings,
        app.state.model_policy_store,
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
        claims = request.app.state.auth_service.authenticate_authorization_header(
            authorization
        )
        return RequestContext(tenant_id=claims.tenant_id, user_id=claims.user_id)
    if (
        request.app.state.settings.dev_request_headers_enabled
        and tenant_id is not None
        and user_id is not None
    ):
        return RequestContext(tenant_id=tenant_id, user_id=user_id)
    raise AuthRequiredError("authentication required")


def get_agent_api_key(
    app_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AgentApiKey:
    return request.app.state.agent_api_key_service.authenticate(
        authorization, app_id
    )


def allowed_oauth_opener_origin(request: Request, settings: Settings) -> str | None:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin:
        return None
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    return origin if origin in allowed else None


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
        raise TenantAccessError(
            decision.reason or f"Permission denied: {action} on {resource}"
        )


def resolve_granted_scopes(
    request: Request,
    context: RequestContext,
    required_scopes: list[str],
) -> list[str]:
    resource = f"tenant:{context.tenant_id}"
    granted_scopes = []
    for scope in required_scopes:
        decision = request.app.state.policy_service.decide(
            PolicyRequest(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                action=scope,
                resource=resource,
            )
        )
        if decision.allowed:
            granted_scopes.append(scope)
    return granted_scopes


def require_sandbox_secret_resolver_token(request: Request) -> None:
    expected_token = request.app.state.settings.sandbox_secret_resolver_token
    if not expected_token:
        return
    provided_token = request.headers.get(SANDBOX_SECRET_RESOLVER_TOKEN_HEADER)
    if provided_token is None or not hmac.compare_digest(
        provided_token, expected_token
    ):
        raise TenantAccessError("sandbox secret resolver token is invalid")


def require_storage_read_access(
    request: Request,
    context: RequestContext,
    storage_object,
) -> None:
    clearance_level = storage_clearance_level(request)
    if clearance_level < storage_object.sensitivity_level:
        raise TenantAccessError(
            "Storage object sensitivity exceeds requester clearance"
        )
    if not storage_object.acl_subjects:
        return
    request_subjects = set(storage_acl_subjects(request, context))
    allowed_subjects = set(storage_object.acl_subjects)
    if not request_subjects.isdisjoint(allowed_subjects):
        return
    if storage_share_grant_allows_read(request, context, storage_object):
        return
    raise TenantAccessError("Storage object ACL denied")


def storage_acl_subjects(request: Request, context: RequestContext) -> list[str]:
    raw_subjects = request.headers.get("X-ACL-Subjects", "")
    subjects = [
        subject.strip() for subject in raw_subjects.split(",") if subject.strip()
    ]
    subjects.extend([f"user:{context.user_id}", f"tenant:{context.tenant_id}"])
    return subjects


def storage_share_grant_allows_read(
    request: Request,
    context: RequestContext,
    storage_object,
) -> bool:
    if storage_object.purpose != StoragePurpose.ARTIFACT:
        return False
    return request.app.state.share_grant_store.authorize(
        tenant_id=context.tenant_id,
        resource_type=ShareResourceType.ARTIFACT,
        resource_id=storage_object.id,
        permission="view",
        user_id=context.user_id,
        workspace_id=storage_object.workspace_id,
        group_ids=storage_share_group_ids(request),
    )


def require_external_share_links_enabled(request: Request) -> None:
    if not request.app.state.settings.external_share_links_enabled:
        raise TenantAccessError("External share links are disabled")


def external_share_link_subject_id(
    external_link_token: str,
    tenant_id: str,
    settings: Settings,
) -> str:
    hash_secret = (
        settings.external_share_link_token_hash_secret or settings.access_token_secret
    )
    digest_message = f"{tenant_id}\0{external_link_token}"
    digest = hmac.new(
        hash_secret.encode("utf-8"),
        digest_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def require_external_share_link_storage_read_access(
    request: Request,
    tenant_id: str,
    external_link_id: str,
    storage_object,
) -> None:
    require_external_share_links_enabled(request)
    if storage_object.purpose != StoragePurpose.ARTIFACT:
        raise TenantAccessError("External share links can only download artifacts")
    if storage_object.sensitivity_level > 0:
        raise TenantAccessError("External share link cannot access sensitive artifacts")
    allowed = request.app.state.share_grant_store.authorize(
        tenant_id=tenant_id,
        resource_type=ShareResourceType.ARTIFACT,
        resource_id=storage_object.id,
        permission="view",
        user_id="external_link",
        workspace_id=storage_object.workspace_id,
        external_link_id=external_share_link_subject_id(
            external_link_id,
            tenant_id,
            request.app.state.settings,
        ),
    )
    if not allowed:
        raise TenantAccessError("External share link denied")


def require_external_artifact_read_access(
    request: Request,
    tenant_id: str,
    external_link_id: str,
    artifact_id: str,
):
    require_external_share_links_enabled(request)
    artifact = request.app.state.store.get_artifact(tenant_id, artifact_id)
    if artifact.storage_object_id:
        storage_object = request.app.state.storage_catalog.get(
            tenant_id, artifact.storage_object_id
        )
        if storage_object.sensitivity_level > 0:
            raise TenantAccessError(
                "External share link cannot access sensitive artifacts"
            )
    allowed = request.app.state.share_grant_store.authorize(
        tenant_id=tenant_id,
        resource_type=ShareResourceType.ARTIFACT,
        resource_id=artifact.id,
        permission="view",
        user_id="external_link",
        workspace_id=artifact.workspace_id,
        external_link_id=external_share_link_subject_id(
            external_link_id, tenant_id, request.app.state.settings
        ),
    )
    if not allowed:
        raise TenantAccessError("External artifact share link denied")
    return artifact


def storage_share_group_ids(request: Request) -> list[str]:
    raw_subjects = request.headers.get("X-ACL-Subjects", "")
    group_ids: list[str] = []
    for subject in [item.strip() for item in raw_subjects.split(",") if item.strip()]:
        group_ids.append(subject)
        if subject.startswith("group:"):
            group_ids.append(subject.split(":", 1)[1])
    return group_ids


def storage_clearance_level(request: Request) -> int:
    raw_clearance = request.headers.get("X-Clearance-Level")
    if raw_clearance is None:
        return 0
    try:
        return int(raw_clearance)
    except ValueError as error:
        raise TenantAccessError("Invalid storage clearance level") from error


def share_grant_response_body(grant) -> dict:
    body = grant.model_dump(mode="json")
    if grant.subject_type == ShareSubjectType.EXTERNAL_LINK:
        body["subject_id"] = "[REDACTED]"
        body["external_link_id_present"] = True
    return body


def customer_feedback_response_body(feedback) -> dict:
    body = feedback.model_dump(mode="json", exclude={"comment", "metadata"})
    body["comment_present"] = feedback.comment is not None
    body["metadata_present"] = bool(feedback.metadata)
    body["metadata_key_count"] = len(feedback.metadata)
    return body


def resolve_event_replay_sequence(
    after_sequence: int | None,
    last_event_id: str | None,
) -> int | None:
    if after_sequence is not None:
        return after_sequence
    if last_event_id is None or not last_event_id.strip():
        return None
    try:
        return int(last_event_id)
    except ValueError as error:
        raise ValueError("Last-Event-ID must be an integer event sequence") from error


def stream_run_events(
    app: FastAPI,
    tenant_id: str,
    run_id: str,
    after_sequence: int | None,
    last_event_id: str | None,
) -> StreamingResponse:
    replay_after_sequence = resolve_event_replay_sequence(
        after_sequence, last_event_id
    )
    events = app.state.store.list_run_events(
        tenant_id,
        run_id,
        after_sequence=replay_after_sequence,
    )

    def stream() -> Iterator[str]:
        for event in events:
            payload = json.dumps(
                event.model_dump(mode="json"), separators=(",", ":")
            )
            yield f"id: {event.sequence}\n"
            yield f"event: {event.type}\n"
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        stream(), media_type=app.state.settings.event_stream_media_type
    )


def get_public_agent_run(app: FastAPI, api_key: AgentApiKey, run_id: str):
    run = app.state.store.get_run(api_key.tenant_id, run_id)
    # ponytail: audit scan is enough at current volume; add an indexed API-run
    # mapping only when this lookup becomes measurable.
    owned = any(
        event.run_id == run_id
        and event.event_type == "agent.api.invoked"
        and event.metadata.get("api_key_id") == api_key.id
        for event in app.state.audit_service.list_for_tenant(api_key.tenant_id)
    )
    if run.agent_id != api_key.agent_id or not owned:
        raise NotFoundError("Agent API run not found")
    return run


def send_auth_action_email(
    settings: Settings,
    account: Any,
    purpose: Literal["email_verification", "password_reset"],
    token: str,
) -> None:
    parameter = "verifyEmail" if purpose == "email_verification" else "resetPassword"
    link = (
        f"{settings.deployment_external_url.rstrip('/')}"
        f"/?{urlencode({parameter: token})}"
    )
    subject = (
        "Verify your Taroai email"
        if purpose == "email_verification"
        else "Reset your Taroai password"
    )
    action = "verify your email" if purpose == "email_verification" else "reset your password"
    send_auth_email(
        settings.auth_smtp_url,
        settings.auth_email_from,
        account.email,
        subject,
        f"Open this link to {action}:\n\n{link}\n\nIf you did not request this, ignore this email.",
    )


def create_app(
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository | None = None,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
    knowledge_service: InMemoryKnowledgeService | SqlKnowledgeService | None = None,
    sandbox_adapter: SandboxAdapter | LocalProcessSandboxAdapter | None = None,
    browser_controller: BrowserController | None = None,
    job_queue: JobQueue | None = None,
    storage_catalog: InMemoryStorageCatalog | SqlStorageCatalog | None = None,
    object_storage: ObjectStorageAdapter | None = None,
    identity_service: InMemoryIdentityService | SqlIdentityService | None = None,
    policy_service: PolicyService | None = None,
    audit_service: Any | None = None,
    auth_service: AuthService | None = None,
    billing_analytics_service: BillingAnalyticsService | None = None,
    billing_invoice_service: BillingInvoiceService | None = None,
    billing_invoice_store: BillingInvoiceStore | None = None,
    billing_pricing_rule_store: BillingPricingRuleStore | None = None,
    billing_pricing_service: BillingPricingService | None = None,
    share_grant_store: ShareGrantStore | None = None,
    run_trace_service: RunTraceService | None = None,
    license_service: LicenseService | None = None,
    embedding_gateway: EmbeddingGateway | None = None,
    model_policy_store: ModelPolicyStore | None = None,
    model_provider_store: ModelProviderStore | None = None,
    tenant_bootstrap_service: TenantBootstrapService | None = None,
    tenant_readiness_service: TenantReadinessService | None = None,
    trigger_service: TriggerService | None = None,
    connector_registry: InMemoryConnectorRegistry | SqlConnectorRegistry | None = None,
    connector_dispatcher: ConnectorDispatchService | None = None,
    connector_oauth_service: ConnectorOAuthService | None = None,
    secret_service: SecretService | None = None,
    skill_registry: InMemorySkillRegistry | SqlSkillRegistry | None = None,
    skill_service: Any | None = None,
    skill_evaluation_runner: Any | None = None,
    agent_registry: InMemoryAgentRegistry | SqlAgentRegistry | None = None,
    browser_profile_registry: BrowserProfileRegistry | None = None,
    agent_engine_registry: AgentEngineRegistry | None = None,
    coding_workspace_registry: CodingWorkspaceRegistry | None = None,
    evaluation_repository: EvaluationRepository | None = None,
    thread_share_store: ThreadShareStore | None = None,
    speech_gateway: SpeechGateway | None = None,
    solution_pack_registry: (
        InMemorySolutionPackRegistry | SqlSolutionPackRegistry | None
    ) = None,
    solution_pack_service: SolutionPackService | None = None,
    customer_success_service: InMemoryCustomerSuccessService | None = None,
    customer_feedback_service: (
        InMemoryCustomerFeedbackService | SqlCustomerFeedbackService | None
    ) = None,
    sso_provider_registry: (
        InMemorySsoProviderRegistry | SqlSsoProviderRegistry | None
    ) = None,
    scim_provisioning_store: (
        InMemoryScimProvisioningStore | SqlScimProvisioningStore | None
    ) = None,
    scim_provisioning_service: ScimProvisioningService | None = None,
    lifecycle_policy_store: (
        InMemoryLifecyclePolicyStore | SqlLifecyclePolicyStore | None
    ) = None,
    restore_drill_schedule_store: RestoreDrillScheduleStore | None = None,
    tenant_offboarding_store: (
        InMemoryTenantOffboardingStore | SqlTenantOffboardingStore | None
    ) = None,
    long_term_memory_service: (
        InMemoryLongTermMemoryService
        | SqlLongTermMemoryService
        | GuardedLongTermMemoryService
        | None
    ) = None,
    short_term_memory_service: (
        InMemoryShortTermMemoryService
        | RedisShortTermMemoryService
        | GuardedShortTermMemoryService
        | None
    ) = None,
    guardrail_service: InMemoryGuardrailService | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    app = FastAPI(title=resolved_settings.api_title)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store or build_control_plane_store(resolved_settings)
    app.state.thread_event_hub = ThreadEventHub()
    app.state.store.event_notifier = app.state.thread_event_hub.notify

    async def bind_thread_event_hub_loop() -> None:
        app.state.thread_event_hub.bind_running_loop()

    def unbind_thread_event_hub_loop() -> None:
        app.state.thread_event_hub.unbind_loop()

    app.add_event_handler("startup", bind_thread_event_hub_loop)
    app.add_event_handler("shutdown", unbind_thread_event_hub_loop)
    app.state.knowledge_service = knowledge_service or build_knowledge_service(
        resolved_settings
    )
    app.state.sandbox_adapter = sandbox_adapter or build_sandbox_adapter(
        resolved_settings
    )
    app.state.browser_controller = browser_controller or build_browser_controller(
        resolved_settings
    )
    app.state.job_queue = job_queue or build_job_queue(resolved_settings)
    app.state.storage_catalog = storage_catalog or build_storage_catalog(
        resolved_settings
    )
    app.state.object_storage = (
        object_storage or S3CompatibleObjectStorage.from_settings(resolved_settings)
    )
    app.state.artifact_service = ArtifactService(
        store=app.state.store,
        storage_catalog=app.state.storage_catalog,
        object_storage=app.state.object_storage,
    )
    app.state.storage_content_scanner = build_storage_content_scanner(resolved_settings)
    app.state.audit_service = audit_service or AuditService(
        store=app.state.store,
        retention_days=resolved_settings.audit_retention_days,
    )
    app.state.guardrail_service = guardrail_service or build_guardrail_service(
        resolved_settings
    )
    app.state.skill_registry = skill_registry or build_skill_registry(resolved_settings)
    app.state.skill_service = skill_service or SkillService(
        registry=app.state.skill_registry,
        github_fetcher=HttpsGithubArchiveFetcher(),
        evaluation_runner=skill_evaluation_runner,
    )
    app.state.store_catalog = BuiltinStoreCatalog()
    app.state.agent_registry = agent_registry or build_agent_registry(resolved_settings)
    app.state.agent_api_key_store = build_agent_api_key_store(resolved_settings)
    app.state.evaluation_repository = (
        evaluation_repository or build_evaluation_repository(resolved_settings)
    )
    app.state.agent_registry_service = AgentRegistryService(
        registry=app.state.agent_registry,
        store=app.state.store,
        storage_catalog=app.state.storage_catalog,
    )
    app.state.speech_gateway = speech_gateway or SpeechGateway()
    app.state.thread_share_store = thread_share_store or build_thread_share_store(
        resolved_settings
    )
    app.state.thread_share_service = ThreadShareService(
        store=app.state.store,
        link_store=app.state.thread_share_store,
        hash_secret=resolved_settings.thread_share_token_hash_secret,
    )
    app.state.solution_pack_registry = (
        solution_pack_registry or build_solution_pack_registry(resolved_settings)
    )
    app.state.solution_pack_service = solution_pack_service or SolutionPackService(
        pack_registry=app.state.solution_pack_registry,
        skill_registry=app.state.skill_registry,
        audit_store=app.state.store,
    )
    app.state.customer_success_service = (
        customer_success_service
        or InMemoryCustomerSuccessService(
            store=app.state.store,
            solution_pack_registry=app.state.solution_pack_registry,
            skill_registry=app.state.skill_registry,
        )
    )
    app.state.customer_feedback_service = (
        customer_feedback_service
        or build_customer_feedback_service(
            settings=resolved_settings,
            audit_store=app.state.store,
            solution_pack_registry=app.state.solution_pack_registry,
        )
    )
    app.state.sso_provider_registry = (
        sso_provider_registry or build_sso_provider_registry(resolved_settings)
    )
    app.state.scim_provisioning_store = (
        scim_provisioning_store or build_scim_provisioning_store(resolved_settings)
    )
    app.state.lifecycle_policy_store = (
        lifecycle_policy_store or build_lifecycle_policy_store(resolved_settings)
    )
    app.state.restore_drill_schedule_store = (
        restore_drill_schedule_store
        or build_restore_drill_schedule_store(resolved_settings)
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
    app.state.agent_api_key_service = AgentApiKeyService(
        store=app.state.agent_api_key_store,
        agent_registry=app.state.agent_registry,
        identity_service=app.state.identity_service,
        hash_secret=resolved_settings.access_token_secret,
    )
    app.state.policy_service = policy_service or IdentityPolicyService(
        identity_service=app.state.identity_service
    )
    app.state.auth_service = auth_service or AuthService(
        identity_service=app.state.identity_service,
        session_store=build_auth_session_store(resolved_settings),
        access_token_secret=resolved_settings.access_token_secret,
        access_token_ttl_seconds=resolved_settings.access_token_ttl_seconds,
        remembered_access_token_ttl_seconds=(
            resolved_settings.remembered_access_token_ttl_seconds
        ),
        sso_provider_registry=app.state.sso_provider_registry,
    )
    app.state.tenant_organization_service = TenantOrganizationService(
        store=app.state.store,
        identity_service=app.state.identity_service,
        audit_service=app.state.audit_service,
        token_secret=resolved_settings.access_token_secret,
    )
    app.state.scim_provisioning_service = (
        scim_provisioning_service
        or ScimProvisioningService(
            identity_service=app.state.identity_service,
            store=app.state.scim_provisioning_store,
        )
    )
    app.state.billing_analytics_service = (
        billing_analytics_service or BillingAnalyticsService()
    )
    app.state.billing_invoice_service = (
        billing_invoice_service or BillingInvoiceService()
    )
    app.state.billing_invoice_store = (
        billing_invoice_store or build_billing_invoice_store(resolved_settings)
    )
    app.state.billing_pricing_rule_store = (
        billing_pricing_rule_store
        or build_billing_pricing_rule_store(resolved_settings)
    )
    app.state.billing_pricing_service = (
        billing_pricing_service
        or build_billing_pricing_service(
            resolved_settings,
            app.state.billing_pricing_rule_store,
        )
    )
    app.state.share_grant_store = share_grant_store or build_share_grant_store(
        resolved_settings
    )
    app.state.run_trace_service = run_trace_service or build_run_trace_service(
        resolved_settings
    )
    app.state.license_service = license_service or LicenseService(
        audit_service=app.state.audit_service,
        signature_verifier=resolved_settings.license_signature_verifier(),
        runtime_enforcement_enabled=resolved_settings.license_runtime_enforcement_enabled,
        validation_store=app.state.store,
    )
    if (
        isinstance(app.state.license_service, LicenseService)
        and app.state.license_service.audit_service is None
    ):
        app.state.license_service.audit_service = app.state.audit_service
    if isinstance(app.state.audit_service, AuditService):
        app.state.audit_service.license_service = app.state.license_service
    app.state.model_policy_store = model_policy_store or build_model_policy_store(
        resolved_settings
    )
    app.state.model_provider_store = model_provider_store or build_model_provider_store(
        resolved_settings
    )
    app.state.secret_service = secret_service or build_secret_service_from_settings(
        resolved_settings
    )
    app.state.agent_engine_registry = (
        agent_engine_registry or build_agent_engine_registry(resolved_settings)
    )
    app.state.agent_engine_service = AgentEngineService(
        app.state.agent_engine_registry, app.state.secret_service, store=app.state.store
    )
    app.state.coding_workspace_registry = (
        coding_workspace_registry or build_coding_workspace_registry(resolved_settings)
    )
    app.state.coding_workspace_service = CodingWorkspaceService(
        app.state.coding_workspace_registry, app.state.store
    )
    app.state.agent_registry_service.coding_workspace_registry = (
        app.state.coding_workspace_registry
    )
    app.state.agent_registry_service.agent_engine_registry = (
        app.state.agent_engine_registry
    )
    app.state.browser_profile_registry = (
        browser_profile_registry or build_browser_profile_registry(resolved_settings)
    )
    app.state.browser_profile_service = BrowserProfileService(
        registry=app.state.browser_profile_registry,
        secret_service=app.state.secret_service,
        browser_controller=app.state.browser_controller,
    )
    app.state.agent_registry_service.browser_profile_service = (
        app.state.browser_profile_service
    )
    app.state.embedding_gateway = embedding_gateway or build_embedding_gateway(
        resolved_settings,
        app.state.secret_service,
    )
    app.state.trigger_service = trigger_service or build_trigger_service(
        resolved_settings
    )
    app.state.trigger_webhook_verifier = build_trigger_webhook_verifier(
        resolved_settings
    )
    app.state.connector_registry = connector_registry or build_connector_registry(
        resolved_settings
    )
    app.state.connector_invocation_service = ConnectorInvocationService()
    oauth_state_store = (
        RedisOAuthAuthorizationStateStore(url=resolved_settings.redis_url)
        if isinstance(app.state.job_queue, RedisJobQueue)
        else None
    )
    if connector_oauth_service is None:
        app.state.connector_oauth_service = ConnectorOAuthService(
            secret_service=app.state.secret_service,
            state_store=oauth_state_store,
        )
    elif connector_oauth_service.secret_service is None or (
        connector_oauth_service.state_store is None and oauth_state_store is not None
    ):
        app.state.connector_oauth_service = connector_oauth_service.model_copy(
            update={
                "secret_service": connector_oauth_service.secret_service
                or app.state.secret_service,
                "state_store": connector_oauth_service.state_store
                or oauth_state_store,
            }
        )
    else:
        app.state.connector_oauth_service = connector_oauth_service
    if connector_dispatcher is None:
        app.state.connector_dispatcher = ConnectorDispatchService(
            secret_service=app.state.secret_service,
        )
    elif connector_dispatcher.secret_service is None:
        app.state.connector_dispatcher = connector_dispatcher.model_copy(
            update={"secret_service": app.state.secret_service}
        )
    else:
        app.state.connector_dispatcher = connector_dispatcher
    app.state.tenant_readiness_service = (
        tenant_readiness_service
        or TenantReadinessService(
            identity_service=app.state.identity_service,
            store=app.state.store,
            settings=resolved_settings,
            job_queue=app.state.job_queue,
            knowledge_service=app.state.knowledge_service,
            skill_registry=app.state.skill_registry,
        )
    )
    app.state.tenant_bootstrap_service = (
        tenant_bootstrap_service
        or TenantBootstrapService(
            identity_service=app.state.identity_service,
            store=app.state.store,
            settings=resolved_settings,
            readiness_service=app.state.tenant_readiness_service,
            audit_service=app.state.audit_service,
            knowledge_service=app.state.knowledge_service,
            solution_pack_service=app.state.solution_pack_service,
        )
    )
    app.state.settings = resolved_settings
    tool_gateway = ToolGateway(
        audit_service=app.state.audit_service,
        secret_service=app.state.secret_service,
        guardrail_service=app.state.guardrail_service,
    )
    if app.state.sandbox_adapter.provider != "disabled":
        register_sandbox_tool_handlers(tool_gateway, app.state.sandbox_adapter)
    if app.state.browser_controller.provider != "disabled":
        register_browser_tool_handlers(
            tool_gateway,
            app.state.browser_controller,
            profile_service=app.state.browser_profile_service,
        )
    if app.state.settings.tavily_api_key:
        register_web_search_tool_handler(
            tool_gateway,
            app.state.settings.tavily_api_key,
            app.state.settings.tavily_timeout_seconds,
        )
    register_memory_tool_handler(
        tool_gateway,
        app.state.long_term_memory_service,
        app.state.store,
    )
    register_skill_tool_handlers(tool_gateway, app.state.skill_service)
    register_agent_tool_handlers(tool_gateway, app.state.agent_registry_service)
    register_ui_render_tool_handler(tool_gateway, app.state.store)
    register_observation_read_tool_handler(tool_gateway, app.state.store)
    app.state.runtime = apply_agent_runtime_settings(
        runtime
        or AgentRuntime(
            store=app.state.store,
            model_gateway=build_model_gateway(
                resolved_settings,
                app.state.secret_service,
                app.state.model_provider_store,
            ),
            model_policy=build_model_policy(
                resolved_settings, app.state.model_policy_store
            ),
            model_budget_guard=build_model_budget_guard(resolved_settings),
            tool_gateway=tool_gateway,
            policy_service=app.state.policy_service,
            audit_service=app.state.audit_service,
            license_service=app.state.license_service,
            knowledge_service=app.state.knowledge_service,
            sandbox_adapter=app.state.sandbox_adapter,
            browser_controller=app.state.browser_controller,
            storage_catalog=app.state.storage_catalog,
            object_storage=app.state.object_storage,
            storage_content_scanner=app.state.storage_content_scanner,
            sandbox_runtime_image=app.state.settings.sandbox_runtime_image,
            sandbox_network_mode=SandboxNetworkMode(
                app.state.settings.sandbox_network_mode
            ),
            sandbox_timeout_seconds=app.state.settings.sandbox_timeout_seconds,
            embedding_gateway=app.state.embedding_gateway,
            billing_pricing_service=app.state.billing_pricing_service,
            long_term_memory_service=app.state.long_term_memory_service,
            guardrail_service=app.state.guardrail_service,
            skill_service=app.state.skill_service,
            connector_registry=app.state.connector_registry,
            connector_dispatcher=app.state.connector_dispatcher,
            connector_invocation_service=app.state.connector_invocation_service,
            agent_registry=app.state.agent_registry,
            browser_profile_service=app.state.browser_profile_service,
            agent_engine_service=app.state.agent_engine_service,
            coding_workspace_service=app.state.coding_workspace_service,
        ),
        resolved_settings,
    )
    if app.state.runtime.skill_service is None:
        app.state.runtime.skill_service = app.state.skill_service
    if app.state.runtime.connector_registry is None:
        app.state.runtime.connector_registry = app.state.connector_registry
    if app.state.runtime.connector_dispatcher is None:
        app.state.runtime.connector_dispatcher = app.state.connector_dispatcher
    if app.state.runtime.connector_invocation_service is None:
        app.state.runtime.connector_invocation_service = (
            app.state.connector_invocation_service
        )
    if app.state.runtime.agent_registry is None:
        app.state.runtime.agent_registry = app.state.agent_registry
    if app.state.runtime.browser_profile_service is None:
        app.state.runtime.browser_profile_service = app.state.browser_profile_service
    if app.state.runtime.agent_engine_service is None:
        app.state.runtime.agent_engine_service = app.state.agent_engine_service
    if app.state.runtime.coding_workspace_service is None:
        app.state.runtime.coding_workspace_service = app.state.coding_workspace_service
    if not app.state.runtime.tool_gateway.can_execute_tool(UI_RENDER_TOOL):
        register_ui_render_tool_handler(app.state.runtime.tool_gateway, app.state.store)
    if not app.state.runtime.tool_gateway.can_execute_tool(OBSERVATION_READ_TOOL):
        register_observation_read_tool_handler(
            app.state.runtime.tool_gateway, app.state.store
        )
    app.state.workflow_coordinator = WorkflowCoordinator(
        store=app.state.store, runtime=app.state.runtime
    )
    app.state.evaluation_service = EvaluationService(
        repository=app.state.evaluation_repository,
        executor=AgentEvaluationExecutor(
            agent_service=app.state.agent_registry_service,
            runtime=app.state.runtime,
            store=app.state.store,
        ),
    )
    app.state.agent_registry_service.evaluation_service = app.state.evaluation_service
    app.state.agent_registry_service.evaluation_repository = (
        app.state.evaluation_repository
    )
    app.state.chat_service = ChatService(
        store=app.state.store,
        model_policy_resolver=lambda: app.state.runtime.model_policy,
        provider_registry_resolver=lambda: ModelProviderRegistry(
            providers=effective_chat_model_gateway_providers(
                app.state.settings,
                app.state.model_provider_store,
            )
        ),
    )

    def execute_chat_run_chain(tenant_id: str, run_id: str) -> None:
        pending_run_ids = [run_id]
        for _ in range(100):
            if not pending_run_ids:
                return
            current_run_id = pending_run_ids.pop(0)
            try:
                app.state.workflow_coordinator.mark_running(
                    app.state.store.get_run(tenant_id, current_run_id)
                )
                state = app.state.runtime.execute_run(tenant_id, current_run_id)
            except Exception as error:
                failed_run = app.state.store.get_run(tenant_id, current_run_id)
                if failed_run.status not in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                    RunStatus.TIMED_OUT,
                }:
                    from taroai.agent.loop import AgentExecutionServices

                    execution = AgentExecutionServices(app.state.runtime)
                    execution._fail(
                        execution._restore_state(failed_run),
                        failed_run,
                        "runtime_execution_error",
                        detail=error.__class__.__name__,
                        metadata={
                            "reason": "runtime_execution_error",
                            "error_type": error.__class__.__name__,
                        },
                    )
                return
            current_run = app.state.store.get_run(tenant_id, current_run_id)
            is_workflow_child = (
                app.state.store.get_workflow_task_for_child_run(
                    tenant_id, current_run_id
                )
                is not None
            )
            if state.status in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.TIMED_OUT,
            }:
                pending_run_ids.extend(
                    run.id
                    for run in app.state.workflow_coordinator.complete_child(
                        current_run, state
                    )
                )
            if is_workflow_child:
                continue
            if current_run.thread_id is None or state.status not in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.TIMED_OUT,
            }:
                return
            continuation = app.state.chat_service.continue_thread(
                tenant_id,
                current_run.thread_id,
            )
            if continuation is None or not continuation.run_started:
                continue
            pending_run_ids.append(continuation.run_id)

    def dispatch_chat_run(
        tenant_id: str,
        dispatch: MessageDispatch,
        requested_by_user_id: str,
        background_tasks: BackgroundTasks,
    ) -> None:
        run = app.state.store.get_run(tenant_id, dispatch.run_id)
        should_resume_waiting_run = (
            dispatch.dispatch_status.value == "steering"
            and run.status == RunStatus.WAITING_FOR_USER
        )
        if not dispatch.run_started and not should_resume_waiting_run:
            return
        if app.state.settings.run_execution_dispatch_mode == "queue":
            queue = app.state.job_queue
            if queue is None:
                raise RedisQueueConfigurationError("job queue backend is disabled")
            job = queue.enqueue(
                JobType.RUN_EXECUTION,
                RunExecutionJob(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=run.user_id,
                    run_id=run.id,
                    requested_by_user_id=requested_by_user_id,
                ),
                max_attempts=app.state.settings.worker_job_max_attempts,
            )
            app.state.store.append_run_event(
                run,
                "run.execution_queued",
                {
                    "job_id": job.id,
                    "queue": app.state.settings.run_execution_queue_name,
                },
            )
            return
        background_tasks.add_task(
            execute_chat_run_chain,
            run.tenant_id,
            run.id,
        )

    def dispatch_workflow_runs(
        runs: list,
        requested_by_user_id: str,
        background_tasks: BackgroundTasks,
    ) -> None:
        for run in runs:
            if app.state.settings.run_execution_dispatch_mode == "queue":
                queue = app.state.job_queue
                if queue is None:
                    raise RedisQueueConfigurationError("job queue backend is disabled")
                job = queue.enqueue(
                    JobType.RUN_EXECUTION,
                    RunExecutionJob(
                        tenant_id=run.tenant_id,
                        workspace_id=run.workspace_id,
                        user_id=run.user_id,
                        run_id=run.id,
                        requested_by_user_id=requested_by_user_id,
                    ),
                    max_attempts=app.state.settings.worker_job_max_attempts,
                )
                app.state.store.append_run_event(
                    run,
                    "run.execution_queued",
                    {"job_id": job.id, "reason": "workflow_dependency_ready"},
                )
            else:
                background_tasks.add_task(
                    execute_chat_run_chain, run.tenant_id, run.id
                )

    def dispatch_next_after_terminal_run(
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        background_tasks: BackgroundTasks,
    ) -> None:
        run = app.state.store.get_run(tenant_id, run_id)
        if run.thread_id is None or run.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }:
            return
        dispatch = app.state.chat_service.continue_thread(
            tenant_id,
            run.thread_id,
        )
        if dispatch is not None:
            dispatch_chat_run(
                tenant_id,
                dispatch,
                requested_by_user_id,
                background_tasks,
            )

    app.state.exception_manager = ApiExceptionManager()
    app.state.exception_manager.register(app)
    app.add_event_handler("shutdown", close_database_pools)

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "service": "taroai-api",
            "environment": app.state.settings.environment,
        }

    @app.get("/readyz")
    def readyz(response: Response) -> dict:
        secret_readiness = build_secret_service_readiness(
            app.state.settings, app.state.secret_service
        )
        browser_readiness = build_browser_readiness(
            app.state.settings,
            app.state.browser_controller,
        )
        readiness = {
            "ready": secret_readiness["configured"] and (
                app.state.settings.browser_provider == "disabled"
                or browser_readiness.configured
            ),
            "checks": {
                "settings": "ok",
                "control_plane_store_backend": app.state.settings.control_plane_store_backend,
                "identity_service_backend": app.state.settings.identity_service_backend,
                "storage_catalog_backend": app.state.settings.storage_catalog_backend,
                "job_queue_backend": app.state.settings.job_queue_backend,
                "secret_service_backend": app.state.settings.secret_service_backend,
                "secret_service": secret_readiness,
                "model_gateway": build_model_gateway_readiness(
                    app.state.settings,
                    app.state.model_provider_store,
                ).model_dump(mode="json"),
                "sandbox": build_sandbox_readiness(
                    app.state.settings,
                    app.state.sandbox_adapter,
                ).model_dump(mode="json", exclude_none=True),
                "sandbox_provider": app.state.settings.sandbox_provider,
                "browser": browser_readiness.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                "browser_provider": app.state.settings.browser_provider,
            },
        }
        if not readiness["ready"]:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return readiness

    @app.post("/api/auth/login")
    def login(payload: AuthLoginRequest) -> dict:
        result = app.state.auth_service.login(
            tenant_id=payload.tenant_id,
            email=payload.email,
            password=payload.password,
            remember_me=payload.remember_me,
        )
        response = result.model_dump(mode="json")
        workspace_ids = app.state.store.list_workspace_ids(result.tenant_id)
        if workspace_ids:
            response["workspace_id"] = workspace_ids[0]
        return response

    @app.get("/api/auth/capabilities")
    def auth_capabilities() -> dict[str, bool]:
        development_registration = (
            app.state.settings.environment.strip().lower()
            in {"local", "local-cloud-poc", "development", "test"}
        )
        return {
            "registration_enabled": (
                development_registration
                or app.state.settings.auth_public_registration_enabled
            ),
            "email_verification_required": (
                not development_registration
                and app.state.settings.auth_public_registration_enabled
            ),
            "password_reset_enabled": app.state.settings.auth_password_reset_enabled,
        }

    @app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
    def register(payload: AuthRegisterRequest) -> dict:
        development_registration = app.state.settings.environment.strip().lower() in {
            "local",
            "local-cloud-poc",
            "development",
            "test",
        }
        if not (
            development_registration
            or app.state.settings.auth_public_registration_enabled
        ):
            raise TenantAccessError("account registration is not enabled")
        if not development_registration:
            existing_accounts = app.state.identity_service.find_users_by_email(
                payload.email
            )
            pending_accounts = [
                account
                for account in existing_accounts
                if account.status == "pending"
            ]
            if pending_accounts:
                account = pending_accounts[0]
                token = app.state.auth_service.issue_action_token(
                    account,
                    "email_verification",
                    app.state.settings.auth_email_verification_ttl_seconds,
                )
                send_auth_action_email(
                    app.state.settings,
                    account,
                    "email_verification",
                    token,
                )
                return {"accepted": True, "verification_required": True}
            if existing_accounts:
                return {"accepted": True, "verification_required": True}
        result = app.state.tenant_bootstrap_service.bootstrap(
            request=TenantBootstrapRequest(
                tenant_id=new_id("tenant"),
                owner_email=payload.email.strip().lower(),
                owner_display_name=payload.display_name.strip(),
                owner_password=payload.password,
            ),
            bootstrap_token=app.state.settings.tenant_bootstrap_token,
        )
        if not development_registration:
            account = app.state.identity_service.mark_user_pending(
                result.tenant_id,
                result.owner_user_id,
            )
            token = app.state.auth_service.issue_action_token(
                account,
                "email_verification",
                app.state.settings.auth_email_verification_ttl_seconds,
            )
            send_auth_action_email(
                app.state.settings,
                account,
                "email_verification",
                token,
            )
            return {"accepted": True, "verification_required": True}
        return result.model_dump(mode="json")

    @app.post("/api/auth/email-verification/request", status_code=status.HTTP_202_ACCEPTED)
    def request_email_verification(
        payload: AuthEmailVerificationSendRequest,
    ) -> dict[str, bool]:
        if app.state.settings.auth_public_registration_enabled:
            pending_accounts = [
                account
                for account in app.state.identity_service.find_users_by_email(
                    payload.email
                )
                if account.status == "pending"
            ]
            if pending_accounts:
                account = pending_accounts[0]
                token = app.state.auth_service.issue_action_token(
                    account,
                    "email_verification",
                    app.state.settings.auth_email_verification_ttl_seconds,
                )
                send_auth_action_email(
                    app.state.settings,
                    account,
                    "email_verification",
                    token,
                )
        return {"accepted": True}

    @app.post("/api/auth/email-verification/confirm")
    def confirm_email_verification(
        payload: AuthEmailVerificationRequest,
    ) -> dict[str, bool]:
        app.state.auth_service.verify_email_action(payload.token)
        return {"verified": True}

    @app.post("/api/auth/password/forgot", status_code=status.HTTP_202_ACCEPTED)
    def forgot_password(payload: AuthPasswordForgotRequest) -> dict[str, bool]:
        if app.state.settings.auth_password_reset_enabled:
            accounts = [
                account
                for account in app.state.identity_service.find_users_by_email(
                    payload.email
                )
                if account.status == "active"
                and (
                    payload.tenant_id is None
                    or account.tenant_id == payload.tenant_id
                )
            ]
            for account in accounts:
                token = app.state.auth_service.issue_action_token(
                    account,
                    "password_reset",
                    app.state.settings.auth_password_reset_ttl_seconds,
                )
                send_auth_action_email(
                    app.state.settings,
                    account,
                    "password_reset",
                    token,
                )
        return {"accepted": True}

    @app.post("/api/auth/password/reset")
    def reset_password(payload: AuthPasswordResetRequest) -> dict[str, bool]:
        app.state.auth_service.reset_password_action(
            payload.token,
            payload.password,
        )
        return {"reset": True}

    @app.get("/api/auth/session")
    def get_auth_session(
        authorization: str | None = Header(default=None, alias="Authorization"),
        requested_workspace_id: str | None = Header(
            default=None,
            alias="X-Workspace-ID",
        ),
    ) -> dict[str, Any]:
        try:
            claims = app.state.auth_service.authenticate_authorization_header(
                authorization
            )
        except AuthRequiredError:
            return {"authenticated": False}
        workspace_ids = app.state.store.list_workspace_ids(claims.tenant_id)
        workspace_id = (
            requested_workspace_id
            if requested_workspace_id in workspace_ids
            else workspace_ids[0] if workspace_ids else None
        )
        return {
            "authenticated": True,
            "tenant_id": claims.tenant_id,
            "user_id": claims.user_id,
            "email": claims.email,
            "display_name": claims.display_name,
            "expires_at": claims.expires_at,
            "workspace_id": workspace_id,
        }

    @app.post("/api/auth/logout")
    def logout(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        result = app.state.auth_service.logout_authorization_header(authorization)
        return result.model_dump(mode="json")

    @app.get("/api/tenants/current")
    def get_current_tenant(
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        return app.state.tenant_organization_service.summary(
            context.tenant_id,
            context.user_id,
        ).model_dump(mode="json")

    @app.patch("/api/tenants/current")
    def patch_current_tenant(
        payload: TenantPatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.rename_tenant(
            context.tenant_id,
            context.user_id,
            payload.name,
        ).model_dump(mode="json")

    @app.post("/api/workspaces", status_code=status.HTTP_201_CREATED)
    def create_workspace(
        payload: WorkspaceCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.create_workspace(
            context.tenant_id,
            context.user_id,
            payload.name,
        ).model_dump(mode="json")

    @app.patch("/api/workspaces/{workspace_id}")
    def patch_workspace(
        workspace_id: str,
        payload: WorkspacePatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.rename_workspace(
            context.tenant_id,
            context.user_id,
            workspace_id,
            payload.name,
        ).model_dump(mode="json")

    @app.post(
        "/api/tenants/current/invitations",
        status_code=status.HTTP_201_CREATED,
    )
    def create_tenant_invitation(
        payload: TenantInvitationCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.invite(
            context.tenant_id,
            context.user_id,
            payload.email,
        ).model_dump(mode="json")

    @app.delete("/api/tenants/current/invitations/{invitation_id}")
    def revoke_tenant_invitation(
        invitation_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.revoke_invitation(
            context.tenant_id,
            context.user_id,
            invitation_id,
        ).model_dump(mode="json")

    @app.post("/api/tenant-invitations/accept")
    def accept_tenant_invitation(payload: TenantInvitationAccept) -> dict:
        member = app.state.tenant_organization_service.accept_invitation(payload)
        result = app.state.auth_service.login(
            tenant_id=payload.tenant_id,
            email=member.email,
            password=payload.password,
        )
        response = result.model_dump(mode="json")
        response["email"] = member.email
        workspace_ids = app.state.store.list_workspace_ids(result.tenant_id)
        if workspace_ids:
            response["workspace_id"] = workspace_ids[0]
        return response

    @app.delete("/api/tenants/current/members/{member_user_id}")
    def remove_tenant_member(
        member_user_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.remove_member(
            context.tenant_id,
            context.user_id,
            member_user_id,
        ).model_dump(mode="json")

    @app.post("/api/tenants/current/members/{member_user_id}/restore")
    def restore_tenant_member(
        member_user_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "organization.manage")
        return app.state.tenant_organization_service.restore_member(
            context.tenant_id,
            context.user_id,
            member_user_id,
        ).model_dump(mode="json")

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

    @app.post("/api/licenses/import", status_code=status.HTTP_201_CREATED)
    def import_license(
        payload: LicenseImportRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "licenses.manage")
        license_key = app.state.license_service.verify_signed_offline_envelope(
            payload.envelope
        )
        if license_key.tenant_id != context.tenant_id:
            raise TenantAccessError("License tenant does not match request tenant")
        validation = app.state.license_service.validate_signed_offline_license_key(
            license_key,
            deployment_mode=payload.deployment_mode,
        )
        if validation.status != LicenseStatus.ACTIVE:
            raise ValueError(validation.reason or "license import rejected")
        app.state.license_service.activate_validation(validation)
        app.state.audit_service.record(
            AuditEventCreate(
                tenant_id=context.tenant_id,
                workspace_id=None,
                user_id=context.user_id,
                run_id=None,
                event_type="license.imported",
                metadata={
                    "license_id": validation.license.id,
                    "customer_name": validation.license.customer_name,
                    "status": validation.status.value,
                    "deployment_mode": validation.deployment_mode,
                    "source": validation.source,
                    "entitlements_count": len(validation.license.entitlements),
                },
                actor=audit_actor_from_request(
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    request=request,
                ),
            )
        )
        return LicenseImportResponse.from_validation(
            validation,
            activated=True,
        ).model_dump(mode="json")

    @app.post("/api/threads", status_code=status.HTTP_201_CREATED)
    def create_chat_thread(
        payload: ChatThreadApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        thread = app.state.chat_service.create_thread(
            context.tenant_id,
            context.user_id,
            payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=thread.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="chat.thread.created",
            metadata={
                "thread_id": thread.id,
                "provider_id": thread.provider_id,
                "model_id": thread.model_id,
                "reasoning_effort": thread.reasoning_effort,
            },
            request=request,
        )
        return thread.model_dump(mode="json")

    @app.get("/api/threads")
    def list_chat_threads(
        workspace_id: str | None = None,
        include_archived: bool = False,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        return [
            thread.model_dump(mode="json")
            for thread in app.state.chat_service.list_threads(
                context.tenant_id,
                workspace_id,
                include_archived=include_archived,
            )
        ]

    @app.get("/api/threads/{thread_id}")
    def get_chat_thread(
        thread_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.chat_service.get_thread(
            context.tenant_id,
            thread_id,
        ).model_dump(mode="json")

    @app.get("/api/threads/{thread_id}/action-manifests")
    def list_thread_action_manifests(
        thread_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        thread = app.state.chat_service.get_thread(context.tenant_id, thread_id)
        runs = (
            run
            for run in app.state.store.list_runs(
                context.tenant_id,
                thread.workspace_id,
            )
            if run.thread_id == thread_id
        )
        return action_manifests_for_runs(app, context.tenant_id, runs)

    @app.post("/api/threads/{thread_id}/action-manifests/{manifest_id}/approve")
    def approve_thread_action_manifest(
        thread_id: str,
        manifest_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.invoke")
        approval = action_manifest_for_thread(
            app,
            context.tenant_id,
            thread_id,
            manifest_id,
        )
        resolved = approve_action_manifest(app, approval, context.user_id)
        emit_action_manifest_event(app, resolved)
        return action_manifest_payload(app, resolved)

    @app.post("/api/threads/{thread_id}/action-manifests/{manifest_id}/reject")
    def reject_thread_action_manifest(
        thread_id: str,
        manifest_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.invoke")
        approval = action_manifest_for_thread(
            app,
            context.tenant_id,
            thread_id,
            manifest_id,
        )
        rejected = reject_action_manifest(app, approval, context.user_id)
        emit_action_manifest_event(app, rejected)
        dispatch_next_after_terminal_run(
            context.tenant_id,
            approval.run_id,
            context.user_id,
            background_tasks,
        )
        return action_manifest_payload(app, rejected)

    @app.post("/api/threads/{thread_id}/action-manifests/{manifest_id}/apply")
    def apply_thread_action_manifest(
        thread_id: str,
        manifest_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.invoke")
        approval = action_manifest_for_thread(
            app,
            context.tenant_id,
            thread_id,
            manifest_id,
        )
        return apply_action_manifest(app, approval, request, context)

    @app.patch("/api/threads/{thread_id}")
    def update_chat_thread(
        thread_id: str,
        payload: ChatThreadPatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        thread = app.state.chat_service.update_thread(
            context.tenant_id,
            context.user_id,
            thread_id,
            payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=thread.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="chat.thread.updated",
            metadata={
                "thread_id": thread.id,
                "updated_fields": sorted(payload.model_fields_set),
                "provider_id": thread.provider_id,
                "model_id": thread.model_id,
                "reasoning_effort": thread.reasoning_effort,
            },
            request=request,
        )
        return thread.model_dump(mode="json")

    @app.delete("/api/threads/{thread_id}")
    def delete_chat_thread(
        thread_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        sandbox_released = app.state.runtime.release_thread_sandbox(
            context.tenant_id,
            thread_id,
        )
        thread = app.state.chat_service.delete_thread(
            context.tenant_id,
            thread_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=thread.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="chat.thread.deleted",
            metadata={
                "thread_id": thread.id,
                "sandbox_released": sandbox_released,
            },
            request=request,
        )
        return thread.model_dump(mode="json")

    @app.get("/api/threads/{thread_id}/messages")
    def list_chat_messages(
        thread_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        return [
            message.model_dump(mode="json", exclude={"execution_content"})
            for message in app.state.chat_service.list_messages(
                context.tenant_id,
                thread_id,
            )
        ]

    @app.patch("/api/threads/{thread_id}/messages/{message_id}")
    def edit_chat_message(
        thread_id: str,
        message_id: str,
        payload: ChatMessageEdit,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        message = app.state.chat_service.edit_message(
            context.tenant_id,
            context.user_id,
            thread_id,
            message_id,
            payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=message.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="chat.message.edited",
            metadata={"thread_id": thread_id, "message_id": message_id},
            request=request,
        )
        return message.model_dump(mode="json", exclude={"execution_content"})

    @app.delete(
        "/api/threads/{thread_id}/messages/{message_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_chat_message(
        thread_id: str,
        message_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> Response:
        app.state.chat_service.delete_message(
            context.tenant_id,
            context.user_id,
            thread_id,
            message_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/threads/{thread_id}/messages/{message_id}/promote")
    def promote_manual_chat_message(
        thread_id: str,
        message_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        message = app.state.chat_service.promote_manual_message(
            context.tenant_id,
            context.user_id,
            thread_id,
            message_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=message.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="chat.message.promoted_to_composer",
            metadata={"thread_id": thread_id, "message_id": message_id},
            request=request,
        )
        return message.model_dump(mode="json", exclude={"execution_content"})

    @app.post(
        "/api/threads/{thread_id}/steer",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def steer_chat_thread(
        thread_id: str,
        payload: ChatSteerSubmit,
        background_tasks: BackgroundTasks,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        dispatch = app.state.chat_service.steer(
            context.tenant_id,
            context.user_id,
            thread_id,
            payload,
        )
        dispatch_chat_run(
            context.tenant_id,
            dispatch,
            context.user_id,
            background_tasks,
        )
        return dispatch.model_dump(mode="json")

    @app.post(
        "/api/threads/{thread_id}/continue",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def continue_chat_thread(
        thread_id: str,
        background_tasks: BackgroundTasks,
        response: Response,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        dispatch = app.state.chat_service.continue_thread(
            context.tenant_id,
            thread_id,
        )
        if dispatch is None:
            response.status_code = status.HTTP_204_NO_CONTENT
            return {}
        dispatch_chat_run(
            context.tenant_id,
            dispatch,
            context.user_id,
            background_tasks,
        )
        return dispatch.model_dump(mode="json")

    @app.get("/api/threads/{thread_id}/events")
    async def get_chat_thread_events(
        thread_id: str,
        after_sequence: int | None = None,
        follow: bool = False,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        context: RequestContext = Depends(get_request_context),
    ) -> StreamingResponse:
        replay_after_sequence = resolve_event_replay_sequence(
            after_sequence, last_event_id
        )
        hub: ThreadEventHub = app.state.thread_event_hub
        # The startup hook binds the loop; rebinding here also covers callers
        # that never ran the lifespan (e.g. a TestClient used without a
        # context manager).
        hub.bind_running_loop()

        async def stream() -> AsyncIterator[str]:
            cursor = replay_after_sequence or 0
            deadline = time.monotonic() + app.state.settings.event_stream_follow_seconds
            next_heartbeat = (
                time.monotonic() + app.state.settings.event_stream_heartbeat_seconds
            )
            notified = False
            while True:
                events = await anyio.to_thread.run_sync(
                    partial(
                        app.state.store.list_thread_events,
                        context.tenant_id,
                        thread_id,
                        after_sequence=cursor,
                    )
                )
                for event in events:
                    cursor = event.thread_sequence or cursor
                    payload = json.dumps(
                        event.model_dump(mode="json"), separators=(",", ":")
                    )
                    yield f"id: {cursor}\n"
                    yield f"event: {event.type}\n"
                    yield f"data: {payload}\n\n"
                now = time.monotonic()
                if not follow or now >= deadline:
                    return
                if now >= next_heartbeat:
                    yield "event: heartbeat\ndata: {}\n\n"
                    next_heartbeat = (
                        now + app.state.settings.event_stream_heartbeat_seconds
                    )
                timeout = min(deadline - now, next_heartbeat - now)
                if notified and not events:
                    # A notification can fire just before the writer's
                    # transaction commits; re-check shortly instead of
                    # waiting a full heartbeat interval.
                    timeout = min(timeout, 0.25)
                notified = await hub.wait(thread_id, timeout=timeout)

        return StreamingResponse(
            stream(),
            media_type=app.state.settings.event_stream_media_type,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/threads/{thread_id}/messages",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def post_chat_message(
        thread_id: str,
        payload: ChatMessageSubmit,
        background_tasks: BackgroundTasks,
        response: Response,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        thread = app.state.chat_service.get_thread(context.tenant_id, thread_id)
        resolved_attachments = list(payload.attachments)
        resolved_resource_refs = list(payload.resource_refs)
        bound_skills = {
            reference.id for reference in resolved_resource_refs if reference.type == "skill"
        }
        for skill_id in payload.skill_ids:
            normalized_skill_id = skill_id.strip()
            if not normalized_skill_id:
                raise ValueError("skill_ids cannot contain an empty id")
            if normalized_skill_id not in bound_skills:
                resolved_resource_refs.append(
                    ResourceReference(type="skill", id=normalized_skill_id)
                )
                bound_skills.add(normalized_skill_id)
        for reference in resolved_resource_refs:
            if reference.type != "file":
                continue
            storage_object = app.state.storage_catalog.get(
                context.tenant_id, reference.id
            )
            if storage_object.workspace_id != thread.workspace_id:
                raise ValueError("Referenced file is not available in this workspace")
            require_storage_read_access(request, context, storage_object)
            if storage_object.id not in resolved_attachments:
                resolved_attachments.append(storage_object.id)
        resolved_payload = payload.model_copy(
            update={
                "attachments": resolved_attachments,
                "resource_refs": resolved_resource_refs,
            }
        )
        path = f"/api/threads/{thread_id}/messages"
        idempotency_request = build_idempotency_request(
            tenant_id=context.tenant_id,
            key=idempotency_key,
            method="POST",
            path=path,
            payload=resolved_payload,
        )

        def accept_message() -> MessageDispatch:
            dispatch = app.state.chat_service.post_message(
                context.tenant_id,
                context.user_id,
                thread_id,
                resolved_payload,
            )
            run = app.state.store.get_run(context.tenant_id, dispatch.run_id)
            record_audit_event(
                app,
                tenant_id=context.tenant_id,
                workspace_id=run.workspace_id,
                user_id=context.user_id,
                run_id=run.id,
                event_type="chat.message.accepted",
                metadata={
                    "thread_id": thread_id,
                    "message_id": dispatch.message_id,
                    "run_id": run.id,
                    "dispatch_status": dispatch.dispatch_status.value,
                    "delivery_mode": resolved_payload.delivery_mode,
                    "attachment_count": len(resolved_payload.attachments),
                    "resource_ref_count": len(resolved_payload.resource_refs),
                },
                request=request,
            )
            if dispatch.run_started:
                record_audit_event(
                    app,
                    tenant_id=context.tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=context.user_id,
                    run_id=run.id,
                    event_type="chat.run.queued",
                    metadata={
                        "thread_id": thread_id,
                        "run_id": run.id,
                        "status": run.status.value,
                        "provider_id": run.provider_id,
                        "model_id": run.model_id,
                        "reasoning_effort": run.reasoning_effort,
                    },
                    request=request,
                )
            dispatch_chat_run(
                context.tenant_id,
                dispatch,
                context.user_id,
                background_tasks,
            )
            return dispatch

        status_code, response_body = app.state.chat_service.execute_idempotently(
            idempotency_request,
            accept_message,
        )
        response.status_code = status_code
        return response_body

    @app.get("/api/model-catalog")
    def get_model_catalog(
        workspace_id: str = Query(min_length=1),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        return [
            entry.model_dump(mode="json")
            for entry in app.state.chat_service.model_catalog(
                context.tenant_id,
                workspace_id,
                context.user_id,
            )
        ]

    @app.get("/api/workspaces/{workspace_id}/capabilities")
    def get_workspace_capabilities(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        installed_skills = [
            {
                "id": summary.skill_id,
                "name": summary.name,
                "description": summary.description,
                "version": summary.version,
                "package_digest": summary.package_digest,
                "source_digest": summary.source_digest,
                "required_scopes": summary.required_scopes,
                "risk_level": summary.risk_level,
            }
            for summary in app.state.skill_service.discover(
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                user_id=context.user_id,
            )
        ]
        connectors = [
            {
                "id": connector.id,
                "name": connector.display_name,
                "type": connector.type.value,
                "capabilities": [
                    capability.model_dump(mode="json")
                    for capability in connector.capabilities
                    if capability.enabled
                ],
            }
            for connector in app.state.connector_registry.list_connectors(
                context.tenant_id, workspace_id
            )
            if connector.status.value == "enabled"
        ]
        agents = [
            {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "version": agent.published_version,
            }
            for agent in app.state.agent_registry.list(context.tenant_id, workspace_id)
            if agent.status == "published" and agent.published_version is not None
        ]
        knowledge = [
            {"id": item.id, "name": item.name, "description": item.description}
            for item in app.state.knowledge_service.list_bases_for_workspace(
                context.tenant_id, workspace_id
            )
        ]
        files = []
        for storage_object in app.state.storage_catalog.list_active(
            context.tenant_id, workspace_id=workspace_id
        ):
            if storage_object.run_id is not None:
                continue
            try:
                require_storage_read_access(request, context, storage_object)
            except TenantAccessError:
                continue
            files.append(
                {
                    "id": storage_object.id,
                    "file_id": storage_object.id,
                    "storage_object_id": storage_object.id,
                    "name": storage_object.filename,
                    "description": (
                        f"{storage_object.content_type} · {storage_object.size_bytes} bytes"
                    ),
                    "content_type": storage_object.content_type,
                    "size_bytes": storage_object.size_bytes,
                }
            )
        browser_profiles = [
            {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description or "Persistent browser identity",
                "version": str(profile.revision),
                "is_default": profile.is_default,
                "allowed_domains": profile.allowed_domains,
            }
            for profile in app.state.browser_profile_service.list_profiles(
                context.tenant_id, workspace_id
            )
            if profile.status == "active"
        ]
        repositories = [
            {
                "id": item.id,
                "name": item.name,
                "description": f"{item.provider} · {item.default_branch}",
                "repository_url": item.repository_url,
                "default_branch": item.default_branch,
            }
            for item in app.state.coding_workspace_registry.list_repositories(
                context.tenant_id, workspace_id
            )
            if item.status == "active"
        ]
        enabled_tools = {
            name
            for name, policy in app.state.runtime.tool_gateway.policies.items()
            if policy.enabled
        }
        return {
            "workspace_id": workspace_id,
            "skills": installed_skills,
            "connectors": connectors,
            "agents": agents,
            "knowledge": knowledge,
            "files": files,
            "browser_profiles": browser_profiles,
            "repositories": repositories,
            "composer_creation": {
                "image": "media.image.generate" in enabled_tools,
                "video": "media.video.generate" in enabled_tools,
                "voice": "speech.synthesize" in enabled_tools,
                "browser": "browser.action" in enabled_tools,
                "workflow": "sandbox.command" in enabled_tools,
                "slides": "sandbox.command" in enabled_tools,
            },
            "skill_service_available": app.state.skill_service is not None,
        }

    @app.get("/api/workspaces/{workspace_id}/skill-runtime")
    def get_workspace_skill_runtime_hook(
        workspace_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        service = app.state.skill_service
        if service is None:
            return {
                "available": False,
                "workspace_id": workspace_id,
                "reason": "Skill Runtime service is not configured",
            }
        if hasattr(service, "capability_snapshot"):
            snapshot = service.capability_snapshot(
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                user_id=context.user_id,
            )
            return {
                "available": True,
                "workspace_id": workspace_id,
                "capabilities": (
                    snapshot.model_dump(mode="json")
                    if hasattr(snapshot, "model_dump")
                    else snapshot
                ),
            }
        return {
            "available": True,
            "workspace_id": workspace_id,
            "capabilities": {},
        }

    @app.get("/api/threads/{thread_id}/suggestions")
    def get_thread_suggestions(
        thread_id: str,
        limit: int = Query(default=3, ge=1, le=6),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        app.state.chat_service.get_thread(context.tenant_id, thread_id)
        messages = app.state.chat_service.list_messages(context.tenant_id, thread_id)
        safe_messages = [
            message
            for message in messages
            if message.role.value in {"user", "assistant"}
        ][-6:]
        last_text = safe_messages[-1].content[:500] if safe_messages else ""
        events = app.state.store.list_thread_events(context.tenant_id, thread_id)
        latest_run_id = next(
            (
                event.run_id
                for event in reversed(events)
                if event.type == "run.succeeded"
            ),
            None,
        )
        candidates = next(
            (
                [str(item) for item in event.payload.get("options", [])]
                for event in reversed(events)
                if event.run_id == latest_run_id
                and event.type == "assistant.suggestions.generated"
            ),
            [],
        )
        return {
            "thread_id": thread_id,
            "context_summary": last_text,
            "suggestions": candidates[:limit],
        }

    @app.get("/api/threads/{thread_id}/bootstrap")
    def bootstrap_chat_thread(
        thread_id: str,
        event_limit: int = Query(default=100, ge=1, le=500),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        thread = app.state.chat_service.get_thread(context.tenant_id, thread_id)
        messages = app.state.chat_service.list_messages(context.tenant_id, thread_id)
        active_run = app.state.store.get_active_thread_run(context.tenant_id, thread_id)
        events = app.state.store.list_thread_events(context.tenant_id, thread_id)
        recent_events = events[-event_limit:]
        recent_event_ids = {event.id for event in recent_events}
        historic_event_types = {
            "action_approval",
            "agent.decision.created",
            "agent.loop.completed",
            "agent.loop.started",
            "agent.waiting_for_user",
            "app_created",
            "app_updated",
            "assistant.message.completed",
            "assistant.suggestions.generated",
            "browser.action.performed",
            "sandbox.command.executed",
            "tool.failed",
            "tool_call.cancelled",
            "tool_call.completed",
            "tool_call.failed",
            "ui_render",
        }
        bootstrap_events = [
            event
            for event in events
            if event.id in recent_event_ids
            or event.type in historic_event_types
            or event.type.startswith(
                ("approval.", "artifact.", "secret_capture.", "workflow")
            )
        ]
        artifacts = [
            artifact.model_dump(mode="json")
            for run_id in dict.fromkeys(
                event.run_id for event in events if event.run_id is not None
            )
            for artifact in app.state.store.list_artifacts(context.tenant_id, run_id)
        ]
        return {
            "thread": thread.model_dump(mode="json"),
            "messages": [
                message.model_dump(mode="json", exclude={"execution_content"})
                for message in messages
            ],
            "active_run": active_run.model_dump(mode="json") if active_run else None,
            "events": [
                event.model_dump(mode="json") for event in bootstrap_events
            ],
            "artifacts": artifacts,
            "last_event_id": events[-1].thread_sequence if events else 0,
            "reconnect": {
                "events_url": f"/api/threads/{thread.id}/events",
                "after_sequence": events[-1].thread_sequence if events else 0,
            },
        }

    @app.post("/api/threads/{thread_id}/shares", status_code=status.HTTP_201_CREATED)
    def create_thread_share(
        thread_id: str,
        payload: ThreadShareCreate,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        link, token = app.state.thread_share_service.create(
            context.tenant_id, context.user_id, thread_id, payload
        )
        return {
            "id": link.id,
            "public_id": link.public_id,
            "token": token,
            "url": f"/public/threads/{link.public_id}?{urlencode({'tenant_id': link.tenant_id, 'token': token})}",
            "expires_at": link.expires_at.isoformat(),
            "redaction_policy": link.redaction_policy,
        }

    @app.get("/api/threads/{thread_id}/shares")
    def list_thread_shares(
        thread_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        app.state.chat_service.get_thread(context.tenant_id, thread_id)
        return [
            link.model_dump(mode="json", exclude={"token_hash"})
            for link in app.state.thread_share_store.list(context.tenant_id, thread_id)
        ]

    @app.delete("/api/threads/{thread_id}/shares/{share_id}")
    def revoke_thread_share(
        thread_id: str,
        share_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        link = app.state.thread_share_store.get(context.tenant_id, share_id)
        if link.thread_id != thread_id:
            raise NotFoundError(f"Thread share link not found: {share_id}")
        return app.state.thread_share_store.revoke(
            context.tenant_id, share_id, context.user_id
        ).model_dump(mode="json", exclude={"token_hash"})

    @app.get("/public/threads/{public_id}", response_model=None)
    def read_public_thread(
        public_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1),
        token: str | None = Query(default=None, min_length=20),
        share_token: str | None = Header(
            default=None, alias="X-Share-Token", min_length=20
        ),
    ) -> dict[str, Any] | HTMLResponse:
        resolved_token = share_token or token
        if resolved_token is None:
            raise AuthRequiredError("share token required")
        payload = app.state.thread_share_service.read_public(
            tenant_id, public_id, resolved_token
        )
        if "text/html" not in request.headers.get("accept", ""):
            return payload
        return HTMLResponse(
            public_thread_html(payload),
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/evaluations/suites")
    def list_evaluation_suites(
        target_kind: EvaluationTargetKind | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        records = app.state.evaluation_repository.list_suites(context.tenant_id)
        return [
            record.model_dump(mode="json")
            for record in records
            if target_kind is None or record.suite.target_kind == target_kind
        ]

    @app.post("/api/evaluations/suites", status_code=status.HTTP_201_CREATED)
    def register_evaluation_suite(
        payload: EvaluationSuite,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        record = app.state.evaluation_service.register_suite(
            tenant_id=context.tenant_id,
            suite=payload,
            created_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="evaluation.suite.registered",
            metadata={
                "suite_id": payload.id,
                "suite_version": payload.version,
                "target_kind": payload.target_kind.value,
                "suite_digest": record.suite_digest,
            },
            request=request,
        )
        return record.model_dump(mode="json")

    @app.get("/api/evaluations/runs")
    def list_evaluation_runs(
        target_id: str = Query(min_length=1),
        target_kind: EvaluationTargetKind | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        runs = app.state.evaluation_repository.list_runs(context.tenant_id, target_id)
        return [
            run.model_dump(mode="json")
            for run in reversed(runs)
            if target_kind is None or run.target_kind == target_kind
        ]

    @app.get("/api/evaluations/runs/{run_id}")
    def get_evaluation_run(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.evaluation_repository.get_run(
            context.tenant_id, run_id
        ).model_dump(mode="json")

    @app.get("/api/evaluations/runs/{run_id}/evidence")
    def get_evaluation_evidence(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.evaluation_service.evidence(
            context.tenant_id, run_id
        ).model_dump(mode="json")

    @app.post("/api/evaluations/runs/{run_id}/baseline")
    def promote_evaluation_baseline(
        run_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        baseline = app.state.evaluation_service.promote_to_baseline(
            tenant_id=context.tenant_id,
            run_id=run_id,
            created_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="evaluation.baseline.promoted",
            metadata={"evaluation_run_id": run_id, "target_id": baseline.target_id},
            request=request,
        )
        return baseline.model_dump(mode="json")

    @app.post("/api/evaluations/agents/{agent_id}/versions/{version}/run")
    def run_agent_evaluation(
        agent_id: str,
        version: int,
        payload: AgentEvaluationRunRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        agent_version = app.state.agent_registry.get_version(
            context.tenant_id, agent_id, version
        )
        evaluation_run = app.state.evaluation_service.run_registered_suite(
            tenant_id=context.tenant_id,
            target_kind=EvaluationTargetKind.AGENT,
            target_id=agent_id,
            target_version=str(version),
            target_digest=canonical_digest(agent_version.spec.model_dump(mode="json")),
            suite_id=payload.suite_id,
            suite_version=payload.suite_version,
            created_by_user_id=context.user_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=agent_version.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="evaluation.run.completed",
            metadata={
                "evaluation_run_id": evaluation_run.id,
                "agent_id": agent_id,
                "agent_version": version,
                "status": evaluation_run.status.value,
                "promotion_allowed": evaluation_run.promotion_gate.allowed,
            },
            request=request,
        )
        return evaluation_run.model_dump(mode="json")

    @app.post("/api/agents", status_code=status.HTTP_201_CREATED)
    def create_agent_definition(
        payload: AgentDefinitionCreate,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition, version = app.state.agent_registry_service.create(
            context.tenant_id, context.user_id, payload
        )
        return {
            "agent": definition.model_dump(mode="json"),
            "version": version.model_dump(mode="json"),
        }

    @app.get("/api/agents")
    def list_agent_definitions(
        workspace_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        # ponytail: 当前直接统计；运行量大到影响列表延迟时再下推 SQL 聚合。
        run_counts: dict[str, int] = {}
        for run in app.state.store.list_runs(context.tenant_id, workspace_id):
            if not run.agent_id:
                continue
            if run.trigger_message_id and app.state.store.get_chat_message(
                context.tenant_id, run.trigger_message_id
            ).kind == "workflow_task":
                continue
            run_counts[run.agent_id] = run_counts.get(run.agent_id, 0) + 1
        return [
            {
                **item.model_dump(mode="json"),
                "run_count": run_counts.get(item.id, 0),
                "skill_bindings": app.state.agent_registry.get_version(
                    context.tenant_id, item.id, item.latest_version
                ).spec.skill_bindings,
            }
            for item in app.state.agent_registry.list(context.tenant_id, workspace_id)
        ]

    @app.get("/api/agents/{agent_id}")
    def get_agent_definition(
        agent_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        return {
            "agent": definition.model_dump(mode="json"),
            "versions": [
                item.model_dump(mode="json")
                for item in app.state.agent_registry.list_versions(
                    context.tenant_id, agent_id
                )
            ],
        }

    @app.get("/api/agentapps/{agent_id}/action-manifests")
    @app.get("/api/agents/{agent_id}/action-manifests")
    def list_agent_action_manifests(
        agent_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        runs = (
            run
            for run in app.state.store.list_runs(
                context.tenant_id,
                definition.workspace_id,
            )
            if run.agent_id == agent_id
        )
        return {
            "items": action_manifests_for_runs(app, context.tenant_id, runs),
            "nextCursor": None,
        }

    @app.post(
        "/api/agentapps/{agent_id}/action-manifests/{manifest_id}/approve"
    )
    @app.post("/api/agents/{agent_id}/action-manifests/{manifest_id}/approve")
    def approve_agent_action_manifest(
        agent_id: str,
        manifest_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.invoke")
        approval = action_manifest_for_agent(
            app,
            context.tenant_id,
            agent_id,
            manifest_id,
        )
        resolved = approve_action_manifest(app, approval, context.user_id)
        emit_action_manifest_event(app, resolved)
        return action_manifest_payload(app, resolved)

    @app.post(
        "/api/agentapps/{agent_id}/action-manifests/{manifest_id}/reject"
    )
    @app.post("/api/agents/{agent_id}/action-manifests/{manifest_id}/reject")
    def reject_agent_action_manifest(
        agent_id: str,
        manifest_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.invoke")
        approval = action_manifest_for_agent(
            app,
            context.tenant_id,
            agent_id,
            manifest_id,
        )
        rejected = reject_action_manifest(app, approval, context.user_id)
        emit_action_manifest_event(app, rejected)
        dispatch_next_after_terminal_run(
            context.tenant_id,
            approval.run_id,
            context.user_id,
            background_tasks,
        )
        return action_manifest_payload(app, rejected)

    @app.post("/api/agentapps/{agent_id}/action-manifests/{manifest_id}/apply")
    @app.post("/api/agents/{agent_id}/action-manifests/{manifest_id}/apply")
    def apply_agent_action_manifest(
        agent_id: str,
        manifest_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.invoke")
        approval = action_manifest_for_agent(
            app,
            context.tenant_id,
            agent_id,
            manifest_id,
        )
        return apply_action_manifest(app, approval, request, context)

    @app.get("/api/agents/{agent_id}/files")
    def get_agent_files(
        agent_id: str,
        version: int | None = Query(default=None, ge=1),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        version_number = (
            version or definition.published_version or definition.latest_version
        )
        target = app.state.agent_registry.get_version(
            context.tenant_id, agent_id, version_number
        )
        config = {
            "agent_id": definition.id,
            "version": target.version,
            "app_kind": definition.app_kind,
            "write_autonomy": definition.write_autonomy,
            "input_schema": target.spec.input_schema,
            "output_contract": target.spec.output_contract,
            "skill_bindings": target.spec.skill_bindings,
            "connector_bindings": target.spec.connector_bindings,
            "knowledge_bindings": target.spec.knowledge_bindings,
            "model_policy": target.spec.model_policy,
        }
        files = [
            {
                "path": "/workspace/agent/SKILL.md",
                "content_type": "text/markdown",
                "content": target.spec.instructions,
            },
            {
                "path": "/workspace/agent/config.json",
                "content_type": "application/json",
                "content": json.dumps(config, ensure_ascii=False, indent=2),
            },
        ]
        files.extend(
            {
                "path": item["sandbox_path"],
                "storage_object_id": item["storage_object_id"],
                "content_type": "application/octet-stream",
                "content": None,
            }
            for item in target.spec.runtime_snapshot.get("files", [])
            if item.get("sandbox_path") and item.get("storage_object_id")
        )
        return {
            "agent_id": agent_id,
            "version": target.version,
            "files": files,
        }

    @app.patch("/api/agents/{agent_id}")
    def update_agent_definition(
        agent_id: str,
        payload: AgentDefinitionPatch,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.agent_registry_service.update_definition(
            context.tenant_id,
            context.user_id,
            agent_id,
            **payload.model_dump(exclude_none=True),
        ).model_dump(mode="json")

    @app.get("/api/agents/{agent_id}/sessions")
    def list_agent_sessions(
        agent_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        sessions = []
        for run in app.state.store.list_runs(
            context.tenant_id, definition.workspace_id
        ):
            if run.agent_id != agent_id:
                continue
            if run.trigger_message_id and app.state.store.get_chat_message(
                context.tenant_id, run.trigger_message_id
            ).kind == "workflow_task":
                continue
            thread = (
                app.state.store.get_chat_thread(context.tenant_id, run.thread_id)
                if run.thread_id
                else None
            )
            sessions.append(
                {
                    "id": run.id,
                    "run_id": run.id,
                    "thread_id": run.thread_id,
                    "title": thread.title
                    if thread is not None
                    else f"{definition.name} run",
                    "status": run.status.value,
                    "created_at": run.created_at.isoformat(),
                    "updated_at": run.updated_at.isoformat(),
                }
            )
        sessions.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return {"agent_id": agent_id, "sessions": sessions}

    @app.get("/api/agents/{agent_id}/activity")
    def list_agent_activity(
        agent_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        activity: list[dict[str, Any]] = [
            {
                "id": version.id,
                "type": "agent.version.created",
                "status": version.status,
                "version": version.version,
                "created_at": version.created_at.isoformat(),
            }
            for version in app.state.agent_registry.list_versions(
                context.tenant_id, agent_id
            )
        ]
        activity.extend(
            {
                "id": event.id,
                "type": event.event_type,
                "status": "recorded",
                "run_id": event.run_id,
                "fields": event.metadata.get("fields", []),
                "created_at": event.created_at.isoformat(),
            }
            for event in app.state.store.list_audit_events(context.tenant_id)
            if event.event_type in {"agent.created", "agent.updated"}
            and event.metadata.get("agent_id") == agent_id
        )
        for run in app.state.store.list_runs(
            context.tenant_id, definition.workspace_id
        ):
            if run.agent_id != agent_id:
                continue
            if run.trigger_message_id and app.state.store.get_chat_message(
                context.tenant_id, run.trigger_message_id
            ).kind == "workflow_task":
                continue
            activity.append(
                {
                    "id": run.id,
                    "type": "agent.run",
                    "status": run.status.value,
                    "run_id": run.id,
                    "thread_id": run.thread_id,
                    "created_at": run.created_at.isoformat(),
                }
            )
            activity.extend(
                {
                    "id": approval.id,
                    "type": "agent.approval",
                    "status": approval.status.value,
                    "execution_status": approval.execution_status,
                    "run_id": run.id,
                    "created_at": approval.created_at.isoformat(),
                }
                for approval in app.state.store.list_approval_requests(
                    context.tenant_id, run.id
                )
            )
        activity.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return {"agent_id": agent_id, "activity": activity[:200]}

    @app.get("/api/agents/{agent_id}/export")
    def export_agent_definition(
        agent_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.read")
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        versions = app.state.agent_registry.list_versions(context.tenant_id, agent_id)
        storage_object_ids: list[str] = []
        for version in versions:
            storage_object_ids.extend(
                item["storage_object_id"]
                for item in version.spec.reference_files
                if item.get("storage_object_id")
                and item["storage_object_id"] not in storage_object_ids
            )
            storage_object_ids.extend(
                item["storage_object_id"]
                for item in version.spec.runtime_snapshot.get("files", [])
                if item.get("storage_object_id")
                and item["storage_object_id"] not in storage_object_ids
            )
        embedded_files = []
        embedded_size = 0
        for storage_object_id in storage_object_ids:
            storage_object = app.state.storage_catalog.get(
                context.tenant_id, storage_object_id
            )
            if storage_object.workspace_id != definition.workspace_id:
                raise ValueError("Agent export contains a file outside its workspace")
            require_storage_read_access(request, context, storage_object)
            content = app.state.object_storage.download(storage_object).content
            embedded_size += len(content)
            if embedded_size > app.state.settings.upload_max_bytes:
                raise ValueError(
                    "Agent export embedded files exceed the configured size limit"
                )
            embedded_files.append(
                {
                    "source_storage_object_id": storage_object.id,
                    "filename": storage_object.filename,
                    "content_type": storage_object.content_type,
                    "purpose": storage_object.purpose.value,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        return {
            "apiVersion": "taroai.ai/v1",
            "kind": "AgentBundle",
            "metadata": {
                "name": definition.name,
                "description": definition.description,
                "published_version": definition.published_version,
            },
            "versions": [
                {
                    "source_version": version.version,
                    "status": version.status,
                    "spec": version.spec.model_dump(mode="json"),
                }
                for version in versions
            ],
            "files": embedded_files,
        }

    @app.post("/api/agents/import", status_code=status.HTTP_201_CREATED)
    def import_agent_definition(
        payload: AgentImportRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.write")
        bundle = payload.bundle
        if (
            bundle.get("apiVersion") != "taroai.ai/v1"
            or bundle.get("kind") != "AgentBundle"
        ):
            raise ValueError("Unsupported Agent bundle format")
        bundled_versions = bundle.get("versions")
        if not isinstance(bundled_versions, list) or not bundled_versions:
            raise ValueError("Agent bundle does not contain versions")
        bundled_files = bundle.get("files", [])
        if not isinstance(bundled_files, list):
            raise ValueError("Agent bundle files must be a list")
        storage_id_map: dict[str, str] = {}
        total_size = 0
        for item in bundled_files:
            if not isinstance(item, dict) or not item.get("filename"):
                raise ValueError("Agent bundle contains invalid file metadata")
            try:
                content = base64.b64decode(item["content_base64"], validate=True)
            except (KeyError, binascii.Error, ValueError) as error:
                raise ValueError(
                    "Agent bundle contains invalid embedded file content"
                ) from error
            total_size += len(content)
            if total_size > app.state.settings.upload_max_bytes:
                raise ValueError(
                    "Agent import embedded files exceed the configured size limit"
                )
            digest = hashlib.sha256(content).hexdigest()
            if digest != item.get("sha256"):
                raise ValueError("Agent bundle embedded file digest does not match")
            storage_object = app.state.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=context.tenant_id,
                    workspace_id=payload.workspace_id,
                    purpose=StoragePurpose(
                        item.get("purpose", StoragePurpose.UPLOAD.value)
                    ),
                    filename=normalize_workspace_file_path(item["filename"]),
                    content_type=item.get("content_type") or "application/octet-stream",
                    size_bytes=len(content),
                )
            )
            scan = app.state.storage_content_scanner.scan(
                StorageContentScanRequest(
                    storage_object=storage_object, content=content
                )
            )
            if not scan.allowed:
                app.state.storage_catalog.mark_deleted(
                    context.tenant_id, storage_object.id, utc_now()
                )
                raise StorageContentRejectedError(
                    "Agent bundle file was rejected by the scanner"
                )
            app.state.object_storage.upload(storage_object, content)
            source_id = str(item.get("source_storage_object_id") or "")
            if source_id:
                storage_id_map[source_id] = storage_object.id
        imported_specs = []
        for item in bundled_versions:
            if not isinstance(item, dict):
                raise ValueError("Agent bundle contains an invalid version")
            raw_spec = json.loads(json.dumps(item.get("spec") or {}))
            for reference in raw_spec.get("reference_files", []):
                source_id = reference.get("storage_object_id")
                if source_id in storage_id_map:
                    reference["storage_object_id"] = storage_id_map[source_id]
            for runtime_file in raw_spec.get("runtime_snapshot", {}).get("files", []):
                source_id = runtime_file.get("storage_object_id")
                if source_id in storage_id_map:
                    runtime_file["storage_object_id"] = storage_id_map[source_id]
            imported_specs.append(AgentVersionSpec.model_validate(raw_spec))
        metadata = bundle.get("metadata") or {}
        definition, first_version = app.state.agent_registry_service.create(
            context.tenant_id,
            context.user_id,
            AgentDefinitionCreate(
                workspace_id=payload.workspace_id,
                name=payload.name or metadata.get("name") or "Imported Agent",
                description=metadata.get("description") or "Imported Agent bundle",
                version=imported_specs[0],
            ),
        )
        imported_versions = [first_version]
        for spec in imported_specs[1:]:
            imported_versions.append(
                app.state.agent_registry_service.create_version(
                    context.tenant_id, context.user_id, definition.id, spec
                )
            )
        if payload.publish:
            source_published = metadata.get("published_version")
            publish_index = (
                max(1, min(len(imported_versions), int(source_published)))
                if source_published is not None
                else len(imported_versions)
            )
            definition, _ = app.state.agent_registry_service.publish(
                context.tenant_id, definition.id, publish_index
            )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="agent.bundle.imported",
            metadata={
                "agent_id": definition.id,
                "version_count": len(imported_versions),
                "embedded_file_count": len(storage_id_map),
            },
            request=request,
        )
        return {
            "agent": definition.model_dump(mode="json"),
            "versions": [item.model_dump(mode="json") for item in imported_versions],
            "embedded_file_count": len(storage_id_map),
        }

    @app.post("/api/threads/{thread_id}/extract-agent")
    def extract_agent_draft(
        thread_id: str,
        payload: AgentExtractRequest | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        request_payload = payload or AgentExtractRequest()
        return app.state.agent_registry_service.extract(
            context.tenant_id,
            thread_id,
            request_payload.name,
            compile_playbook=request_payload.compile_playbook,
        ).model_dump(mode="json")

    @app.post("/api/agents/{agent_id}/versions", status_code=status.HTTP_201_CREATED)
    def create_agent_version(
        agent_id: str,
        payload: AgentVersionCreate,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.agent_registry_service.create_version(
            context.tenant_id, context.user_id, agent_id, payload.version
        ).model_dump(mode="json")

    @app.get("/api/agents/{agent_id}/versions")
    def list_agent_versions(
        agent_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in app.state.agent_registry.list_versions(
                context.tenant_id, agent_id
            )
        ]

    @app.get("/api/agents/{agent_id}/history")
    def get_agent_history(
        agent_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition = app.state.agent_registry.get(context.tenant_id, agent_id)
        return {
            "agent_id": agent_id,
            "published_version": definition.published_version,
            "versions": [
                item.model_dump(mode="json")
                for item in app.state.agent_registry.list_versions(
                    context.tenant_id, agent_id
                )
            ],
        }

    @app.post("/api/agents/{agent_id}/versions/{version}/publish")
    def publish_agent_version(
        agent_id: str,
        version: int,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        definition, published = app.state.agent_registry_service.publish(
            context.tenant_id, agent_id, version
        )
        return {
            "agent": definition.model_dump(mode="json"),
            "version": published.model_dump(mode="json"),
        }

    @app.post("/api/agents/{agent_id}/versions/{version}/restore")
    def restore_agent_version(
        agent_id: str,
        version: int,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.agent_registry_service.restore_as_new(
            context.tenant_id, context.user_id, agent_id, version
        ).model_dump(mode="json")

    @app.post("/api/agents/{agent_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def run_agent_definition(
        agent_id: str,
        payload: AgentRunRequest,
        background_tasks: BackgroundTasks,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        invocation = app.state.agent_registry_service.run(
            context.tenant_id, context.user_id, agent_id, payload
        )
        dispatch_chat_run(
            context.tenant_id,
            MessageDispatch(
                message_id=invocation.message_id,
                run_id=invocation.run_id,
                dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                events_url=invocation.events_url,
                run_started=True,
            ),
            context.user_id,
            background_tasks,
        )
        return invocation.model_dump(mode="json")

    @app.get("/api/api-keys")
    def list_agent_api_keys(
        agent_id: str | None = Query(default=None, min_length=1),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        if agent_id is not None:
            app.state.agent_registry.get(context.tenant_id, agent_id)
        return {
            "items": [
                item.model_dump(mode="json")
                for item in app.state.agent_api_key_store.list(
                    context.tenant_id, agent_id, context.user_id
                )
            ]
        }

    @app.post("/api/api-keys", status_code=status.HTTP_201_CREATED)
    def create_agent_api_key(
        payload: AgentApiKeyCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        api_key, raw_token = app.state.agent_api_key_service.create(
            context.tenant_id, context.user_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=api_key.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="agent.api_key.created",
            metadata={"api_key_id": api_key.id, "agent_id": api_key.agent_id},
            request=request,
        )
        return {"key": api_key.model_dump(mode="json"), "rawToken": raw_token}

    @app.delete("/api/api-keys/{key_id}")
    def revoke_agent_api_key(
        key_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        api_key = app.state.agent_api_key_service.revoke(
            context.tenant_id, context.user_id, key_id
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=api_key.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="agent.api_key.revoked",
            metadata={"api_key_id": api_key.id, "agent_id": api_key.agent_id},
            request=request,
        )
        return api_key.model_dump(mode="json")

    @app.post(
        "/api/v1/apps/{app_id}/runs",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=PublicAgentRunCreated,
    )
    def invoke_public_agent(
        app_id: str,
        payload: PublicAgentRunRequest,
        background_tasks: BackgroundTasks,
        response: Response,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        api_key: AgentApiKey = Depends(get_agent_api_key),
    ) -> dict[str, Any]:
        path = f"/api/v1/apps/{app_id}/runs"
        idempotency_request = build_idempotency_request(
            tenant_id=api_key.tenant_id,
            key=f"{api_key.id}:{idempotency_key}" if idempotency_key else None,
            method="POST",
            path=path,
            payload=payload,
        )

        def start_agent_run() -> PublicAgentRunCreated:
            invocation = app.state.agent_registry_service.run(
                api_key.tenant_id,
                api_key.created_by_user_id,
                app_id,
                AgentRunRequest(input=payload.inputs),
            )
            app.state.agent_api_key_service.record_use(api_key)
            record_audit_event(
                app,
                tenant_id=api_key.tenant_id,
                workspace_id=api_key.workspace_id,
                user_id=api_key.created_by_user_id,
                run_id=invocation.run_id,
                event_type="agent.api.invoked",
                metadata={
                    "api_key_id": api_key.id,
                    "agent_id": app_id,
                    "agent_version": invocation.agent_version,
                },
                request=request,
            )
            dispatch_chat_run(
                api_key.tenant_id,
                MessageDispatch(
                    message_id=invocation.message_id,
                    run_id=invocation.run_id,
                    dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                    events_url=invocation.events_url,
                    run_started=True,
                ),
                api_key.created_by_user_id,
                background_tasks,
            )
            return PublicAgentRunCreated(
                run_id=invocation.run_id,
                agent_id=app_id,
                agent_version=invocation.agent_version,
                status="queued",
                status_url=f"{path}/{invocation.run_id}",
                events_url=f"{path}/{invocation.run_id}/events",
            )

        response.status_code, response_body = (
            app.state.chat_service.execute_idempotently(
                idempotency_request, start_agent_run
            )
        )
        return response_body

    @app.get(
        "/api/v1/apps/{app_id}/runs/{run_id}",
        response_model=PublicAgentRunResult,
    )
    def get_public_agent_result(
        app_id: str,
        run_id: str,
        api_key: AgentApiKey = Depends(get_agent_api_key),
    ) -> dict[str, Any]:
        run = get_public_agent_run(app, api_key, run_id)
        output = next(
            (
                event.payload.get("content")
                for event in reversed(
                    app.state.store.list_run_events(api_key.tenant_id, run.id)
                )
                if event.type == "assistant.message.completed"
                and event.payload.get("content")
            ),
            None,
        )
        app.state.agent_api_key_service.record_use(api_key)
        return PublicAgentRunResult(
            run_id=run.id,
            agent_id=app_id,
            status=run.status.value,
            output=output,
            created_at=run.created_at,
            updated_at=run.updated_at,
        ).model_dump(mode="json")

    @app.get("/api/v1/apps/{app_id}/runs/{run_id}/events")
    def get_public_agent_events(
        app_id: str,
        run_id: str,
        after_sequence: int | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        api_key: AgentApiKey = Depends(get_agent_api_key),
    ) -> StreamingResponse:
        get_public_agent_run(app, api_key, run_id)
        app.state.agent_api_key_service.record_use(api_key)
        return stream_run_events(
            app, api_key.tenant_id, run_id, after_sequence, last_event_id
        )

    @app.get("/api/speech/capabilities")
    def get_speech_capabilities() -> dict[str, Any]:
        capability = app.state.speech_gateway.capabilities().model_copy(
            update={"max_audio_bytes": app.state.settings.speech_max_audio_bytes}
        )
        return capability.model_dump(mode="json")

    @app.post("/api/speech/transcribe")
    def transcribe_speech(
        payload: TranscriptionRequest,
        response: Response,
    ) -> dict[str, Any]:
        capability = app.state.speech_gateway.capabilities()
        if not capability.transcription:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"capability": get_speech_capabilities(), "transcript": None}
        max_audio_bytes = app.state.settings.speech_max_audio_bytes
        if len(payload.audio_base64) > ((max_audio_bytes + 2) // 3) * 4 + 8:
            raise ValueError("Audio exceeds the configured speech size limit")
        try:
            audio = base64.b64decode(payload.audio_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("audio_base64 is not valid base64") from error
        if not audio or len(audio) > max_audio_bytes:
            raise ValueError(
                "Audio is empty or exceeds the configured speech size limit"
            )
        return {
            "transcript": app.state.speech_gateway.transcribe(
                audio=audio,
                content_type=payload.content_type,
                language=payload.language,
            )
        }

    @app.post("/api/speech/summarize")
    def summarize_speech_text(
        payload: SpeechSummaryRequest,
        response: Response,
    ) -> dict[str, Any]:
        if not app.state.speech_gateway.capabilities().summarization:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"capability": get_speech_capabilities(), "summary": None}
        return {
            "summary": app.state.speech_gateway.summarize(
                text=payload.text, max_characters=payload.max_characters
            )
        }

    @app.post("/api/speech/synthesize")
    def synthesize_speech(
        payload: TextToSpeechRequest,
        response: Response,
    ) -> dict[str, Any]:
        if not app.state.speech_gateway.capabilities().text_to_speech:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"capability": get_speech_capabilities(), "audio_base64": None}
        audio, content_type = app.state.speech_gateway.synthesize(
            text=payload.text, voice=payload.voice, format=payload.format
        )
        return {
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "content_type": content_type,
        }

    @app.post("/api/runs", status_code=status.HTTP_201_CREATED)
    def create_run(
        payload: RunCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        idempotency_request = build_idempotency_request(
            tenant_id=context.tenant_id,
            key=idempotency_key,
            method=RUN_CREATE_METHOD,
            path=RUN_CREATE_PATH,
            payload=payload,
        )
        replay_record = find_idempotent_replay(app.state.store, idempotency_request)
        if replay_record is not None:
            response.status_code = replay_record.status_code
            return replay_record.response_body

        run = app.state.store.create_run(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            payload=payload,
        )
        response_body = {
            "run_id": run.id,
            "status": run.status.value,
            "events_url": f"/api/runs/{run.id}/events",
        }
        save_idempotent_response(
            app.state.store,
            idempotency_request,
            status.HTTP_201_CREATED,
            response_body,
        )
        return response_body

    @app.get("/api/runs")
    def list_runs(
        page: PageRequest = Depends(get_page_request),
        workspace_id: str | None = None,
        run_status: RunStatus | None = Query(default=None, alias="status"),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        result = paginate_created_at_records(
            app.state.store.list_runs(
                context.tenant_id,
                workspace_id=workspace_id,
                status=run_status,
            ),
            page,
        )
        # ponytail: pages are capped at 100; use a repository join if this becomes hot.
        for item in result.items:
            trigger_message_id = item.get("trigger_message_id")
            item["message"] = (
                app.state.store.get_chat_message(
                    context.tenant_id, trigger_message_id
                ).content
                if trigger_message_id
                else None
            )
        return result.model_dump(mode="json")

    @app.get("/api/notifications")
    def list_notifications(
        limit: int = Query(default=50, ge=1, le=100),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in app.state.store.list_notifications(
                    context.tenant_id, context.user_id, limit
                )
            ]
        }

    @app.get("/api/notifications/unread-count")
    def count_unread_notifications(
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, int]:
        return {
            "count": app.state.store.count_unread_notifications(
                context.tenant_id, context.user_id
            )
        }

    @app.post("/api/notifications/read-all")
    def mark_all_notifications_read(
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, int]:
        return {
            "updated": app.state.store.mark_all_notifications_read(
                context.tenant_id, context.user_id
            )
        }

    @app.post("/api/notifications/{notification_id}/read")
    def mark_notification_read(
        notification_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.store.mark_notification_read(
            context.tenant_id, context.user_id, notification_id
        ).model_dump(mode="json")

    @app.post("/api/triggers", status_code=status.HTTP_201_CREATED)
    def create_trigger(
        payload: TriggerCreateRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.manage")
        trigger = app.state.trigger_service.create_trigger(
            TriggerDefinitionCreate(
                tenant_id=context.tenant_id,
                workspace_id=payload.workspace_id,
                agent_id=payload.agent_id,
                created_by_user_id=context.user_id,
                service_account_id=payload.service_account_id,
                type=payload.type,
                name=payload.name,
                status=payload.status,
                input_template=payload.input_template,
                policy_profile=payload.policy_profile,
                budget_profile=payload.budget_profile,
                schedule=payload.schedule,
                connector_event=payload.connector_event,
                agent_handoff=payload.agent_handoff,
                next_run_at=payload.next_run_at,
            )
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=trigger.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="trigger.created",
            metadata={
                "trigger_id": trigger.id,
                "trigger_type": trigger.type.value,
                "status": trigger.status.value,
            },
            request=request,
        )
        return trigger.model_dump(mode="json")

    @app.get("/api/triggers")
    def list_triggers(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "triggers.read")
        return [
            trigger.model_dump(mode="json")
            for trigger in app.state.trigger_service.list_triggers(context.tenant_id)
        ]

    @app.get("/api/triggers/operations")
    def get_trigger_operations(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.read")
        return (
            TriggerOperationsService(
                stuck_after_seconds=(
                    app.state.settings.trigger_operations_stuck_after_seconds
                ),
            )
            .summarize(
                triggers=app.state.trigger_service.list_triggers(context.tenant_id),
                audit_events=app.state.audit_service.list_for_tenant(context.tenant_id),
                now=utc_now(),
                tenant_id=context.tenant_id,
            )
            .model_dump(mode="json")
        )

    @app.post("/api/connectors", status_code=status.HTTP_201_CREATED)
    def create_connector(
        payload: ConnectorCreateRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        current_connector_count = len(
            app.state.connector_registry.list_connectors(context.tenant_id)
        )
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT,
            requested_amount=current_connector_count + 1,
        )
        connector = app.state.connector_registry.register_connector(
            payload.to_definition_create(
                tenant_id=context.tenant_id,
                owner_user_id=context.user_id,
            )
        )
        app.state.audit_service.record(
            AuditEventCreate(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=None,
                event_type="connector.registered",
                metadata=connector_audit_metadata(connector),
            )
        )
        return connector.model_dump(mode="json")

    @app.post("/api/connectors/{connector_id}/mcp-credential")
    def capture_mcp_connector_credential(
        connector_id: str,
        payload: SecretCaptureResolveRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        if connector.type != ConnectorType.MCP_SERVER:
            raise ValueError("Connector is not an MCP server")
        if (
            connector.auth_mode == ConnectorAuthMode.NONE
            and connector.status != ConnectorStatus.NEEDS_REAUTH
        ):
            raise ValueError("Unauthenticated MCP servers do not accept a credential")
        value = payload.value.get_secret_value().strip()
        if not value:
            raise ValueError("MCP credential must not be blank")
        actions = (
            connector.credential_ref.required_actions
            if connector.credential_ref is not None
            else ["mcp.call"]
        )
        secret = app.state.secret_service.create_secret(
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            name=f"{connector.display_name} credential",
            value=value,
            scope=SecretScope(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                allowed_tool_names=[f"connector.{connector.id}.*"],
                actions=actions,
            ),
        )
        connector = app.state.connector_registry.update_connector_credential(
            context.tenant_id,
            connector.id,
            ConnectorCredentialRef(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                secret_ref_id=secret.id,
                required_actions=actions,
                secret_backend=secret.backend,
                secret_external_name=secret.external_name,
            ),
        )
        connector = app.state.connector_registry.update_connector_status(
            context.tenant_id,
            connector.id,
            ConnectorStatus.DRAFT,
        )
        app.state.audit_service.record(
            AuditEventCreate(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=None,
                event_type="connector.credential_updated",
                metadata=connector_audit_metadata(connector),
            )
        )
        return connector.model_dump(mode="json")

    @app.get("/api/connectors")
    def list_connectors(
        request: Request,
        workspace_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "connectors.read")
        return [
            connector.model_dump(mode="json")
            for connector in app.state.connector_registry.list_connectors(
                context.tenant_id,
                workspace_id=workspace_id,
            )
        ]

    @app.get("/api/connectors/{connector_id}")
    def get_connector(
        connector_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.read")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        return connector.model_dump(mode="json")

    @app.patch("/api/connectors/{connector_id}")
    def update_connector(
        connector_id: str,
        payload: ConnectorUpdateRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.update_connector(
            context.tenant_id,
            connector_id,
            payload,
        )
        app.state.audit_service.record(
            AuditEventCreate(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=None,
                event_type="connector.updated",
                metadata=connector_audit_metadata(connector)
                | {"updated_fields": sorted(payload.update_values().keys())},
            )
        )
        return connector.model_dump(mode="json")

    @app.post("/api/connectors/{connector_id}/enable")
    def enable_connector(
        connector_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        if (
            connector.auth_mode != ConnectorAuthMode.NONE
            and connector.credential_ref is None
        ):
            raise ValueError("Connector credential is required before enabling")
        if connector.type == ConnectorType.MCP_SERVER:
            try:
                connector = app.state.connector_registry.update_connector(
                    context.tenant_id,
                    connector_id,
                    ConnectorUpdateRequest(
                        capabilities=(
                            app.state.connector_dispatcher.discover_mcp_capabilities(
                                connector
                            )
                        )
                    ),
                )
            except ConnectorCredentialExpiredError:
                app.state.connector_registry.update_connector_status(
                    context.tenant_id,
                    connector_id,
                    ConnectorStatus.NEEDS_REAUTH,
                )
                raise
        connector = app.state.connector_registry.update_connector_status(
            context.tenant_id,
            connector_id,
            ConnectorStatus.ENABLED,
        )
        app.state.audit_service.record(
            AuditEventCreate(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=None,
                event_type="connector.enabled",
                metadata=connector_audit_metadata(connector),
            )
        )
        return connector.model_dump(mode="json")

    @app.post("/api/connectors/{connector_id}/disable")
    def disable_connector(
        connector_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.update_connector_status(
            context.tenant_id,
            connector_id,
            ConnectorStatus.DISABLED,
        )
        app.state.audit_service.record(
            AuditEventCreate(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=None,
                event_type="connector.disabled",
                metadata=connector_audit_metadata(connector),
            )
        )
        return connector.model_dump(mode="json")

    def resume_reconnected_connector_action(
        connector: ConnectorDefinition,
        oauth_result: Any,
        resolved_by_user_id: str,
        background_tasks: BackgroundTasks,
    ) -> None:
        app.state.connector_registry.update_connector_status(
            connector.tenant_id,
            connector.id,
            ConnectorStatus.ENABLED,
        )
        if not oauth_result.reconnect_action_id or not oauth_result.reconnect_run_id:
            return
        app.state.store.retry_connector_action_after_reconnect(
            connector.tenant_id,
            oauth_result.reconnect_action_id,
            connector_id=connector.id,
            resolved_by_user_id=resolved_by_user_id,
        )
        run = app.state.store.get_run(
            connector.tenant_id, oauth_result.reconnect_run_id
        )
        if app.state.settings.run_execution_dispatch_mode == "queue":
            queue = app.state.job_queue
            if queue is None:
                raise RedisQueueConfigurationError("job queue backend is disabled")
            queue.enqueue(
                JobType.RUN_EXECUTION,
                RunExecutionJob(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=run.user_id,
                    run_id=run.id,
                    requested_by_user_id=resolved_by_user_id,
                ),
                max_attempts=app.state.settings.worker_job_max_attempts,
            )
            return
        background_tasks.add_task(
            execute_chat_run_chain,
            run.tenant_id,
            run.id,
        )

    @app.post("/api/connectors/{connector_id}/oauth/authorize")
    def authorize_connector_oauth(
        connector_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        result = app.state.connector_oauth_service.build_authorization_url(
            connector=connector,
            requested_by_user_id=context.user_id,
            opener_origin=allowed_oauth_opener_origin(request, app.state.settings),
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="connector.oauth_authorization_started",
            metadata=connector_oauth_audit_metadata(connector, result),
            request=request,
        )
        return result.model_dump(mode="json")

    @app.post("/api/connectors/{connector_id}/reconnect")
    def reconnect_connector_action(
        connector_id: str,
        payload: ConnectorReconnectRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        run = app.state.store.get_run(context.tenant_id, payload.run_id)
        action = app.state.store.get_agent_action(context.tenant_id, payload.action_id)
        if (
            run.thread_id != payload.thread_id
            or action.run_id != run.id
            or action.thread_id != payload.thread_id
            or action.status != "uncertain"
            or action.failure_class != "connector_reconnect_required"
            or action.observation is None
            or action.observation.output.get("connector_id") != connector.id
        ):
            raise NotFoundError("Connector reconnect action is no longer available")
        result = app.state.connector_oauth_service.build_authorization_url(
            connector=connector,
            requested_by_user_id=context.user_id,
            reconnect_thread_id=payload.thread_id,
            reconnect_run_id=payload.run_id,
            reconnect_action_id=payload.action_id,
            opener_origin=allowed_oauth_opener_origin(request, app.state.settings),
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            user_id=context.user_id,
            run_id=run.id,
            event_type="connector.reconnect_started",
            metadata={
                "connector_id": connector.id,
                "action_id": action.id,
                "thread_id": payload.thread_id,
            },
            request=request,
        )
        return result.model_dump(mode="json")

    @app.post("/api/connectors/{connector_id}/oauth/callback")
    def complete_connector_oauth(
        connector_id: str,
        payload: ConnectorOAuthCallbackRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        result = app.state.connector_oauth_service.complete_callback(
            connector=connector,
            request=payload,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="connector.oauth_completed",
            metadata=connector_oauth_audit_metadata(connector, result),
            request=request,
        )
        resume_reconnected_connector_action(
            connector,
            result,
            context.user_id,
            background_tasks,
        )
        return result.model_dump(mode="json")

    @app.get(
        "/api/connectors/{connector_id}/oauth/callback",
        response_class=HTMLResponse,
    )
    def complete_connector_oauth_redirect(
        connector_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        code: str = Query(min_length=1),
        state: str = Query(min_length=1),
    ) -> HTMLResponse:
        session = app.state.connector_oauth_service.pending_authorization(state)
        if session.connector_id != connector_id:
            raise NotFoundError("Connector OAuth state does not match callback")
        connector = app.state.connector_registry.get_connector(
            session.tenant_id,
            connector_id,
        )
        result = app.state.connector_oauth_service.complete_callback(
            connector=connector,
            request=ConnectorOAuthCallbackRequest(code=code, state=state),
        )
        record_audit_event(
            app=app,
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            user_id=session.requested_by_user_id,
            run_id=result.reconnect_run_id,
            event_type="connector.oauth_completed",
            metadata=connector_oauth_audit_metadata(connector, result),
            request=request,
        )
        resume_reconnected_connector_action(
            connector,
            result,
            session.requested_by_user_id,
            background_tasks,
        )
        message = (
            "Connector reconnected. The agent action is resuming."
            if result.reconnect_action_id
            else "Connector connected successfully."
        )
        payload = json.dumps(
            {
                "type": "taroai.connector.oauth.completed",
                "connector_id": connector.id,
                "run_id": result.reconnect_run_id,
                "action_id": result.reconnect_action_id,
            }
        )
        target_origin = json.dumps(
            session.opener_origin or str(request.base_url).rstrip("/")
        )
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{message}</title>"
            "<style>body{font:15px system-ui;margin:0;display:grid;place-items:center;"
            "min-height:100vh;background:#f7f6f2;color:#242421}main{max-width:28rem;"
            "padding:2rem;border:1px solid #deddd7;border-radius:16px;background:white}"
            "h1{font-size:20px}p{color:#62625d}</style>"
            f"<main><h1>{message}</h1><p>You can close this window.</p></main>"
            f"<script>window.opener?.postMessage({payload}, {target_origin});"
            "window.setTimeout(()=>window.close(),900);</script>"
        )

    @app.get("/api/connectors/oauth/callback", response_class=HTMLResponse)
    def complete_connector_oauth_redirect_from_state(
        background_tasks: BackgroundTasks,
        request: Request,
        code: str = Query(min_length=1),
        state: str = Query(min_length=1),
    ) -> HTMLResponse:
        session = app.state.connector_oauth_service.pending_authorization(state)
        return complete_connector_oauth_redirect(
            connector_id=session.connector_id,
            background_tasks=background_tasks,
            request=request,
            code=code,
            state=state,
        )

    @app.post("/api/connectors/{connector_id}/oauth/refresh")
    def refresh_connector_oauth(
        connector_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.manage")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        result = app.state.connector_oauth_service.refresh(connector)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="connector.oauth_refreshed",
            metadata=connector_oauth_audit_metadata(connector, result),
            request=request,
        )
        return result.model_dump(mode="json")

    @app.post(
        "/api/connectors/{connector_id}/sync-jobs", status_code=status.HTTP_202_ACCEPTED
    )
    def enqueue_connector_sync_job(
        connector_id: str,
        payload: ConnectorSyncJobCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.sync")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )
        for document in payload.documents:
            if document.tenant_id != context.tenant_id:
                raise TenantAccessError("connector sync document is not in tenant")
            if document.workspace_id != connector.workspace_id:
                raise TenantAccessError("connector sync document is not in workspace")
            if document.connector_id != connector.id:
                raise TenantAccessError(
                    "connector sync document does not match connector"
                )
        queue = app.state.job_queue
        if queue is None:
            raise RedisQueueConfigurationError("job queue backend is disabled")
        run = app.state.store.create_run(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            payload=RunCreate(
                workspace_id=connector.workspace_id,
                agent_id="connector_sync",
                message=f"Sync connector {connector.id} into knowledge base.",
                mode=RunMode.AUTONOMOUS,
            ),
        )
        queued_run = app.state.store.update_run_status(
            context.tenant_id,
            run.id,
            RunStatus.QUEUED,
        )
        job_payload = ConnectorSyncJob(
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            connector_id=connector.id,
            run_id=queued_run.id,
            knowledge_base_id=payload.knowledge_base_id,
            requested_by_user_id=context.user_id,
            documents=payload.documents,
            acl_mapping=payload.acl_mapping,
            cursor=payload.cursor,
        )
        job = queue.enqueue(
            JobType.CONNECTOR_SYNC,
            job_payload,
            max_attempts=app.state.settings.worker_job_max_attempts,
        )
        app.state.connector_registry.update_connector_sync_state(
            context.tenant_id,
            connector.id,
            ConnectorSyncStateUpdate(
                status=ConnectorSyncStatus.PENDING,
                run_id=queued_run.id,
                job_id=job.id,
                knowledge_base_id=payload.knowledge_base_id,
                cursor=payload.cursor,
            ),
        )
        app.state.store.append_run_event(
            queued_run,
            "connector.sync_queued",
            {
                "job_id": job.id,
                "queue": JobType.CONNECTOR_SYNC.value,
                "connector_id": connector.id,
                "knowledge_base_id": payload.knowledge_base_id,
            },
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            user_id=context.user_id,
            run_id=queued_run.id,
            event_type="connector.sync_requested",
            metadata=connector_sync_audit_metadata(
                connector_id=connector.id,
                knowledge_base_id=payload.knowledge_base_id,
                documents=payload.documents,
                cursor=payload.cursor,
                job_id=job.id,
            ),
            request=request,
        )
        return {
            "job_id": job.id,
            "run_id": queued_run.id,
            "status": job.status.value,
            "queue": JobType.CONNECTOR_SYNC.value,
        }

    @app.post(
        "/api/connectors/{connector_id}/invoke", status_code=status.HTTP_202_ACCEPTED
    )
    def invoke_connector(
        connector_id: str,
        payload: ConnectorInvocationCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "connectors.invoke")
        connector = app.state.connector_registry.get_connector(
            context.tenant_id,
            connector_id,
        )

        run = app.state.store.get_run(context.tenant_id, payload.run_id)
        if run.workspace_id != connector.workspace_id:
            raise TenantAccessError("connector workspace does not match run workspace")

        decision = app.state.connector_invocation_service.evaluate(
            connector=connector,
            request=payload.to_invocation_request(
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                connector_id=connector.id,
            ),
        )

        if decision.status == ConnectorInvocationStatus.DENIED:
            record_audit_event(
                app=app,
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=payload.run_id,
                event_type="connector.invocation_denied",
                metadata=connector_invocation_audit_metadata(
                    decision,
                    connector=connector,
                ),
                request=request,
            )
            raise TenantAccessError(decision.reason or "connector invocation denied")

        if decision.status == ConnectorInvocationStatus.APPROVAL_REQUIRED:
            try:
                app.state.connector_dispatcher.preflight(
                    connector,
                    payload.tool_input,
                    decision.tool_name,
                )
            except ConnectorDispatchError as error:
                record_audit_event(
                    app=app,
                    tenant_id=context.tenant_id,
                    workspace_id=connector.workspace_id,
                    user_id=context.user_id,
                    run_id=payload.run_id,
                    event_type="connector.preflight_failed",
                    metadata=connector_invocation_audit_metadata(
                        decision,
                        connector=connector,
                        error_code="connector_preflight_failed",
                    ),
                    request=request,
                )
                raise error
            approval, created = get_or_create_connector_approval(
                app=app,
                tenant_id=context.tenant_id,
                run_id=payload.run_id,
                provider=connector.display_name,
                connector_id=connector.id,
                capability_name=decision.capability_name,
                tool_name=decision.tool_name,
                step_id=decision.step_id,
                risk_level=decision.risk_level,
                input_keys=decision.input_keys,
                missing_scopes=decision.missing_scopes,
                tool_input=payload.tool_input,
                granted_scopes=payload.granted_scopes,
            )
            if created:
                emit_action_manifest_event(app, approval)
            record_audit_event(
                app=app,
                tenant_id=context.tenant_id,
                workspace_id=connector.workspace_id,
                user_id=context.user_id,
                run_id=payload.run_id,
                event_type="connector.approval_required",
                metadata=connector_invocation_audit_metadata(
                    decision,
                    connector=connector,
                    approval_id=approval.id,
                ),
                request=request,
            )
            response_body = decision.model_dump(mode="json")
            response_body["approval_id"] = approval.id
            return response_body

        approved_approval = None
        if decision.approval_required:
            approved_approval = require_approved_connector_approval(
                app=app,
                tenant_id=context.tenant_id,
                run_id=payload.run_id,
                approval_id=payload.approval_id,
                connector_id=connector.id,
                capability_name=decision.capability_name,
                step_id=decision.step_id,
            )
        dispatch_result = execute_connector_invocation(
            app=app,
            context=context,
            request=request,
            connector=connector,
            decision=decision,
            tool_input=payload.tool_input,
            approval=approved_approval,
        )
        response_body = decision.model_dump(mode="json")
        if dispatch_result is not None:
            response_body["output"] = dispatch_result.output
        return response_body

    @app.post("/api/triggers/connector-events", status_code=status.HTTP_202_ACCEPTED)
    def ingest_connector_event(
        payload: ConnectorEventIngestRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.invoke")
        event = ConnectorEvent(
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            connector_id=payload.connector_id,
            event_type=payload.event_type,
            external_event_id=payload.external_event_id,
            payload=payload.payload,
        )
        matched_triggers = match_connector_event_triggers(
            app.state.trigger_service.list_triggers(context.tenant_id),
            event,
        )
        runs: list[ConnectorEventIngestRun] = []
        for trigger in matched_triggers:
            try:
                run_request = app.state.trigger_service.build_run_request(
                    tenant_id=context.tenant_id,
                    trigger_id=trigger.id,
                    invoked_by_user_id=trigger.service_account_id,
                    invocation_payload=event.payload,
                )
            except TriggerDisabledError as error:
                raise TenantAccessError(str(error)) from error
            run = app.state.store.create_run(
                tenant_id=context.tenant_id,
                user_id=run_request.requested_by_user_id,
                payload=RunCreate(
                    workspace_id=run_request.workspace_id,
                    agent_id=run_request.agent_id,
                    message=run_request.message,
                    mode=RunMode.AUTONOMOUS,
                ),
            )
            record_audit_event(
                app=app,
                tenant_id=context.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run_request.requested_by_user_id,
                run_id=run.id,
                event_type="trigger.invoked",
                metadata={
                    "trigger_id": run_request.trigger_id,
                    "trigger_type": run_request.trigger_type.value,
                    "run_id": run.id,
                    "invocation_payload_keys": run_request.invocation_payload_keys,
                    "connector_id": event.connector_id,
                    "connector_event_type": event.event_type,
                    "connector_external_event_id": event.external_event_id,
                },
                request=request,
            )
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=run.id,
                meter_type="trigger_invocation_count",
                quantity=1,
                unit="invocation",
                metadata={
                    "trigger_id": run_request.trigger_id,
                    "trigger_type": run_request.trigger_type.value,
                    "connector_id": event.connector_id,
                    "connector_event_type": event.event_type,
                },
            )
            runs.append(
                ConnectorEventIngestRun(
                    trigger_id=run_request.trigger_id,
                    run_id=run.id,
                    status=run.status.value,
                    events_url=f"/api/runs/{run.id}/events",
                )
            )
        return ConnectorEventIngestResponse(
            connector_id=event.connector_id,
            event_type=event.event_type,
            external_event_id=event.external_event_id,
            matched_trigger_count=len(matched_triggers),
            runs=runs,
        ).model_dump(mode="json")

    @app.get("/api/triggers/{trigger_id}")
    def get_trigger(
        trigger_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.read")
        return app.state.trigger_service.get_trigger(
            context.tenant_id,
            trigger_id,
        ).model_dump(mode="json")

    @app.post("/api/triggers/{trigger_id}/enable")
    def enable_trigger(
        trigger_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.manage")
        trigger = app.state.trigger_service.enable_trigger(
            context.tenant_id, trigger_id
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=trigger.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="trigger.enabled",
            metadata={"trigger_id": trigger.id, "trigger_type": trigger.type.value},
            request=request,
        )
        return trigger.model_dump(mode="json")

    @app.post("/api/triggers/{trigger_id}/disable")
    def disable_trigger(
        trigger_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.manage")
        trigger = app.state.trigger_service.disable_trigger(
            context.tenant_id, trigger_id
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=trigger.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="trigger.disabled",
            metadata={"trigger_id": trigger.id, "trigger_type": trigger.type.value},
            request=request,
        )
        return trigger.model_dump(mode="json")

    @app.delete("/api/triggers/{trigger_id}")
    def delete_trigger(
        trigger_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.manage")
        trigger = app.state.trigger_service.delete_trigger(
            context.tenant_id, trigger_id
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=trigger.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="trigger.deleted",
            metadata={"trigger_id": trigger.id, "trigger_type": trigger.type.value},
            request=request,
        )
        return trigger.model_dump(mode="json")

    @app.post("/api/triggers/{trigger_id}/invoke", status_code=status.HTTP_202_ACCEPTED)
    def invoke_trigger(
        trigger_id: str,
        payload: TriggerInvokeRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.invoke")
        try:
            run_request = app.state.trigger_service.build_run_request(
                tenant_id=context.tenant_id,
                trigger_id=trigger_id,
                invoked_by_user_id=context.user_id,
                invocation_payload=payload.payload,
            )
        except TriggerDisabledError as error:
            raise TenantAccessError(str(error)) from error
        run = app.state.store.create_run(
            tenant_id=context.tenant_id,
            user_id=run_request.requested_by_user_id,
            payload=RunCreate(
                workspace_id=run_request.workspace_id,
                agent_id=run_request.agent_id,
                message=run_request.message,
                mode=RunMode.AUTONOMOUS,
            ),
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=run.workspace_id,
            user_id=context.user_id,
            run_id=run.id,
            event_type="trigger.invoked",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
                "run_id": run.id,
                "invocation_payload_keys": run_request.invocation_payload_keys,
            },
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=run.id,
            meter_type="trigger_invocation_count",
            quantity=1,
            unit="invocation",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
            },
        )
        return TriggerInvokeResponse(
            trigger_id=run_request.trigger_id,
            run_id=run.id,
            status=run.status.value,
            events_url=f"/api/runs/{run.id}/events",
        ).model_dump(mode="json")

    @app.post(
        "/api/triggers/{trigger_id}/webhook", status_code=status.HTTP_202_ACCEPTED
    )
    async def invoke_webhook_trigger(
        trigger_id: str,
        request: Request,
        response: Response,
        tenant_id: str = Header(alias="X-Tenant-ID"),
        timestamp: str | None = Header(
            default=None,
            alias="X-Taroai-Webhook-Timestamp",
        ),
        signature: str | None = Header(
            default=None,
            alias="X-Taroai-Webhook-Signature",
        ),
        webhook_delivery_id: str | None = Header(
            default=None,
            alias="X-Taroai-Webhook-Delivery-ID",
        ),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict:
        body = await request.body()
        verification = app.state.trigger_webhook_verifier.verify(
            body=body,
            timestamp_header=timestamp,
            signature_header=signature,
        )
        idempotency_request = build_idempotency_request(
            tenant_id=tenant_id,
            key=select_webhook_idempotency_key(webhook_delivery_id, idempotency_key),
            method=TRIGGER_WEBHOOK_METHOD,
            path=trigger_webhook_path(trigger_id),
            payload={
                "trigger_id": trigger_id,
                "webhook_body_sha256": verification.body_sha256,
            },
        )
        replay_record = find_idempotent_replay(app.state.store, idempotency_request)
        if replay_record is not None:
            response.status_code = replay_record.status_code
            return replay_record.response_body

        trigger = app.state.trigger_service.get_trigger(tenant_id, trigger_id)
        if trigger.type != TriggerType.WEBHOOK:
            raise TenantAccessError("trigger is not a webhook trigger")
        payload = parse_webhook_json_payload(body)
        try:
            run_request = app.state.trigger_service.build_run_request(
                tenant_id=tenant_id,
                trigger_id=trigger_id,
                invoked_by_user_id=None,
                invocation_payload=payload,
            )
        except TriggerDisabledError as error:
            raise TenantAccessError(str(error)) from error
        run = app.state.store.create_run(
            tenant_id=tenant_id,
            user_id=run_request.requested_by_user_id,
            payload=RunCreate(
                workspace_id=run_request.workspace_id,
                agent_id=run_request.agent_id,
                message=run_request.message,
                mode=RunMode.AUTONOMOUS,
            ),
        )
        record_audit_event(
            app=app,
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=run_request.requested_by_user_id,
            run_id=run.id,
            event_type="trigger.invoked",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
                "run_id": run.id,
                "invocation_payload_keys": run_request.invocation_payload_keys,
                "webhook_signature_verified": verification.verified,
                "webhook_signature_algorithm": verification.algorithm,
                "webhook_body_sha256": verification.body_sha256,
            },
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=tenant_id,
            run_id=run.id,
            meter_type="trigger_invocation_count",
            quantity=1,
            unit="invocation",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
            },
        )
        response_body = TriggerInvokeResponse(
            trigger_id=run_request.trigger_id,
            run_id=run.id,
            status=run.status.value,
            events_url=f"/api/runs/{run.id}/events",
        ).model_dump(mode="json")
        save_idempotent_response(
            app.state.store,
            idempotency_request,
            status.HTTP_202_ACCEPTED,
            response_body,
        )
        return response_body

    @app.post(
        "/api/triggers/{trigger_id}/agent-handoff", status_code=status.HTTP_202_ACCEPTED
    )
    def invoke_agent_handoff_trigger(
        trigger_id: str,
        payload: AgentHandoffRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "triggers.invoke")
        trigger = app.state.trigger_service.get_trigger(context.tenant_id, trigger_id)
        if trigger.agent_handoff is not None:
            for required_permission in trigger.agent_handoff.required_permissions:
                require_permission(request, context, required_permission)
        source_run = app.state.store.get_run(context.tenant_id, payload.source_run_id)
        target_depth = assert_agent_handoff_allowed(trigger, source_run, payload)
        try:
            run_request = app.state.trigger_service.build_run_request(
                tenant_id=context.tenant_id,
                trigger_id=trigger_id,
                invoked_by_user_id=trigger.service_account_id,
                invocation_payload=payload.handoff_input,
            )
        except TriggerDisabledError as error:
            raise TenantAccessError(str(error)) from error
        run = app.state.store.create_run(
            tenant_id=context.tenant_id,
            user_id=run_request.requested_by_user_id,
            payload=RunCreate(
                workspace_id=run_request.workspace_id,
                agent_id=run_request.agent_id,
                message=run_request.message,
                mode=RunMode.AUTONOMOUS,
            ),
        )
        handoff_metadata = {
            "trigger_id": run_request.trigger_id,
            "source_run_id": source_run.id,
            "source_agent_id": source_run.agent_id,
            "target_run_id": run.id,
            "target_agent_id": run.agent_id,
            "reason_code": payload.reason_code,
            "handoff_depth": target_depth,
            "max_depth": (
                trigger.agent_handoff.max_depth
                if trigger.agent_handoff
                else target_depth
            ),
            "handoff_input_keys": sorted(payload.handoff_input.keys()),
        }
        app.state.store.append_run_event(
            source_run,
            "agent.handoff.requested",
            handoff_metadata,
        )
        app.state.store.append_run_event(
            run,
            "agent.handoff.received",
            handoff_metadata,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run_request.requested_by_user_id,
            run_id=run.id,
            event_type="trigger.invoked",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
                "run_id": run.id,
                "source_run_id": source_run.id,
                "source_agent_id": source_run.agent_id,
                "target_agent_id": run.agent_id,
                "handoff_depth": target_depth,
                "max_depth": (
                    trigger.agent_handoff.max_depth
                    if trigger.agent_handoff
                    else target_depth
                ),
                "reason_code": payload.reason_code,
                "invocation_payload_keys": run_request.invocation_payload_keys,
            },
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=run.id,
            meter_type="trigger_invocation_count",
            quantity=1,
            unit="invocation",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
                "source_run_id": source_run.id,
                "target_agent_id": run.agent_id,
            },
        )
        return AgentHandoffResponse(
            trigger_id=run_request.trigger_id,
            run_id=run.id,
            status=run.status.value,
            events_url=f"/api/runs/{run.id}/events",
        ).model_dump(mode="json")

    @app.get("/api/runs/{run_id}")
    def get_run(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        return app.state.store.get_run(context.tenant_id, run_id).model_dump(
            mode="json"
        )

    @app.get("/api/runs/{run_id}/state")
    def get_run_state(
        run_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        try:
            snapshot = app.state.store.get_runtime_state(context.tenant_id, run_id)
        except NotFoundError:
            run = app.state.store.get_run(context.tenant_id, run_id)
            return {
                **app.state.runtime._initial_state(run).model_dump(mode="json"),
                "updated_at": run.updated_at,
            }
        return {
            **snapshot.to_runtime_state_payload(),
            "updated_at": snapshot.updated_at,
        }

    @app.get("/api/runs/{run_id}/events")
    def get_run_events(
        run_id: str,
        after_sequence: int | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        context: RequestContext = Depends(get_request_context),
    ) -> StreamingResponse:
        return stream_run_events(
            app, context.tenant_id, run_id, after_sequence, last_event_id
        )

    @app.post("/api/runs/{run_id}/execute")
    def execute_run(
        run_id: str,
        background_tasks: BackgroundTasks,
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
                {
                    "job_id": job.id,
                    "queue": app.state.settings.run_execution_queue_name,
                },
            )
            response.status_code = status.HTTP_202_ACCEPTED
            return RunQueuedResponse(
                run_id=run_id,
                job_id=job.id,
                queue=app.state.settings.run_execution_queue_name,
            ).model_dump(mode="json")
        state = app.state.runtime.execute_run(context.tenant_id, run_id)
        if state.graph_failure_code == "model_policy_denied":
            raise ModelPolicyDeniedError(
                state.graph_failure_detail or "model request denied by policy"
            )
        dispatch_next_after_terminal_run(
            context.tenant_id,
            run_id,
            context.user_id,
            background_tasks,
        )
        return state.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        background_tasks: BackgroundTasks,
        payload: RunCancelRequest | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        resolved_payload = payload or RunCancelRequest()
        workflow = app.state.store.get_workflow_for_parent_run(
            context.tenant_id, run_id
        )
        if workflow is not None:
            app.state.workflow_coordinator.cancel(
                context.tenant_id, workflow.id, context.user_id
            )
            run = app.state.store.get_run(context.tenant_id, run_id)
        else:
            run = app.state.runtime.cancel_run(
                tenant_id=context.tenant_id,
                run_id=run_id,
                cancelled_by_user_id=context.user_id,
                reason_code=resolved_payload.reason_code,
            )
        dispatch_next_after_terminal_run(
            context.tenant_id,
            run_id,
            context.user_id,
            background_tasks,
        )
        return run.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/actions/{action_id}/resolve")
    def resolve_uncertain_agent_action(
        run_id: str,
        action_id: str,
        payload: UncertainActionResolutionRequest,
        background_tasks: BackgroundTasks,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        run = app.state.store.get_run(context.tenant_id, run_id)
        action = app.state.store.get_agent_action(context.tenant_id, action_id)
        if action.run_id != run.id:
            raise NotFoundError(f"Agent action not found: {action_id}")
        resolved = app.state.store.resolve_uncertain_agent_action(
            context.tenant_id,
            action_id,
            resolution=payload.resolution,
            resolved_by_user_id=context.user_id,
            note=payload.note,
        )
        if app.state.settings.run_execution_dispatch_mode == "queue":
            queue = app.state.job_queue
            if queue is None:
                raise RedisQueueConfigurationError("job queue backend is disabled")
            queue.enqueue(
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
        else:
            background_tasks.add_task(
                execute_chat_run_chain,
                run.tenant_id,
                run.id,
            )
        return resolved.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(
        run_id: str,
        background_tasks: BackgroundTasks,
        response: Response,
        payload: RunRetryRequest | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        resolved_payload = payload or RunRetryRequest()
        if app.state.settings.run_execution_dispatch_mode == "queue":
            queue = app.state.job_queue
            if queue is None:
                raise RedisQueueConfigurationError("job queue backend is disabled")
            run = app.state.runtime.request_run_retry(
                tenant_id=context.tenant_id,
                run_id=run_id,
                requested_by_user_id=context.user_id,
                reason_code=resolved_payload.reason_code,
            )
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
                {
                    "job_id": job.id,
                    "queue": app.state.settings.run_execution_queue_name,
                    "reason": "retry",
                },
            )
            response.status_code = status.HTTP_202_ACCEPTED
            return RunQueuedResponse(
                run_id=run_id,
                job_id=job.id,
                queue=app.state.settings.run_execution_queue_name,
            ).model_dump(mode="json")
        state = app.state.runtime.retry_run(
            tenant_id=context.tenant_id,
            run_id=run_id,
            requested_by_user_id=context.user_id,
            reason_code=resolved_payload.reason_code,
        )
        dispatch_next_after_terminal_run(
            context.tenant_id,
            run_id,
            context.user_id,
            background_tasks,
        )
        return state.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/approvals")
    def resolve_approval(
        run_id: str,
        payload: ApprovalResolveRequest,
        background_tasks: BackgroundTasks,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        idempotency_request = build_idempotency_request(
            tenant_id=context.tenant_id,
            key=idempotency_key,
            method=RUN_APPROVAL_METHOD,
            path=run_approval_path(run_id),
            payload=payload,
        )
        replay_record = find_idempotent_replay(app.state.store, idempotency_request)
        if replay_record is not None:
            response.status_code = replay_record.status_code
            return replay_record.response_body

        connector_approval = find_connector_approval(
            app=app,
            tenant_id=context.tenant_id,
            run_id=run_id,
            approval_id=payload.approval_id,
        )
        if connector_approval is not None:
            resolved_approval = app.state.store.resolve_approval_request(
                tenant_id=context.tenant_id,
                run_id=run_id,
                approval_id=payload.approval_id,
                approved_by_user_id=context.user_id,
            )
            emit_action_manifest_event(app, resolved_approval)
            response_body = {
                "run_id": run_id,
                "approval_id": resolved_approval.id,
                "status": resolved_approval.status.value,
            }
        else:
            state = app.state.runtime.resume_after_approval(
                tenant_id=context.tenant_id,
                run_id=run_id,
                approval_id=payload.approval_id,
                approved_by_user_id=context.user_id,
            )
            response_body = state.model_dump(mode="json")
            workflow = app.state.store.get_workflow_for_parent_run(
                context.tenant_id, run_id
            )
            if workflow is not None and workflow.status == "running":
                ready = app.state.workflow_coordinator.ready_runs(
                    context.tenant_id, workflow.id
                )
                dispatch_workflow_runs(ready, context.user_id, background_tasks)
                response_body["workflow"] = workflow.model_dump(
                    mode="json", by_alias=True
                )
            dispatch_next_after_terminal_run(
                context.tenant_id,
                run_id,
                context.user_id,
                background_tasks,
            )
        save_idempotent_response(
            app.state.store,
            idempotency_request,
            status.HTTP_200_OK,
            response_body,
        )
        return response_body

    @app.post("/api/runs/{run_id}/approvals/reject")
    def reject_approval(
        run_id: str,
        payload: ApprovalRejectRequest,
        background_tasks: BackgroundTasks,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        idempotency_request = build_idempotency_request(
            tenant_id=context.tenant_id,
            key=idempotency_key,
            method=RUN_APPROVAL_METHOD,
            path=run_approval_reject_path(run_id),
            payload=payload,
        )
        replay_record = find_idempotent_replay(app.state.store, idempotency_request)
        if replay_record is not None:
            response.status_code = replay_record.status_code
            return replay_record.response_body

        connector_approval = find_connector_approval(
            app=app,
            tenant_id=context.tenant_id,
            run_id=run_id,
            approval_id=payload.approval_id,
        )
        if connector_approval is not None:
            rejected_approval = reject_action_manifest(
                app,
                connector_approval,
                context.user_id,
            )
            emit_action_manifest_event(app, rejected_approval)
            response_body = {
                "run_id": run_id,
                "approval_id": rejected_approval.id,
                "status": rejected_approval.status.value,
            }
            dispatch_next_after_terminal_run(
                context.tenant_id,
                run_id,
                context.user_id,
                background_tasks,
            )
        else:
            state = app.state.runtime.reject_approval(
                tenant_id=context.tenant_id,
                run_id=run_id,
                approval_id=payload.approval_id,
                rejected_by_user_id=context.user_id,
            )
            response_body = state.model_dump(mode="json")
            dispatch_next_after_terminal_run(
                context.tenant_id,
                run_id,
                context.user_id,
                background_tasks,
            )
        save_idempotent_response(
            app.state.store,
            idempotency_request,
            status.HTTP_200_OK,
            response_body,
        )
        return response_body

    @app.get("/api/workflows/{workflow_id}")
    def get_workflow(
        workflow_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        workflow = app.state.store.get_workflow(context.tenant_id, workflow_id)
        return {
            "workflow": workflow.model_dump(mode="json", by_alias=True),
            "tasks": [
                task.model_dump(mode="json")
                for task in app.state.store.list_workflow_tasks(
                    context.tenant_id, workflow.id
                )
            ],
        }

    @app.patch("/api/workflows/{workflow_id}/preview")
    def update_workflow_preview(
        workflow_id: str,
        payload: WorkflowPreviewUpdate,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        spec = payload.spec
        workflow = app.state.store.get_workflow(context.tenant_id, workflow_id)
        if workflow.status != "awaiting_approval":
            raise HTTPException(
                status_code=409,
                detail="Only a pending workflow preview can be edited",
            )
        tasks = app.state.store.list_workflow_tasks(
            context.tenant_id, workflow.id
        )
        if any(task.status != "pending" for task in tasks):
            raise HTTPException(
                status_code=409,
                detail="Workflow tasks have already started",
            )
        current_phases = {
            task.id: phase.id
            for phase in workflow.spec.phases
            for task in phase.tasks
        }
        revised_phases = {
            task.id: phase.id for phase in spec.phases for task in phase.tasks
        }
        if revised_phases != current_phases:
            raise HTTPException(
                status_code=409,
                detail="Preview edits cannot add, remove, or move workflow tasks",
            )

        updated = app.state.store.update_workflow(
            context.tenant_id,
            workflow.id,
            spec=spec,
        )
        app.state.store.cancel_pending_approval_requests(
            context.tenant_id,
            workflow.parent_run_id,
            context.user_id,
        )
        approval = app.state.store.create_approval_request(
            context.tenant_id,
            workflow.parent_run_id,
            f"workflow:{workflow.id}",
            f"Approve workflow: {len(revised_phases)} steps",
            kind="workflow",
            subject_type="workflow",
            subject_id=workflow.id,
            preview_payload=spec.model_dump(mode="json", by_alias=True),
            validation_payload={"valid": True},
        )
        updated = app.state.store.update_workflow(
            context.tenant_id,
            workflow.id,
            approval_id=approval.id,
        )
        state = app.state.runtime._load_state(
            context.tenant_id, workflow.parent_run_id
        )
        state.approval_id = approval.id
        app.state.runtime._save_state(state)
        parent_run = app.state.store.get_run(
            context.tenant_id, workflow.parent_run_id
        )
        app.state.store.append_run_event(
            parent_run,
            "workflow_preview",
            {
                "workflowId": workflow.id,
                "previewId": workflow.id,
                "status": "pending",
                "spec": spec.model_dump(mode="json", by_alias=True),
            },
        )
        app.state.store.append_run_event(
            parent_run,
            "workflow.preview_updated",
            {"workflowId": workflow.id, "approvalId": approval.id},
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workflow.workspace_id,
            user_id=context.user_id,
            run_id=workflow.parent_run_id,
            event_type="workflow.preview_updated",
            metadata={
                "workflow_id": workflow.id,
                "approval_id": approval.id,
                "task_count": len(revised_phases),
            },
        )
        return {
            "workflow": updated.model_dump(mode="json", by_alias=True),
            "approval_id": approval.id,
        }

    @app.post("/api/workflows/{workflow_id}/pause")
    def pause_workflow(
        workflow_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.workflow_coordinator.pause(
            context.tenant_id, workflow_id
        ).model_dump(mode="json", by_alias=True)

    @app.post("/api/workflows/{workflow_id}/resume")
    def resume_workflow(
        workflow_id: str,
        background_tasks: BackgroundTasks,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        workflow, ready = app.state.workflow_coordinator.resume(
            context.tenant_id, workflow_id
        )
        dispatch_workflow_runs(ready, context.user_id, background_tasks)
        return workflow.model_dump(mode="json", by_alias=True)

    @app.post("/api/workflows/{workflow_id}/cancel")
    def cancel_workflow(
        workflow_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        return app.state.workflow_coordinator.cancel(
            context.tenant_id, workflow_id, context.user_id
        ).model_dump(mode="json", by_alias=True)

    @app.post("/api/workflows/{workflow_id}/tasks/{task_id}/retry")
    def retry_workflow_task(
        workflow_id: str,
        task_id: str,
        background_tasks: BackgroundTasks,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        ready = app.state.workflow_coordinator.retry_task(
            context.tenant_id, workflow_id, task_id
        )
        dispatch_workflow_runs(ready, context.user_id, background_tasks)
        return get_workflow(workflow_id, context)

    @app.get("/api/workflows/{workflow_id}/tasks/{task_id}/messages")
    def list_workflow_task_messages(
        workflow_id: str,
        task_id: str,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        workflow = app.state.store.get_workflow(context.tenant_id, workflow_id)
        task = next(
            (
                item
                for item in app.state.store.list_workflow_tasks(
                    context.tenant_id, workflow.id
                )
                if item.task_id == task_id
            ),
            None,
        )
        if task is None:
            raise NotFoundError(f"Workflow task not found: {task_id}")
        messages = (
            app.state.store.list_chat_messages(
                context.tenant_id, task.child_thread_id
            )
            if task.child_thread_id
            else []
        )
        return {
            "task": task.model_dump(mode="json"),
            "workerThreadId": task.child_thread_id,
            "messages": [
                message.model_dump(mode="json", exclude={"execution_content"})
                for message in messages
            ],
        }

    @app.post("/api/secret-captures/{request_id}")
    def resolve_secret_capture(
        request_id: str,
        payload: SecretCaptureResolveRequest,
        background_tasks: BackgroundTasks,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        capture = app.state.store.get_secret_capture_request(
            context.tenant_id, request_id
        )
        if capture.status != "pending":
            raise ValueError("secret capture request is no longer pending")
        run = app.state.store.get_run(context.tenant_id, capture.run_id)
        if run.workspace_id != capture.workspace_id:
            raise TenantAccessError("secret capture is not in the run workspace")
        connector = (
            app.state.connector_registry.get_connector(
                context.tenant_id, capture.connector_id
            )
            if capture.connector_id
            else None
        )
        if connector is not None:
            require_permission(request, context, "connectors.manage")
            if connector.workspace_id != capture.workspace_id:
                raise TenantAccessError("connector is not in the secret capture workspace")
        elif run.user_id != context.user_id:
            raise TenantAccessError("secret capture is not owned by this run user")
        secret = app.state.secret_service.create_secret(
            tenant_id=context.tenant_id,
            workspace_id=capture.workspace_id,
            name=capture.name,
            value=payload.value.get_secret_value(),
            scope=SecretScope(
                tenant_id=context.tenant_id,
                workspace_id=capture.workspace_id,
                allowed_tool_names=(
                    [f"connector.{connector.id}.*"]
                    if connector is not None
                    else ([capture.tool_name] if capture.tool_name else [])
                ),
                actions=capture.actions,
            ),
        )
        if connector is not None:
            app.state.connector_registry.update_connector_credential(
                context.tenant_id,
                connector.id,
                ConnectorCredentialRef(
                    tenant_id=context.tenant_id,
                    workspace_id=connector.workspace_id,
                    secret_ref_id=secret.id,
                    required_actions=capture.actions,
                    secret_backend=secret.backend,
                    secret_external_name=secret.external_name,
                ),
            )
        resolved = app.state.store.resolve_secret_capture_request(
            context.tenant_id, request_id, secret.id
        )
        if capture.action_id and capture.connector_id:
            app.state.store.retry_connector_action_after_reconnect(
                context.tenant_id,
                capture.action_id,
                connector_id=capture.connector_id,
                resolved_by_user_id=context.user_id,
            )
            run = app.state.store.update_run_status(
                context.tenant_id, capture.run_id, RunStatus.RUNNING
            )
            if app.state.settings.run_execution_dispatch_mode == "queue":
                dispatch_workflow_runs([run], context.user_id, background_tasks)
            else:
                background_tasks.add_task(
                    execute_chat_run_chain, run.tenant_id, run.id
                )
        return {
            "requestId": resolved.id,
            "status": resolved.status,
            "secretRefId": resolved.secret_ref_id,
        }

    @app.get("/api/runs/{run_id}/artifacts")
    def list_artifacts(
        run_id: str,
        request: Request,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        return list_or_page_created_at_records(
            app.state.store.list_artifacts(context.tenant_id, run_id),
            request,
            page,
        )

    @app.post("/api/runs/{run_id}/artifacts", status_code=status.HTTP_201_CREATED)
    def create_rich_artifact(
        run_id: str,
        payload: RichArtifactCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.write")
        if payload.run_id != run_id:
            raise ValueError("Artifact Run path and payload must match")
        return app.state.artifact_service.create(context.tenant_id, payload).model_dump(
            mode="json"
        )

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(
        artifact_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.read")
        artifact = app.state.store.get_artifact(context.tenant_id, artifact_id)
        if artifact.storage_object_id:
            storage_object = app.state.storage_catalog.get(
                context.tenant_id, artifact.storage_object_id
            )
            require_storage_read_access(request, context, storage_object)
        return artifact.model_dump(mode="json")

    @app.get("/api/artifacts/{artifact_id}/preview")
    def preview_artifact(
        artifact_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.read")
        artifact = app.state.store.get_artifact(context.tenant_id, artifact_id)
        if artifact.storage_object_id:
            require_storage_read_access(
                request,
                context,
                app.state.storage_catalog.get(
                    context.tenant_id, artifact.storage_object_id
                ),
            )
        return app.state.artifact_service.preview(
            context.tenant_id, artifact_id
        ).model_dump(mode="json")

    @app.get("/api/artifacts/{artifact_id}/source")
    def get_artifact_source(
        artifact_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.read")
        artifact = app.state.store.get_artifact(context.tenant_id, artifact_id)
        if artifact.storage_object_id:
            require_storage_read_access(
                request,
                context,
                app.state.storage_catalog.get(
                    context.tenant_id, artifact.storage_object_id
                ),
            )
        return app.state.artifact_service.source(
            context.tenant_id, artifact_id
        ).model_dump(mode="json")

    @app.get("/api/artifacts/{artifact_id}/diff")
    def diff_artifact(
        artifact_id: str,
        request: Request,
        compare_to: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.read")
        artifact = app.state.store.get_artifact(context.tenant_id, artifact_id)
        if artifact.storage_object_id:
            require_storage_read_access(
                request,
                context,
                app.state.storage_catalog.get(
                    context.tenant_id, artifact.storage_object_id
                ),
            )
        return app.state.artifact_service.diff(
            context.tenant_id, artifact_id, compare_to
        ).model_dump(mode="json")

    @app.post(
        "/api/artifacts/{artifact_id}/share",
        status_code=status.HTTP_201_CREATED,
    )
    def create_artifact_share_link(
        artifact_id: str,
        payload: ArtifactShareCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sharing.manage")
        require_external_share_links_enabled(request)
        artifact = app.state.store.get_artifact(context.tenant_id, artifact_id)
        if artifact.storage_object_id:
            storage_object = app.state.storage_catalog.get(
                context.tenant_id, artifact.storage_object_id
            )
            require_storage_read_access(request, context, storage_object)
            if storage_object.sensitivity_level > 0:
                raise TenantAccessError(
                    "Sensitive artifacts cannot be exposed by an external link"
                )
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=payload.expires_in_hours
        )
        grant_payload = ShareGrantApiCreate(
            resource_type=ShareResourceType.ARTIFACT,
            resource_id=artifact.id,
            subject_type=ShareSubjectType.EXTERNAL_LINK,
            subject_id=external_share_link_subject_id(
                token, context.tenant_id, request.app.state.settings
            ),
            permission="view",
            reason="Artifact external link",
            expires_at=expires_at,
        )
        grant = app.state.share_grant_store.create_grant(
            grant_payload.to_create(context.tenant_id, context.user_id)
        )
        url = request.url_for(
            "view_external_artifact",
            external_link_id=token,
            artifact_id=artifact.id,
        ).include_query_params(tenant_id=context.tenant_id)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=artifact.workspace_id,
            user_id=context.user_id,
            run_id=artifact.run_id,
            event_type="artifact.share_link.created",
            metadata={
                **share_grant_audit_metadata(grant),
                "artifact_id": artifact.id,
            },
            request=request,
        )
        return {
            "grant_id": grant.id,
            "artifact_id": artifact.id,
            "url": str(url),
            "expires_at": expires_at.isoformat(),
        }

    @app.get("/api/artifacts/{artifact_id}/download")
    def download_artifact(
        artifact_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> Response:
        require_permission(request, context, "storage.read")
        artifact_record = app.state.store.get_artifact(context.tenant_id, artifact_id)
        if artifact_record.storage_object_id:
            require_storage_read_access(
                request,
                context,
                app.state.storage_catalog.get(
                    context.tenant_id, artifact_record.storage_object_id
                ),
            )
        artifact, download = app.state.artifact_service.download(
            context.tenant_id, artifact_id
        )
        safe_filename = (
            Path(artifact.name)
            .name.replace('"', "")
            .replace("\r", "")
            .replace("\n", "")
        )
        return Response(
            content=download.content,
            media_type=download.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

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
        refresh_runtime_model_policy(app)
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
                "model_sensitivity_limit_count": len(record.model_sensitivity_limits),
            },
            request=request,
        )
        return record.model_dump(mode="json")

    @app.get("/api/model-policies/versions")
    def list_model_policy_versions(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "model_policy.read")
        return [
            _model_policy_version_api_payload(record)
            for record in app.state.model_policy_store.list_policy_versions(
                context.tenant_id
            )
        ]

    @app.get("/api/model-policies/change-requests")
    def list_model_policy_change_requests(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "model_policy.read")
        return [
            _model_policy_change_request_api_payload(record)
            for record in app.state.model_policy_store.list_policy_change_requests(
                context.tenant_id
            )
        ]

    @app.post(
        "/api/model-policies/change-requests",
        status_code=status.HTTP_201_CREATED,
    )
    def create_model_policy_change_request(
        payload: ModelPolicyChangeRequestApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_policy.manage")
        record = app.state.model_policy_store.create_policy_change_request(
            payload.to_create(
                tenant_id=context.tenant_id,
                requested_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.scope_upsert.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_policy.change_requested",
            metadata=model_policy_change_audit_metadata(record),
            request=request,
        )
        return _model_policy_change_request_api_payload(record)

    @app.post("/api/model-policies/change-requests/{request_id}/approve")
    def approve_model_policy_change_request(
        request_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_policy.approve")
        result = app.state.model_policy_store.approve_policy_change_request(
            tenant_id=context.tenant_id,
            request_id=request_id,
            reviewed_by_user_id=context.user_id,
        )
        refresh_runtime_model_policy(app)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=result.change_request.scope_upsert.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_policy.change_approved",
            metadata=model_policy_change_audit_metadata(result.change_request),
            request=request,
        )
        return {
            "change_request": _model_policy_change_request_api_payload(
                result.change_request
            ),
            "scope": result.scope_record.model_dump(mode="json")
            if result.scope_record is not None
            else None,
        }

    @app.post("/api/model-policies/change-requests/{request_id}/reject")
    def reject_model_policy_change_request(
        request_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_policy.approve")
        record = app.state.model_policy_store.reject_policy_change_request(
            tenant_id=context.tenant_id,
            request_id=request_id,
            reviewed_by_user_id=context.user_id,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.scope_upsert.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_policy.change_rejected",
            metadata=model_policy_change_audit_metadata(record),
            request=request,
        )
        return _model_policy_change_request_api_payload(record)

    @app.get("/api/model-providers")
    def list_model_providers(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "model_providers.read")
        settings_providers = [
            _model_provider_api_payload(
                app.state.settings,
                provider,
                status="active",
                source="settings",
            )
            for provider in app.state.settings.model_gateway_providers
            if _model_provider_visible_to_tenant(provider, context.tenant_id)
        ]
        stored_providers = [
            _model_provider_record_api_payload(app.state.settings, record)
            for record in app.state.model_provider_store.list_providers(
                context.tenant_id
            )
        ]
        return settings_providers + stored_providers

    @app.get("/api/model-providers/change-requests")
    def list_model_provider_change_requests(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "model_providers.read")
        return [
            _model_provider_change_request_api_payload(app.state.settings, record)
            for record in app.state.model_provider_store.list_provider_change_requests(
                context.tenant_id
            )
        ]

    @app.post(
        "/api/model-providers/{provider_id}/change-requests",
        status_code=status.HTTP_201_CREATED,
    )
    def create_model_provider_change_request(
        provider_id: str,
        payload: ModelProviderChangeRequestApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.manage")
        record = app.state.model_provider_store.create_provider_change_request(
            payload.to_create(
                tenant_id=context.tenant_id,
                provider_id=provider_id,
                requested_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider_upsert.workspace_id
            if record.provider_upsert is not None
            else None,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.change_requested",
            metadata=model_provider_change_audit_metadata(record),
            request=request,
        )
        return _model_provider_change_request_api_payload(app.state.settings, record)

    @app.post("/api/model-providers/change-requests/{request_id}/approve")
    def approve_model_provider_change_request(
        request_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.approve")
        result = app.state.model_provider_store.approve_provider_change_request(
            tenant_id=context.tenant_id,
            request_id=request_id,
            reviewed_by_user_id=context.user_id,
        )
        refresh_runtime_model_gateway(app)
        metadata = model_provider_change_audit_metadata(result.change_request)
        if result.provider_record is not None:
            metadata["current_version"] = result.provider_record.current_version
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=result.provider_record.provider.workspace_id
            if result.provider_record is not None
            else None,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.change_approved",
            metadata=metadata,
            request=request,
        )
        return {
            "change_request": _model_provider_change_request_api_payload(
                app.state.settings,
                result.change_request,
            ),
            "provider": _model_provider_record_api_payload(
                app.state.settings,
                result.provider_record,
            )
            if result.provider_record is not None
            else None,
        }

    @app.post("/api/model-providers/change-requests/{request_id}/reject")
    def reject_model_provider_change_request(
        request_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.approve")
        record = app.state.model_provider_store.reject_provider_change_request(
            tenant_id=context.tenant_id,
            request_id=request_id,
            reviewed_by_user_id=context.user_id,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider_upsert.workspace_id
            if record.provider_upsert is not None
            else None,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.change_rejected",
            metadata=model_provider_change_audit_metadata(record),
            request=request,
        )
        return _model_provider_change_request_api_payload(app.state.settings, record)

    @app.put("/api/model-providers/{provider_id}")
    def upsert_model_provider(
        provider_id: str,
        payload: ModelProviderApiUpsert,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.manage")
        record = app.state.model_provider_store.upsert_provider(
            payload.to_upsert(
                tenant_id=context.tenant_id,
                provider_id=provider_id,
                updated_by_user_id=context.user_id,
            )
        )
        refresh_runtime_model_gateway(app)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.upserted",
            metadata=model_provider_audit_metadata(record),
            request=request,
        )
        return _model_provider_record_api_payload(app.state.settings, record)

    @app.post("/api/model-providers/{provider_id}/disable")
    def disable_model_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.manage")
        record = app.state.model_provider_store.set_status(
            tenant_id=context.tenant_id,
            provider_id=provider_id,
            status="disabled",
            updated_by_user_id=context.user_id,
        )
        refresh_runtime_model_gateway(app)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.disabled",
            metadata=model_provider_audit_metadata(record),
            request=request,
        )
        return _model_provider_record_api_payload(app.state.settings, record)

    @app.post("/api/model-providers/{provider_id}/enable")
    def enable_model_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.manage")
        record = app.state.model_provider_store.set_status(
            tenant_id=context.tenant_id,
            provider_id=provider_id,
            status="active",
            updated_by_user_id=context.user_id,
        )
        refresh_runtime_model_gateway(app)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.enabled",
            metadata=model_provider_audit_metadata(record),
            request=request,
        )
        return _model_provider_record_api_payload(app.state.settings, record)

    @app.post("/api/model-providers/{provider_id}/credential")
    def rotate_model_provider_credential(
        provider_id: str,
        payload: ModelProviderCredentialRotateRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.manage")
        record = app.state.model_provider_store.rotate_credential(
            tenant_id=context.tenant_id,
            provider_id=provider_id,
            api_key_secret_ref_id=payload.api_key_secret_ref_id,
            updated_by_user_id=context.user_id,
        )
        refresh_runtime_model_gateway(app)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.credential_rotated",
            metadata=model_provider_audit_metadata(record),
            request=request,
        )
        return _model_provider_record_api_payload(app.state.settings, record)

    @app.get("/api/model-providers/{provider_id}/versions")
    def list_model_provider_versions(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "model_providers.read")
        return [
            _model_provider_version_api_payload(app.state.settings, record)
            for record in app.state.model_provider_store.list_provider_versions(
                context.tenant_id,
                provider_id,
            )
        ]

    @app.post("/api/model-providers/{provider_id}/versions/{version}/rollback")
    def rollback_model_provider_version(
        provider_id: str,
        version: int,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "model_providers.manage")
        record = app.state.model_provider_store.rollback_provider_version(
            tenant_id=context.tenant_id,
            provider_id=provider_id,
            version=version,
            updated_by_user_id=context.user_id,
        )
        refresh_runtime_model_gateway(app)
        metadata = model_provider_audit_metadata(record)
        metadata["restored_version"] = version
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.provider.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="model_provider.version_rolled_back",
            metadata=metadata,
            request=request,
        )
        return _model_provider_record_api_payload(app.state.settings, record)

    @app.get("/api/billing/invoices")
    def list_billing_invoices(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "billing.read")
        return [
            record.model_dump(mode="json")
            for record in app.state.billing_invoice_store.list_invoices(
                context.tenant_id
            )
        ]

    @app.post("/api/billing/invoices", status_code=status.HTTP_201_CREATED)
    def create_billing_invoice_snapshot(
        payload: BillingInvoiceQuery,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "billing.manage")
        invoice = app.state.billing_invoice_service.create_invoice(
            tenant_id=context.tenant_id,
            meters=app.state.store.list_billing_meters(context.tenant_id),
            query=payload,
        )
        record = app.state.billing_invoice_store.create_invoice(
            BillingInvoiceRecord(
                invoice_id=new_id("invoice"),
                tenant_id=context.tenant_id,
                invoice=invoice,
                created_by_user_id=context.user_id,
                created_at=utc_now(),
            )
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="billing.invoice.created",
            metadata={
                "invoice_id": record.invoice_id,
                "currency": invoice.currency,
                "group_by": invoice.group_by,
                "meter_event_count": invoice.meter_event_count,
                "unpriced_event_count": invoice.unpriced_event_count,
                "line_count": len(invoice.lines),
            },
            request=request,
        )
        return record.model_dump(mode="json")

    @app.get("/api/billing/invoices/{invoice_id}")
    def get_billing_invoice_snapshot(
        invoice_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "billing.read")
        return app.state.billing_invoice_store.get_invoice(
            context.tenant_id,
            invoice_id,
        ).model_dump(mode="json")

    @app.get("/api/billing/pricing-rules")
    def list_billing_pricing_rules(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "billing.read")
        return [
            rule.model_dump(mode="json")
            for rule in app.state.billing_pricing_rule_store.list_rules(
                context.tenant_id
            )
        ]

    @app.put("/api/billing/pricing-rules")
    def upsert_billing_pricing_rule(
        payload: BillingPricingRuleApiUpsert,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "billing.manage")
        record = app.state.billing_pricing_rule_store.upsert_rule(
            payload.to_upsert(
                tenant_id=context.tenant_id,
                updated_by_user_id=context.user_id,
            )
        )
        app.state.billing_pricing_service = build_billing_pricing_service(
            app.state.settings,
            app.state.billing_pricing_rule_store,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=record.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="billing.pricing_rule.upserted",
            metadata={
                "workspace_id": record.workspace_id,
                "skill_id": record.skill_id,
                "meter_type": record.meter_type,
                "unit": record.unit,
                "provider_present": record.provider is not None,
                "model_present": record.model is not None,
                "currency": record.currency,
            },
            request=request,
        )
        return record.model_dump(mode="json")

    @app.get("/api/share-grants")
    def list_share_grants(
        request: Request,
        resource_type: ShareResourceType | None = None,
        resource_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "sharing.read")
        return [
            share_grant_response_body(grant)
            for grant in app.state.share_grant_store.list_grants(
                tenant_id=context.tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        ]

    @app.post("/api/share-grants", status_code=status.HTTP_201_CREATED)
    def create_share_grant(
        payload: ShareGrantApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sharing.manage")
        create_payload = payload
        if payload.subject_type == ShareSubjectType.EXTERNAL_LINK:
            require_external_share_links_enabled(request)
            create_payload = payload.model_copy(
                update={
                    "subject_id": external_share_link_subject_id(
                        payload.subject_id,
                        context.tenant_id,
                        request.app.state.settings,
                    )
                }
            )
        grant = app.state.share_grant_store.create_grant(
            create_payload.to_create(
                tenant_id=context.tenant_id,
                created_by_user_id=context.user_id,
            )
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="share.grant.created",
            metadata=share_grant_audit_metadata(grant),
            request=request,
        )
        return share_grant_response_body(grant)

    @app.post("/api/share-grants/{grant_id}/revoke")
    def revoke_share_grant(
        grant_id: str,
        request: Request,
        payload: ShareGrantRevokeRequest | None = Body(default=None),
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sharing.manage")
        grant = app.state.share_grant_store.revoke_grant(
            tenant_id=context.tenant_id,
            grant_id=grant_id,
            revoked_by_user_id=context.user_id,
        )
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="share.grant.revoked",
            metadata=share_grant_audit_metadata(grant),
            request=request,
        )
        return share_grant_response_body(grant)

    @app.get("/api/billing/meters")
    def list_billing_meters(
        request: Request,
        query: BillingMeterQuery = Depends(get_billing_meter_query),
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "billing.read")
        return list_or_page_created_at_records(
            query.apply(app.state.store.list_billing_meters(context.tenant_id)),
            request,
            page,
        )

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

    @app.get("/api/billing/invoice")
    def export_billing_invoice(
        request: Request,
        query: BillingInvoiceQuery = Depends(get_billing_invoice_query),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "billing.read")
        invoice = app.state.billing_invoice_service.create_invoice(
            tenant_id=context.tenant_id,
            meters=app.state.store.list_billing_meters(context.tenant_id),
            query=query,
        )
        return invoice.model_dump(mode="json")

    @app.get("/api/audit-events")
    def list_audit_events(
        request: Request,
        query: AuditEventQuery = Depends(get_audit_event_query),
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "audit.read")
        return list_or_page_created_at_records(
            query.apply(app.state.audit_service.list_for_tenant(context.tenant_id)),
            request,
            page,
        )

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

    @app.get("/api/customer-success/summary")
    def get_customer_success_summary(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.read")
        return app.state.customer_success_service.build_tenant_summary(
            context.tenant_id
        ).model_dump(mode="json")

    @app.post("/api/customer-success/feedback", status_code=status.HTTP_201_CREATED)
    def submit_customer_feedback(
        payload: CustomerFeedbackCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.feedback")
        feedback = app.state.customer_feedback_service.capture_feedback(
            tenant_id=context.tenant_id,
            payload=payload.model_copy(
                update={"submitted_by_user_id": context.user_id}
            ),
        )
        return customer_feedback_response_body(feedback)

    @app.get("/api/customer-success/feedback")
    def list_customer_feedback(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.read")
        return [
            customer_feedback_response_body(feedback)
            for feedback in app.state.customer_feedback_service.list_feedback(
                context.tenant_id
            )
        ]

    @app.post(
        "/api/customer-success/evaluation-candidates",
        status_code=status.HTTP_201_CREATED,
    )
    def create_customer_success_evaluation_candidates(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.manage")
        feedback_service = app.state.customer_feedback_service
        return [
            candidate.model_dump(mode="json")
            for candidate in feedback_service.create_evaluation_candidates_for_low_rated_runs(
                tenant_id=context.tenant_id,
                reviewed_by_user_id=context.user_id,
            )
        ]

    @app.get("/api/customer-success/evaluation-candidates")
    def list_customer_success_evaluation_candidates(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.manage")
        return [
            candidate.model_dump(mode="json")
            for candidate in app.state.customer_feedback_service.list_evaluation_candidates(
                context.tenant_id,
            )
        ]

    @app.get("/api/customer-success/evaluation-cases")
    def list_customer_success_evaluation_cases(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.manage")
        feedback_service = app.state.customer_feedback_service
        return [
            evaluation_case.model_dump(mode="json")
            for evaluation_case in feedback_service.list_evaluation_cases(
                context.tenant_id,
            )
        ]

    @app.post("/api/customer-success/evaluation-candidates/{candidate_id}/review")
    def review_customer_success_evaluation_candidate(
        candidate_id: str,
        payload: CustomerSuccessCandidateReview,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.manage")
        return app.state.customer_feedback_service.review_evaluation_candidate(
            tenant_id=context.tenant_id,
            candidate_id=candidate_id,
            reviewed_by_user_id=context.user_id,
            status=payload.status,
            review_note=payload.review_note,
        ).model_dump(mode="json")

    @app.post(
        "/api/customer-success/solution-pack-candidates",
        status_code=status.HTTP_201_CREATED,
    )
    def create_customer_success_solution_pack_candidates(
        payload: CustomerSuccessCandidateCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.manage")
        feedback_service = app.state.customer_feedback_service
        return [
            candidate.model_dump(mode="json")
            for candidate in feedback_service.create_solution_pack_improvement_candidates(
                tenant_id=context.tenant_id,
                reviewed_by_user_id=context.user_id,
                minimum_repeated_feedback=payload.minimum_repeated_feedback,
            )
        ]

    @app.get("/api/customer-success/solution-pack-candidates")
    def list_customer_success_solution_pack_candidates(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.manage")
        return [
            candidate.model_dump(mode="json")
            for candidate in app.state.customer_feedback_service.list_solution_pack_candidates(
                context.tenant_id,
            )
        ]

    @app.get("/api/customer-success/solution-pack-drafts")
    def list_customer_success_solution_pack_drafts(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "customer_success.manage")
        feedback_service = app.state.customer_feedback_service
        return [
            publication_draft.model_dump(mode="json")
            for publication_draft in feedback_service.list_solution_pack_publication_drafts(
                context.tenant_id,
            )
        ]

    @app.patch("/api/customer-success/solution-pack-drafts/{publication_draft_id}")
    def update_customer_success_solution_pack_draft(
        publication_draft_id: str,
        payload: CustomerSuccessSolutionPackDraftUpdate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.manage")
        return (
            app.state.customer_feedback_service.update_solution_pack_publication_draft(
                tenant_id=context.tenant_id,
                publication_draft_id=publication_draft_id,
                updated_by_user_id=context.user_id,
                requested_skill_name=payload.requested_skill_name,
                proposed_change_summary=payload.proposed_change_summary,
                proposed_pack_version=payload.proposed_pack_version,
                proposed_skill_manifest=payload.proposed_skill_manifest,
                proposed_skill_manifests=payload.proposed_skill_manifests,
            ).model_dump(mode="json")
        )

    @app.post(
        "/api/customer-success/solution-pack-drafts/{publication_draft_id}/submit"
    )
    def submit_customer_success_solution_pack_draft(
        publication_draft_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.manage")
        return (
            app.state.customer_feedback_service.submit_solution_pack_publication_draft(
                tenant_id=context.tenant_id,
                publication_draft_id=publication_draft_id,
                submitted_by_user_id=context.user_id,
            ).model_dump(mode="json")
        )

    @app.post(
        "/api/customer-success/solution-pack-drafts/{publication_draft_id}/review"
    )
    def review_customer_success_solution_pack_draft(
        publication_draft_id: str,
        payload: CustomerSuccessSolutionPackDraftReview,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.manage")
        return (
            app.state.customer_feedback_service.review_solution_pack_publication_draft(
                tenant_id=context.tenant_id,
                publication_draft_id=publication_draft_id,
                reviewed_by_user_id=context.user_id,
                status=payload.status,
                review_note=payload.review_note,
            ).model_dump(mode="json")
        )

    @app.post("/api/customer-success/solution-pack-drafts/{publication_draft_id}/apply")
    def apply_customer_success_solution_pack_draft(
        publication_draft_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.manage")
        require_permission(request, context, "solution_packs.manage")
        return (
            app.state.customer_feedback_service.apply_solution_pack_publication_draft(
                tenant_id=context.tenant_id,
                publication_draft_id=publication_draft_id,
                applied_by_user_id=context.user_id,
            ).model_dump(mode="json")
        )

    @app.post("/api/customer-success/solution-pack-candidates/{candidate_id}/review")
    def review_customer_success_solution_pack_candidate(
        candidate_id: str,
        payload: CustomerSuccessCandidateReview,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "customer_success.manage")
        return app.state.customer_feedback_service.review_solution_pack_candidate(
            tenant_id=context.tenant_id,
            candidate_id=candidate_id,
            reviewed_by_user_id=context.user_id,
            status=payload.status,
            review_note=payload.review_note,
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

    @app.post(
        "/api/lifecycle/data-residency/reports", status_code=status.HTTP_201_CREATED
    )
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

    @app.post(
        "/api/lifecycle/restore-drill-schedules",
        status_code=status.HTTP_201_CREATED,
    )
    def create_restore_drill_schedule(
        payload: RestoreDrillScheduleApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        schedule = app.state.restore_drill_schedule_store.create_schedule(
            RestoreDrillScheduleCreate(
                tenant_id=context.tenant_id,
                created_by_user_id=context.user_id,
                **payload.model_dump(),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=schedule.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="restore_drill.schedule.created",
            metadata=restore_drill_schedule_audit_metadata(schedule),
            request=request,
        )
        return schedule.model_dump(mode="json")

    @app.get("/api/lifecycle/restore-drill-schedules")
    def list_restore_drill_schedules(
        request: Request,
        workspace_id: str | None = None,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "lifecycle.read")
        return [
            schedule.model_dump(mode="json")
            for schedule in app.state.restore_drill_schedule_store.list_schedules(
                context.tenant_id,
            )
            if workspace_id is None or schedule.workspace_id == workspace_id
        ]

    @app.patch("/api/lifecycle/restore-drill-schedules/{schedule_id}")
    def update_restore_drill_schedule(
        schedule_id: str,
        payload: RestoreDrillScheduleApiUpdate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        updated = app.state.restore_drill_schedule_store.update_schedule_status(
            tenant_id=context.tenant_id,
            schedule_id=schedule_id,
            status=payload.status,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=updated.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="restore_drill.schedule.updated",
            metadata=restore_drill_schedule_audit_metadata(updated),
            request=request,
        )
        return updated.model_dump(mode="json")

    @app.get("/api/lifecycle/restore-drill-schedules/{schedule_id}/runs")
    def list_restore_drill_run_records(
        schedule_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "lifecycle.read")
        app.state.restore_drill_schedule_store.get_schedule(
            context.tenant_id,
            schedule_id,
        )
        return [
            record.model_dump(mode="json")
            for record in app.state.restore_drill_schedule_store.list_run_records(
                context.tenant_id,
                schedule_id,
            )
        ]

    @app.post(
        "/api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}/execute",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def enqueue_restore_drill_run_record_execution(
        schedule_id: str,
        run_record_id: str,
        payload: RestoreDrillRunExecutionApiCreate,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        idempotency_request = build_idempotency_request(
            tenant_id=context.tenant_id,
            key=idempotency_key,
            method=RESTORE_DRILL_EXECUTION_METHOD,
            path=restore_drill_execution_path(schedule_id, run_record_id),
            payload=payload,
        )
        replay_record = find_idempotent_replay(app.state.store, idempotency_request)
        if replay_record is not None:
            response.status_code = replay_record.status_code
            return replay_record.response_body

        queue = app.state.job_queue
        if queue is None:
            raise RedisQueueConfigurationError("job queue backend is disabled")
        schedule = app.state.restore_drill_schedule_store.get_schedule(
            context.tenant_id,
            schedule_id,
        )
        existing = app.state.restore_drill_schedule_store.get_run_record(
            context.tenant_id,
            run_record_id,
        )
        if existing.schedule_id != schedule.id:
            raise NotFoundError(f"Restore drill run record not found: {run_record_id}")
        require_restore_drill_run_record_update_allowed(existing)
        job = queue.enqueue(
            JobType.RESTORE_DRILL_EXECUTION,
            RestoreDrillExecutionJob(
                tenant_id=context.tenant_id,
                workspace_id=schedule.workspace_id,
                schedule_id=schedule.id,
                run_record_id=existing.id,
                requested_by_user_id=existing.requested_by_user_id,
                verification_config=payload.verification_config,
                retention_expires_at=payload.retention_expires_at,
            ),
            max_attempts=app.state.settings.worker_job_max_attempts,
        )
        queue_name = JobType.RESTORE_DRILL_EXECUTION.value
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=existing.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="restore_drill.execution_queued",
            metadata={
                **restore_drill_run_record_audit_metadata(existing),
                "job_id": job.id,
                "queue": queue_name,
                "drill_id": payload.verification_config.drill_id,
                "has_redis_queue_verification": (
                    payload.verification_config.redis_queue_verification_path
                    is not None
                ),
            },
            request=request,
        )
        response_body = RestoreDrillExecutionQueuedResponse(
            schedule_id=schedule.id,
            run_record_id=existing.id,
            job_id=job.id,
            queue=queue_name,
        ).model_dump(mode="json")
        save_idempotent_response(
            app.state.store,
            idempotency_request,
            status.HTTP_202_ACCEPTED,
            response_body,
        )
        return response_body

    @app.post(
        "/api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}/evidence",
        status_code=status.HTTP_201_CREATED,
    )
    def upload_restore_drill_run_record_evidence(
        schedule_id: str,
        run_record_id: str,
        payload: RestoreDrillRunEvidenceApiCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        schedule = app.state.restore_drill_schedule_store.get_schedule(
            context.tenant_id,
            schedule_id,
        )
        existing = app.state.restore_drill_schedule_store.get_run_record(
            context.tenant_id,
            run_record_id,
        )
        if existing.schedule_id != schedule.id:
            raise NotFoundError(f"Restore drill run record not found: {run_record_id}")
        require_restore_drill_run_record_update_allowed(existing)
        require_restore_drill_verification_result_ready(payload.verification)
        evidence_content = restore_drill_evidence_content(payload.verification)
        evidence_object = app.state.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=context.tenant_id,
                workspace_id=schedule.workspace_id,
                purpose=StoragePurpose.DATA_EXPORT,
                filename=restore_drill_evidence_filename(run_record_id),
                content_type="application/json",
                size_bytes=len(evidence_content),
                retention_expires_at=payload.retention_expires_at,
            )
        )
        upload_storage_object(
            app=app,
            storage_object=evidence_object,
            content=evidence_content,
            request=request,
            context=context,
        )
        evidence_object_id = validate_restore_drill_evidence_object(
            RestoreDrillEvidenceValidationRequest(
                tenant_id=context.tenant_id,
                workspace_id=schedule.workspace_id,
                evidence_object_id=evidence_object.id,
            ),
            app.state.storage_catalog,
            app.state.object_storage,
        )
        updated = app.state.restore_drill_schedule_store.update_run_record_status(
            tenant_id=context.tenant_id,
            run_record_id=run_record_id,
            status=RestoreDrillRunStatus.EVIDENCE_READY,
            evidence_object_id=evidence_object_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=updated.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="restore_drill.run_record.updated",
            metadata=restore_drill_run_record_audit_metadata(updated),
            request=request,
        )
        return updated.model_dump(mode="json")

    @app.patch(
        "/api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}"
    )
    def update_restore_drill_run_record(
        schedule_id: str,
        run_record_id: str,
        payload: RestoreDrillRunRecordApiUpdate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "lifecycle.manage")
        schedule = app.state.restore_drill_schedule_store.get_schedule(
            context.tenant_id,
            schedule_id,
        )
        existing = app.state.restore_drill_schedule_store.get_run_record(
            context.tenant_id,
            run_record_id,
        )
        if existing.schedule_id != schedule.id:
            raise NotFoundError(f"Restore drill run record not found: {run_record_id}")
        require_restore_drill_run_record_update_allowed(existing)
        updated = app.state.restore_drill_schedule_store.update_run_record_status(
            tenant_id=context.tenant_id,
            run_record_id=run_record_id,
            status=payload.status,
            evidence_object_id=validate_restore_drill_evidence_object(
                RestoreDrillEvidenceValidationRequest(
                    tenant_id=context.tenant_id,
                    workspace_id=schedule.workspace_id,
                    evidence_object_id=payload.evidence_object_id,
                ),
                app.state.storage_catalog,
                app.state.object_storage,
            ),
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=updated.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="restore_drill.run_record.updated",
            metadata=restore_drill_run_record_audit_metadata(updated),
            request=request,
        )
        return updated.model_dump(mode="json")

    @app.post(
        "/api/lifecycle/tenant-offboarding-requests",
        status_code=status.HTTP_201_CREATED,
    )
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
        return (
            build_tenant_offboarding_service(app)
            .get_plan(
                context.tenant_id,
                plan_id,
            )
            .model_dump(mode="json")
        )

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

    @app.post("/api/sso/providers", status_code=status.HTTP_201_CREATED)
    def register_sso_provider(
        payload: SsoProviderCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sso.manage")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SSO,
        )
        entry = app.state.sso_provider_registry.create_or_update(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user_id,
            request=payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="sso.provider.configured",
            metadata=sso_provider_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.get("/api/sso/providers")
    def list_sso_providers(
        request: Request,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "sso.read")
        return list_or_page_created_at_records(
            app.state.sso_provider_registry.list_for_tenant(context.tenant_id),
            request,
            page,
        )

    @app.get("/api/sso/providers/{provider_id}")
    def get_sso_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sso.read")
        return app.state.sso_provider_registry.get_for_tenant(
            context.tenant_id,
            provider_id,
        ).model_dump(mode="json")

    @app.post("/api/sso/providers/{provider_id}/enable")
    def enable_sso_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sso.manage")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SSO,
        )
        entry = app.state.sso_provider_registry.enable(context.tenant_id, provider_id)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="sso.provider.enabled",
            metadata=sso_provider_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.post("/api/sso/providers/{provider_id}/disable")
    def disable_sso_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sso.manage")
        entry = app.state.sso_provider_registry.disable(context.tenant_id, provider_id)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="sso.provider.disabled",
            metadata=sso_provider_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.post("/api/scim/providers", status_code=status.HTTP_201_CREATED)
    def register_scim_provider(
        payload: ScimProviderCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "scim.manage")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SCIM,
        )
        entry = app.state.scim_provisioning_store.create_or_update_provider(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user_id,
            request=payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="scim.provider.configured",
            metadata=scim_provider_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.get("/api/scim/providers")
    def list_scim_providers(
        request: Request,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "scim.read")
        return list_or_page_created_at_records(
            app.state.scim_provisioning_store.list_providers(context.tenant_id),
            request,
            page,
        )

    @app.get("/api/scim/providers/{provider_id}")
    def get_scim_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "scim.read")
        return app.state.scim_provisioning_store.get_provider(
            context.tenant_id,
            provider_id,
        ).model_dump(mode="json")

    @app.post("/api/scim/providers/{provider_id}/enable")
    def enable_scim_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "scim.manage")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SCIM,
        )
        entry = app.state.scim_provisioning_store.enable_provider(
            context.tenant_id,
            provider_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="scim.provider.enabled",
            metadata=scim_provider_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.post("/api/scim/providers/{provider_id}/disable")
    def disable_scim_provider(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "scim.manage")
        entry = app.state.scim_provisioning_store.disable_provider(
            context.tenant_id,
            provider_id,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="scim.provider.disabled",
            metadata=scim_provider_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.post(
        "/api/scim/providers/{provider_id}/group-role-mappings",
        status_code=status.HTTP_201_CREATED,
    )
    def upsert_scim_group_role_mapping(
        provider_id: str,
        payload: ScimGroupRoleMapping,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "scim.manage")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SCIM,
        )
        for role_id in payload.role_ids:
            app.state.identity_service.get_role(context.tenant_id, role_id)
        entry = app.state.scim_provisioning_store.upsert_group_role_mapping(
            tenant_id=context.tenant_id,
            provider_id=provider_id,
            created_by_user_id=context.user_id,
            mapping=payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="scim.group_role_mapping.configured",
            metadata=scim_group_role_mapping_audit_metadata(entry),
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.get("/api/scim/providers/{provider_id}/group-role-mappings")
    def list_scim_group_role_mappings(
        provider_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "scim.read")
        return [
            entry.model_dump(mode="json")
            for entry in app.state.scim_provisioning_store.list_group_role_mappings(
                context.tenant_id,
                provider_id,
            )
        ]

    @app.post(
        "/api/scim/providers/{provider_id}/import", status_code=status.HTTP_201_CREATED
    )
    def import_scim_resources(
        provider_id: str,
        payload: ScimImportRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "scim.manage")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SCIM,
        )
        result = app.state.scim_provisioning_service.apply_import(
            tenant_id=context.tenant_id,
            provider_id=provider_id,
            imported_by_user_id=context.user_id,
            request=payload,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="scim.import.completed",
            metadata=scim_import_audit_metadata(result),
            request=request,
        )
        return result.model_dump(mode="json")

    @app.get("/api/scim/providers/{provider_id}/imports")
    def list_scim_imports(
        provider_id: str,
        request: Request,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "scim.read")
        return list_or_page_created_at_records(
            app.state.scim_provisioning_store.list_import_records(
                context.tenant_id,
                provider_id,
            ),
            request,
            page,
        )

    @app.get("/api/store")
    def list_public_store_items() -> dict[str, Any]:
        return {
            "items": [
                item.summary() for item in app.state.store_catalog.list_items()
            ],
            "nextCursor": None,
        }

    @app.get("/api/store/featured")
    def list_featured_store_items() -> list[dict[str, Any]]:
        return [
            item.summary()
            for item in app.state.store_catalog.list_items()
            if item.featured
        ]

    @app.get("/api/skills/builtin")
    def list_public_builtin_skills() -> list[dict[str, Any]]:
        return [
            {
                key: skill[key]
                for key in ("id", "displayName", "description", "tags")
            }
            for skill in app.state.store_catalog.list_skills()
        ]

    @app.get("/api/discover/skills")
    def discover_public_skills() -> list[dict[str, Any]]:
        return [
            {
                "id": skill["id"],
                "repoUrl": None,
                "name": skill["displayName"],
                "blurb": skill["description"],
                "owner": skill["owner"],
                "xHandle": None,
                "starsSnapshot": None,
                "installCount": None,
                "origin": "builtin",
            }
            for skill in app.state.store_catalog.list_skills()
        ]

    @app.get("/api/store/items")
    def list_builtin_store_items(
        request: Request,
        q: str | None = Query(default=None, min_length=1, max_length=200),
        kind: str | None = Query(default=None, min_length=1, max_length=40),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "skills.read")
        items = app.state.store_catalog.list_items()
        if kind is not None and kind.casefold() != "solution_pack":
            items = []
        if q is not None:
            needle = q.casefold()
            items = [
                item
                for item in items
                if needle
                in " ".join(
                    [
                        item.manifest.id,
                        item.manifest.name,
                        item.manifest.description,
                        item.category,
                        item.publisher,
                        *item.manifest.use_cases,
                    ]
                ).casefold()
            ]
        return {"items": [item.summary() for item in items]}

    @app.get("/api/store/items/{item_id}")
    def get_builtin_store_item(
        item_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "skills.read")
        return app.state.store_catalog.get(item_id).detail()

    @app.post(
        "/api/store/items/{item_id}/install",
        status_code=status.HTTP_201_CREATED,
    )
    def install_builtin_store_catalog_item(
        item_id: str,
        payload: StoreItemInstallRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "skills.install")
        if payload.workspace_id not in app.state.store.list_workspace_ids(
            context.tenant_id
        ):
            raise NotFoundError(f"Workspace not found: {payload.workspace_id}")
        item = app.state.store_catalog.get(item_id)
        if payload.expected_digest is not None and payload.expected_digest != item.digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Store item digest changed: {item_id}",
            )
        try:
            install_result = install_builtin_store_item(
                item=item,
                skill_service=app.state.skill_service,
                solution_pack_registry=app.state.solution_pack_registry,
                tenant_id=context.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=context.user_id,
            )
        except StoreInstallConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        response = {
            "item_id": item.manifest.id,
            "version": item.manifest.version,
            "digest": item.digest,
            "workspace_id": payload.workspace_id,
            "status": "installed",
            **install_result,
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="store.item.installed",
            metadata=response,
            request=request,
        )
        return response

    @app.post("/api/solution-packs", status_code=status.HTTP_201_CREATED)
    def register_solution_pack(
        payload: SolutionPackManifest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "solution_packs.manage")
        entry = app.state.solution_pack_registry.register_for_tenant(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user_id,
            manifest=payload,
        )
        return entry.model_dump(mode="json")

    @app.get("/api/solution-packs")
    def list_solution_packs(
        request: Request,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "solution_packs.read")
        return list_or_page_created_at_records(
            app.state.solution_pack_registry.list_for_tenant(context.tenant_id),
            request,
            page,
        )

    @app.get("/api/solution-packs/{pack_id}/versions")
    def list_solution_pack_versions(
        pack_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "solution_packs.read")
        return [
            entry.model_dump(mode="json")
            for entry in app.state.solution_pack_registry.list_versions(
                context.tenant_id,
                pack_id,
            )
        ]

    @app.post("/api/solution-packs/{pack_id}/publish")
    def publish_solution_pack(
        pack_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "solution_packs.manage")
        entry = app.state.solution_pack_registry.publish(context.tenant_id, pack_id)
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="solution_pack.published",
            metadata={
                "pack_id": entry.manifest.id,
                "version": entry.manifest.version,
                "status": entry.status.value,
                "skill_count": len(entry.manifest.skills),
            },
            request=request,
        )
        return entry.model_dump(mode="json")

    @app.post("/api/solution-packs/{pack_id}/disable")
    def disable_solution_pack(
        pack_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "solution_packs.manage")
        return app.state.solution_pack_registry.disable(
            context.tenant_id,
            pack_id,
        ).model_dump(mode="json")

    @app.post(
        "/api/solution-packs/{pack_id}/install", status_code=status.HTTP_201_CREATED
    )
    def install_solution_pack(
        pack_id: str,
        payload: SolutionPackInstallRequest,
        response: Response,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "solution_packs.install")
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SOLUTION_PACKS,
        )
        if payload.dry_run:
            response.status_code = status.HTTP_200_OK
            return app.state.solution_pack_service.preview_install(
                tenant_id=context.tenant_id,
                pack_id=pack_id,
                workspace_ids=payload.workspace_ids,
                installed_by_user_id=context.user_id,
                selected_resource_ids=payload.selected_resource_ids,
            ).model_dump(mode="json")
        installation = app.state.solution_pack_service.install_for_tenant(
            tenant_id=context.tenant_id,
            pack_id=pack_id,
            workspace_ids=payload.workspace_ids,
            installed_by_user_id=context.user_id,
            selected_resource_ids=payload.selected_resource_ids,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="solution_pack.installed",
            metadata={
                "pack_id": installation.pack_id,
                "version": installation.version,
                "workspace_ids": installation.workspace_ids,
                "workspace_count": len(installation.workspace_ids),
                "installed_skill_ids": installation.installed_skill_ids,
                "installed_skill_count": len(installation.installed_skill_ids),
                "status": installation.status.value,
            },
            request=request,
        )
        return installation.model_dump(mode="json")

    @app.get("/api/solution-pack-installations")
    def list_solution_pack_installations(
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "solution_packs.read")
        return [
            installation.model_dump(mode="json")
            for installation in app.state.solution_pack_registry.list_installations(
                context.tenant_id,
            )
        ]

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

    @app.post("/api/skills/import/zip", status_code=status.HTTP_201_CREATED)
    def import_skill_zip(
        payload: SkillZipImportRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        try:
            archive = base64.b64decode(payload.archive_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("archive_base64 must be valid base64") from error
        package = app.state.skill_service.import_zip(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user_id,
            archive_bytes=archive,
            manifest=payload.manifest,
            source_url=payload.source_url,
            source_ref=payload.source_ref,
            subdirectory=payload.subdirectory,
        )
        metadata = {
            "skill_id": package.skill_id,
            "version": package.version,
            "package_digest": package.package_digest,
            "source_digest": package.provenance.source_digest,
            "source_type": package.provenance.source_type.value,
            "file_count": len(package.files),
            "size_bytes": sum(item.size_bytes for item in package.files),
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.package_imported",
            metadata=metadata,
            request=request,
        )
        if payload.workspace_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=None,
                workspace_id=payload.workspace_id,
                user_id=context.user_id,
                skill_id=package.skill_id,
                meter_type="storage_bytes",
                quantity=float(metadata["size_bytes"]),
                unit="byte",
                metadata=metadata | {"resource_type": "skill_package"},
            )
        return package.model_dump(mode="json")

    @app.post("/api/skills/import/github", status_code=status.HTTP_201_CREATED)
    def import_skill_github(
        payload: SkillGithubImportRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        package = app.state.skill_service.import_github(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user_id,
            source=payload.source,
            manifest=payload.manifest,
        )
        metadata = {
            "skill_id": package.skill_id,
            "version": package.version,
            "package_digest": package.package_digest,
            "source_digest": package.provenance.source_digest,
            "source_type": package.provenance.source_type.value,
            "source_url": package.provenance.source_url,
            "source_ref": package.provenance.source_ref,
            "file_count": len(package.files),
            "size_bytes": sum(item.size_bytes for item in package.files),
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.package_imported",
            metadata=metadata,
            request=request,
        )
        if payload.workspace_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=context.tenant_id,
                run_id=None,
                workspace_id=payload.workspace_id,
                user_id=context.user_id,
                skill_id=package.skill_id,
                meter_type="storage_bytes",
                quantity=float(metadata["size_bytes"]),
                unit="byte",
                metadata=metadata | {"resource_type": "skill_package"},
            )
        return package.model_dump(mode="json")

    @app.get("/api/skills/{skill_id}/packages")
    def list_skill_packages(
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        return [
            item.model_dump(mode="json")
            for item in app.state.skill_service.list_package_versions(
                tenant_id=context.tenant_id,
                skill_id=skill_id,
            )
        ]

    @app.get("/api/skills/{skill_id}/packages/{version}")
    def get_skill_package(
        skill_id: str,
        version: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        return app.state.skill_service.get_package(
            tenant_id=context.tenant_id,
            skill_id=skill_id,
            version=version,
        ).model_dump(mode="json")

    @app.get("/api/skills/{skill_id}/packages/{version}/skill-md")
    def get_skill_package_instructions(
        skill_id: str,
        version: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        package = app.state.skill_service.get_package(
            tenant_id=context.tenant_id,
            skill_id=skill_id,
            version=version,
        )
        return {
            "skill_id": skill_id,
            "version": version,
            "package_digest": package.package_digest,
            "source_digest": package.provenance.source_digest,
            "skill_md": package.skill_md,
        }

    @app.get("/api/skills/{skill_id}/packages/{version}/files")
    def list_skill_package_files(
        skill_id: str,
        version: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        return [
            item.model_dump(mode="json")
            for item in app.state.skill_service.list_files(
                context.tenant_id, skill_id, version
            )
        ]

    @app.get("/api/skills/{skill_id}/packages/{version}/files/{path:path}")
    def get_skill_package_file(
        skill_id: str,
        version: str,
        path: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        item = app.state.skill_service.get_file(
            context.tenant_id, skill_id, version, path
        )
        try:
            text_content = item.content.decode("utf-8")
        except UnicodeDecodeError:
            text_content = None
        return {
            "path": item.path,
            "kind": item.kind.value,
            "size_bytes": item.size_bytes,
            "content_digest": item.content_digest,
            "content": text_content,
            "content_base64": base64.b64encode(item.content).decode("ascii"),
        }

    @app.get("/api/skills/{skill_id}/packages/{version}/source")
    def get_skill_package_source(
        skill_id: str,
        version: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        package = app.state.skill_service.get_package(
            tenant_id=context.tenant_id,
            skill_id=skill_id,
            version=version,
        )
        return {
            "skill_id": skill_id,
            "version": version,
            "package_digest": package.package_digest,
            **package.provenance.model_dump(mode="json"),
        }

    @app.get("/api/skills/{skill_id}/packages/{version}/diff")
    def diff_skill_package_versions(
        skill_id: str,
        version: str,
        request: Request,
        compare_to: str = Query(min_length=1),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "skills.read")
        return app.state.skill_service.diff_versions(
            context.tenant_id, skill_id, version, compare_to
        )

    @app.post("/api/skills/{skill_id}/packages/{version}/evaluate")
    def evaluate_skill_package(
        skill_id: str,
        version: str,
        payload: SkillEvaluateRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        evaluation = app.state.skill_service.evaluate(
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            skill_id=skill_id,
            version=version,
            created_by_user_id=context.user_id,
            suite=payload.suite,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.package_evaluated",
            metadata={
                "skill_id": skill_id,
                "version": version,
                "evaluation_run_id": evaluation.id,
                "package_digest": evaluation.package_digest,
                "suite_digest": evaluation.suite_digest,
                "status": evaluation.status.value,
                "score": evaluation.score,
                "passed": evaluation.passed,
            },
            request=request,
        )
        return evaluation.model_dump(mode="json")

    @app.get("/api/skills/{skill_id}/packages/{version}/evaluations")
    def list_skill_package_evaluations(
        skill_id: str,
        version: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        return [
            item.model_dump(mode="json")
            for item in app.state.skill_registry.list_evaluation_runs(
                context.tenant_id, skill_id, version
            )
        ]

    @app.post("/api/skills/{skill_id}/packages/{version}/publish")
    def publish_skill_package(
        skill_id: str,
        version: str,
        payload: SkillPackagePublishRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        record = app.state.skill_service.publish(
            tenant_id=context.tenant_id,
            skill_id=skill_id,
            version=version,
            evaluation_run_id=payload.evaluation_run_id,
        )
        metadata = {
            "skill_id": skill_id,
            "version": version,
            "package_digest": record.package.package_digest,
            "source_digest": record.package.provenance.source_digest,
            "evaluation_run_id": payload.evaluation_run_id,
            "status": record.status.value,
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.package_published",
            metadata=metadata,
            request=request,
        )
        return record.model_dump(mode="json")

    @app.post("/api/skills/{skill_id}/packages/{version}/disable")
    def disable_skill_package(
        skill_id: str,
        version: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.publish")
        record = app.state.skill_service.disable(
            tenant_id=context.tenant_id,
            skill_id=skill_id,
            version=version,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=None,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.package_disabled",
            metadata={
                "skill_id": skill_id,
                "version": version,
                "package_digest": record.package.package_digest,
                "source_digest": record.package.provenance.source_digest,
                "status": record.status.value,
            },
            request=request,
        )
        return record.model_dump(mode="json")

    @app.get("/api/skills")
    def list_skills(
        request: Request,
        workspace_id: str | None = None,
        department_id: str | None = None,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "skills.read")
        return list_or_page_created_at_records(
            app.state.skill_registry.list_visible_for_tenant(
                context.tenant_id,
                user_id=context.user_id,
                workspace_id=workspace_id,
                department_id=department_id,
            ),
            request,
            page,
        )

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

    @app.post(
        "/api/workspaces/{workspace_id}/skills/{skill_id}/install",
        status_code=status.HTTP_201_CREATED,
    )
    def install_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        payload: SkillExactInstallRequest | None = Body(default=None),
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        if payload is None:
            installation = app.state.skill_registry.install_for_workspace(
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
                installed_by_user_id=context.user_id,
            )
        else:
            installation = app.state.skill_service.install(
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
                version=payload.version,
                package_digest=payload.package_digest,
                installed_by_user_id=context.user_id,
            )
        metadata = {
            "skill_id": skill_id,
            "version": installation.installed_version,
            "package_digest": installation.package_digest,
            "source_digest": installation.source_digest,
            "package_kind": installation.package_kind.value,
            "status": installation.status.value,
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.installed",
            metadata=metadata,
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            workspace_id=workspace_id,
            user_id=context.user_id,
            skill_id=skill_id,
            meter_type="skill_management_operation_count",
            quantity=1,
            unit="operation",
            metadata={"operation": "install", **metadata},
        )
        return installation.model_dump(mode="json")

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/upgrade")
    def upgrade_workspace_skill(
        workspace_id: str,
        skill_id: str,
        payload: SkillVersionMoveRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        installation = app.state.skill_service.upgrade(
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            target_version=payload.target_version,
            expected_package_digest=payload.expected_package_digest,
            updated_by_user_id=context.user_id,
        )
        metadata = {
            "skill_id": skill_id,
            "version": installation.installed_version,
            "package_digest": installation.package_digest,
            "source_digest": installation.source_digest,
            "operation": "upgrade",
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.upgraded",
            metadata=metadata,
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            workspace_id=workspace_id,
            user_id=context.user_id,
            skill_id=skill_id,
            meter_type="skill_management_operation_count",
            quantity=1,
            unit="operation",
            metadata=metadata,
        )
        return installation.model_dump(mode="json")

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/rollback")
    def rollback_workspace_skill(
        workspace_id: str,
        skill_id: str,
        payload: SkillVersionMoveRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        installation = app.state.skill_service.rollback(
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            target_version=payload.target_version,
            expected_package_digest=payload.expected_package_digest,
            rolled_back_by_user_id=context.user_id,
        )
        metadata = {
            "skill_id": skill_id,
            "version": installation.installed_version,
            "package_digest": installation.package_digest,
            "source_digest": installation.source_digest,
            "operation": "rollback",
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.rolled_back",
            metadata=metadata,
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            workspace_id=workspace_id,
            user_id=context.user_id,
            skill_id=skill_id,
            meter_type="skill_management_operation_count",
            quantity=1,
            unit="operation",
            metadata=metadata,
        )
        return installation.model_dump(mode="json")

    @app.delete("/api/workspaces/{workspace_id}/skills/{skill_id}")
    def uninstall_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        installation = app.state.skill_service.uninstall(
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
        )
        metadata = {
            "skill_id": skill_id,
            "version": installation.installed_version,
            "package_digest": installation.package_digest,
            "source_digest": installation.source_digest,
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.uninstalled",
            metadata=metadata,
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            workspace_id=workspace_id,
            user_id=context.user_id,
            skill_id=skill_id,
            meter_type="skill_management_operation_count",
            quantity=1,
            unit="operation",
            metadata={"operation": "uninstall", **metadata},
        )
        return installation.model_dump(mode="json")

    @app.get("/api/workspaces/{workspace_id}/skills/{skill_id}/materialization")
    def preview_workspace_skill_materialization(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.read")
        plan = app.state.skill_service.materialization_plan(
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
        )
        return {
            "skill_id": plan.skill_id,
            "version": plan.version,
            "root_path": plan.root_path,
            "package_digest": plan.package_digest,
            "source_digest": plan.source_digest,
            "runtime_sandbox": plan.runtime_sandbox,
            "timeout_seconds": plan.timeout_seconds,
            "resolved_dependencies": plan.resolved_dependencies,
            "writes": [
                {
                    "path": item.path,
                    "content_digest": item.content_digest,
                    "size_bytes": item.size_bytes,
                    "mode": item.mode,
                }
                for item in plan.writes
            ],
        }

    @app.get("/api/workspaces/{workspace_id}/skills")
    def list_workspace_skills(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict]:
        require_permission(request, context, "skills.read")
        items = []
        for installation in app.state.skill_registry.list_for_workspace(
            context.tenant_id,
            workspace_id,
        ):
            item = installation.model_dump(mode="json")
            try:
                entry = app.state.skill_registry.get_visible_for_tenant(
                    context.tenant_id,
                    installation.skill_id,
                    user_id=context.user_id,
                    workspace_id=workspace_id,
                )
            except NotFoundError:
                item.update(
                    {
                        "invocation_mode": "unavailable",
                        "invocation_ready": False,
                        "missing_required_scopes": [],
                    }
                )
                items.append(item)
                continue
            granted_scopes = resolve_granted_scopes(
                request,
                context,
                entry.manifest.required_scopes,
            )
            missing_scopes = sorted(
                set(entry.manifest.required_scopes) - set(granted_scopes)
            )
            package_backed = installation.package_kind == SkillPackageKind.PACKAGE
            invocation_mode = (
                "agent_skill"
                if package_backed
                else skill_invocation_mode(entry.manifest)
            )
            executable = (
                package_backed
                or is_agent_run_skill(entry.manifest)
                or app.state.runtime.tool_gateway.can_execute_tool(entry.manifest.id)
            )
            item.update(
                {
                    "invocation_mode": invocation_mode,
                    "invocation_ready": (
                        installation.status == SkillInstallationStatus.ENABLED
                        and entry.status == SkillStatus.PUBLISHED
                        and not missing_scopes
                        and executable
                    ),
                    "missing_required_scopes": missing_scopes,
                }
            )
            items.append(item)
        return items

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/enable")
    def enable_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        installation = app.state.skill_service.enable(
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
        )
        metadata = {
            "skill_id": skill_id,
            "version": installation.installed_version,
            "package_digest": installation.package_digest,
            "source_digest": installation.source_digest,
            "status": installation.status.value,
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.enabled",
            metadata=metadata,
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            workspace_id=workspace_id,
            user_id=context.user_id,
            skill_id=skill_id,
            meter_type="skill_management_operation_count",
            quantity=1,
            unit="operation",
            metadata={"operation": "enable", **metadata},
        )
        return installation.model_dump(mode="json")

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/disable")
    def disable_workspace_skill(
        workspace_id: str,
        skill_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.install")
        installation = app.state.skill_registry.disable_for_workspace(
            context.tenant_id,
            workspace_id,
            skill_id,
        )
        metadata = {
            "skill_id": skill_id,
            "version": installation.installed_version,
            "package_digest": installation.package_digest,
            "source_digest": installation.source_digest,
            "status": installation.status.value,
        }
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.disabled",
            metadata=metadata,
            request=request,
        )
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            workspace_id=workspace_id,
            user_id=context.user_id,
            skill_id=skill_id,
            meter_type="skill_management_operation_count",
            quantity=1,
            unit="operation",
            metadata={"operation": "disable", **metadata},
        )
        return installation.model_dump(mode="json")

    @app.post("/api/workspaces/{workspace_id}/skills/{skill_id}/invoke")
    def invoke_workspace_skill(
        workspace_id: str,
        skill_id: str,
        payload: SkillInvokeRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "skills.invoke")
        entry = app.state.skill_registry.get_visible_for_tenant(
            context.tenant_id,
            skill_id,
            user_id=context.user_id,
            workspace_id=workspace_id,
        )
        if entry.status != SkillStatus.PUBLISHED:
            raise TenantAccessError(f"Skill is not published: {skill_id}")
        installation = next(
            (
                installed
                for installed in app.state.skill_registry.list_for_workspace(
                    context.tenant_id,
                    workspace_id,
                )
                if installed.skill_id == skill_id
            ),
            None,
        )
        if installation is None:
            raise NotFoundError(f"Skill installation not found: {skill_id}")
        if installation.status != SkillInstallationStatus.ENABLED:
            raise TenantAccessError(f"Skill installation is disabled: {skill_id}")

        granted_scopes = resolve_granted_scopes(
            request,
            context,
            entry.manifest.required_scopes,
        )
        missing_scopes = sorted(
            set(entry.manifest.required_scopes) - set(granted_scopes)
        )
        if missing_scopes:
            raise TenantAccessError(
                f"Permission denied: missing skill scopes: {', '.join(missing_scopes)}"
            )

        package_backed = installation.package_kind == SkillPackageKind.PACKAGE
        if package_backed or is_agent_run_skill(entry.manifest):
            validation = JsonSchemaValidator(
                json_schema=entry.manifest.input_schema
            ).validate(payload.input)
            if not validation.valid:
                raise ValueError(
                    f"Skill input is invalid: {'; '.join(validation.errors)}"
                )
            tool_name = "agent.skill" if package_backed else "agent.workflow"
            event_type = (
                "skill.invoked" if package_backed else "skill.workflow_invoked"
            )
            run = app.state.store.create_run(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                payload=RunCreate(
                    workspace_id=workspace_id,
                    agent_id=None if package_backed else entry.manifest.id,
                    message=build_agent_run_skill_message(
                        entry.manifest,
                        payload.input,
                    ),
                    mode=RunMode.CHAT if package_backed else RunMode.WORKFLOW,
                    resource_refs=(
                        [
                            ResourceReference(
                                type="skill",
                                id=entry.manifest.id,
                                version=installation.installed_version,
                            )
                        ]
                        if package_backed
                        else []
                    ),
                ),
            )
            app.state.store.append_run_event(
                run,
                event_type,
                {
                    "skill_id": entry.manifest.id,
                    "skill_version": installation.installed_version,
                    "input_keys": sorted(payload.input.keys()),
                },
            )
            state = app.state.runtime.execute_run(context.tenant_id, run.id)
            output = {
                "run_id": run.id,
                "status": state.status.value,
                "events_url": f"/api/runs/{run.id}/events",
            }
            if state.final_response_text:
                output["response"] = state.final_response_text
            metadata = {
                "skill_id": entry.manifest.id,
                "tool_name": tool_name,
                "workspace_id": workspace_id,
                "run_id": run.id,
                "input_keys": sorted(payload.input.keys()),
                "output_keys": sorted(output.keys()),
            }
            if not package_backed:
                app.state.store.record_billing_meter(
                    tenant_id=context.tenant_id,
                    run_id=run.id,
                    meter_type="skill_call_count",
                    quantity=1,
                    unit="call",
                    skill_id=entry.manifest.id,
                    workspace_id=workspace_id,
                    user_id=context.user_id,
                    metadata=metadata,
                )
            record_audit_event(
                app,
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                user_id=context.user_id,
                run_id=run.id,
                event_type=event_type,
                metadata=metadata,
                request=request,
            )
            return {
                "skill_id": entry.manifest.id,
                "tool_name": tool_name,
                "run_id": run.id,
                "status": state.status.value,
                "output": output,
            }

        result = app.state.runtime.tool_gateway.execute_request(
            ToolGatewayRequest(
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                user_id=context.user_id,
                run_id="workspace_skill_invoke",
                step_id=new_id("skill_step"),
                tool_name=entry.manifest.id,
                skill_id=entry.manifest.id,
                tool_input=payload.input,
                granted_scopes=granted_scopes,
            )
        )
        if result.tool_name != entry.manifest.id:
            raise ToolExecutionError("skill tool result does not match invoked skill")

        metadata = {
            "skill_id": entry.manifest.id,
            "tool_name": result.tool_name,
            "workspace_id": workspace_id,
            "input_keys": sorted(payload.input.keys()),
            "output_keys": sorted(result.output.keys()),
        }
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=None,
            meter_type="skill_call_count",
            quantity=1,
            unit="call",
            skill_id=entry.manifest.id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            metadata=metadata,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="skill.invoked",
            metadata=metadata,
            request=request,
        )
        return {
            "skill_id": entry.manifest.id,
            "tool_name": result.tool_name,
            "output": result.output,
        }

    @app.post("/api/uploads", status_code=status.HTTP_201_CREATED)
    def create_atomic_upload(
        payload: AtomicUploadRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.write")
        if Path(payload.filename).name != payload.filename or payload.filename in {
            ".",
            "..",
        }:
            raise ValueError("Upload filename must not contain a path")
        if payload.content_type not in app.state.settings.upload_allowed_content_types:
            raise ValueError("Upload content type is not allowed")
        max_bytes = app.state.settings.upload_max_bytes
        if len(payload.content_base64) > ((max_bytes + 2) // 3) * 4 + 8:
            raise ValueError("Upload exceeds the configured size limit")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 is not valid base64") from error
        if not content or len(content) > max_bytes:
            raise ValueError("Upload is empty or exceeds the configured size limit")
        storage_object = app.state.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=context.tenant_id,
                workspace_id=payload.workspace_id,
                purpose=StoragePurpose.UPLOAD,
                filename=payload.filename,
                content_type=payload.content_type,
                size_bytes=len(content),
                acl_subjects=payload.acl_subjects,
                sensitivity_level=payload.sensitivity_level,
            )
        )
        try:
            scan = app.state.storage_content_scanner.scan(
                StorageContentScanRequest(
                    storage_object=storage_object, content=content
                )
            )
            if not scan.allowed:
                raise StorageContentRejectedError(
                    "Upload content was rejected by the scanner"
                )
            upload = app.state.object_storage.upload(storage_object, content)
            uploaded = app.state.storage_catalog.mark_uploaded(
                context.tenant_id, storage_object.id, len(content)
            )
        except Exception:
            try:
                app.state.object_storage.delete(storage_object)
            except Exception:
                pass
            try:
                app.state.storage_catalog.mark_deleted(
                    context.tenant_id, storage_object.id, utc_now()
                )
            except Exception:
                pass
            raise
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="storage.uploaded",
            metadata={
                "storage_object_id": uploaded.id,
                "filename": uploaded.filename,
                "content_type": uploaded.content_type,
                "size_bytes": uploaded.size_bytes,
            },
            request=request,
        )
        return {
            "storage_object": uploaded.model_dump(mode="json"),
            "upload": upload.model_dump(mode="json"),
        }

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
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "storage.read")
        return list_or_page_created_at_records(
            app.state.storage_catalog.list_for_run(context.tenant_id, run_id),
            request,
            page,
        )

    @app.get("/api/workspaces/{workspace_id}/files")
    def list_workspace_files(
        workspace_id: str,
        request: Request,
        query: str = Query(default="", max_length=200),
        folder: str = Query(default="", max_length=512),
        include_run_files: bool = Query(default=False),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.read")
        normalized_folder = normalize_workspace_file_path(folder, allow_empty=True)
        normalized_query = query.strip().casefold()
        visible = []
        for storage_object in app.state.storage_catalog.list_active(
            context.tenant_id, workspace_id=workspace_id
        ):
            if not include_run_files and storage_object.run_id is not None:
                continue
            logical_path = storage_object.filename.replace("\\", "/")
            if normalized_folder and not logical_path.startswith(
                f"{normalized_folder}/"
            ):
                continue
            if normalized_query and normalized_query not in logical_path.casefold():
                continue
            try:
                require_storage_read_access(request, context, storage_object)
            except TenantAccessError:
                continue
            item = storage_object.model_dump(mode="json")
            item.update(
                {
                    "storage_object_id": storage_object.id,
                    "logical_path": logical_path,
                    "folder": (
                        ""
                        if PurePosixPath(logical_path).parent == PurePosixPath(".")
                        else str(PurePosixPath(logical_path).parent)
                    ),
                    "pinned_reference": storage_object_agent_reference_count(
                        app.state.agent_registry, context.tenant_id, storage_object.id
                    ),
                }
            )
            visible.append(item)
        visible.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return {
            "workspace_id": workspace_id,
            "files": visible,
            "count": len(visible),
            "include_run_files": include_run_files,
        }

    @app.patch("/api/storage/objects/{storage_object_id}")
    def update_storage_object_metadata(
        storage_object_id: str,
        payload: StorageObjectPatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "storage.write")
        storage_object = app.state.storage_catalog.get(
            context.tenant_id, storage_object_id
        )
        filename = normalize_workspace_file_path(payload.filename)
        updated = app.state.storage_catalog.update_metadata(
            context.tenant_id,
            storage_object_id,
            filename=filename,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=updated.workspace_id,
            user_id=context.user_id,
            run_id=updated.run_id,
            event_type="storage.metadata.updated",
            metadata={
                "storage_object_id": updated.id,
                "previous_filename": storage_object.filename,
                "filename": updated.filename,
            },
            request=request,
        )
        return updated.model_dump(mode="json")

    @app.post("/api/storage/objects/{storage_object_id}/signed-url")
    def create_storage_signed_url(
        storage_object_id: str,
        payload: StorageSignedUrlCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        storage_object = app.state.storage_catalog.get(
            context.tenant_id, storage_object_id
        )
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
        storage_object = app.state.storage_catalog.get(
            context.tenant_id, storage_object_id
        )
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
        storage_object = app.state.storage_catalog.get(
            context.tenant_id, storage_object_id
        )
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

    @app.get(
        "/api/share-links/{external_link_id}/artifacts/{artifact_id}",
        response_class=HTMLResponse,
    )
    def view_external_artifact(
        external_link_id: str,
        artifact_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1),
    ) -> HTMLResponse:
        artifact = require_external_artifact_read_access(
            request, tenant_id, external_link_id, artifact_id
        )
        preview = app.state.artifact_service.preview(tenant_id, artifact.id)
        download_url = request.url_for(
            "download_external_artifact",
            external_link_id=external_link_id,
            artifact_id=artifact.id,
        ).include_query_params(tenant_id=tenant_id)
        if preview.mode == "image":
            content = f'<img src="{escape(str(download_url), quote=True)}" alt="">'
        elif preview.mode == "pdf":
            content = (
                f'<iframe src="{escape(str(download_url), quote=True)}" '
                'title="PDF artifact"></iframe>'
            )
        elif preview.mode == "iframe":
            content = (
                '<iframe sandbox="" title="HTML artifact" srcdoc="'
                f'{escape(preview.srcdoc or "", quote=True)}"></iframe>'
            )
        elif preview.mode == "dashboard" and preview.dashboard is not None:
            content = (
                "<pre>"
                + escape(
                    json.dumps(
                        preview.dashboard.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                + "</pre>"
            )
        elif preview.text is not None:
            content = f"<pre>{escape(preview.text)}</pre>"
        else:
            content = '<p class="empty">This artifact is available as a download.</p>'
        safe_name = escape(artifact.name)
        safe_type = escape(artifact.artifact_type)
        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{safe_name}</title>
<style>body{{margin:0;background:#f1ede7;color:#393530;font:14px Inter,system-ui,sans-serif}}main{{max-width:1120px;margin:32px auto;padding:0 20px}}header{{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:14px}}small{{color:#8a8179;text-transform:uppercase;letter-spacing:.12em}}h1{{margin:4px 0 0;font-size:22px}}a{{padding:9px 13px;border:1px solid #cfc6bc;border-radius:8px;color:#4d4741;background:#faf8f5;text-decoration:none}}section{{min-height:420px;padding:18px;border:1px solid #d9d1c8;border-radius:14px;background:#fbf9f6;box-shadow:0 12px 36px rgb(55 44 34 / 8%)}}pre{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}}img,iframe{{display:block;width:100%;min-height:520px;border:0;object-fit:contain}}.empty{{color:#817970}}</style></head>
<body><main><header><div><small>{safe_type} artifact</small><h1>{safe_name}</h1></div><a href="{escape(str(download_url), quote=True)}">Download</a></header><section>{content}</section></main></body></html>"""
        record_audit_event(
            app,
            tenant_id=tenant_id,
            workspace_id=artifact.workspace_id,
            user_id=None,
            run_id=artifact.run_id,
            event_type="artifact.share_link.viewed",
            metadata={
                "artifact_id": artifact.id,
                "access_via": "external_link",
                "external_link_id_present": True,
            },
            request=request,
        )
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": "default-src 'none'; img-src 'self'; frame-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/api/share-links/{external_link_id}/artifacts/{artifact_id}/download")
    def download_external_artifact(
        external_link_id: str,
        artifact_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1),
    ) -> Response:
        artifact = require_external_artifact_read_access(
            request, tenant_id, external_link_id, artifact_id
        )
        if artifact.storage_object_id is None:
            source = app.state.artifact_service.source(tenant_id, artifact.id)
            content = source.source.encode("utf-8")
            content_type = source.content_type
        else:
            _, result = app.state.artifact_service.download(tenant_id, artifact.id)
            content = result.content
            content_type = result.content_type
        safe_filename = (
            Path(artifact.name)
            .name.replace('"', "")
            .replace("\r", "")
            .replace("\n", "")
        )
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/api/share-links/{external_link_id}/storage/objects/{storage_object_id}/content"
    )
    def download_external_share_link_storage_object_content(
        external_link_id: str,
        storage_object_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1),
    ) -> Response:
        storage_object = app.state.storage_catalog.get(tenant_id, storage_object_id)
        require_external_share_link_storage_read_access(
            request=request,
            tenant_id=tenant_id,
            external_link_id=external_link_id,
            storage_object=storage_object,
        )
        result = app.state.object_storage.download(storage_object)
        metadata = {
            **storage_audit_metadata(storage_object),
            "access_via": "external_link",
            "external_link_id_present": True,
        }
        if storage_object.run_id is not None:
            app.state.store.record_billing_meter(
                tenant_id=tenant_id,
                run_id=storage_object.run_id,
                meter_type="external_artifact_download_bytes",
                quantity=len(result.content),
                unit="byte",
                metadata=metadata,
            )
        record_audit_event(
            app,
            tenant_id=tenant_id,
            workspace_id=storage_object.workspace_id,
            user_id=None,
            run_id=storage_object.run_id,
            event_type="storage.downloaded",
            metadata=metadata,
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
        storage_object = app.state.storage_catalog.get(
            context.tenant_id, storage_object_id
        )
        reference_count = storage_object_agent_reference_count(
            app.state.agent_registry, context.tenant_id, storage_object_id
        )
        if reference_count:
            raise ValueError(
                f"Storage object is pinned by {reference_count} Agent version reference(s)"
            )
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
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "memory.read")
        return list_or_page_created_at_records(
            app.state.short_term_memory_service.list_for_run(
                context.tenant_id,
                run_id,
            ),
            request,
            page,
        )

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
        memory_status: MemoryStatus = Query(
            default=MemoryStatus.ACTIVE,
            alias="status",
        ),
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "memory.read")
        return list_or_page_created_at_records(
            app.state.long_term_memory_service.list_by_scope(
                context.tenant_id,
                scope_type,
                scope_id,
                memory_status,
            ),
            request,
            page,
        )

    @app.delete("/api/memory/{memory_id}")
    def forget_memory(
        memory_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "memory.write")
        existing = app.state.long_term_memory_service.get(
            context.tenant_id, memory_id
        )
        if (
            existing.scope_type == MemoryScopeType.USER
            and existing.scope_id != context.user_id
        ):
            require_permission(request, context, "memory.review")
        memory = app.state.long_term_memory_service.forget(
            context.tenant_id, memory_id
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=memory.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="memory.forgotten",
            metadata=memory_audit_metadata(memory),
            request=request,
        )
        return {"id": memory.id, "status": memory.status.value}

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

    @app.get("/api/knowledge-bases")
    def list_knowledge_bases(
        request: Request,
        workspace_id: str | None = None,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "knowledge.read")
        if workspace_id is None:
            bases = app.state.knowledge_service.list_bases_for_tenant(context.tenant_id)
        else:
            bases = app.state.knowledge_service.list_bases_for_workspace(
                context.tenant_id,
                workspace_id,
            )
        return list_or_page_created_at_records(bases, request, page)

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

    @app.get("/api/knowledge-documents")
    def list_knowledge_documents(
        request: Request,
        knowledge_base_id: str | None = None,
        workspace_id: str | None = None,
        page: PageRequest = Depends(get_page_request),
        context: RequestContext = Depends(get_request_context),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        require_permission(request, context, "knowledge.read")
        return list_or_page_created_at_records(
            app.state.knowledge_service.list_documents(
                context.tenant_id,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
            ),
            request,
            page,
        )

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
        document_chunks = knowledge_document_chunks(
            payload=payload,
            content=document_content,
            settings=app.state.settings,
        )
        document_chunks = embed_knowledge_document_chunks(
            app=app,
            request=request,
            payload=payload,
            context=context,
            chunks=document_chunks,
            embedding_gateway=app.state.embedding_gateway,
            settings=app.state.settings,
        )
        document = app.state.knowledge_service.register_document(
            KnowledgeDocumentCreate(
                tenant_id=context.tenant_id,
                uploaded_by_user_id=context.user_id,
                storage_object_id=storage_object.id,
                chunks=document_chunks,
                **payload.model_dump(exclude={"content", "content_type", "chunks"}),
            )
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=document.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="knowledge.document.registered",
            metadata=knowledge_document_audit_metadata(document, len(document_chunks)),
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
        retrieval_request = knowledge_retrieval_request(
            app=app,
            request=request,
            payload=payload,
            context=context,
            embedding_gateway=app.state.embedding_gateway,
            settings=app.state.settings,
        )
        results = app.state.knowledge_service.retrieve(retrieval_request)
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
        active_session_count = len(
            [
                session
                for session in app.state.sandbox_adapter.list_sessions(
                    context.tenant_id
                )
                if session.status == SandboxSessionStatus.ACTIVE
            ]
        )
        app.state.license_service.require_entitlement(
            tenant_id=context.tenant_id,
            feature=LicensedFeature.SANDBOX_CONCURRENCY,
            requested_amount=active_session_count + 1,
        )
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
        timeout_seconds = (
            payload.timeout_seconds or app.state.settings.sandbox_timeout_seconds
        )
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

    @app.post("/api/sandbox/secret-leases/resolve")
    def resolve_sandbox_secret_lease(
        payload: SecretLeaseResolveRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        require_sandbox_secret_resolver_token(request)
        resolution = app.state.secret_service.resolve_lease(
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            run_id=payload.run_id,
            step_id=payload.step_id,
            session_id=payload.session_id,
            lease_token=payload.lease_token,
            tool_name="sandbox.command",
            action=payload.action,
            require_bound_context=True,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="secret.lease.resolved",
            metadata=resolution.to_audit_metadata(),
            request=request,
        )
        return resolution.model_dump(mode="json")

    @app.post(
        "/api/sandbox/sessions/{session_id}/files", status_code=status.HTTP_201_CREATED
    )
    def upload_sandbox_file(
        session_id: str,
        payload: SandboxFileWriteRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        require_permission(request, context, "sandbox.execute")
        session = app.state.sandbox_adapter.get_session(context.tenant_id, session_id)
        file_write = SandboxFileWrite(
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            path=payload.path,
            content=payload.content,
            content_base64=payload.content_base64,
            content_type=payload.content_type,
        )
        content = file_write.content_bytes()
        if len(content) > app.state.settings.upload_max_bytes:
            raise ValueError("Sandbox file exceeds the configured size limit")
        file_ref = app.state.sandbox_adapter.upload_file(file_write)
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
                content=content,
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
        max_bytes = app.state.settings.upload_max_bytes
        listed_file = next(
            (
                file_ref
                for file_ref in app.state.sandbox_adapter.list_files(
                    context.tenant_id, session_id
                )
                if file_ref.path == path
            ),
            None,
        )
        if listed_file is not None and listed_file.size_bytes > max_bytes:
            raise ValueError("Sandbox file exceeds the configured size limit")
        file_ref = app.state.sandbox_adapter.download_file(
            tenant_id=context.tenant_id,
            session_id=session_id,
            path=path,
        )
        if file_ref.size_bytes > max_bytes:
            raise ValueError("Sandbox file exceeds the configured size limit")
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
            snapshot = snapshot.model_copy(update={"uri": snapshot_storage_object.uri})
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

    @app.get("/api/repositories")
    def list_repository_bindings(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.manage")
        return {
            "workspace_id": workspace_id,
            "repositories": [
                item.model_dump(mode="json")
                for item in app.state.coding_workspace_registry.list_repositories(
                    context.tenant_id, workspace_id
                )
            ],
        }

    @app.post("/api/repositories", status_code=status.HTTP_201_CREATED)
    def create_repository_binding(
        payload: RepositoryBindingCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.manage")
        item = app.state.coding_workspace_service.create_repository(
            context.tenant_id, context.user_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=item.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="coding.repository.connected",
            metadata={
                "repository_id": item.id,
                "provider": item.provider,
                "connector_id_present": item.connector_id is not None,
            },
            request=request,
        )
        return item.model_dump(mode="json")

    @app.patch("/api/repositories/{repository_id}")
    def update_repository_binding(
        repository_id: str,
        payload: RepositoryBindingPatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.manage")
        return app.state.coding_workspace_service.update_repository(
            context.tenant_id, repository_id, payload
        ).model_dump(mode="json")

    @app.get("/api/coding-workspaces")
    def list_coding_workspaces(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        return {
            "workspace_id": workspace_id,
            "coding_workspaces": [
                item.model_dump(mode="json")
                for item in app.state.coding_workspace_registry.list_workspaces(
                    context.tenant_id, workspace_id
                )
            ],
        }

    @app.post("/api/coding-workspaces", status_code=status.HTTP_201_CREATED)
    def create_coding_workspace(
        payload: CodingWorkspaceCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        item = app.state.coding_workspace_service.create_workspace(
            context.tenant_id, context.user_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=item.workspace_id,
            user_id=context.user_id,
            run_id=item.run_id,
            event_type="coding.workspace.created",
            metadata={
                "coding_workspace_id": item.id,
                "repository_id": item.repository_id,
                "branch": item.branch,
                "engine_session_id": item.engine_session_id,
            },
            request=request,
        )
        return item.model_dump(mode="json")

    @app.get("/api/coding-workspaces/{coding_workspace_id}")
    def get_coding_workspace(
        coding_workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        return app.state.coding_workspace_service.detail(
            context.tenant_id, coding_workspace_id
        )

    @app.get("/api/runs/{run_id}/coding-workspace")
    def get_run_coding_workspace(
        run_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        run = app.state.store.get_run(context.tenant_id, run_id)
        item = next(
            (
                candidate
                for candidate in app.state.coding_workspace_registry.list_workspaces(
                    context.tenant_id, run.workspace_id
                )
                if candidate.run_id == run_id
            ),
            None,
        )
        return {
            "run_id": run_id,
            "available": item is not None,
            "detail": app.state.coding_workspace_service.detail(
                context.tenant_id, item.id
            )
            if item is not None
            else None,
        }

    @app.put("/api/coding-workspaces/{coding_workspace_id}/changes")
    def submit_coding_changes(
        coding_workspace_id: str,
        payload: CodingChangesSubmit,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        item = app.state.coding_workspace_service.submit_changes(
            context.tenant_id, coding_workspace_id, payload
        )
        run = app.state.store.get_run(context.tenant_id, item.run_id)
        app.state.store.append_run_event(
            run,
            "coding.changes.updated",
            {
                "coding_workspace_id": item.id,
                "file_count": len(payload.changes),
                "head_revision": payload.head_revision,
            },
        )
        return item.model_dump(mode="json")

    @app.post(
        "/api/coding-workspaces/{coding_workspace_id}/tests",
        status_code=status.HTTP_201_CREATED,
    )
    def add_coding_test_result(
        coding_workspace_id: str,
        payload: CodingTestResultCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        result = app.state.coding_workspace_service.add_test(
            context.tenant_id, coding_workspace_id, payload
        )
        workspace = app.state.coding_workspace_registry.get_workspace(
            context.tenant_id, coding_workspace_id
        )
        run = app.state.store.get_run(context.tenant_id, workspace.run_id)
        app.state.store.append_run_event(
            run,
            "coding.test.completed",
            {
                "coding_workspace_id": workspace.id,
                "test_result_id": result.id,
                "status": result.status,
                "command": result.command,
                "duration_seconds": result.duration_seconds,
            },
        )
        return result.model_dump(mode="json")

    @app.post(
        "/api/coding-workspaces/{coding_workspace_id}/checkpoints",
        status_code=status.HTTP_201_CREATED,
    )
    def add_coding_checkpoint(
        coding_workspace_id: str,
        payload: CodingCheckpointCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        checkpoint = app.state.coding_workspace_service.add_checkpoint(
            context.tenant_id, context.user_id, coding_workspace_id, payload
        )
        workspace = app.state.coding_workspace_registry.get_workspace(
            context.tenant_id, coding_workspace_id
        )
        run = app.state.store.get_run(context.tenant_id, workspace.run_id)
        app.state.store.append_run_event(
            run,
            "coding.checkpoint.created",
            {
                "coding_workspace_id": workspace.id,
                "checkpoint_id": checkpoint.id,
                "revision": checkpoint.revision,
                "label": checkpoint.label,
            },
        )
        return checkpoint.model_dump(mode="json")

    @app.post(
        "/api/coding-workspaces/{coding_workspace_id}/deliveries",
        status_code=status.HTTP_201_CREATED,
    )
    def add_coding_delivery(
        coding_workspace_id: str,
        payload: CodingDeliveryCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        item = app.state.coding_workspace_service.add_delivery(
            context.tenant_id, context.user_id, coding_workspace_id, payload
        )
        workspace = app.state.coding_workspace_registry.get_workspace(
            context.tenant_id, coding_workspace_id
        )
        run = app.state.store.get_run(context.tenant_id, workspace.run_id)
        app.state.store.append_run_event(
            run,
            "coding.delivery.updated",
            {
                "coding_workspace_id": workspace.id,
                "delivery_id": item.id,
                "status": item.status,
                "commit_sha": item.commit_sha,
                "pull_request_url": item.pull_request_url,
            },
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace.workspace_id,
            user_id=context.user_id,
            run_id=workspace.run_id,
            event_type="coding.delivery.recorded",
            metadata={
                "coding_workspace_id": workspace.id,
                "delivery_id": item.id,
                "status": item.status,
                "commit_sha_present": item.commit_sha is not None,
                "pull_request_present": item.pull_request_url is not None,
            },
            request=request,
        )
        return item.model_dump(mode="json")

    @app.post("/api/coding-workspaces/{coding_workspace_id}/actions")
    def request_coding_workspace_action(
        coding_workspace_id: str,
        payload: CodingActionRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        workspace = app.state.coding_workspace_registry.get_workspace(
            context.tenant_id, coding_workspace_id
        )
        if workspace.engine_session_id is None:
            raise ValueError(
                "Coding Workspace is not attached to an Agent Engine session"
            )
        session = app.state.agent_engine_service.operation(
            context.tenant_id,
            workspace.engine_session_id,
            "coding/actions",
            {"coding_workspace_id": workspace.id, **payload.model_dump(mode="json")},
        )
        run = app.state.store.get_run(context.tenant_id, workspace.run_id)
        app.state.store.append_run_event(
            run,
            "coding.action.requested",
            {
                "coding_workspace_id": workspace.id,
                "engine_session_id": session.id,
                "action": payload.action,
                "message_present": payload.message is not None,
                "command_present": payload.command is not None,
            },
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=workspace.workspace_id,
            user_id=context.user_id,
            run_id=workspace.run_id,
            event_type="coding.action.requested",
            metadata={
                "coding_workspace_id": workspace.id,
                "engine_session_id": session.id,
                "action": payload.action,
            },
            request=request,
        )
        return {
            "accepted": True,
            "coding_workspace_id": workspace.id,
            "engine_session": session.model_dump(mode="json"),
        }

    @app.get("/api/agent-engines/connections")
    def list_agent_engine_connections(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.manage")
        return {
            "workspace_id": workspace_id,
            "connections": [
                agent_engine_connection_payload(item)
                for item in app.state.agent_engine_registry.list_connections(
                    context.tenant_id, workspace_id
                )
            ],
        }

    @app.post("/api/agent-engines/connections", status_code=status.HTTP_201_CREATED)
    def create_agent_engine_connection(
        payload: AgentEngineConnectionCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.manage")
        connection = app.state.agent_engine_service.create_connection(
            context.tenant_id, context.user_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=connection.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="agent_engine.connection.created",
            metadata={
                "connection_id": connection.id,
                "engine_type": connection.engine_type.value,
                "secret_ref_present": connection.secret_ref_id is not None,
            },
            request=request,
        )
        return agent_engine_connection_payload(connection)

    @app.patch("/api/agent-engines/connections/{connection_id}")
    def update_agent_engine_connection(
        connection_id: str,
        payload: AgentEngineConnectionPatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "connectors.manage")
        connection = app.state.agent_engine_service.update_connection(
            context.tenant_id, connection_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=connection.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="agent_engine.connection.updated",
            metadata={
                "connection_id": connection.id,
                "engine_type": connection.engine_type.value,
                "status": connection.status,
            },
            request=request,
        )
        return agent_engine_connection_payload(connection)

    @app.get("/api/agent-engines/sessions")
    def list_agent_engine_sessions(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        return {
            "workspace_id": workspace_id,
            "sessions": [
                item.model_dump(mode="json")
                for item in app.state.agent_engine_registry.list_sessions(
                    context.tenant_id, workspace_id
                )
            ],
        }

    @app.post("/api/agent-engines/sessions", status_code=status.HTTP_201_CREATED)
    def create_agent_engine_session(
        payload: AgentEngineSessionCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        if payload.run_id is not None:
            run = app.state.store.get_run(context.tenant_id, payload.run_id)
            if run.workspace_id != payload.workspace_id:
                raise TenantAccessError("Agent Engine Run is outside the workspace")
        session = app.state.agent_engine_service.start_session(
            context.tenant_id, context.user_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=session.run_id,
            event_type="agent_engine.session.started",
            metadata={
                "session_id": session.id,
                "connection_id": session.connection_id,
                "engine_type": session.engine_type.value,
            },
            request=request,
        )
        return session.model_dump(mode="json")

    @app.get("/api/agent-engines/sessions/{session_id}/events")
    def list_agent_engine_events(
        session_id: str,
        request: Request,
        refresh: bool = False,
        after_sequence: int = Query(default=0, ge=0),
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        events = (
            app.state.agent_engine_service.refresh_events(context.tenant_id, session_id)
            if refresh
            else app.state.agent_engine_registry.list_events(
                context.tenant_id, session_id, after_sequence
            )
        )
        return {
            "session_id": session_id,
            "events": [
                item.model_dump(mode="json")
                for item in events
                if item.sequence > after_sequence
            ],
        }

    @app.post("/api/agent-engines/sessions/{session_id}/turns")
    def send_agent_engine_turn(
        session_id: str,
        payload: AgentEngineTurn,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        return app.state.agent_engine_service.operation(
            context.tenant_id, session_id, "turns", payload.model_dump()
        ).model_dump(mode="json")

    @app.post("/api/agent-engines/sessions/{session_id}/steer")
    def steer_agent_engine_session(
        session_id: str,
        payload: AgentEngineTurn,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        return app.state.agent_engine_service.operation(
            context.tenant_id, session_id, "steer", payload.model_dump()
        ).model_dump(mode="json")

    @app.post("/api/agent-engines/sessions/{session_id}/approvals/{approval_id}")
    def decide_agent_engine_approval(
        session_id: str,
        approval_id: str,
        payload: AgentEngineApprovalDecision,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        session = app.state.agent_engine_service.operation(
            context.tenant_id,
            session_id,
            f"approvals/{approval_id}",
            payload.model_dump(),
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=session.run_id,
            event_type=f"agent_engine.approval.{payload.decision}",
            metadata={"session_id": session.id, "approval_id": approval_id},
            request=request,
        )
        return session.model_dump(mode="json")

    @app.post("/api/agent-engines/sessions/{session_id}/{operation}")
    def control_agent_engine_session(
        session_id: str,
        operation: Literal["cancel", "resume", "close"],
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "sandbox.create")
        session = app.state.agent_engine_service.operation(
            context.tenant_id, session_id, operation
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=session.run_id,
            event_type=f"agent_engine.session.{operation}",
            metadata={
                "session_id": session.id,
                "engine_type": session.engine_type.value,
            },
            request=request,
        )
        return session.model_dump(mode="json")

    @app.get("/api/browser/profiles")
    def list_browser_profiles(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        profiles = app.state.browser_profile_service.list_profiles(
            context.tenant_id, workspace_id
        )
        return {
            "workspace_id": workspace_id,
            "profiles": [browser_profile_public_payload(item) for item in profiles],
        }

    @app.post("/api/browser/profiles", status_code=status.HTTP_201_CREATED)
    def create_browser_profile(
        payload: BrowserProfileCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        profile = app.state.browser_profile_service.create(
            context.tenant_id, context.user_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=profile.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="browser.profile.created",
            metadata=browser_profile_audit_metadata(profile),
            request=request,
        )
        return browser_profile_public_payload(profile)

    @app.patch("/api/browser/profiles/{profile_id}")
    def update_browser_profile(
        profile_id: str,
        payload: BrowserProfilePatch,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        profile = app.state.browser_profile_service.update(
            context.tenant_id, profile_id, payload
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=profile.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="browser.profile.updated",
            metadata=browser_profile_audit_metadata(profile),
            request=request,
        )
        return browser_profile_public_payload(profile)

    @app.delete("/api/browser/profiles/{profile_id}")
    def disable_browser_profile(
        profile_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        profile = app.state.browser_profile_service.get_profile(
            context.tenant_id, profile_id
        )
        for session in app.state.browser_profile_service.list_sessions(
            context.tenant_id, profile.workspace_id
        ):
            if session.profile_id == profile_id and session.status == "active":
                app.state.browser_profile_service.close_session(
                    context.tenant_id, session.session_id
                )
        disabled = app.state.browser_profile_service.update(
            context.tenant_id,
            profile_id,
            BrowserProfilePatch(status="disabled", is_default=False),
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=disabled.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="browser.profile.disabled",
            metadata=browser_profile_audit_metadata(disabled),
            request=request,
        )
        return browser_profile_public_payload(disabled)

    @app.get("/api/browser/profile-sessions")
    def list_browser_profile_sessions(
        workspace_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        sessions = app.state.browser_profile_service.list_sessions(
            context.tenant_id, workspace_id
        )
        return {
            "workspace_id": workspace_id,
            "sessions": [item.model_dump(mode="json") for item in sessions],
        }

    @app.post(
        "/api/browser/profiles/{profile_id}/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    def open_browser_profile_session(
        profile_id: str,
        payload: BrowserProfileSessionCreate,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        profile = app.state.browser_profile_service.get_profile(
            context.tenant_id, profile_id
        )
        session = app.state.browser_profile_service.open_session(
            tenant_id=context.tenant_id,
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
            run_id=None,
            user_id=context.user_id,
            start_url=payload.start_url,
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=profile.workspace_id,
            user_id=context.user_id,
            run_id=None,
            event_type="browser.profile_session.opened",
            metadata={
                "profile_id": profile.id,
                "session_id": session.session_id,
                "current_url": session.current_url,
            },
            request=request,
        )
        return session.model_dump(mode="json")

    @app.post("/api/browser/profile-sessions/{session_id}/actions")
    def apply_browser_profile_action(
        session_id: str,
        payload: BrowserActionRequest,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        observation = app.state.browser_profile_service.apply_action(
            tenant_id=context.tenant_id,
            session_id=session_id,
            action_type=payload.action_type,
            url=payload.url,
            selector=payload.selector,
            text=payload.text,
            metadata=payload.metadata,
        )
        response_payload = observation.model_dump(mode="json")
        if observation.screenshot_content is not None:
            session = app.state.browser_profile_registry.get_session(
                context.tenant_id, session_id
            )
            storage_object = app.state.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=context.tenant_id,
                    workspace_id=session.workspace_id,
                    purpose=StoragePurpose.BROWSER_SCREENSHOT,
                    filename=f"{session_id}.png",
                    content_type="image/png",
                    size_bytes=len(observation.screenshot_content),
                )
            )
            app.state.object_storage.upload(
                storage_object, observation.screenshot_content
            )
            response_payload["storage_object_id"] = storage_object.id
        record = app.state.browser_profile_registry.get_session(
            context.tenant_id, session_id
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=record.workspace_id,
            user_id=context.user_id,
            run_id=record.run_id,
            event_type="browser.profile_session.action",
            metadata={
                "profile_id": record.profile_id,
                "session_id": session_id,
                "action_type": payload.action_type.value,
                "current_url": observation.current_url,
            },
            request=request,
        )
        return response_payload

    @app.delete("/api/browser/profile-sessions/{session_id}")
    def close_browser_profile_session(
        session_id: str,
        request: Request,
        context: RequestContext = Depends(get_request_context),
    ) -> dict[str, Any]:
        require_permission(request, context, "browser.act")
        session = app.state.browser_profile_service.close_session(
            context.tenant_id, session_id
        )
        record_audit_event(
            app,
            tenant_id=context.tenant_id,
            workspace_id=session.workspace_id,
            user_id=context.user_id,
            run_id=session.run_id,
            event_type="browser.profile_session.closed",
            metadata={
                "profile_id": session.profile_id,
                "session_id": session.session_id,
                "revision_saved": True,
            },
            request=request,
        )
        return session.model_dump(mode="json")

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
    if settings.browser_provider in {"playwright", "browserbase"}:
        return HttpBrowserController(
            provider=settings.browser_provider,
            base_url=settings.browser_controller_base_url,
            api_key=settings.browser_controller_api_key,
            timeout_seconds=settings.browser_controller_timeout_seconds,
        )
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


def build_billing_invoice_store(settings: Settings) -> BillingInvoiceStore:
    if settings.billing_invoice_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlBillingInvoiceStore(config=config)
    return InMemoryBillingInvoiceStore()


def build_billing_pricing_rule_store(settings: Settings) -> BillingPricingRuleStore:
    if settings.billing_pricing_rule_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlBillingPricingRuleStore(config=config)
    return InMemoryBillingPricingRuleStore()


def build_share_grant_store(settings: Settings) -> ShareGrantStore:
    if settings.share_grant_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlShareGrantStore(config=config)
    return InMemoryShareGrantStore()


def build_agent_registry(settings: Settings):
    if settings.agent_registry_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlAgentRegistry(config=config)
    return InMemoryAgentRegistry()


def build_agent_api_key_store(settings: Settings):
    if settings.agent_registry_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlAgentApiKeyStore(config=config)
    return InMemoryAgentApiKeyStore()


def build_evaluation_repository(settings: Settings) -> EvaluationRepository:
    if settings.evaluation_repository_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlEvaluationRepository(config=config)
    return InMemoryEvaluationRepository()


def build_browser_profile_registry(settings: Settings) -> BrowserProfileRegistry:
    if settings.browser_profile_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlBrowserProfileRegistry(config=config)
    return InMemoryBrowserProfileRegistry()


def build_agent_engine_registry(settings: Settings) -> AgentEngineRegistry:
    if settings.agent_engine_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlAgentEngineRegistry(config=config)
    return InMemoryAgentEngineRegistry()


def build_coding_workspace_registry(settings: Settings) -> CodingWorkspaceRegistry:
    if settings.coding_workspace_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlCodingWorkspaceRegistry(config=config)
    return CodingWorkspaceRegistry()


def build_thread_share_store(settings: Settings) -> ThreadShareStore:
    if settings.thread_share_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config, migrations_path=Path("apps/api/migrations")
        ).apply()
        return SqlThreadShareStore(config=config)
    return InMemoryThreadShareStore()


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
        rule.skill_id,
        rule.meter_type,
        rule.unit,
        rule.provider,
        rule.model,
        rule.currency,
    )


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


def build_model_gateway(
    settings: Settings,
    secret_service: SecretService | None,
    model_provider_store: ModelProviderStore | None = None,
) -> ModelGateway:
    providers = effective_model_gateway_providers(settings, model_provider_store)
    if providers:
        return ModelGatewayRouter(
            provider_registry=ModelProviderRegistry(providers=providers),
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


def effective_model_gateway_providers(
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
    if providers and settings.model_gateway_model:
        providers.append(_direct_model_gateway_provider(settings))
    return providers


def effective_chat_model_gateway_providers(
    settings: Settings,
    model_provider_store: ModelProviderStore | None = None,
) -> list[ModelProviderConfig]:
    providers = effective_model_gateway_providers(settings, model_provider_store)
    if providers or not settings.model_gateway_model:
        return providers
    return [_direct_model_gateway_provider(settings)]


def _direct_model_gateway_provider(settings: Settings) -> ModelProviderConfig:
    return ModelProviderConfig(
        id="default",
        display_name="Default",
        base_url=settings.model_gateway_base_url,
        api_key=settings.model_gateway_api_key,
        api_key_secret_ref_id=settings.model_gateway_api_key_secret_ref_id or None,
        secret_lease_ttl_seconds=settings.model_gateway_secret_lease_ttl_seconds,
        default_model=settings.model_gateway_model,
        model_ids=[settings.model_gateway_model] if settings.model_gateway_model else [],
        reasoning_efforts=settings.model_gateway_reasoning_efforts,
        default_reasoning_effort=settings.model_gateway_default_reasoning_effort,
        timeout_seconds=settings.model_gateway_timeout_seconds,
        chat_request_options=settings.model_gateway_chat_request_options,
    )


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


def build_secret_service_readiness(
    settings: Settings,
    secret_service: SecretService,
) -> dict[str, Any]:
    if settings.secret_service_backend == "memory":
        missing = (
            ["durable_secret_backend"]
            if settings.run_execution_dispatch_mode == "queue"
            else []
        )
        return {
            "configured": not missing,
            "backend": "memory",
            "credentials_configured": None,
            "endpoint_configured": False,
            "missing": missing,
        }

    if settings.secret_service_backend == "local":
        configured = (
            isinstance(secret_service, LocalEncryptedSecretService)
            and secret_service.is_ready()
        )
        return {
            "configured": configured,
            "backend": "local",
            "credentials_configured": None,
            "endpoint_configured": False,
            "missing": [] if configured else ["local_secret_store"],
        }

    credentials_configured = (
        isinstance(secret_service, AwsSecretsManagerSecretService)
        and secret_service.credentials_available()
    )
    missing = [] if credentials_configured else ["aws_credentials"]
    return {
        "configured": not missing,
        "backend": "aws_secrets_manager",
        "credentials_configured": credentials_configured,
        "endpoint_configured": bool(settings.secret_service_endpoint_url),
        "missing": missing,
    }


def build_model_gateway_readiness(
    settings: Settings,
    model_provider_store: ModelProviderStore | None = None,
) -> ModelGatewayReadiness:
    providers = effective_model_gateway_providers(settings, model_provider_store)
    if providers:
        return _build_provider_model_gateway_readiness(settings, providers)
    missing: list[str] = []
    model_source = _model_gateway_model_source(settings)
    credential_source = _model_gateway_direct_credential_source(settings)
    if model_source == "none":
        missing.append("model")
    if credential_source == "none":
        missing.append("credential")
    return ModelGatewayReadiness(
        configured=not missing,
        gateway_type="openai_compatible",
        base_url=settings.model_gateway_base_url,
        model=settings.model_gateway_model,
        provider_count=0,
        configured_provider_count=0,
        missing=missing,
        model_source=model_source,
        credential_source=credential_source,
    )


def build_sandbox_readiness(
    settings: Settings,
    sandbox_adapter: SandboxAdapter | None = None,
) -> SandboxReadiness:
    missing: list[str] = []
    direct_e2b = settings.sandbox_provider == "e2b" and bool(settings.e2b_api_key)
    controller_required = (
        settings.sandbox_provider in ENTERPRISE_SANDBOX_PROVIDERS and not direct_e2b
    )
    controller_endpoint_configured = bool(settings.sandbox_controller_base_url.strip())
    controller_auth_configured = bool(settings.sandbox_controller_api_key.strip())
    controller_configured = (
        controller_endpoint_configured and controller_auth_configured
        if controller_required
        else False
    )
    if settings.sandbox_provider == "disabled":
        missing.append("provider")
    if controller_required and not controller_endpoint_configured:
        missing.append("sandbox_controller_base_url")
    if controller_required and not controller_auth_configured:
        missing.append("sandbox_controller_api_key")
    readiness = SandboxReadiness(
        configured=not missing,
        provider=settings.sandbox_provider,
        controller_required=controller_required,
        controller_configured=controller_configured,
        controller_endpoint_configured=controller_endpoint_configured,
        controller_auth_configured=controller_auth_configured,
        missing=missing,
    )
    if (
        sandbox_adapter is not None
        and settings.sandbox_provider != "disabled"
        and (not controller_required or controller_configured)
    ):
        try:
            capabilities = sandbox_adapter.get_capabilities()
        except Exception:
            if controller_required:
                controller_missing = list(readiness.missing)
                if "sandbox_controller_capabilities" not in controller_missing:
                    controller_missing.append("sandbox_controller_capabilities")
                return readiness.model_copy(
                    update={
                        "configured": False,
                        "capabilities_checked": False,
                        "missing": controller_missing,
                    }
                )
            return readiness
        readiness = readiness.model_copy(
            update={
                "capabilities_checked": True,
                "network_isolation_declared": capabilities.network_isolation,
                "filesystem_isolation_declared": capabilities.filesystem_isolation,
                "resource_limits_declared": capabilities.resource_limits,
                "destroy_supported_declared": capabilities.destroy_supported,
                "session_ttl_enforced_declared": capabilities.session_ttl_enforced,
                "runtime_isolation_declared": capabilities.runtime_isolation,
                "image_policy_enforced_declared": capabilities.image_policy_enforced,
                "allowed_image_count": capabilities.allowed_image_count,
                "max_session_ttl_seconds": capabilities.max_session_ttl_seconds,
                "max_sessions": capabilities.max_sessions,
                "max_sessions_per_tenant": capabilities.max_sessions_per_tenant,
                "max_sessions_per_run": capabilities.max_sessions_per_run,
            }
        )
    return readiness


def build_browser_readiness(
    settings: Settings,
    browser_controller: BrowserController | None = None,
) -> BrowserReadiness:
    missing: list[str] = []
    controller_required = settings.browser_provider in {"playwright", "browserbase"}
    controller_endpoint_configured = bool(settings.browser_controller_base_url.strip())
    controller_auth_configured = bool(settings.browser_controller_api_key.strip())
    controller_configured = (
        controller_endpoint_configured and controller_auth_configured
        if controller_required
        else False
    )
    if settings.browser_provider == "disabled":
        missing.append("provider")
    if controller_required and not controller_endpoint_configured:
        missing.append("browser_controller_base_url")
    if controller_required and not controller_auth_configured:
        missing.append("browser_controller_api_key")
    if controller_required and not settings.browser_controller_navigation_allowed_hosts:
        missing.append("browser_controller_navigation_allowed_hosts")
    readiness = BrowserReadiness(
        configured=not missing,
        provider=settings.browser_provider,
        controller_required=controller_required,
        controller_configured=controller_configured,
        controller_endpoint_configured=controller_endpoint_configured,
        controller_auth_configured=controller_auth_configured,
        missing=missing,
    )
    if (
        browser_controller is not None
        and settings.browser_provider != "disabled"
        and (not controller_required or controller_configured)
    ):
        try:
            capabilities = browser_controller.capabilities()
        except Exception:
            if controller_required:
                controller_missing = list(readiness.missing)
                if "browser_controller_capabilities" not in controller_missing:
                    controller_missing.append("browser_controller_capabilities")
                return readiness.model_copy(
                    update={
                        "configured": False,
                        "capabilities_checked": False,
                        "missing": controller_missing,
                    }
                )
            return readiness
        capability_missing = list(readiness.missing)
        if not capabilities.navigation_allowlist_enforced:
            capability_missing.append("browser_navigation_allowlist")
        readiness = readiness.model_copy(
            update={
                "configured": not capability_missing,
                "missing": capability_missing,
                "capabilities_checked": True,
                "auth_required_declared": capabilities.auth_required,
                "session_ttl_enforced_declared": capabilities.session_ttl_enforced,
                "max_session_ttl_seconds": capabilities.max_session_ttl_seconds,
                "max_sessions": capabilities.max_sessions,
                "max_sessions_per_tenant": capabilities.max_sessions_per_tenant,
                "max_sessions_per_run": capabilities.max_sessions_per_run,
                "navigation_allowlist_enforced_declared": (
                    capabilities.navigation_allowlist_enforced
                ),
                "navigation_allowed_host_count": (
                    capabilities.navigation_allowed_host_count
                ),
            }
        )
    return readiness


def _build_provider_model_gateway_readiness(
    settings: Settings,
    providers: list[ModelProviderConfig],
) -> ModelGatewayReadiness:
    model_source = (
        "provider"
        if any(provider.default_model or provider.model_ids for provider in providers)
        else _model_gateway_model_source(settings)
    )
    configured_provider_count = 0
    provider_ids: list[str] = []
    configured_provider_ids: list[str] = []
    has_model = False
    has_credential = False
    for provider in providers:
        provider_ids.append(provider.id)
        provider_model_source = _model_provider_model_source(settings, provider)
        provider_credential_source = _model_provider_credential_source(provider)
        if provider_model_source != "none":
            has_model = True
        if provider_credential_source != "none":
            has_credential = True
        if provider_model_source != "none" and provider_credential_source != "none":
            configured_provider_count += 1
            configured_provider_ids.append(provider.id)
    missing: list[str] = []
    if not has_model:
        missing.append("model")
    if not has_credential:
        missing.append("credential")
    if configured_provider_count == 0:
        missing.append("configured_provider")
    return ModelGatewayReadiness(
        configured=configured_provider_count > 0,
        gateway_type="provider_registry",
        provider_count=len(providers),
        configured_provider_count=configured_provider_count,
        provider_ids=provider_ids,
        configured_provider_ids=configured_provider_ids,
        missing=missing,
        model_source=model_source,
        credential_source="provider_registry" if has_credential else "none",
    )


def _model_gateway_model_source(settings: Settings) -> str:
    if settings.model_gateway_model:
        return "settings"
    if any(scope.default_model for scope in settings.model_gateway_policy_scopes):
        return "policy_scope"
    if any(
        provider.default_model or provider.model_ids
        for provider in settings.model_gateway_providers
    ):
        return "provider"
    return "none"


def _model_gateway_direct_credential_source(settings: Settings) -> str:
    if settings.model_gateway_api_key_secret_ref_id:
        return "secret_ref"
    if settings.model_gateway_api_key:
        return "api_key"
    return "none"


def _model_provider_model_source(
    settings: Settings,
    provider: ModelProviderConfig,
) -> str:
    if provider.default_model or provider.model_ids:
        return "provider"
    if settings.model_gateway_model:
        return "settings"
    if any(scope.default_model for scope in settings.model_gateway_policy_scopes):
        return "policy_scope"
    return "none"


def _model_provider_credential_source(provider: ModelProviderConfig) -> str:
    if provider.api_key_secret_ref_id:
        return "secret_ref"
    if provider.api_key:
        return "api_key"
    return "none"


def _model_provider_api_payload(
    settings: Settings,
    provider: ModelProviderConfig,
    status: str = "active",
    source: str = "settings",
) -> dict:
    payload = provider.model_dump(mode="json")
    payload.pop("api_key", None)
    payload["model_source"] = _model_provider_model_source(settings, provider)
    payload["credential_source"] = _model_provider_credential_source(provider)
    payload["status"] = status
    payload["source"] = source
    return payload


def _model_provider_record_api_payload(
    settings: Settings,
    record: ModelProviderRecord,
) -> dict:
    payload = _model_provider_api_payload(
        settings=settings,
        provider=record.provider,
        status=record.status,
        source="store",
    )
    payload["current_version"] = record.current_version
    return payload


def _model_provider_version_api_payload(
    settings: Settings,
    record: ModelProviderVersionRecord,
) -> dict:
    payload = _model_provider_api_payload(
        settings=settings,
        provider=record.provider,
        status=record.status,
        source="store",
    )
    payload["provider_id"] = record.provider_id
    payload["version"] = record.version
    payload["change_type"] = record.change_type
    payload["created_by_user_id"] = record.created_by_user_id
    payload["created_at"] = record.created_at.isoformat()
    return payload


def _model_policy_version_api_payload(record: ModelPolicyVersionRecord) -> dict:
    return {
        "tenant_id": record.tenant_id,
        "workspace_id": record.workspace_id,
        "version": record.version,
        "default_model": record.default_model,
        "allowed_models": record.allowed_models,
        "denied_models": record.denied_models,
        "model_sensitivity_limits": record.model_sensitivity_limits,
        "change_type": record.change_type,
        "change_request_id": record.change_request_id,
        "created_by_user_id": record.created_by_user_id,
        "created_at": record.created_at.isoformat(),
    }


def _model_policy_change_request_api_payload(
    record: ModelPolicyChangeRequestRecord,
) -> dict:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "operation": record.operation,
        "status": record.status,
        "requested_by_user_id": record.requested_by_user_id,
        "reviewed_by_user_id": record.reviewed_by_user_id,
        "created_at": record.created_at.isoformat(),
        "reviewed_at": record.reviewed_at.isoformat()
        if record.reviewed_at is not None
        else None,
        "scope": record.scope_upsert.model_dump(mode="json"),
    }


def _model_provider_change_request_api_payload(
    settings: Settings,
    record: ModelProviderChangeRequestRecord,
) -> dict:
    payload = {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "provider_id": record.provider_id,
        "operation": record.operation,
        "status": record.status,
        "requested_by_user_id": record.requested_by_user_id,
        "reviewed_by_user_id": record.reviewed_by_user_id,
        "created_at": record.created_at.isoformat(),
        "reviewed_at": record.reviewed_at.isoformat()
        if record.reviewed_at is not None
        else None,
    }
    if record.provider_upsert is not None:
        payload["provider"] = _model_provider_api_payload(
            settings=settings,
            provider=record.provider_upsert.to_provider_config(),
            status="pending",
            source="change_request",
        )
    if record.api_key_secret_ref_id is not None:
        payload["credential_source"] = "secret_ref"
    if record.target_status is not None:
        payload["target_status"] = record.target_status
    if record.rollback_version is not None:
        payload["rollback_version"] = record.rollback_version
    return payload


def _model_provider_visible_to_tenant(
    provider: ModelProviderConfig,
    tenant_id: str,
) -> bool:
    return provider.tenant_id == tenant_id


def build_embedding_gateway(
    settings: Settings,
    secret_service: SecretService | None,
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


def build_trigger_service(settings: Settings) -> TriggerService:
    if settings.trigger_store_backend == "sql":
        config = settings.database_config()
        MigrationRunner(
            config=config,
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return TriggerService(store=SqlTriggerStore(config=config))
    return TriggerService(store=InMemoryTriggerStore())


def build_trigger_webhook_verifier(settings: Settings) -> TriggerWebhookVerifier:
    return TriggerWebhookVerifier(
        signing_secrets=settings.trigger_webhook_signing_secrets,
        tolerance_seconds=settings.trigger_webhook_signature_tolerance_seconds,
        allow_unsigned=settings.trigger_webhook_allow_unsigned,
    )


def build_connector_registry(
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


def build_control_plane_store(
    settings: Settings,
) -> InMemoryControlPlaneStore | SqlControlPlaneRepository:
    if settings.control_plane_store_backend == "sql":
        repository = SqlControlPlaneRepository(config=settings.database_config())
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
            config=settings.database_config(),
            password_hasher=password_hasher,
            audit_service=audit_service,
        )
    return InMemoryIdentityService(
        password_hasher=password_hasher,
        audit_service=audit_service,
    )


def build_auth_session_store(settings: Settings) -> AuthSessionStore:
    if settings.auth_session_backend == "sql" or (
        settings.auth_session_backend == "auto"
        and settings.identity_service_backend == "sql"
    ):
        return SqlAuthSessionStore(config=settings.database_config())
    return InMemoryAuthSessionStore()


def build_storage_catalog(
    settings: Settings,
) -> InMemoryStorageCatalog | SqlStorageCatalog:
    if settings.storage_catalog_backend == "sql":
        return SqlStorageCatalog(
            config=settings.database_config(),
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
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlLifecyclePolicyStore(config=settings.database_config())
    return InMemoryLifecyclePolicyStore()


def build_restore_drill_schedule_store(
    settings: Settings,
) -> RestoreDrillScheduleStore:
    if settings.restore_drill_schedule_backend == "sql":
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlRestoreDrillScheduleStore(config=settings.database_config())
    return InMemoryRestoreDrillScheduleStore()


def build_tenant_offboarding_store(
    settings: Settings,
) -> InMemoryTenantOffboardingStore | SqlTenantOffboardingStore:
    if settings.lifecycle_policy_backend == "sql":
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlTenantOffboardingStore(config=settings.database_config())
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


def build_tenant_offboarding_deletion_service(
    app: FastAPI,
) -> TenantOffboardingDeletionService:
    return TenantOffboardingDeletionService(
        lifecycle_policy_store=app.state.lifecycle_policy_store,
        offboarding_store=app.state.tenant_offboarding_store,
        storage_catalog=app.state.storage_catalog,
        object_storage=app.state.object_storage,
        long_term_memory_service=app.state.long_term_memory_service,
        short_term_memory_service=app.state.short_term_memory_service,
        knowledge_service=app.state.knowledge_service,
    )


def build_knowledge_service(
    settings: Settings,
) -> InMemoryKnowledgeService | SqlKnowledgeService:
    if settings.knowledge_service_backend == "sql":
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlKnowledgeService(config=settings.database_config())
    return InMemoryKnowledgeService()


def build_skill_registry(
    settings: Settings,
) -> InMemorySkillRegistry | SqlSkillRegistry:
    if settings.skill_registry_backend == "sql":
        repository = SqlSkillRegistry(config=settings.database_config())
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return repository
    return InMemorySkillRegistry()


def build_solution_pack_registry(
    settings: Settings,
) -> InMemorySolutionPackRegistry | SqlSolutionPackRegistry:
    if settings.solution_pack_registry_backend == "sql":
        repository = SqlSolutionPackRegistry(config=settings.database_config())
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return repository
    return InMemorySolutionPackRegistry()


def build_customer_feedback_service(
    settings: Settings,
    audit_store,
    solution_pack_registry,
) -> InMemoryCustomerFeedbackService | SqlCustomerFeedbackService:
    if settings.customer_feedback_service_backend == "sql":
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlCustomerFeedbackService(
            config=settings.database_config(),
            audit_store=audit_store,
            solution_pack_registry=solution_pack_registry,
        )
    return InMemoryCustomerFeedbackService(
        audit_store=audit_store,
        solution_pack_registry=solution_pack_registry,
    )


def build_sso_provider_registry(
    settings: Settings,
) -> InMemorySsoProviderRegistry | SqlSsoProviderRegistry:
    if settings.sso_provider_registry_backend == "sql":
        repository = SqlSsoProviderRegistry(config=settings.database_config())
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return repository
    return InMemorySsoProviderRegistry()


def build_scim_provisioning_store(
    settings: Settings,
) -> InMemoryScimProvisioningStore | SqlScimProvisioningStore:
    if settings.scim_provisioning_store_backend == "sql":
        repository = SqlScimProvisioningStore(config=settings.database_config())
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return repository
    return InMemoryScimProvisioningStore()


def build_long_term_memory_service(
    settings: Settings,
) -> InMemoryLongTermMemoryService | SqlLongTermMemoryService:
    if settings.long_term_memory_backend == "sql":
        MigrationRunner(
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlLongTermMemoryService(config=settings.database_config())
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
                action=GuardrailAction(
                    settings.guardrail_prompt_threat_detector_action
                ),
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
    service: (
        InMemoryLongTermMemoryService
        | SqlLongTermMemoryService
        | GuardedLongTermMemoryService
    ),
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
    service: (
        InMemoryShortTermMemoryService
        | RedisShortTermMemoryService
        | GuardedShortTermMemoryService
    ),
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
            config=settings.database_config(),
            migrations_path=Path("apps/api/migrations"),
        ).apply()
        return SqlShortTermMemoryReviewStore(config=settings.database_config())
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


def knowledge_document_chunks(
    payload: KnowledgeDocumentApiCreate,
    content: bytes,
    settings: Settings,
) -> list[DocumentChunkCreate]:
    if payload.chunks:
        return payload.chunks
    return chunk_text_content(
        content.decode("utf-8"),
        source_document_id=payload.source_document_id,
        max_characters=settings.knowledge_chunk_max_characters,
        overlap_characters=settings.knowledge_chunk_overlap_characters,
    )


def embed_knowledge_document_chunks(
    app: FastAPI,
    request: Request,
    payload: KnowledgeDocumentApiCreate,
    context: RequestContext,
    chunks: list[DocumentChunkCreate],
    embedding_gateway: EmbeddingGateway | None,
    settings: Settings,
) -> list[DocumentChunkCreate]:
    if embedding_gateway is None or not chunks:
        return chunks
    response = embedding_gateway.embed(
        EmbeddingGatewayRequest(
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            purpose="knowledge_index",
            input=[chunk.content for chunk in chunks],
            model=settings.embedding_gateway_model,
            dimensions=settings.embedding_gateway_dimensions,
            metadata={
                "knowledge_base_id": payload.knowledge_base_id,
                "source_document_id": payload.source_document_id,
                "chunk_count": len(chunks),
            },
        )
    )
    record_embedding_gateway_usage(
        app=app,
        request=request,
        record=EmbeddingUsageRecord(
            tenant_id=context.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=context.user_id,
            purpose="knowledge_index",
            response=response,
            input_count=len(chunks),
            metadata={
                "knowledge_base_id": payload.knowledge_base_id,
                "source_document_id": payload.source_document_id,
                "chunk_count": len(chunks),
            },
        ),
    )
    embeddings_by_index = {
        embedding.index: embedding.embedding for embedding in response.embeddings
    }
    embedded_at = utc_now()
    embedding_model = response.model or settings.embedding_gateway_model
    return [
        chunk.model_copy(
            update={
                "embedding": embeddings_by_index.get(index, chunk.embedding),
                "embedding_model": embedding_model,
                "embedding_provider": "openai_compatible",
                "embedded_at": embedded_at,
            }
        )
        for index, chunk in enumerate(chunks)
    ]


def knowledge_retrieval_request(
    app: FastAPI,
    request: Request,
    payload: KnowledgeQueryRequest,
    context: RequestContext,
    embedding_gateway: EmbeddingGateway | None,
    settings: Settings,
) -> RetrievalRequest:
    request_data = payload.model_dump()
    if embedding_gateway is None:
        return RetrievalRequest(tenant_id=context.tenant_id, **request_data)

    response = embedding_gateway.embed(
        EmbeddingGatewayRequest(
            tenant_id=context.tenant_id,
            workspace_id=(
                payload.allowed_workspace_ids[0]
                if payload.allowed_workspace_ids
                else None
            ),
            user_id=context.user_id,
            purpose="knowledge_query",
            input=[payload.query],
            model=settings.embedding_gateway_model,
            dimensions=settings.embedding_gateway_dimensions,
            metadata={
                "allowed_workspace_count": len(payload.allowed_workspace_ids),
                "clearance_level": payload.clearance_level,
            },
        )
    )
    record_embedding_gateway_usage(
        app=app,
        request=request,
        record=EmbeddingUsageRecord(
            tenant_id=context.tenant_id,
            workspace_id=(
                payload.allowed_workspace_ids[0]
                if payload.allowed_workspace_ids
                else None
            ),
            user_id=context.user_id,
            purpose="knowledge_query",
            response=response,
            input_count=1,
            metadata={
                "allowed_workspace_count": len(payload.allowed_workspace_ids),
                "clearance_level": payload.clearance_level,
            },
        ),
    )
    if not response.embeddings:
        raise ValueError("embedding gateway returned no query embedding")
    return RetrievalRequest(
        tenant_id=context.tenant_id,
        query_embedding=response.embeddings[0].embedding,
        embedding_model=response.model or settings.embedding_gateway_model,
        **request_data,
    )


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


def connector_audit_metadata(connector: ConnectorDefinition) -> dict:
    credential_ref_id = None
    if connector.credential_ref is not None:
        credential_ref_id = connector.credential_ref.secret_ref_id
    return {
        "connector_id": connector.id,
        "workspace_id": connector.workspace_id,
        "connector_type": connector.type.value,
        "auth_mode": connector.auth_mode.value,
        "status": connector.status.value,
        "capability_count": len(connector.capabilities),
        "credential_ref_id": credential_ref_id,
        "sensitivity_level": connector.sensitivity_level,
    }


def connector_oauth_audit_metadata(
    connector: ConnectorDefinition, result: Any | None = None
) -> dict:
    metadata = connector_audit_metadata(connector)
    oauth_config = connector.metadata.get("oauth2")
    if isinstance(oauth_config, dict):
        scopes = oauth_config.get("scopes")
        metadata.update(
            {
                "callback_url": oauth_config.get("callback_url"),
                "scope_count": len(scopes) if isinstance(scopes, list) else 0,
                "access_token_secret_ref_id": oauth_config.get(
                    "access_token_secret_ref_id"
                ),
                "refresh_token_secret_ref_id": oauth_config.get(
                    "refresh_token_secret_ref_id"
                ),
            }
        )
    if result is None:
        return metadata
    expires_at = getattr(result, "expires_at", None)
    if expires_at is not None:
        metadata["expires_at"] = expires_at.isoformat()
    status_value = getattr(result, "status", None)
    if status_value is not None:
        metadata["oauth_status"] = status_value
    expires_in = getattr(result, "expires_in", None)
    if expires_in is not None:
        metadata["expires_in"] = expires_in
    token_type = getattr(result, "token_type", None)
    if token_type is not None:
        metadata["token_type"] = token_type
    return metadata


def connector_sync_audit_metadata(
    connector_id: str,
    knowledge_base_id: str,
    documents: list,
    cursor: str | None,
    job_id: str | None = None,
) -> dict:
    metadata = {
        "connector_id": connector_id,
        "knowledge_base_id": knowledge_base_id,
        "document_count": len(documents),
        "chunk_count": sum(len(document.chunks) for document in documents),
        "cursor": cursor,
    }
    if job_id is not None:
        metadata["job_id"] = job_id
    return metadata


def connector_invocation_audit_metadata(
    decision: ConnectorInvocationDecision,
    connector: ConnectorDefinition | None = None,
    dispatch_result: ConnectorDispatchResult | None = None,
    approval_id: str | None = None,
    error_code: str | None = None,
) -> dict:
    metadata = {
        "connector_id": decision.connector_id,
        "workspace_id": decision.workspace_id,
        "run_id": decision.run_id,
        "step_id": decision.step_id,
        "capability_name": decision.capability_name,
        "tool_name": decision.tool_name,
        "status": decision.status.value,
        "required_scopes": decision.required_scopes,
        "granted_scope_count": len(decision.granted_scopes),
        "missing_scopes": decision.missing_scopes,
        "risk_level": decision.risk_level,
        "approval_required": decision.approval_required,
        "approved": decision.approved,
        "input_keys": decision.input_keys,
        "billing_meter_type": decision.billing_meter_type,
    }
    if connector is not None and connector.credential_ref is not None:
        metadata["credential_ref_id"] = connector.credential_ref.secret_ref_id
        metadata["credential_actions"] = connector.credential_ref.required_actions
    if dispatch_result is not None:
        metadata.update(connector_dispatch_billing_metadata(dispatch_result))
    if approval_id is not None:
        metadata["approval_id"] = approval_id
    if error_code is not None:
        metadata["error_code"] = error_code
    return metadata


def action_manifests_for_runs(
    app: FastAPI,
    tenant_id: str,
    runs: Iterable,
) -> list[dict[str, Any]]:
    approvals = [
        approval
        for run in runs
        for approval in app.state.store.list_approval_requests(tenant_id, run.id)
        if approval.kind == "connector_action"
        or is_connector_approval_reason(approval.reason)
    ]
    approvals.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return [action_manifest_payload(app, approval) for approval in approvals]


def action_manifest_payload(
    app: FastAPI,
    approval: ApprovalRequest,
) -> dict[str, Any]:
    run = app.state.store.get_run(approval.tenant_id, approval.run_id)
    preview = approval.preview_payload
    execution_status = approval.execution_status
    if execution_status != "not_started":
        manifest_status = execution_status
    elif approval.status == ApprovalStatus.PENDING:
        manifest_status = "approval_required"
    elif approval.status == ApprovalStatus.APPROVED:
        manifest_status = "approved"
    elif approval.status == ApprovalStatus.REJECTED:
        manifest_status = "rejected"
    else:
        manifest_status = "superseded"
    approval_status = {
        ApprovalStatus.PENDING: "approval_required",
        ApprovalStatus.APPROVED: "approved",
        ApprovalStatus.REJECTED: "rejected",
        ApprovalStatus.CANCELLED: "superseded",
    }[approval.status]
    payload = {
        "manifestId": approval.id,
        "runId": approval.run_id,
        "provider": preview.get("provider") or approval.subject_id or "connector",
        "toolName": preview.get("toolName")
        or connector_tool_name_from_preview(approval),
        "status": manifest_status,
        "approvalStatus": approval_status,
        "preview": {
            key: preview[key]
            for key in (
                "connectorId",
                "capability",
                "riskLevel",
                "inputKeys",
                "input",
            )
            if key in preview
        },
        "validationResults": approval.validation_payload,
        "createdAt": approval.created_at.isoformat(),
        "resolvedAt": approval.resolved_at.isoformat()
        if approval.resolved_at
        else None,
        "error": approval.error,
    }
    if run.agent_id:
        payload["appId"] = run.agent_id
    if run.thread_id:
        payload["threadId"] = run.thread_id
    return payload


def connector_tool_name_from_preview(approval: ApprovalRequest) -> str:
    connector_id = approval.preview_payload.get("connectorId") or approval.subject_id
    capability = approval.preview_payload.get("capability")
    if connector_id and capability:
        return f"connector.{connector_id}.{capability}"
    return "connector.action"


def action_manifest_for_thread(
    app: FastAPI,
    tenant_id: str,
    thread_id: str,
    manifest_id: str,
) -> ApprovalRequest:
    thread = app.state.chat_service.get_thread(tenant_id, thread_id)
    for run in app.state.store.list_runs(tenant_id, thread.workspace_id):
        if run.thread_id != thread_id:
            continue
        approval = find_connector_approval(app, tenant_id, run.id, manifest_id)
        if approval is not None:
            return approval
    raise HTTPException(status_code=404, detail="Action manifest not found")


def action_manifest_for_agent(
    app: FastAPI,
    tenant_id: str,
    agent_id: str,
    manifest_id: str,
) -> ApprovalRequest:
    definition = app.state.agent_registry.get(tenant_id, agent_id)
    for run in app.state.store.list_runs(tenant_id, definition.workspace_id):
        if run.agent_id != agent_id:
            continue
        approval = find_connector_approval(app, tenant_id, run.id, manifest_id)
        if approval is not None:
            return approval
    raise HTTPException(status_code=404, detail="Action manifest not found")


def approve_action_manifest(
    app: FastAPI,
    approval: ApprovalRequest,
    user_id: str,
) -> ApprovalRequest:
    if approval.status == ApprovalStatus.APPROVED:
        return approval
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Action manifest is not pending")
    return app.state.store.resolve_approval_request(
        approval.tenant_id,
        approval.run_id,
        approval.id,
        user_id,
    )


def reject_action_manifest(
    app: FastAPI,
    approval: ApprovalRequest,
    user_id: str,
) -> ApprovalRequest:
    if approval.status == ApprovalStatus.REJECTED:
        return approval
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Action manifest is not pending")
    run = app.state.store.get_run(approval.tenant_id, approval.run_id)
    if run.status == RunStatus.AWAITING_APPROVAL:
        state = app.state.runtime._load_state(approval.tenant_id, approval.run_id)
        if state.approval_id == approval.id:
            app.state.runtime.reject_approval(
                tenant_id=approval.tenant_id,
                run_id=approval.run_id,
                approval_id=approval.id,
                rejected_by_user_id=user_id,
            )
            rejected = find_connector_approval(
                app,
                approval.tenant_id,
                approval.run_id,
                approval.id,
            )
            if rejected is None:
                raise HTTPException(status_code=404, detail="Action manifest not found")
            return rejected
    return app.state.store.reject_approval_request(
        approval.tenant_id,
        approval.run_id,
        approval.id,
        user_id,
    )


def emit_action_manifest_event(app: FastAPI, approval: ApprovalRequest) -> None:
    run = app.state.store.get_run(approval.tenant_id, approval.run_id)
    app.state.store.append_run_event(
        run,
        "action_approval",
        action_manifest_payload(app, approval),
    )


def apply_action_manifest(
    app: FastAPI,
    approval: ApprovalRequest,
    request: Request,
    context: RequestContext,
) -> dict[str, Any]:
    if approval.execution_status == "applied":
        return action_manifest_payload(app, approval)
    if approval.status != ApprovalStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Approve the action before applying it")
    if approval.validation_payload.get("valid") is False:
        blocked = app.state.store.update_approval_execution(
            approval.tenant_id,
            approval.run_id,
            approval.id,
            "blocked_by_validation",
            "action manifest validation failed",
        )
        emit_action_manifest_event(app, blocked)
        raise HTTPException(status_code=409, detail="Action manifest validation failed")

    run = app.state.store.get_run(approval.tenant_id, approval.run_id)
    if run.status == RunStatus.AWAITING_APPROVAL:
        state = app.state.runtime._load_state(approval.tenant_id, approval.run_id)
        if state.approval_id == approval.id:
            app.state.runtime.resume_after_approval(
                tenant_id=approval.tenant_id,
                run_id=approval.run_id,
                approval_id=approval.id,
                approved_by_user_id=context.user_id,
            )
            applied = find_connector_approval(
                app,
                approval.tenant_id,
                approval.run_id,
                approval.id,
            )
            if applied is None:
                raise HTTPException(status_code=404, detail="Action manifest not found")
            emit_action_manifest_event(app, applied)
            return action_manifest_payload(app, applied)

    preview = approval.preview_payload
    connector_id = preview.get("connectorId") or approval.subject_id
    capability_name = preview.get("capability")
    tool_input = preview.get("input")
    granted_scopes = preview.get("grantedScopes", [])
    if (
        not isinstance(connector_id, str)
        or not isinstance(capability_name, str)
        or not isinstance(tool_input, dict)
        or not isinstance(granted_scopes, list)
    ):
        raise HTTPException(status_code=409, detail="Action manifest is incomplete")

    connector = app.state.connector_registry.get_connector(
        approval.tenant_id,
        connector_id,
    )
    invocation = ConnectorInvocationCreate(
        run_id=approval.run_id,
        step_id=approval.step_id,
        capability_name=capability_name,
        tool_input=tool_input,
        granted_scopes=granted_scopes,
        approved=True,
        approval_id=approval.id,
    )
    decision = app.state.connector_invocation_service.evaluate(
        connector,
        invocation.to_invocation_request(
            tenant_id=approval.tenant_id,
            workspace_id=approval.workspace_id,
            user_id=context.user_id,
            connector_id=connector.id,
        ),
    )
    if decision.status != ConnectorInvocationStatus.READY:
        blocked = app.state.store.update_approval_execution(
            approval.tenant_id,
            approval.run_id,
            approval.id,
            "blocked_by_validation",
            decision.reason or "connector action is no longer valid",
        )
        emit_action_manifest_event(app, blocked)
        raise HTTPException(
            status_code=409,
            detail=decision.reason or "Connector action is no longer valid",
        )
    result = execute_connector_invocation(
        app=app,
        context=context,
        request=request,
        connector=connector,
        decision=decision,
        tool_input=tool_input,
        approval=approval,
    )
    applied = find_connector_approval(
        app,
        approval.tenant_id,
        approval.run_id,
        approval.id,
    )
    if applied is None:
        raise HTTPException(status_code=404, detail="Action manifest not found")
    payload = action_manifest_payload(app, applied)
    if result is not None:
        payload["result"] = result.output
    return payload


def execute_connector_invocation(
    *,
    app: FastAPI,
    context: RequestContext,
    request: Request,
    connector: ConnectorDefinition,
    decision: ConnectorInvocationDecision,
    tool_input: dict[str, Any],
    approval: ApprovalRequest | None = None,
) -> ConnectorDispatchResult | None:
    if approval is not None and approval.execution_status == "applied":
        return None
    approval_id = approval.id if approval is not None else None
    if approval is not None:
        applying = app.state.store.update_approval_execution(
            context.tenant_id,
            decision.run_id,
            approval.id,
            "applying",
        )
        emit_action_manifest_event(app, applying)
    try:
        dispatch_result = app.state.connector_dispatcher.dispatch(
            connector=connector,
            tool_input=tool_input,
            tool_name=decision.tool_name,
        )
    except ConnectorDispatchError as error:
        if approval is not None:
            failed = app.state.store.update_approval_execution(
                context.tenant_id,
                decision.run_id,
                approval.id,
                "apply_failed",
                str(error),
            )
            emit_action_manifest_event(app, failed)
        record_audit_event(
            app=app,
            tenant_id=context.tenant_id,
            workspace_id=connector.workspace_id,
            user_id=context.user_id,
            run_id=decision.run_id,
            event_type="connector.dispatch_failed",
            metadata=connector_invocation_audit_metadata(
                decision,
                connector=connector,
                error_code="connector_dispatch_failed",
                approval_id=approval_id,
            ),
            request=request,
        )
        raise

    if approval is not None:
        applied = app.state.store.update_approval_execution(
            context.tenant_id,
            decision.run_id,
            approval.id,
            "applied",
        )
        emit_action_manifest_event(app, applied)
    if decision.billing_meter_type is not None:
        app.state.store.record_billing_meter(
            tenant_id=context.tenant_id,
            run_id=decision.run_id,
            meter_type=decision.billing_meter_type,
            quantity=1,
            unit="invocation",
            metadata={
                "connector_id": connector.id,
                "capability_name": decision.capability_name,
                "tool_name": decision.tool_name,
                "risk_level": decision.risk_level,
            }
            | connector_dispatch_billing_metadata(dispatch_result),
        )
    record_audit_event(
        app=app,
        tenant_id=context.tenant_id,
        workspace_id=connector.workspace_id,
        user_id=context.user_id,
        run_id=decision.run_id,
        event_type="connector.invoked",
        metadata=connector_invocation_audit_metadata(
            decision,
            connector=connector,
            dispatch_result=dispatch_result,
            approval_id=approval_id,
        ),
        request=request,
    )
    return dispatch_result


def get_or_create_connector_approval(
    app: FastAPI,
    tenant_id: str,
    run_id: str,
    provider: str,
    connector_id: str,
    capability_name: str,
    tool_name: str,
    step_id: str,
    risk_level: str | None = None,
    input_keys: list[str] | None = None,
    missing_scopes: list[str] | None = None,
    tool_input: dict[str, Any] | None = None,
    granted_scopes: list[str] | None = None,
) -> tuple[ApprovalRequest, bool]:
    reason = connector_approval_reason(connector_id, capability_name)
    for approval in app.state.store.list_approval_requests(tenant_id, run_id):
        if (
            approval.status == ApprovalStatus.PENDING
            and approval.step_id == step_id
            and approval.reason == reason
            and approval.preview_payload.get("input") == (tool_input or {})
        ):
            return approval, False
    approval = app.state.store.create_approval_request(
        tenant_id=tenant_id,
        run_id=run_id,
        step_id=step_id,
        reason=reason,
        kind="connector_action",
        subject_type="connector",
        subject_id=connector_id,
        preview_payload={
            "provider": provider,
            "connectorId": connector_id,
            "capability": capability_name,
            "toolName": tool_name,
            "riskLevel": risk_level,
            "inputKeys": input_keys or [],
            "input": tool_input or {},
            "grantedScopes": granted_scopes or [],
        },
        validation_payload={
            "valid": not missing_scopes,
            "missingScopes": missing_scopes or [],
        },
    )
    return approval, True


def find_connector_approval(
    app: FastAPI,
    tenant_id: str,
    run_id: str,
    approval_id: str,
) -> ApprovalRequest | None:
    for approval in app.state.store.list_approval_requests(tenant_id, run_id):
        if approval.id == approval_id and (
            approval.kind == "connector_action"
            or is_connector_approval_reason(approval.reason)
        ):
            return approval
    return None


def require_approved_connector_approval(
    app: FastAPI,
    tenant_id: str,
    run_id: str,
    approval_id: str | None,
    connector_id: str,
    capability_name: str,
    step_id: str,
) -> ApprovalRequest:
    if approval_id is None:
        raise TenantAccessError("approved connector invocation requires approval_id")
    expected_reason = connector_approval_reason(connector_id, capability_name)
    for approval in app.state.store.list_approval_requests(tenant_id, run_id):
        if approval.id != approval_id:
            continue
        if approval.step_id != step_id or approval.reason != expected_reason:
            raise TenantAccessError("connector approval does not match invocation")
        if approval.status != ApprovalStatus.APPROVED:
            raise TenantAccessError("connector approval is not approved")
        return approval
    raise TenantAccessError("connector approval is required")


def connector_approval_reason(connector_id: str, capability_name: str) -> str:
    return f"connector approval required: {connector_id}:{capability_name}"


def is_connector_approval_reason(reason: str) -> bool:
    return reason.startswith("connector approval required: ")


def connector_dispatch_billing_metadata(
    dispatch_result: ConnectorDispatchResult | None,
) -> dict:
    if dispatch_result is None:
        return {}
    return {
        "dispatch_status_code": dispatch_result.status_code,
        "response_size_bytes": dispatch_result.response_size_bytes,
    }


def storage_audit_metadata(storage_object) -> dict:
    return storage_object_audit_metadata(storage_object)


def normalize_workspace_file_path(value: str, *, allow_empty: bool = False) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    if not normalized:
        if allow_empty:
            return ""
        raise ValueError("Workspace file path is required")
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("Workspace file path contains an invalid segment")
    if len(normalized) > 512:
        raise ValueError("Workspace file path is too long")
    return str(PurePosixPath(*raw_parts))


def storage_object_agent_reference_count(
    registry,
    tenant_id: str,
    storage_object_id: str,
) -> int:
    if registry is None:
        return 0
    reference_count = 0
    for definition in registry.list(tenant_id):
        for version in registry.list_versions(tenant_id, definition.id):
            reference_count += sum(
                1
                for reference in version.spec.reference_files
                if reference.get("storage_object_id") == storage_object_id
            )
            reference_count += sum(
                1
                for reference in version.spec.runtime_snapshot.get("files", [])
                if reference.get("storage_object_id") == storage_object_id
            )
    return reference_count


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
        "checked_resource_types": [
            check.resource_type.value for check in report.checks
        ],
        "disallowed_resource_types": [
            check.resource_type.value for check in disallowed_checks
        ],
        "checked_regions": sorted({check.region for check in report.checks}),
    }


def restore_drill_schedule_audit_metadata(schedule) -> dict:
    return {
        "schedule_id": schedule.id,
        "workspace_id": schedule.workspace_id,
        "status": schedule.status.value,
        "interval_days": schedule.interval_days,
        "max_catch_up_runs": schedule.max_catch_up_runs,
        "next_run_at": (
            schedule.next_run_at.isoformat()
            if schedule.next_run_at is not None
            else None
        ),
        "has_service_account": schedule.service_account_id is not None,
        "created_by_user_id": schedule.created_by_user_id,
    }


def restore_drill_run_record_audit_metadata(record) -> dict:
    return {
        "run_record_id": record.id,
        "workspace_id": record.workspace_id,
        "schedule_id": record.schedule_id,
        "scheduled_for": record.scheduled_for.isoformat(),
        "requested_by_user_id": record.requested_by_user_id,
        "status": record.status.value,
        "has_evidence_object": record.evidence_object_id is not None,
    }


def require_restore_drill_run_record_update_allowed(record) -> None:
    if record.status == RestoreDrillRunStatus.REQUESTED:
        return
    raise ValueError("restore drill run record is already terminal")


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
        "approved_at": (
            plan.approved_at.isoformat() if plan.approved_at is not None else None
        ),
        "export_bundle_id": plan.export_bundle_id,
        "export_storage_object_id": plan.export_storage_object_id,
        "export_completed_by_user_id": plan.export_completed_by_user_id,
        "export_completed_at": (
            plan.export_completed_at.isoformat()
            if plan.export_completed_at is not None
            else None
        ),
        "deleted_by_user_id": plan.deleted_by_user_id,
        "deleted_at": (
            plan.deleted_at.isoformat() if plan.deleted_at is not None else None
        ),
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
        "expires_at": (
            hold.expires_at.isoformat() if hold.expires_at is not None else None
        ),
        "released_at": (
            hold.released_at.isoformat() if hold.released_at is not None else None
        ),
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


def sandbox_snapshot_audit_metadata(snapshot) -> dict:
    return {
        "snapshot_id": snapshot.id,
        "session_id": snapshot.session_id,
        "workspace_id": snapshot.workspace_id,
        "run_id": snapshot.run_id,
        "uri": snapshot.uri,
    }


def agent_engine_connection_payload(connection) -> dict[str, Any]:
    payload = connection.model_dump(mode="json", exclude={"secret_ref_id"})
    payload["secret_ref_present"] = connection.secret_ref_id is not None
    return payload


def browser_profile_public_payload(profile) -> dict[str, Any]:
    payload = profile.model_dump(
        mode="json",
        exclude={"secret_ref_id", "secret_backend", "secret_external_name"},
    )
    payload["has_saved_state"] = profile.secret_ref_id is not None
    return payload


def browser_profile_audit_metadata(profile) -> dict[str, Any]:
    return {
        "profile_id": profile.id,
        "workspace_id": profile.workspace_id,
        "status": profile.status,
        "is_default": profile.is_default,
        "allowed_domain_count": len(profile.allowed_domains),
        "has_saved_state": profile.secret_ref_id is not None,
        "revision": profile.revision,
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

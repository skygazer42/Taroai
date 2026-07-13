from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest

from pydantic import Field

import taroai.agent.runtime as runtime_module
from taroai.domain import ApprovalStatus, RunCreate, RunStatus, utc_now
from taroai.agent import AgentRuntime, AgentRuntimeState, PlanStep
from taroai.agent.models import AgentDecision, AgentVerificationResult
from taroai.billing import BillingPricingRule, BillingPricingService
from taroai.guardrails import (
    GuardrailAction,
    GuardrailCondition,
    GuardrailRule,
    GuardrailSeverity,
    GuardrailStage,
    InMemoryGuardrailService,
)
from taroai.identity import (
    InMemoryIdentityService,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.licensing import (
    Entitlement,
    LicenseKey,
    LicenseService,
    LicensedFeature,
)
from taroai.model_gateway import (
    ModelBudgetGuard,
    ModelBudgetPolicy,
    ModelPolicy,
    ModelPolicyDeniedError,
    ModelPolicyScope,
    ModelUsage,
    PlannedToolCall,
)
from taroai.model_gateway import ModelGateway, ModelGatewayRequest, ModelGatewayResponse
from taroai.policy import IdentityPolicyService
from taroai.sandbox import (
    BrowserProviderUnavailableError,
    LocalProcessSandboxAdapter,
    SandboxExecutionError,
    register_browser_tool_handlers,
    register_sandbox_tool_handlers,
)
from taroai.sandbox.models import SandboxSessionStatus
from taroai.storage import (
    InMemoryStorageCatalog,
    ObjectStorageAdapter,
    StorageDeleteResult,
    StorageDownloadResult,
    StorageObject,
    StorageContentScanner,
    StorageSignedUrl,
    StoragePurpose,
    StorageUploadResult,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import (
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)
from tests.api.adapters import DeterministicModelGateway, DeterministicToolGateway
from tests.api.sandbox_adapters import InMemoryBrowserController


class RecordingPlanGateway(ModelGateway):
    output_text: str = ""
    plan: list[PlannedToolCall] = Field(default_factory=list)
    requests: list[ModelGatewayRequest] = Field(default_factory=list)

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        self.requests.append(request)
        return ModelGatewayResponse(
            id=f"response_{request.run_id}",
            model="recording-test",
            output_text=self.output_text,
            planned_steps=self.plan,
        )


class RecordingGraphGateway(ModelGateway):
    decisions: list[AgentDecision] = Field(default_factory=list)
    verifications: list[AgentVerificationResult] = Field(default_factory=list)

    def decide_next_action(self, request: ModelGatewayRequest) -> AgentDecision:
        del request
        return self.decisions.pop(0)

    def verify_completion(
        self,
        request: ModelGatewayRequest,
    ) -> AgentVerificationResult:
        del request
        return self.verifications.pop(0)

    def stream_response(self, request: ModelGatewayRequest):
        del request
        yield "任务已完成。"


class RecordingObjectStorage(ObjectStorageAdapter):
    objects: dict[str, bytes] = Field(default_factory=dict)

    def upload(
        self, storage_object: StorageObject, content: bytes
    ) -> StorageUploadResult:
        self.objects[storage_object.uri] = content
        return StorageUploadResult(
            storage_object_id=storage_object.id, uri=storage_object.uri
        )

    def download(self, storage_object: StorageObject) -> StorageDownloadResult:
        return StorageDownloadResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            content=self.objects[storage_object.uri],
            content_type=storage_object.content_type,
        )

    def delete(self, storage_object: StorageObject) -> StorageDeleteResult:
        self.objects.pop(storage_object.uri, None)
        return StorageDeleteResult(
            storage_object_id=storage_object.id, uri=storage_object.uri
        )

    def create_signed_url(
        self,
        storage_object: StorageObject,
        operation: Literal["read", "write"],
        expires_in_seconds: int,
        now=None,
    ) -> StorageSignedUrl:
        return StorageSignedUrl(
            storage_object_id=storage_object.id,
            tenant_id=storage_object.tenant_id,
            url=f"https://storage.local/{storage_object.key}",
            method="GET" if operation == "read" else "PUT",
            expires_at=(now or utc_now()) + timedelta(seconds=expires_in_seconds),
        )


class DestroyFailingSandboxAdapter(LocalProcessSandboxAdapter):
    def destroy(self, tenant_id: str, session_id: str):
        raise SandboxExecutionError("sandbox destroy provider failed")


class DeleteFailingBrowserController(InMemoryBrowserController):
    def delete_session(self, tenant_id: str, session_id: str):
        raise BrowserProviderUnavailableError("browser delete provider failed")


def create_runtime_run(message: str = "Create a prospect brief."):
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message=message,
            mode="autonomous",
        ),
    )
    return store, run


def create_sandbox_artifact_content_guardrail_runtime(
    tmp_path: Path,
    action: GuardrailAction = GuardrailAction.REQUIRE_APPROVAL,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report that requires artifact approval.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["review-required-output"]),
            action=action,
            severity=GuardrailSeverity.HIGH,
            message="Artifact file content requires approval",
        )
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('artifacts/report.md').write_text("
                            "''.join(chr(c) for c in "
                            "[114,101,118,105,101,119,45,114,101,113,117,105,114,101,100,45,111,117,116,112,117,116])"
                            " + '\\n')\""
                        ),
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        guardrail_service=guardrail_service,
    )
    return {
        "runtime": runtime,
        "store": store,
        "run": run,
        "sandbox_adapter": sandbox_adapter,
        "tool_gateway": tool_gateway,
        "storage_catalog": storage_catalog,
        "object_storage": object_storage,
        "guardrail_service": guardrail_service,
        "rule": rule,
        "identity": identity,
    }


def test_agent_runtime_planning_prompt_guides_real_models_to_publish_artifacts():
    store, run = create_runtime_run("Create a hello report.")
    model_gateway = RecordingPlanGateway(
        plan=[
            PlannedToolCall(
                id="step_plan",
                title="Plan artifact creation",
                tool_name="planning.record",
                tool_input={"status": "ok"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        tool_gateway=DeterministicToolGateway(),
    )

    runtime.execute_run("tenant_acme", run.id)

    system_prompt = model_gateway.requests[0].messages[0].content
    assert "sandbox.command" in system_prompt
    assert "browser.action" in system_prompt
    assert "/workspace/artifacts/" in system_prompt
    assert "mkdir -p /workspace/artifacts" in system_prompt
    assert "artifact_path" in system_prompt
    assert "rejected for artifact publication" in system_prompt
    assert "strict JSON" in system_prompt


def test_agent_runtime_completes_run_and_creates_artifact():
    store, run = create_runtime_run()
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

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert [
        artifact.name for artifact in store.list_artifacts("tenant_acme", run.id)
    ] == ["agent-result.md"]

    events = store.list_run_events("tenant_acme", run.id)
    event_types = [event.type for event in events]
    assert event_types[-17:] == [
        "run.status_changed",
        "context.loaded",
        "billing.metered",
        "audit.recorded",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
        "plan.created",
        "policy.checked",
        "step.started",
        "tool_call.started",
        "tool_call.completed",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
        "artifact.created",
        "run.succeeded",
    ]
    assert [
        event.payload["type"] for event in events if event.type == "billing.metered"
    ] == [
        "run_count",
        "model_call_count",
        "model_latency_ms",
        "tool_call_count",
    ]


def test_agent_runtime_creates_sandbox_session_and_promotes_generated_file_artifact(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a hello report.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "print(''.join(chr(c) for c in "
                            "[114,117,110,116,105,109,101,45,111,117,116,112,117,116,45,116,111,107,101,110])); "
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('artifacts/report.md').write_text('# Hello Report\\nGenerated in sandbox.\\n')\""
                        ),
                        "artifact_paths": ["/workspace/artifacts/report.md"],
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert len(sandbox_adapter.sessions) == 1
    session = next(iter(sandbox_adapter.sessions.values()))
    assert state.sandbox_session_id == session.id
    assert session.status == SandboxSessionStatus.DESTROYED
    assert state.tool_results[0].output["session_id"] == session.id
    assert state.plan[0].tool_input["session_id"] == session.id
    artifacts = store.list_artifacts("tenant_acme", run.id)
    assert [artifact.name for artifact in artifacts] == ["report.md"]
    storage_objects = storage_catalog.list_for_run("tenant_acme", run.id)
    assert [storage_object.filename for storage_object in storage_objects] == [
        "report.md"
    ]
    assert (
        object_storage.objects[artifacts[0].uri]
        == b"# Hello Report\nGenerated in sandbox.\n"
    )
    run_events = store.list_run_events("tenant_acme", run.id)
    assert [event.type for event in run_events].count("sandbox.command.executed") == 1
    assert "runtime-output-token" not in str(
        [event.model_dump(mode="json") for event in run_events]
    )
    assert "artifact.created" in [
        event.type for event in run_events
    ]


def test_agent_runtime_reports_sandbox_destroy_failure_without_blocking_success(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report while cleanup is degraded.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = DestroyFailingSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            "mkdir -p /workspace/artifacts && "
                            "printf '# Cleanup Report\\n' > /workspace/artifacts/report.md"
                        ),
                        "artifact_paths": ["/workspace/artifacts/report.md"],
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    artifacts = store.list_artifacts("tenant_acme", run.id)
    assert [artifact.name for artifact in artifacts] == ["report.md"]
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "sandbox.session.destroy_failed" in event_types
    assert "run.succeeded" in event_types
    destroy_failed = [
        event
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "sandbox.session.destroy_failed"
    ][0]
    assert destroy_failed.payload["reason"] == "success"
    assert destroy_failed.payload["error_type"] == "SandboxExecutionError"


def test_agent_runtime_enforces_sandbox_concurrency_license_before_auto_session(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    sandbox_adapter.create(
        runtime_module.SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_existing",
            image="python:3.12-slim",
        )
    )
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_private",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=utc_now() - timedelta(days=1),
            expires_at=utc_now() + timedelta(days=365),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.SANDBOX_CONCURRENCY, limit=1),
            ],
        ),
        deployment_mode="private",
    )
    license_service.activate_validation(validation)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={"command": "python --version"},
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        license_service=license_service,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert state.failure_reason is not None
    assert "sandbox_concurrency" in state.failure_reason
    assert len(sandbox_adapter.list_sessions("tenant_acme")) == 1


def test_agent_runtime_fails_sandbox_artifact_step_when_command_exits_nonzero(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report with a failing command.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": "sh -c 'exit 2'",
                        "artifact_paths": ["/workspace/artifacts/report.md"],
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert state.failure_reason == "sandbox.command failed with exit code 2"
    assert object_storage.objects == {}
    assert store.list_artifacts("tenant_acme", run.id) == []
    session = next(iter(sandbox_adapter.sessions.values()))
    assert session.status == SandboxSessionStatus.DESTROYED
    events = store.list_run_events("tenant_acme", run.id)
    event_types = [event.type for event in events]
    assert event_types.count("sandbox.command.executed") == 1
    assert event_types.count("sandbox.artifact.promoted") == 0
    failed_events = [event for event in events if event.type == "run.failed"]
    assert failed_events[-1].payload == {
        "reason": "sandbox_command_failed",
        "step_id": "step_report",
        "exit_code": 2,
    }


def test_agent_runtime_rejects_sandbox_artifact_paths_outside_artifacts_dir(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report at an unsafe path.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('report.md').write_text('# Unsafe Report\\n')\""
                        ),
                        "artifact_paths": ["/workspace/report.md"],
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert state.failure_reason == (
        "sandbox artifact path must be under /workspace/artifacts/"
    )
    assert object_storage.objects == {}
    assert store.list_artifacts("tenant_acme", run.id) == []
    session = next(iter(sandbox_adapter.sessions.values()))
    assert session.status == SandboxSessionStatus.DESTROYED
    events = store.list_run_events("tenant_acme", run.id)
    assert [event.type for event in events].count("sandbox.artifact.rejected") == 1
    failed_events = [event for event in events if event.type == "run.failed"]
    assert failed_events[-1].payload == {
        "reason": "sandbox_artifact_path_rejected",
        "step_id": "step_report",
    }


def test_agent_runtime_discovers_and_promotes_generated_sandbox_files(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report without declaring artifact paths.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('work').mkdir(exist_ok=True); "
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('work/input-copy.txt').write_text('internal input\\n'); "
                            "Path('artifacts/report.md').write_text('# Auto Report\\n')\""
                        ),
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert state.promoted_sandbox_artifact_paths == ["/workspace/artifacts/report.md"]
    artifacts = store.list_artifacts("tenant_acme", run.id)
    assert [artifact.name for artifact in artifacts] == ["report.md"]
    assert object_storage.objects[artifacts[0].uri] == b"# Auto Report\n"
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert event_types.count("sandbox.artifacts.discovered") == 1
    assert event_types.count("sandbox.artifact.promoted") == 1
    assert event_types.count("sandbox.session.destroyed") == 1


def test_agent_runtime_scans_sandbox_artifact_content_before_upload(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report that must be scanned.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('artifacts/report.md').write_text('customer-secret\\n')\""
                        ),
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        storage_content_scanner=StorageContentScanner(
            blocked_terms=["customer-secret"]
        ),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert state.failure_reason == "storage content rejected by scan policy"
    assert object_storage.objects == {}
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert [
        event.type for event in store.list_run_events("tenant_acme", run.id)
    ].count("storage.content_rejected") == 1
    failed_events = [
        event
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "run.failed"
    ]
    assert failed_events[-1].payload == {
        "reason": "storage_content_rejected",
        "step_id": "step_report",
    }


def test_agent_runtime_applies_artifact_guardrails_to_sandbox_file_content(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report that must pass artifact policy.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["confidential-output"]),
            action=GuardrailAction.BLOCK,
            severity=GuardrailSeverity.HIGH,
            message="Artifact file content is blocked by policy",
        )
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('artifacts/report.md').write_text("
                            "''.join(chr(c) for c in "
                            "[99,111,110,102,105,100,101,110,116,105,97,108,45,111,117,116,112,117,116])"
                            " + '\\n')\""
                        ),
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        guardrail_service=guardrail_service,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert object_storage.objects == {}
    assert store.list_artifacts("tenant_acme", run.id) == []
    session = next(iter(sandbox_adapter.sessions.values()))
    assert session.status == SandboxSessionStatus.DESTROYED
    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.artifact_blocked"
    ]
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [
        [rule.id]
    ]
    events = store.list_run_events("tenant_acme", run.id)
    failed_events = [event for event in events if event.type == "run.failed"]
    assert failed_events[-1].payload["reason"] == "artifact_guardrail_blocked"
    assert "confidential-output" not in str(
        [event.model_dump(mode="json") for event in events]
    )
    assert "confidential-output" not in str(
        [event.model_dump(mode="json") for event in guardrail_audits]
    )


def test_agent_runtime_resumes_sandbox_artifact_content_guardrail_approval(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Generate a report that requires artifact approval.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["review-required-output"]),
            action=GuardrailAction.REQUIRE_APPROVAL,
            severity=GuardrailSeverity.HIGH,
            message="Artifact file content requires approval",
        )
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('artifacts/report.md').write_text("
                            "''.join(chr(c) for c in "
                            "[114,101,118,105,101,119,45,114,101,113,117,105,114,101,100,45,111,117,116,112,117,116])"
                            " + '\\n')\""
                        ),
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        guardrail_service=guardrail_service,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    session = next(iter(sandbox_adapter.sessions.values()))
    snapshot = store.get_runtime_state("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert approval.reason == "Artifact file content requires approval"
    assert approval.step_id == "guardrail:artifact"
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert object_storage.objects == {}
    assert storage_catalog.list_for_run("tenant_acme", run.id) == []
    assert session.status != SandboxSessionStatus.DESTROYED
    assert snapshot.current_step_id == "step_report"
    assert snapshot.pending_guardrail_approval_stage == "artifact"

    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        guardrail_service=guardrail_service,
    )
    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    artifacts = store.list_artifacts("tenant_acme", run.id)
    assert resumed_state.status == RunStatus.SUCCEEDED
    assert [artifact.name for artifact in artifacts] == ["report.md"]
    assert object_storage.objects[artifacts[0].uri] == b"review-required-output\n"
    assert resumed_state.promoted_sandbox_artifact_paths == [
        "/workspace/artifacts/report.md"
    ]
    assert resumed_state.completed_step_ids == ["step_report"]
    assert resumed_state.approved_guardrail_keys == [f"artifact:{rule.id}"]
    assert (
        sandbox_adapter.get_session("tenant_acme", session.id).status
        == SandboxSessionStatus.DESTROYED
    )
    assert "review-required-output" not in str(
        [
            event.model_dump(mode="json")
            for event in store.list_run_events("tenant_acme", run.id)
        ]
    )


def test_agent_runtime_rejecting_sandbox_artifact_guardrail_approval_destroys_session(
    tmp_path: Path,
):
    context = create_sandbox_artifact_content_guardrail_runtime(tmp_path)
    runtime = context["runtime"]
    store = context["store"]
    run = context["run"]
    sandbox_adapter = context["sandbox_adapter"]
    object_storage = context["object_storage"]
    storage_catalog = context["storage_catalog"]

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    session = next(iter(sandbox_adapter.sessions.values()))

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert session.status != SandboxSessionStatus.DESTROYED

    rejected_state = runtime.reject_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        rejected_by_user_id="manager_1",
    )

    assert rejected_state.status == RunStatus.FAILED
    assert rejected_state.approval_id is None
    assert rejected_state.pending_guardrail_approval_key is None
    assert rejected_state.pending_guardrail_approval_stage is None
    assert object_storage.objects == {}
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert storage_catalog.list_for_run("tenant_acme", run.id) == []
    assert (
        sandbox_adapter.get_session("tenant_acme", session.id).status
        == SandboxSessionStatus.DESTROYED
    )
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.REJECTED
    )
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert event_types.count("sandbox.session.destroyed") == 1


def test_agent_runtime_cancelling_sandbox_artifact_guardrail_approval_destroys_session(
    tmp_path: Path,
):
    context = create_sandbox_artifact_content_guardrail_runtime(tmp_path)
    runtime = context["runtime"]
    store = context["store"]
    run = context["run"]
    sandbox_adapter = context["sandbox_adapter"]
    object_storage = context["object_storage"]
    storage_catalog = context["storage_catalog"]

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    session = next(iter(sandbox_adapter.sessions.values()))

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert session.status != SandboxSessionStatus.DESTROYED

    cancelled_run = runtime.cancel_run(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
        reason_code="user_requested",
    )
    snapshot = store.get_runtime_state("tenant_acme", run.id)

    assert paused_state.status == RunStatus.CANCELLED
    assert cancelled_run.status == RunStatus.CANCELLED
    assert snapshot.status == RunStatus.CANCELLED
    assert snapshot.approval_id is None
    assert object_storage.objects == {}
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert storage_catalog.list_for_run("tenant_acme", run.id) == []
    assert (
        sandbox_adapter.get_session("tenant_acme", session.id).status
        == SandboxSessionStatus.DESTROYED
    )
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.CANCELLED
    )
    assert store.list_approval_requests("tenant_acme", run.id)[0].id == approval.id
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert event_types.count("sandbox.session.destroyed") == 1


def test_agent_runtime_destroys_sandbox_session_after_tool_gateway_failure(
    tmp_path: Path,
):
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_report",
            message="Attempt a sandbox command without scope.",
            mode="autonomous",
        ),
    )
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    tool_gateway = ToolGateway()
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={"command": "printf denied > report.md"},
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        sandbox_adapter=sandbox_adapter,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert len(sandbox_adapter.sessions) == 1
    session = next(iter(sandbox_adapter.sessions.values()))
    assert state.sandbox_session_id == session.id
    assert session.status == SandboxSessionStatus.DESTROYED
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "tool_call.failed" in event_types
    assert event_types.count("sandbox.session.destroyed") == 1


def test_agent_runtime_creates_browser_session_and_injects_session_id():
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="browser-runner@example.com",
            display_name="Browser Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_browser_runner",
            name="Browser Runner",
            permissions=[
                Permission(action="browser.act", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_browser_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_browser",
            message="Open the vendor page and capture a screenshot.",
            mode="autonomous",
        ),
    )
    browser = InMemoryBrowserController()
    tool_gateway = ToolGateway()
    register_browser_tool_handlers(tool_gateway, browser)
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_browser",
                    title="Capture browser page",
                    tool_name="browser.action",
                    tool_input={
                        "action_type": "screenshot",
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        browser_controller=browser,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert state.browser_session_id is not None
    assert state.plan[0].tool_input["session_id"] == state.browser_session_id
    assert state.tool_results[0].output["session_id"] == state.browser_session_id
    assert browser.deleted_sessions == [state.browser_session_id]
    assert list(browser.sessions) == []
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert event_types.count("browser.session.created") == 1
    assert event_types.count("browser.action.performed") == 1
    assert event_types.count("browser.session.destroyed") == 1


def test_agent_runtime_reports_browser_delete_failure_without_blocking_success():
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="browser-runner@example.com",
            display_name="Browser Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_browser_runner",
            name="Browser Runner",
            permissions=[
                Permission(action="browser.act", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_browser_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_browser",
            message="Capture a page while cleanup is degraded.",
            mode="autonomous",
        ),
    )
    browser = DeleteFailingBrowserController()
    tool_gateway = ToolGateway()
    register_browser_tool_handlers(tool_gateway, browser)
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_browser",
                    title="Capture browser page",
                    tool_name="browser.action",
                    tool_input={"action_type": "screenshot"},
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        browser_controller=browser,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "browser.session.destroy_failed" in event_types
    assert "run.succeeded" in event_types
    destroy_failed = [
        event
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "browser.session.destroy_failed"
    ][0]
    assert destroy_failed.payload["session_id"] == state.browser_session_id
    assert destroy_failed.payload["reason"] == "success"
    assert destroy_failed.payload["error_type"] == "BrowserProviderUnavailableError"


def test_agent_runtime_uploads_browser_screenshot_for_workspace_capture():
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="browser-capture@example.com",
            display_name="Browser Capture",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_browser_capture",
            name="Browser Capture",
            permissions=[
                Permission(action="browser.act", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_browser_capture")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_browser",
            message="Capture the browser page.",
            mode="autonomous",
        ),
    )
    browser = InMemoryBrowserController()
    tool_gateway = ToolGateway()
    register_browser_tool_handlers(tool_gateway, browser)
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_browser_capture",
                    title="Capture browser page",
                    tool_name="browser.action",
                    tool_input={
                        "action_type": "screenshot",
                    },
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        browser_controller=browser,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    screenshot_objects = [
        storage_object
        for storage_object in storage_catalog.list_for_run("tenant_acme", run.id)
        if storage_object.purpose == StoragePurpose.BROWSER_SCREENSHOT
    ]
    assert len(screenshot_objects) == 1
    screenshot_object = screenshot_objects[0]
    assert screenshot_object.content_type == "image/png"
    assert screenshot_object.filename == f"{state.browser_session_id}.png"
    assert object_storage.objects[screenshot_object.uri].startswith(
        b"\x89PNG\r\n\x1a\n"
    )
    assert state.tool_results[0].output["screenshot_uri"] == screenshot_object.uri
    assert "screenshot_content_base64" not in state.tool_results[0].output
    browser_events = [
        event
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "browser.action.performed"
    ]
    assert browser_events[0].payload["screenshot_uri"] == screenshot_object.uri
    assert browser_events[0].payload["storage_object_id"] == screenshot_object.id


def test_agent_runtime_destroys_browser_session_after_later_step_failure():
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="browser-failure@example.com",
            display_name="Browser Failure",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_browser_failure",
            name="Browser Failure",
            permissions=[
                Permission(action="browser.act", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_browser_failure")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_browser",
            message="Capture a page then run an unavailable tool.",
            mode="autonomous",
        ),
    )
    browser = InMemoryBrowserController()
    tool_gateway = ToolGateway()
    register_browser_tool_handlers(tool_gateway, browser)
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_browser",
                    title="Capture browser page",
                    tool_name="browser.action",
                    tool_input={"action_type": "screenshot"},
                ),
                PlannedToolCall(
                    id="step_missing_tool",
                    title="Run missing tool",
                    tool_name="internal.missing",
                    tool_input={},
                ),
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
        browser_controller=browser,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert state.browser_session_id is not None
    assert browser.deleted_sessions == [state.browser_session_id]
    assert list(browser.sessions) == []
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert event_types.count("browser.session.destroyed") == 1
    assert event_types[-1] == "browser.session.destroyed"


def test_agent_runtime_resolves_tool_gateway_scopes_from_policy_service():
    identity = InMemoryIdentityService()
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-runner@example.com",
            display_name="Sandbox Runner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_runner",
            name="Sandbox Runner",
            permissions=[
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sandbox_runner")
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Run a sandbox command.",
            mode="autonomous",
        ),
    )
    captured_requests: list[ToolGatewayRequest] = []
    tool_gateway = ToolGateway()
    tool_gateway.register_tool(
        ToolPolicy(
            tool_name="sandbox.command",
            required_scopes=["sandbox.execute"],
        ),
        handler=lambda request: captured_requests.append(request)
        or ToolResult(
            tool_name=request.tool_name,
            output={"stdout": "ok"},
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingPlanGateway(
            plan=[
                PlannedToolCall(
                    id="step_sandbox",
                    title="Run sandbox command",
                    tool_name="sandbox.command",
                    tool_input={"command": "echo ok"},
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=IdentityPolicyService(identity_service=identity),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert captured_requests[0].granted_scopes == ["sandbox.execute"]


def test_agent_runtime_blocks_guarded_artifact_publication():
    store, run = create_runtime_run()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["agent-result.md"]),
            action=GuardrailAction.BLOCK,
            severity=GuardrailSeverity.HIGH,
            message="Artifact publication is blocked by policy",
        )
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
        guardrail_service=guardrail_service,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.artifact_blocked"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [
        [rule.id]
    ]
    assert guardrail_audits[0].metadata["guardrail_action"] == "block"
    assert run_events[-1].type == "run.failed"
    assert run_events[-1].payload["reason"] == "artifact_guardrail_blocked"
    assert "agent-result.md" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_redacts_guarded_artifact_metadata_before_publication():
    store, run = create_runtime_run()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["agent-result"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.MEDIUM,
            message="Artifact name contains restricted label",
            redaction_replacement="governed-result",
        )
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
        guardrail_service=guardrail_service,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    artifact = store.list_artifacts("tenant_acme", run.id)[0]
    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.artifact_redacted"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert artifact.name == "governed-result.md"
    assert artifact.uri.endswith("/governed-result.md")
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [
        [rule.id]
    ]
    assert run_events[-1].payload["artifact_name"] == "governed-result.md"
    assert "agent-result" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_resumes_artifact_guardrail_approval_after_worker_restart():
    store, run = create_runtime_run()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["agent-result.md"]),
            action=GuardrailAction.REQUIRE_APPROVAL,
            severity=GuardrailSeverity.HIGH,
            message="Artifact publication requires approval",
        )
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
        guardrail_service=guardrail_service,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    snapshot = store.get_runtime_state("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert approval.step_id == "guardrail:artifact"
    assert approval.reason == "Artifact publication requires approval"
    assert snapshot.pending_guardrail_approval_stage == "artifact"

    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    artifacts = store.list_artifacts("tenant_acme", run.id)

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert [artifact.name for artifact in artifacts] == ["agent-result.md"]
    assert resumed_state.approved_guardrail_keys == [f"artifact:{rule.id}"]
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.APPROVED
    )
    assert store.get_runtime_state("tenant_acme", run.id).approved_guardrail_keys == [
        f"artifact:{rule.id}"
    ]


def test_agent_runtime_records_tool_call_audit_and_billing():
    store, run = create_runtime_run()
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
        billing_pricing_service=BillingPricingService(
            rules=[
                BillingPricingRule(
                    meter_type="model_tokens_input",
                    unit="token",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.009,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_input",
                    unit="token",
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.003,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_output",
                    unit="token",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.012,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_output",
                    unit="token",
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.006,
                    pricing_unit_quantity=1000,
                ),
            ]
        ),
    )

    runtime.execute_run("tenant_acme", run.id)

    meters = store.list_billing_meters("tenant_acme")
    audits = store.list_audit_events("tenant_acme")

    tool_meters = [meter for meter in meters if meter.meter_type == "tool_call_count"]
    tool_audits = [event for event in audits if event.event_type == "tool.executed"]
    assert len(tool_meters) == 1
    assert tool_meters[0].run_id == run.id
    assert tool_meters[0].metadata["tool_name"] == "research.lookup"
    assert len(tool_audits) == 1
    assert tool_audits[0].metadata["step_id"] == "step_research"
    assert tool_audits[0].metadata["tool_name"] == "research.lookup"


def test_agent_runtime_records_skill_specific_meter_for_skill_backed_tool():
    store, run = create_runtime_run()
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_crm",
                    title="Lookup CRM account",
                    tool_name="crm.lookup",
                    skill_id="sales.crm_lookup",
                    tool_input={"account_id": "acct_123"},
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
        billing_pricing_service=BillingPricingService(
            rules=[
                BillingPricingRule(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    skill_id="sales.crm_lookup",
                    meter_type="skill_call_count",
                    unit="call",
                    price_per_unit=0.08,
                )
            ]
        ),
    )

    runtime.execute_run("tenant_acme", run.id)

    meters = store.list_billing_meters("tenant_acme")
    skill_meters = [meter for meter in meters if meter.meter_type == "skill_call_count"]
    tool_meters = [meter for meter in meters if meter.meter_type == "tool_call_count"]

    assert len(tool_meters) == 1
    assert len(skill_meters) == 1
    assert skill_meters[0].skill_id == "sales.crm_lookup"
    assert skill_meters[0].cost_estimate == 0.08
    assert skill_meters[0].metadata == {
        "step_id": "step_crm",
        "tool_name": "crm.lookup",
        "skill_id": "sales.crm_lookup",
    }


def test_agent_runtime_records_model_usage_audit_and_billing_without_prompt_content(
    monkeypatch,
):
    perf_counter_values = iter([100.0, 100.187])
    monkeypatch.setattr(
        runtime_module.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )
    store, run = create_runtime_run(
        "Create a prospect brief with private account context."
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            model_name="gpt-enterprise-planner",
            usage=ModelUsage(
                input_tokens=120,
                output_tokens=45,
                total_tokens=165,
                cached_input_tokens=48,
            ),
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect"},
                )
            ],
        ),
        tool_gateway=DeterministicToolGateway(),
        billing_pricing_service=BillingPricingService(
            rules=[
                BillingPricingRule(
                    meter_type="model_tokens_cached_input",
                    unit="token",
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.001,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_input",
                    unit="token",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.009,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_input",
                    unit="token",
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.003,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_output",
                    unit="token",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.012,
                    pricing_unit_quantity=1000,
                ),
                BillingPricingRule(
                    meter_type="model_tokens_output",
                    unit="token",
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    model="gpt-enterprise-planner",
                    price_per_unit=0.006,
                    pricing_unit_quantity=1000,
                ),
            ]
        ),
    )

    runtime.execute_run("tenant_acme", run.id)

    meters = store.list_billing_meters("tenant_acme")
    audits = store.list_audit_events("tenant_acme")
    plan_events = [
        event
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "plan.created"
    ]
    model_meters = [
        meter
        for meter in meters
        if meter.meter_type
        in {
            "model_call_count",
            "model_tokens_input",
            "model_tokens_output",
            "model_tokens_cached_input",
            "model_latency_ms",
        }
    ]
    model_audits = [
        event for event in audits if event.event_type == "model.plan.created"
    ]

    assert [
        (meter.meter_type, meter.quantity, meter.unit) for meter in model_meters
    ] == [
        ("model_call_count", 1, "call"),
        ("model_tokens_input", 120, "token"),
        ("model_tokens_output", 45, "token"),
        ("model_tokens_cached_input", 48, "token"),
        ("model_latency_ms", 187, "millisecond"),
    ]
    assert {meter.model for meter in model_meters} == {"gpt-enterprise-planner"}
    assert [(meter.meter_type, meter.cost_estimate) for meter in model_meters] == [
        ("model_call_count", None),
        ("model_tokens_input", 0.00036),
        ("model_tokens_output", 0.00027),
        ("model_tokens_cached_input", 0.000048),
        ("model_latency_ms", None),
    ]
    assert model_meters[0].metadata["response_id"] == f"response_{run.id}"
    assert model_meters[0].metadata["planned_step_count"] == 1
    assert model_meters[0].metadata["latency_ms"] == 187
    assert len(model_audits) == 1
    assert model_audits[0].metadata["response_id"] == f"response_{run.id}"
    assert model_audits[0].metadata["model"] == "gpt-enterprise-planner"
    assert model_audits[0].metadata["usage"] == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
        "cached_input_tokens": 48,
    }
    assert model_audits[0].metadata["latency_ms"] == 187
    assert model_audits[0].metadata["planned_step_count"] == 1
    assert "private account context" not in str(model_audits[0].metadata)
    assert len(plan_events) == 1
    assert plan_events[0].payload["model"] == "gpt-enterprise-planner"
    assert plan_events[0].payload["planned_step_count"] == 1
    assert plan_events[0].payload["latency_ms"] == 187
    assert plan_events[0].payload["usage"] == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
        "cached_input_tokens": 48,
    }
    assert "private account context" not in str(plan_events[0].payload)


def test_agent_runtime_records_model_provider_attempts_without_error_detail():
    store, run = create_runtime_run()

    class ProviderAttemptGateway(ModelGateway):
        def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
            return ModelGatewayResponse(
                id=f"response_{request.run_id}",
                provider="secondary",
                model="gpt-enterprise-planner",
                planned_steps=[
                    PlannedToolCall(
                        id="step_research",
                        title="Research prospect",
                        tool_name="research.lookup",
                        tool_input={"query": "prospect"},
                    )
                ],
                provider_attempts=[
                    {
                        "provider_id": "primary",
                        "model": "gpt-enterprise-planner",
                        "status": "response_error",
                        "invoked": True,
                        "fallback_allowed": True,
                        "error_type": "ModelGatewayResponseError",
                    },
                    {
                        "provider_id": "secondary",
                        "model": "gpt-enterprise-planner",
                        "status": "succeeded",
                        "invoked": True,
                        "fallback_allowed": False,
                        "error_type": None,
                    },
                ],
            )

    runtime = AgentRuntime(
        store=store,
        model_gateway=ProviderAttemptGateway(),
        tool_gateway=DeterministicToolGateway(),
    )

    runtime.execute_run("tenant_acme", run.id)

    model_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "model.plan.created"
    ]
    plan_events = [
        event
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "plan.created"
    ]

    assert len(model_audits) == 1
    assert model_audits[0].metadata["provider_attempts"] == [
        {
            "provider_id": "primary",
            "model": "gpt-enterprise-planner",
            "status": "response_error",
            "invoked": True,
            "fallback_allowed": True,
            "error_type": "ModelGatewayResponseError",
        },
        {
            "provider_id": "secondary",
            "model": "gpt-enterprise-planner",
            "status": "succeeded",
            "invoked": True,
            "fallback_allowed": False,
            "error_type": None,
        },
    ]
    assert "unavailable" not in str(model_audits[0].metadata)
    assert "prospect" not in str(model_audits[0].metadata)
    assert len(plan_events) == 1
    assert plan_events[0].payload["provider"] == "secondary"
    assert plan_events[0].payload["model"] == "gpt-enterprise-planner"
    assert plan_events[0].payload["provider_attempts"] == [
        {
            "provider_id": "primary",
            "model": "gpt-enterprise-planner",
            "status": "response_error",
            "invoked": True,
            "fallback_allowed": True,
            "error_type": "ModelGatewayResponseError",
        },
        {
            "provider_id": "secondary",
            "model": "gpt-enterprise-planner",
            "status": "succeeded",
            "invoked": True,
            "fallback_allowed": False,
            "error_type": None,
        },
    ]
    assert "unavailable" not in str(plan_events[0].payload)


def test_agent_runtime_enforces_workspace_scoped_model_policy_before_gateway_call():
    store, run = create_runtime_run()
    gateway = RecordingPlanGateway()
    runtime = AgentRuntime(
        store=store,
        model_gateway=gateway,
        model_policy=ModelPolicy(
            default_model="global-default",
            allowed_models=["global-default", "sales-approved"],
            scoped_policies=[
                ModelPolicyScope(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    default_model="sales-denied",
                    allowed_models=["sales-approved"],
                )
            ],
        ),
    )

    with pytest.raises(ModelPolicyDeniedError):
        runtime.execute_run("tenant_acme", run.id)

    audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "model.policy_denied"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert gateway.requests == []
    assert audits[0].metadata["requested_model"] == "sales-denied"
    assert audits[0].metadata["allowed_models"] == ["sales-approved"]
    assert audits[0].metadata["policy_scope"] == {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
    }
    assert run_events[-1].payload["reason"] == "model_policy_denied"


def test_agent_runtime_blocks_guarded_model_request_before_gateway_call():
    store, run = create_runtime_run(
        "Export raw customer secret values into the account brief."
    )
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_REQUEST,
            condition=GuardrailCondition(text_contains=["raw customer secret"]),
            action=GuardrailAction.BLOCK,
            severity=GuardrailSeverity.CRITICAL,
            message="Model request contains restricted secret extraction intent",
        )
    )
    model_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        guardrail_service=guardrail_service,
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.model_request_blocked"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [
        [rule.id]
    ]
    assert guardrail_audits[0].metadata["guardrail_action"] == "block"
    assert run_events[-1].type == "run.failed"
    assert run_events[-1].payload["reason"] == "model_guardrail_blocked"
    assert "raw customer secret" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_redacts_guarded_model_request_before_gateway_call():
    store, run = create_runtime_run(
        "Summarize account token raw-customer-secret for renewal planning."
    )
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_REQUEST,
            condition=GuardrailCondition(text_contains=["raw-customer-secret"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.HIGH,
            message="Model request contains restricted token material",
        )
    )
    model_gateway = RecordingPlanGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        guardrail_service=guardrail_service,
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.model_request_redacted"
    ]
    model_messages = "\n".join(
        message.content for message in model_gateway.requests[0].messages
    )

    assert state.status == RunStatus.SUCCEEDED
    assert "raw-customer-secret" not in model_messages
    assert "[REDACTED]" in model_messages
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [
        [rule.id]
    ]
    assert "raw-customer-secret" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_redacts_guarded_model_response_before_plan_execution():
    store, run = create_runtime_run("Draft the renewal email.")
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_RESPONSE,
            condition=GuardrailCondition(text_contains=["raw-customer-secret"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.HIGH,
            message="Model response contains restricted token material",
        )
    )
    output_text = (
        '{"steps":[{"id":"step_send","title":"Send summary",'
        '"tool_name":"communication.send_email",'
        '"tool_input":{"body":"Account token is raw-customer-secret"},'
        '"approval_required":false}]}'
    )
    model_gateway = RecordingPlanGateway(
        output_text=output_text,
        plan=[
            PlannedToolCall(
                id="step_send",
                title="Send summary",
                tool_name="communication.send_email",
                tool_input={"body": "Account token is raw-customer-secret"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        guardrail_service=guardrail_service,
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.model_response_redacted"
    ]

    assert state.status == RunStatus.SUCCEEDED
    assert state.plan[0].tool_input["body"] == "Account token is [REDACTED]"
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [
        [rule.id]
    ]
    assert "raw-customer-secret" not in str(state.model_dump(mode="json"))
    assert "raw-customer-secret" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_blocks_model_call_when_run_budget_is_exhausted():
    store, run = create_runtime_run(
        "Create a prospect brief with private account context."
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
        metadata={"reason": "previous planning call"},
    )
    model_gateway = DeterministicModelGateway(
        model_name="gpt-enterprise-planner",
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        model_budget_guard=ModelBudgetGuard(
            policy=ModelBudgetPolicy(max_model_calls_per_run=1),
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    audits = store.list_audit_events("tenant_acme")
    budget_audits = [
        event for event in audits if event.event_type == "model.budget_exceeded"
    ]
    assert state.status == RunStatus.FAILED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    assert len(budget_audits) == 1
    assert budget_audits[0].metadata["limit_type"] == "model_call_count"
    assert budget_audits[0].metadata["current_quantity"] == 1
    assert budget_audits[0].metadata["limit"] == 1
    assert "private account context" not in str(budget_audits[0].metadata)


def test_agent_runtime_blocks_model_call_when_workspace_budget_is_exhausted():
    store, previous_run = create_runtime_run("Previous model use.")
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Create a prospect brief with private account context.",
            mode="autonomous",
        ),
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=previous_run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
        metadata={"reason": "previous planning call"},
    )
    model_gateway = DeterministicModelGateway(
        model_name="gpt-enterprise-planner",
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        model_budget_guard=ModelBudgetGuard(
            policy=ModelBudgetPolicy(max_model_calls_per_workspace=1),
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    audits = store.list_audit_events("tenant_acme")
    budget_audits = [
        event for event in audits if event.event_type == "model.budget_exceeded"
    ]
    assert state.status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    assert len(budget_audits) == 1
    assert budget_audits[0].metadata["scope_type"] == "workspace"
    assert budget_audits[0].metadata["scope_id"] == "workspace_sales"
    assert budget_audits[0].metadata["limit_type"] == "model_call_count"
    assert "private account context" not in str(budget_audits[0].metadata)


def test_agent_runtime_pauses_for_approval_and_resumes_after_approval():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert store.get_run("tenant_acme", run.id).status == RunStatus.AWAITING_APPROVAL
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    assert approval.status == ApprovalStatus.PENDING
    assert approval.reason == "Step requires approval: Send customer email"

    resumed_state = runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.APPROVED
    )
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED

    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "approval.requested" in event_types
    assert "approval.resolved" in event_types
    assert event_types[-1] == "run.succeeded"


def test_agent_runtime_rejects_paused_approval_without_executing_step():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    paused_state = runtime.execute_run("tenant_acme", run.id)
    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    approval = store.list_approval_requests("tenant_acme", run.id)[0]

    rejected_state = runtime.reject_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        rejected_by_user_id="manager_1",
    )

    assert rejected_state.status == RunStatus.FAILED
    assert rejected_state.approval_id is None
    assert rejected_state.tool_results == []
    assert runtime.pending_states == {}
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.REJECTED
    )
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "approval.rejected" in event_types
    assert event_types[-1] == "run.failed"


def test_agent_runtime_cancels_paused_run_and_pending_approval():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]

    cancelled_run = runtime.cancel_run(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
        reason_code="user_requested",
    )

    snapshot = store.get_runtime_state("tenant_acme", run.id)
    assert paused_state.status == RunStatus.CANCELLED
    assert cancelled_run.status == RunStatus.CANCELLED
    assert snapshot.status == RunStatus.CANCELLED
    assert snapshot.approval_id is None
    assert runtime.pending_states == {}
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.CANCELLED
    )
    assert store.list_approval_requests("tenant_acme", run.id)[0].id == approval.id
    assert store.list_artifacts("tenant_acme", run.id) == []
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "approval.cancelled" in event_types
    assert event_types[-1] == "run.cancelled"


def test_agent_runtime_pauses_when_tool_policy_requires_approval():
    store, run = create_runtime_run("Send this brief to an external customer.")
    tool_gateway = ToolGateway()
    tool_gateway.register_tool(
        policy=ToolPolicy(
            tool_name="communication.send_email",
            risk_level=ToolRiskLevel.HIGH,
            approval_required=True,
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"sent": True},
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=False,
                )
            ]
        ),
        tool_gateway=tool_gateway,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    assert approval.step_id == "step_send"
    assert approval.reason == "Tool approval required: communication.send_email"

    resumed_state = runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def test_agent_runtime_audits_failed_tool_call_without_raw_sensitive_inputs():
    store, run = create_runtime_run("Look up a prospect.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect", "api_key": "raw-key-value"},
                )
            ]
        ),
        tool_gateway=ToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    audits = store.list_audit_events("tenant_acme")
    failed_tool_audits = [
        event for event in audits if event.event_type == "tool.failed"
    ]
    assert state.status == RunStatus.FAILED
    assert len(failed_tool_audits) == 1
    assert failed_tool_audits[0].metadata["step_id"] == "step_research"
    assert failed_tool_audits[0].metadata["tool_name"] == "research.lookup"
    assert failed_tool_audits[0].metadata["tool_input"]["query"] == "prospect"
    assert failed_tool_audits[0].metadata["tool_input"]["api_key"] == "[REDACTED]"
    assert "raw-key-value" not in str(failed_tool_audits[0].metadata)


def test_agent_runtime_resumes_approval_from_persisted_state_after_worker_restart():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert (
        store.get_runtime_state("tenant_acme", run.id).status
        == RunStatus.AWAITING_APPROVAL
    )

    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        tool_gateway=DeterministicToolGateway(),
    )

    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert resumed_state.completed_step_ids == ["step_send"]
    assert store.get_runtime_state("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def test_agent_runtime_resumes_model_request_guardrail_approval_after_worker_restart():
    store, run = create_runtime_run("Research requires-review customer context.")
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_REQUEST,
            condition=GuardrailCondition(text_contains=["requires-review"]),
            action=GuardrailAction.REQUIRE_APPROVAL,
            severity=GuardrailSeverity.HIGH,
            message="Model request requires approval before provider invocation",
        )
    )
    first_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research account",
                tool_name="research.lookup",
                tool_input={"query": "account"},
            )
        ]
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=first_gateway,
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    snapshot = store.get_runtime_state("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert first_gateway.call_count == 0
    assert approval.step_id == "guardrail:model_request"
    assert (
        approval.reason == "Model request requires approval before provider invocation"
    )
    assert snapshot.status == RunStatus.AWAITING_APPROVAL
    assert snapshot.pending_guardrail_approval_stage == "model_request"
    assert snapshot.approved_guardrail_keys == []

    resumed_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research account",
                tool_name="research.lookup",
                tool_input={"query": "account"},
            )
        ]
    )
    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=resumed_gateway,
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert resumed_gateway.call_count == 1
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.APPROVED
    )
    assert resumed_state.approved_guardrail_keys == [f"model_request:{rule.id}"]
    assert store.get_runtime_state("tenant_acme", run.id).approved_guardrail_keys == [
        f"model_request:{rule.id}"
    ]


def test_agent_runtime_retries_transient_tool_failure():
    store, run = create_runtime_run()
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_data",
                    title="Analyze source data",
                    tool_name="data.analyze",
                    tool_input={"file_id": "file_123"},
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(fail_once_for=["data.analyze"]),
        max_step_retries=1,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert runtime.tool_gateway.call_counts["data.analyze"] == 2
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "step.retrying" in event_types
    assert "tool_call.failed" in event_types


def test_agent_runtime_builds_langgraph_graph():
    runtime = AgentRuntime(store=InMemoryControlPlaneStore())

    graph = runtime.build_graph()
    compiled = graph.compile()

    assert compiled is not None
    assert graph.state_schema is AgentRuntimeState


def test_agent_runtime_executes_native_run_through_compiled_langgraph(monkeypatch):
    store, run = create_runtime_run()
    invocations: list[AgentRuntimeState] = []

    class RecordingCompiledGraph:
        def invoke(self, state, *args, **kwargs):
            del args, kwargs
            invocations.append(state)
            return state.model_dump(mode="python")

    class RecordingGraph:
        def compile(self):
            return RecordingCompiledGraph()

    monkeypatch.setattr(
        runtime_module,
        "build_runtime_graph",
        lambda executor: RecordingGraph(),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert len(invocations) == 1
    assert invocations[0].run_id == run.id
    assert isinstance(state, AgentRuntimeState)


def test_langgraph_executes_action_and_verification_nodes():
    store, run = create_runtime_run()
    runtime = AgentRuntime(
        store=store,
        model_gateway=RecordingGraphGateway(
            decisions=[
                AgentDecision(
                    kind="action",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect"},
                )
            ],
            verifications=[AgentVerificationResult(outcome="complete")],
        ),
        tool_gateway=DeterministicToolGateway(),
        full_auto_requires_isolation=False,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert state.graph_route == "end"
    assert len(state.observations) == 1
    assert state.verifier_result is not None
    assert state.verifier_result.outcome == "complete"
    events = store.list_run_events("tenant_acme", run.id)
    started = next(event for event in events if event.type == "agent.loop.started")
    assert started.payload["mode"] == "langgraph"

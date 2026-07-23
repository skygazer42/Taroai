from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import Field

from taroai.agent import AgentRuntime
from taroai.app import create_app
from taroai.audit import AuditService
from taroai.config import Settings
from taroai.domain import utc_now
from taroai.identity import InMemoryIdentityService, PasswordHasher
from taroai.knowledge import InMemoryKnowledgeService
from taroai.model_gateway import (
    ModelGateway,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelUsage,
    PlannedToolCall,
)
from taroai.policy import IdentityPolicyService
from taroai.sandbox import LocalProcessSandboxAdapter, register_sandbox_tool_handlers
from taroai.storage import (
    InMemoryStorageCatalog,
    ObjectStorageAdapter,
    StorageDeleteResult,
    StorageDownloadResult,
    StorageObject,
    StorageSignedUrl,
    StorageUploadResult,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import ToolGateway, ToolPolicy, ToolResult


class ScriptedAcceptanceModelGateway(ModelGateway):
    requests: list[ModelGatewayRequest] = Field(default_factory=list)

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        self.requests.append(request)
        return ModelGatewayResponse(
            id=f"response_{request.run_id}",
            model=request.model or "acceptance-openai-compatible",
            planned_steps=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate sandbox report",
                    tool_name="sandbox.command",
                    tool_input={
                        "command": (
                            'python -c "from pathlib import Path; '
                            "Path('artifacts').mkdir(exist_ok=True); "
                            "Path('artifacts/report.md').write_text("
                            "'# MVP Acceptance Report\\n"
                            "Governed artifact output from sandbox.\\n'"
                            ')\"'
                        ),
                        "artifact_paths": ["/workspace/artifacts/report.md"],
                    },
                ),
                PlannedToolCall(
                    id="step_notify",
                    title="Prepare external notification",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com", "subject": "MVP report"},
                    approval_required=True,
                ),
            ],
            usage=ModelUsage(input_tokens=12, output_tokens=18, total_tokens=30),
        )


class RecordingObjectStorage(ObjectStorageAdapter):
    objects: dict[str, bytes] = Field(default_factory=dict)

    def upload(
        self,
        storage_object: StorageObject,
        content: bytes,
    ) -> StorageUploadResult:
        self.objects[storage_object.uri] = content
        return StorageUploadResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            etag="etag_acceptance",
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
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            delete_marker=True,
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
            url=f"https://storage.example.com/{storage_object.key}?signed=1",
            method="GET" if operation == "read" else "PUT",
            expires_at=(now or utc_now()) + timedelta(seconds=expires_in_seconds),
        )


def build_acceptance_client(tmp_path: Path) -> TestClient:
    store = InMemoryControlPlaneStore()
    identity = InMemoryIdentityService(
        password_hasher=PasswordHasher(salt="acceptance_salt")
    )
    policy_service = IdentityPolicyService(identity_service=identity)
    audit_service = AuditService(store=store)
    knowledge_service = InMemoryKnowledgeService()
    sandbox_adapter = LocalProcessSandboxAdapter(root_dir=tmp_path / "sandboxes")
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    tool_gateway = ToolGateway(audit_service=audit_service)
    register_sandbox_tool_handlers(tool_gateway, sandbox_adapter)
    tool_gateway.register_tool(
        ToolPolicy(tool_name="communication.send_email"),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"prepared": True, "recipient": request.tool_input["to"]},
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=ScriptedAcceptanceModelGateway(),
        tool_gateway=tool_gateway,
        policy_service=policy_service,
        audit_service=audit_service,
        knowledge_service=knowledge_service,
        sandbox_adapter=sandbox_adapter,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
    )
    return TestClient(
        create_app(
            store=store,
            settings=Settings(
                _env_file=None,
                tenant_bootstrap_token="acceptance_bootstrap",
                dev_request_headers_enabled=False,
                access_token_secret="acceptance_access_token_secret",
                password_hash_salt="acceptance_password_salt",
                model_gateway_model="gpt-acceptance",
            ),
            runtime=runtime,
            knowledge_service=knowledge_service,
            sandbox_adapter=sandbox_adapter,
            storage_catalog=storage_catalog,
            object_storage=object_storage,
            identity_service=identity,
            policy_service=policy_service,
            audit_service=audit_service,
        )
    )


def bootstrap_and_login(
    client: TestClient,
    tenant_slug: str,
    email: str,
) -> tuple[dict, dict[str, str]]:
    password = "correct horse battery staple"
    bootstrap = client.post(
        "/api/tenants/bootstrap",
        headers={"X-Bootstrap-Token": "acceptance_bootstrap"},
        json={
            "tenant_slug": tenant_slug,
            "owner_email": email,
            "owner_display_name": "Acceptance Owner",
            "owner_password": password,
        },
    )
    assert bootstrap.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": bootstrap.json()["tenant_id"],
            "email": email,
            "password": password,
        },
    )
    assert login.status_code == 200
    return bootstrap.json(), {"Authorization": f"Bearer {login.json()['access_token']}"}


def event_types_from_sse(body: str) -> list[str]:
    return [
        line.removeprefix("event: ")
        for line in body.splitlines()
        if line.startswith("event: ")
    ]


def assert_events_in_order(event_types: list[str], expected: list[str]) -> None:
    cursor = 0
    for event_type in event_types:
        if cursor < len(expected) and event_type == expected[cursor]:
            cursor += 1
    assert cursor == len(expected), event_types


def test_mvp_acceptance_scenario_onboards_executes_approves_and_enforces_tenant_isolation(
    tmp_path: Path,
):
    client = build_acceptance_client(tmp_path)
    owner, headers = bootstrap_and_login(
        client,
        tenant_slug="acceptance",
        email="owner@acceptance.example",
    )
    workspace_id = owner["starter_workspace_id"]
    owner_user_id = owner["owner_user_id"]

    readiness = client.get("/api/tenants/current/readiness", headers=headers)
    base = client.post(
        "/api/knowledge-bases",
        headers=headers,
        json={
            "workspace_id": workspace_id,
            "name": "Acceptance Knowledge",
            "description": "Pilot workspace guidance.",
        },
    )
    document = client.post(
        "/api/knowledge-documents",
        headers=headers,
        json={
            "workspace_id": workspace_id,
            "knowledge_base_id": base.json()["id"],
            "source_uri": "s3://acceptance/sales.md",
            "source_document_id": "acceptance_sales",
            "title": "Acceptance Sales Guidance",
            "content": "Renewal pricing requires finance approval before sharing.",
            "content_type": "text/plain",
            "acl_subjects": [f"user:{owner_user_id}"],
            "sensitivity_level": 1,
            "document_version": "v1",
            "content_hash": "sha256:acceptance",
        },
    )
    knowledge = client.post(
        "/api/knowledge/query",
        headers=headers,
        json={
            "query": "renewal pricing approval",
            "allowed_workspace_ids": [workspace_id],
            "acl_subjects": [f"user:{owner_user_id}"],
            "clearance_level": 1,
        },
    )
    run = client.post(
        "/api/runs",
        headers=headers,
        json={
            "workspace_id": workspace_id,
            "agent_id": "agent_acceptance",
            "message": "Create a governed report and prepare customer notification.",
            "mode": "autonomous",
        },
    )
    run_id = run.json()["run_id"]
    paused = client.post(f"/api/runs/{run_id}/execute", headers=headers)
    approval_id = paused.json()["approval_id"]
    resumed = client.post(
        f"/api/runs/{run_id}/approvals",
        headers=headers,
        json={"approval_id": approval_id},
    )
    artifacts = client.get(f"/api/runs/{run_id}/artifacts", headers=headers)
    downloaded = client.get(
        f"/api/storage/objects/{artifacts.json()[0]['storage_object_id']}/content",
        headers=headers,
    )
    events = client.get(f"/api/runs/{run_id}/events", headers=headers)
    meters = client.get(
        "/api/billing/meters",
        headers=headers,
        params={"run_id": run_id},
    )
    audits = client.get(
        "/api/audit-events",
        headers=headers,
        params={"run_id": run_id},
    )
    trace = client.get(f"/api/runs/{run_id}/trace", headers=headers)
    other, other_headers = bootstrap_and_login(
        client,
        tenant_slug="acceptance-other",
        email="owner@acceptance-other.example",
    )
    cross_tenant_run = client.get(f"/api/runs/{run_id}", headers=other_headers)

    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert base.status_code == 201
    assert document.status_code == 201
    assert knowledge.status_code == 200
    assert knowledge.json()[0]["source_document_id"] == "acceptance_sales"
    assert paused.status_code == 200
    assert paused.json()["status"] == "awaiting_approval"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "succeeded"
    assert artifacts.status_code == 200
    assert [artifact["name"] for artifact in artifacts.json()] == ["report.md"]
    assert downloaded.status_code == 200
    assert downloaded.content == (
        b"# MVP Acceptance Report\n"
        b"Governed artifact output from sandbox.\n"
    )
    event_types = event_types_from_sse(events.text)
    assert_events_in_order(
        event_types,
        [
            "run.created",
            "run.status_changed",
            "context.loaded",
            "plan.created",
            "policy.checked",
            "step.started",
            "tool_call.started",
            "sandbox.session.created",
            "artifact.created",
            "sandbox.artifact.promoted",
            "sandbox.command.executed",
            "tool_call.completed",
            "approval.requested",
            "approval.resolved",
            "step.started",
            "tool_call.started",
            "tool_call.completed",
            "sandbox.session.destroyed",
            "run.succeeded",
        ],
    )
    meter_types = {meter["meter_type"] for meter in meters.json()}
    assert {
        "run_count",
        "model_call_count",
        "model_tokens_input",
        "model_tokens_output",
        "tool_call_count",
    } <= meter_types
    audit_types = {event["event_type"] for event in audits.json()}
    assert {"model.plan.created", "tool.executed", "approval.resolved"} <= audit_types
    assert trace.status_code == 200
    assert {"events", "billing_meters", "audit_events", "trace_events"} <= set(
        trace.json()
    )
    assert cross_tenant_run.status_code == 403
    assert other["tenant_id"] != owner["tenant_id"]


def test_mvp_acceptance_scenario_document_is_present():
    document = Path("docs/mvp/acceptance-scenario.md")

    assert document.exists()
    body = document.read_text()
    assert "MVP End-to-End Acceptance Scenario" in body
    assert "tenant isolation" in body
    assert "approval" in body

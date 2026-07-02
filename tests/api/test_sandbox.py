from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.domain import RunCreate
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.sandbox import (
    BrowserAction,
    BrowserActionType,
    SandboxCommand,
    SandboxCreateRequest,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSessionCreateRequest,
    SandboxSessionStatus,
    register_browser_tool_handlers,
    register_sandbox_tool_handlers,
)
from taroai.storage import InMemoryStorageCatalog, S3CompatibleObjectStorage
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import ToolGateway, ToolGatewayRequest
from tests.api.sandbox_adapters import InMemoryBrowserController, InMemorySandboxAdapter


class RecordingBody:
    def __init__(self, content: bytes):
        self.content = content

    def read(self) -> bytes:
        return self.content


class RecordingS3Client:
    def __init__(self):
        self.put_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {"ETag": '"etag_from_sandbox"'}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": RecordingBody(self.objects[(kwargs["Bucket"], kwargs["Key"])])}


def create_sandbox_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    operator = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-operator@example.com",
            display_name="Sandbox Operator",
            password="correct horse battery staple",
        )
    )
    creator = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sandbox-creator@example.com",
            display_name="Sandbox Creator",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_operator",
            name="Sandbox Operator",
            permissions=[
                Permission(action="sandbox.create", resource="tenant:tenant_acme"),
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
                Permission(action="browser.act", resource="tenant:tenant_acme"),
                Permission(action="storage.read", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
                Permission(action="billing.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_creator",
            name="Sandbox Creator",
            permissions=[
                Permission(action="sandbox.create", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", operator.id, "role_sandbox_operator")
    identity.assign_role("tenant_acme", creator.id, "role_sandbox_creator")
    return identity, operator, creator


def test_sandbox_adapter_manages_session_command_file_snapshot_and_destroy():
    adapter = InMemorySandboxAdapter()
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12",
            network_mode=SandboxNetworkMode.DISABLED,
        )
    )

    result = adapter.execute(
        SandboxCommand(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.id,
            command="python --version",
        )
    )
    uploaded = adapter.upload_file(
        SandboxFileWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.id,
            path="/workspace/input.txt",
            content="hello",
        )
    )
    downloaded = adapter.download_file(
        tenant_id="tenant_acme",
        session_id=session.id,
        path="/workspace/input.txt",
    )
    snapshot = adapter.snapshot("tenant_acme", session.id)
    destroyed = adapter.destroy("tenant_acme", session.id)

    assert session.status == SandboxSessionStatus.ACTIVE
    assert result.exit_code == 0
    assert result.stdout == "accepted: python --version"
    assert uploaded.size_bytes == 5
    assert downloaded.content == "hello"
    assert snapshot.session_id == session.id
    assert destroyed.status == SandboxSessionStatus.DESTROYED


def test_browser_controller_records_actions_with_run_scope():
    browser = InMemoryBrowserController()
    session = browser.open_session(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="sandbox_1",
    )

    navigation = browser.apply(
        BrowserAction(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.session_id,
            action_type=BrowserActionType.NAVIGATE,
            url="https://example.com",
        )
    )
    screenshot = browser.apply(
        BrowserAction(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.session_id,
            action_type=BrowserActionType.SCREENSHOT,
        )
    )

    assert navigation.current_url == "https://example.com"
    assert screenshot.screenshot_uri == "s3://tenant_acme/runs/run_1/browser/sandbox_1.png"
    assert len(browser.sessions[session.session_id].actions) == 2


def test_sandbox_tool_gateway_handler_executes_command_with_scope():
    adapter = InMemorySandboxAdapter()
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12",
        )
    )
    gateway = ToolGateway()
    register_sandbox_tool_handlers(gateway, adapter)

    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_code",
            tool_name="sandbox.command",
            tool_input={"session_id": session.id, "command": "python --version"},
            granted_scopes=["sandbox.execute"],
        )
    )

    assert result.tool_name == "sandbox.command"
    assert result.output["exit_code"] == 0
    assert result.output["stdout"] == "accepted: python --version"


def test_browser_tool_gateway_handler_applies_action_with_scope():
    browser = InMemoryBrowserController()
    session = browser.open_session(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="sandbox_1",
    )
    gateway = ToolGateway()
    register_browser_tool_handlers(gateway, browser)

    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_browser",
            tool_name="browser.action",
            tool_input={
                "session_id": session.session_id,
                "action_type": "navigate",
                "url": "https://example.com",
            },
            granted_scopes=["browser.act"],
        )
    )

    assert result.tool_name == "browser.action"
    assert result.output["action_type"] == "navigate"
    assert result.output["current_url"] == "https://example.com"


def test_browser_screenshot_content_is_not_serialized_in_observation_response():
    browser = InMemoryBrowserController()
    session = browser.open_session(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="sandbox_1",
    )

    observation = browser.apply(
        BrowserAction(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.session_id,
            action_type=BrowserActionType.SCREENSHOT,
        )
    )

    assert observation.screenshot_content is not None
    serialized = observation.model_dump(mode="json")
    assert "screenshot_content" not in serialized


def test_app_default_runtime_registers_sandbox_tool_gateway_handler():
    adapter = InMemorySandboxAdapter()
    app = create_app(sandbox_adapter=adapter)
    run = app.state.store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Run code in the sandbox.",
        ),
    )
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id=run.id,
            image="python:3.12",
        )
    )

    result = app.state.runtime.tool_gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=run.id,
            step_id="step_code",
            tool_name="sandbox.command",
            tool_input={"session_id": session.id, "command": "python --version"},
            granted_scopes=["sandbox.execute"],
        )
    )

    assert result.tool_name == "sandbox.command"
    assert result.output["exit_code"] == 0
    assert result.output["stdout"] == "accepted: python --version"


def test_app_default_runtime_registers_browser_tool_gateway_handler():
    browser = InMemoryBrowserController()
    app = create_app(browser_controller=browser)
    run = app.state.store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Inspect the vendor page in a browser.",
        ),
    )
    session = browser.open_session(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id=run.id,
        session_id="sandbox_1",
    )

    result = app.state.runtime.tool_gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=run.id,
            step_id="step_browser",
            tool_name="browser.action",
            tool_input={
                "session_id": session.session_id,
                "action_type": "screenshot",
            },
            granted_scopes=["browser.act"],
        )
    )

    assert result.tool_name == "browser.action"
    assert result.output["action_type"] == "screenshot"
    assert result.output["screenshot_uri"].endswith(f"/browser/{session.session_id}.png")


def test_sandbox_api_creates_session_and_executes_command_tenant_scoped():
    identity, operator, _creator = create_sandbox_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
        )
    )
    session_response = client.post(
        "/api/sandbox/sessions",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id},
        json={
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "image": "python:3.12",
            "network_mode": "disabled",
        },
    )

    assert session_response.status_code == 201

    command_response = client.post(
        f"/api/sandbox/sessions/{session_response.json()['id']}/commands",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id},
        json={"command": "python --version"},
    )
    other_tenant_response = client.post(
        f"/api/sandbox/sessions/{session_response.json()['id']}/commands",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": "user_2"},
        json={"command": "python --version"},
    )

    assert SandboxSessionCreateRequest.model_validate(session_response.json()).run_id == "run_1"
    assert command_response.status_code == 200
    assert command_response.json()["stdout"] == "accepted: python --version"
    assert other_tenant_response.status_code == 404


def test_sandbox_api_requires_permissions_and_records_audit_billing():
    identity, operator, creator = create_sandbox_identity()
    store = InMemoryControlPlaneStore()
    storage_client = RecordingS3Client()
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            store=store,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                client=storage_client,
            ),
        )
    )
    operator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}
    creator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": creator.id}
    run = client.post(
        "/api/runs",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "message": "Run a Python data check.",
            "mode": "workflow",
        },
    ).json()

    session_response = client.post(
        "/api/sandbox/sessions",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "run_id": run["run_id"],
            "image": "python:3.12",
            "network_mode": "disabled",
            "timeout_seconds": 120,
        },
    )
    forbidden_command = client.post(
        f"/api/sandbox/sessions/{session_response.json()['id']}/commands",
        headers=creator_headers,
        json={
            "command": "python check.py",
            "timeout_seconds": 120,
            "env": {"API_TOKEN": "secret-token-value"},
        },
    )
    command_response = client.post(
        f"/api/sandbox/sessions/{session_response.json()['id']}/commands",
        headers=operator_headers,
        json={
            "command": "python check.py",
            "timeout_seconds": 120,
            "env": {"API_TOKEN": "secret-token-value"},
        },
    )
    meters = client.get("/api/billing/meters", headers=operator_headers)
    audits = client.get("/api/audit-events", headers=operator_headers)
    storage_objects = client.get(
        f"/api/runs/{run['run_id']}/storage-objects",
        headers=operator_headers,
    )

    assert session_response.status_code == 201
    assert forbidden_command.status_code == 403
    assert command_response.status_code == 200
    command_output_objects = [
        storage_object
        for storage_object in storage_objects.json()
        if storage_object["purpose"] == "sandbox-command-outputs"
    ]
    assert len(command_output_objects) == 1
    assert command_response.json()["output_uri"] == (
        f"s3://{command_output_objects[0]['bucket']}/{command_output_objects[0]['key']}"
    )
    command_output_content = client.get(
        f"/api/storage/objects/{command_output_objects[0]['id']}/content",
        headers=operator_headers,
    )
    assert command_output_content.status_code == 200
    command_output = command_output_content.json()
    assert command_output["session_id"] == session_response.json()["id"]
    assert command_output["command"] == "python check.py"
    assert command_output["exit_code"] == 0
    assert command_output["stdout"] == "accepted: python check.py"
    assert command_output["stderr"] == ""
    assert "secret-token-value" not in command_output_content.text
    sandbox_meters = [
        meter for meter in meters.json() if meter["meter_type"] == "sandbox_minutes"
    ]
    assert sandbox_meters[0]["quantity"] == 2
    assert sandbox_meters[0]["metadata"]["session_id"] == session_response.json()["id"]
    audit_events = [
        event
        for event in audits.json()
        if event["event_type"] in {"sandbox.session.created", "sandbox.command.executed"}
    ]
    assert [event["event_type"] for event in audit_events] == [
        "sandbox.session.created",
        "sandbox.command.executed",
    ]
    command_metadata = audit_events[1]["metadata"]
    assert command_metadata["env_keys"] == ["API_TOKEN"]
    assert command_metadata["storage_object_id"] == command_output_objects[0]["id"]
    assert "secret-token-value" not in str(command_metadata)
    assert "accepted: python check.py" not in str(command_metadata)


def test_sandbox_api_manages_files_snapshots_and_destroy_with_audit_billing():
    identity, operator, creator = create_sandbox_identity()
    store = InMemoryControlPlaneStore()
    storage_client = RecordingS3Client()
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            store=store,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                client=storage_client,
            ),
        )
    )
    operator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}
    creator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": creator.id}
    run = client.post(
        "/api/runs",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "message": "Prepare a workspace file.",
            "mode": "workflow",
        },
    ).json()
    session = client.post(
        "/api/sandbox/sessions",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "run_id": run["run_id"],
            "image": "python:3.12",
            "network_mode": "disabled",
        },
    ).json()

    forbidden_upload = client.post(
        f"/api/sandbox/sessions/{session['id']}/files",
        headers=creator_headers,
        json={
            "path": "/workspace/report.txt",
            "content": "customer-secret",
            "content_type": "text/plain",
        },
    )
    uploaded = client.post(
        f"/api/sandbox/sessions/{session['id']}/files",
        headers=operator_headers,
        json={
            "path": "/workspace/report.txt",
            "content": "customer-secret",
            "content_type": "text/plain",
        },
    )
    downloaded = client.get(
        f"/api/sandbox/sessions/{session['id']}/files",
        headers=operator_headers,
        params={"path": "/workspace/report.txt"},
    )
    snapshot = client.post(
        f"/api/sandbox/sessions/{session['id']}/snapshot",
        headers=operator_headers,
    )
    destroyed = client.delete(
        f"/api/sandbox/sessions/{session['id']}",
        headers=operator_headers,
    )
    command_after_destroy = client.post(
        f"/api/sandbox/sessions/{session['id']}/commands",
        headers=operator_headers,
        json={"command": "python --version"},
    )
    meters = client.get("/api/billing/meters", headers=operator_headers)
    audits = client.get("/api/audit-events", headers=operator_headers)
    storage_objects = client.get(
        f"/api/runs/{run['run_id']}/storage-objects",
        headers=operator_headers,
    )

    assert forbidden_upload.status_code == 403
    assert uploaded.status_code == 201
    assert uploaded.json()["size_bytes"] == len("customer-secret")
    assert downloaded.status_code == 200
    assert downloaded.json()["content"] == "customer-secret"
    sandbox_file_objects = [
        storage_object
        for storage_object in storage_objects.json()
        if storage_object["purpose"] == "sandbox-files"
    ]
    assert len(sandbox_file_objects) == 1
    assert sandbox_file_objects[0]["content_type"] == "text/plain"
    assert sandbox_file_objects[0]["filename"] == "report.txt"
    assert sandbox_file_objects[0]["size_bytes"] == len("customer-secret")
    sandbox_file_content = client.get(
        f"/api/storage/objects/{sandbox_file_objects[0]['id']}/content",
        headers=operator_headers,
    )
    assert sandbox_file_content.status_code == 200
    assert sandbox_file_content.content == b"customer-secret"
    assert snapshot.status_code == 200
    assert snapshot.json()["uri"].endswith("/snapshot.json")
    snapshot_objects = [
        storage_object
        for storage_object in storage_objects.json()
        if storage_object["purpose"] == "sandbox-snapshots"
    ]
    assert len(snapshot_objects) == 1
    assert snapshot_objects[0]["content_type"] == "application/json"
    assert snapshot_objects[0]["filename"] == "snapshot.json"
    assert snapshot.json()["uri"] == (
        f"s3://{snapshot_objects[0]['bucket']}/{snapshot_objects[0]['key']}"
    )
    snapshot_content = client.get(
        f"/api/storage/objects/{snapshot_objects[0]['id']}/content",
        headers=operator_headers,
    )
    assert snapshot_content.status_code == 200
    assert snapshot_content.json()["session_id"] == session["id"]
    assert snapshot_content.json()["run_id"] == run["run_id"]
    sandbox_file_put = next(
        call for call in storage_client.put_calls if call["Key"] == sandbox_file_objects[0]["key"]
    )
    snapshot_put = next(
        call for call in storage_client.put_calls if call["Key"] == snapshot_objects[0]["key"]
    )
    assert sandbox_file_put["Bucket"] == "taroai-artifacts"
    assert sandbox_file_put["ContentType"] == "text/plain"
    assert sandbox_file_put["Body"] == b"customer-secret"
    assert snapshot_put["Bucket"] == "taroai-artifacts"
    assert snapshot_put["ContentType"] == "application/json"
    assert snapshot_objects[0]["size_bytes"] == len(snapshot_put["Body"])
    assert destroyed.status_code == 200
    assert destroyed.json()["status"] == "destroyed"
    assert command_after_destroy.status_code == 422
    artifact_meters = [
        meter for meter in meters.json() if meter["meter_type"] == "artifact_bytes"
    ]
    assert artifact_meters[0]["quantity"] == len("customer-secret")
    audit_events = [
        event
        for event in audits.json()
        if event["event_type"]
        in {
            "sandbox.file.uploaded",
            "sandbox.file.downloaded",
            "sandbox.snapshot.created",
            "sandbox.session.destroyed",
        }
    ]
    assert [event["event_type"] for event in audit_events] == [
        "sandbox.file.uploaded",
        "sandbox.file.downloaded",
        "sandbox.snapshot.created",
        "sandbox.session.destroyed",
    ]
    assert audit_events[0]["metadata"]["path"] == "/workspace/report.txt"
    assert audit_events[0]["metadata"]["size_bytes"] == len("customer-secret")
    assert audit_events[0]["metadata"]["storage_object_id"] == sandbox_file_objects[0]["id"]
    assert audit_events[2]["metadata"]["storage_object_id"] == snapshot_objects[0]["id"]
    assert "customer-secret" not in str(audit_events)


def test_default_browser_api_requires_configured_browser_provider():
    identity, operator, _creator = create_sandbox_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
        )
    )
    operator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}
    session = client.post(
        "/api/sandbox/sessions",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "image": "python:3.12",
            "network_mode": "disabled",
        },
    ).json()

    response = client.post(
        f"/api/browser/sessions/{session['id']}/actions",
        headers=operator_headers,
        json={"action_type": "navigate", "url": "https://example.com"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "browser_provider_unavailable"


def test_browser_api_applies_actions_with_permissions_audit_and_billing():
    identity, operator, creator = create_sandbox_identity()
    store = InMemoryControlPlaneStore()
    storage_client = RecordingS3Client()
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            browser_controller=InMemoryBrowserController(),
            store=store,
            storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="access",
                secret_access_key="secret",
                client=storage_client,
            ),
        )
    )
    operator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}
    creator_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": creator.id}
    run = client.post(
        "/api/runs",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "message": "Use a browser to inspect a vendor page.",
            "mode": "workflow",
        },
    ).json()
    session = client.post(
        "/api/sandbox/sessions",
        headers=operator_headers,
        json={
            "workspace_id": "workspace_sales",
            "run_id": run["run_id"],
            "image": "python:3.12",
            "network_mode": "disabled",
        },
    ).json()

    forbidden = client.post(
        f"/api/browser/sessions/{session['id']}/actions",
        headers=creator_headers,
        json={"action_type": "navigate", "url": "https://example.com"},
    )
    navigation = client.post(
        f"/api/browser/sessions/{session['id']}/actions",
        headers=operator_headers,
        json={"action_type": "navigate", "url": "https://example.com"},
    )
    typed = client.post(
        f"/api/browser/sessions/{session['id']}/actions",
        headers=operator_headers,
        json={
            "action_type": "type",
            "selector": "input[name='q']",
            "text": "secret-browser-query",
        },
    )
    screenshot = client.post(
        f"/api/browser/sessions/{session['id']}/actions",
        headers=operator_headers,
        json={"action_type": "screenshot"},
    )
    meters = client.get("/api/billing/meters", headers=operator_headers)
    audits = client.get("/api/audit-events", headers=operator_headers)
    storage_objects = client.get(
        f"/api/runs/{run['run_id']}/storage-objects",
        headers=operator_headers,
    )

    assert forbidden.status_code == 403
    assert navigation.status_code == 200
    assert navigation.json()["current_url"] == "https://example.com"
    assert typed.status_code == 200
    assert screenshot.status_code == 200
    assert screenshot.json()["screenshot_uri"].endswith(f"/browser/{session['id']}.png")
    screenshot_objects = [
        storage_object
        for storage_object in storage_objects.json()
        if storage_object["purpose"] == "browser"
    ]
    assert len(screenshot_objects) == 1
    assert screenshot_objects[0]["content_type"] == "image/png"
    assert screenshot_objects[0]["filename"] == f"{session['id']}.png"
    assert screenshot.json()["screenshot_uri"] == (
        f"s3://{screenshot_objects[0]['bucket']}/{screenshot_objects[0]['key']}"
    )
    screenshot_content = client.get(
        f"/api/storage/objects/{screenshot_objects[0]['id']}/content",
        headers=operator_headers,
    )
    assert screenshot_content.status_code == 200
    assert screenshot_content.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert storage_client.put_calls[0]["Bucket"] == "taroai-artifacts"
    assert storage_client.put_calls[0]["Key"] == screenshot_objects[0]["key"]
    assert storage_client.put_calls[0]["ContentType"] == "image/png"
    assert screenshot_objects[0]["size_bytes"] == len(storage_client.put_calls[0]["Body"])

    browser_meters = [
        meter for meter in meters.json() if meter["meter_type"] == "browser_action_count"
    ]
    assert [meter["quantity"] for meter in browser_meters] == [1, 1, 1]
    assert [meter["metadata"]["action_type"] for meter in browser_meters] == [
        "navigate",
        "type",
        "screenshot",
    ]

    browser_audits = [
        event for event in audits.json() if event["event_type"] == "browser.action.performed"
    ]
    assert [event["metadata"]["action_type"] for event in browser_audits] == [
        "navigate",
        "type",
        "screenshot",
    ]
    assert browser_audits[2]["metadata"]["storage_object_id"] == screenshot_objects[0]["id"]
    type_metadata = browser_audits[1]["metadata"]
    assert type_metadata["selector"] == "input[name='q']"
    assert type_metadata["has_text"] is True
    assert type_metadata["text_length"] == len("secret-browser-query")
    assert "secret-browser-query" not in str(browser_audits)

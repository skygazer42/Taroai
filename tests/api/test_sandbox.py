import base64
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from taroai.app import build_browser_controller, create_app
from taroai.config import Settings
from taroai.domain import RunCreate
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.licensing import Entitlement, LicenseKey, LicenseService, LicensedFeature
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
from taroai.sandbox.browser import BrowserProviderUnavailableError
from taroai.secrets import InMemorySecretService, SecretScope
from taroai.storage import InMemoryStorageCatalog, S3CompatibleObjectStorage
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import (
    ToolExecutionError,
    ToolGateway,
    ToolGatewayRequest,
    ToolSecretRequirement,
)
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


class RecordingBrowserProvider:
    def __init__(
        self,
        response_overrides: dict[str, object] | None = None,
        stale_delete_list: bool = False,
    ):
        self.requests: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.response_overrides = response_overrides or {}
        self.stale_delete_list = stale_delete_list
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, _error_type, _error, _traceback):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _handler(self):
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self._read_json()
                provider.requests.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                if self.path == "/sessions":
                    session = {
                        "session_id": body["session_id"],
                        "tenant_id": body["tenant_id"],
                        "workspace_id": body["workspace_id"],
                        "run_id": body["run_id"],
                        "current_url": None,
                        "actions": [],
                        "created_at": "2026-07-02T00:00:00+00:00",
                    }
                    provider.sessions[session["session_id"]] = session
                    self._write_json(201, provider._response_body("sessions", session))
                    return
                if self.path == "/actions":
                    session = provider.sessions[body["session_id"]]
                    if body["action_type"] == "navigate":
                        session["current_url"] = body["url"]
                    session["actions"].append(body)
                    observation = {
                        "tenant_id": body["tenant_id"],
                        "workspace_id": body["workspace_id"],
                        "run_id": body["run_id"],
                        "session_id": body["session_id"],
                        "action_type": body["action_type"],
                        "current_url": session["current_url"],
                        "text": "provider text" if body["action_type"] == "extract" else None,
                        "screenshot_uri": (
                            f"provider://browser/{body['session_id']}.png"
                            if body["action_type"] == "screenshot"
                            else None
                        ),
                        "screenshot_content_base64": (
                            base64.b64encode(b"browser-image").decode("ascii")
                            if body["action_type"] == "screenshot"
                            else None
                        ),
                        "metadata": body.get("metadata", {}),
                        "created_at": "2026-07-02T00:00:01+00:00",
                    }
                    self._write_json(
                        200,
                        provider._response_body("actions", observation),
                    )
                    return
                self._write_json(404, {"code": "not_found"})

            def do_GET(self):
                parsed = urlparse(self.path)
                provider.requests.append(
                    {
                        "method": "GET",
                        "path": parsed.path,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                if parsed.path.startswith("/sessions/"):
                    session_id = parsed.path.rsplit("/", 1)[-1]
                    tenant_id = parse_qs(parsed.query).get("tenant_id", [""])[0]
                    session = provider.sessions.get(session_id)
                    if session is None or session["tenant_id"] != tenant_id:
                        self._write_json(404, {"code": "not_found"})
                        return
                    self._write_json(200, provider._response_body("sessions", session))
                    return
                if parsed.path == "/sessions":
                    query = parse_qs(parsed.query)
                    if "tenant_id" in query:
                        tenant_id = query.get("tenant_id", [""])[0]
                        sessions = [
                            session
                            for session in provider.sessions.values()
                            if session["tenant_id"] == tenant_id
                        ]
                    else:
                        sessions = list(provider.sessions.values())
                    self._write_json(200, {"sessions": sessions})
                    return
                if parsed.path == "/capabilities":
                    capabilities = {
                        "provider": "playwright",
                        "auth_required": True,
                        "session_ttl_enforced": True,
                        "max_session_ttl_seconds": 1800,
                        "max_sessions": 50,
                        "max_sessions_per_tenant": 20,
                        "max_sessions_per_run": 3,
                        "navigation_allowlist_enforced": False,
                        "navigation_allowed_host_count": 0,
                    }
                    self._write_json(
                        200,
                        provider._response_body("capabilities", capabilities),
                    )
                    return
                self._write_json(404, {"code": "not_found"})

            def do_DELETE(self):
                parsed = urlparse(self.path)
                provider.requests.append(
                    {
                        "method": "DELETE",
                        "path": parsed.path,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                if parsed.path.startswith("/sessions/"):
                    session_id = parsed.path.rsplit("/", 1)[-1]
                    tenant_id = parse_qs(parsed.query).get("tenant_id", [""])[0]
                    session = provider.sessions.get(session_id)
                    if session is None or session["tenant_id"] != tenant_id:
                        self._write_json(404, {"code": "not_found"})
                        return
                    if not provider.stale_delete_list:
                        provider.sessions.pop(session_id, None)
                    if provider.response_overrides.get("delete_status_code") == 204:
                        self._write_empty(204)
                        return
                    self._write_json(200, provider._response_body("sessions", session))
                    return
                self._write_json(404, {"code": "not_found"})

            def log_message(self, _format, *_args):
                return

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def _write_json(self, status_code: int, body: dict) -> None:
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _write_empty(self, status_code: int) -> None:
                self.send_response(status_code)
                self.send_header("Content-Length", "0")
                self.end_headers()

        return Handler

    def _response_body(self, key: str, body: dict) -> dict:
        override = self.response_overrides.get(key, {})
        return body | (override if isinstance(override, dict) else {})


class RecordingSandboxCommandAdapter(InMemorySandboxAdapter):
    commands: list[SandboxCommand] = []

    def execute(self, command: SandboxCommand):
        self.commands.append(command)
        return super().execute(command)


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


def test_sandbox_tool_gateway_handler_injects_secret_lease_handles_without_secret_values():
    adapter = RecordingSandboxCommandAdapter()
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12",
        )
    )
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["sandbox.command"],
            actions=["read"],
        ),
    )
    gateway = ToolGateway(secret_service=secret_service)
    register_sandbox_tool_handlers(gateway, adapter)
    gateway.policies["sandbox.command"] = gateway.policies["sandbox.command"].model_copy(
        update={
            "secret_requirements": [
                ToolSecretRequirement(
                    secret_id=secret.id,
                    actions=["read"],
                    ttl_seconds=60,
                )
            ]
        }
    )

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

    command_env = adapter.commands[0].env
    lease_payload = json.loads(command_env["TAROAI_SECRET_LEASES"])
    assert result.output["exit_code"] == 0
    assert command_env["TAROAI_SECRET_LEASE_COUNT"] == "1"
    assert lease_payload[0]["secret_ref_id"] == secret.id
    assert lease_payload[0]["tool_name"] == "sandbox.command"
    assert lease_payload[0]["run_id"] == "run_1"
    assert lease_payload[0]["step_id"] == "step_code"
    assert lease_payload[0]["session_id"] == session.id
    assert lease_payload[0]["actions"] == ["read"]
    assert "lease_token" in lease_payload[0]
    assert "raw-secret-value" not in json.dumps(command_env, sort_keys=True)
    assert secret_service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease_payload[0]["lease_token"],
    ) == "raw-secret-value"


def test_sandbox_tool_gateway_handler_rejects_caller_secret_lease_env_override():
    adapter = RecordingSandboxCommandAdapter()
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12",
        )
    )
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["sandbox.command"],
            actions=["read"],
        ),
    )
    gateway = ToolGateway(secret_service=secret_service)
    register_sandbox_tool_handlers(gateway, adapter)
    gateway.policies["sandbox.command"] = gateway.policies["sandbox.command"].model_copy(
        update={
            "secret_requirements": [
                ToolSecretRequirement(secret_id=secret.id, actions=["read"])
            ]
        }
    )

    try:
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_code",
                tool_name="sandbox.command",
                tool_input={
                    "session_id": session.id,
                    "command": "python --version",
                    "env": {"TAROAI_SECRET_LEASES": "caller-controlled"},
                },
                granted_scopes=["sandbox.execute"],
            )
        )
    except ToolExecutionError as error:
        assert "reserved secret lease env" in str(error)
    else:
        raise AssertionError("caller-controlled secret lease env should be rejected")

    assert adapter.commands == []


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


def test_configured_browser_controller_uses_provider_protocol():
    with RecordingBrowserProvider() as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )

        session = controller.open_session(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="sandbox_1",
        )
        fetched = controller.get_session("tenant_acme", "sandbox_1")
        navigation = controller.apply(
            BrowserAction(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id="sandbox_1",
                action_type=BrowserActionType.NAVIGATE,
                url="https://example.com",
            )
        )
        screenshot = controller.apply(
            BrowserAction(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id="sandbox_1",
                action_type=BrowserActionType.SCREENSHOT,
            )
        )
        deleted = controller.delete_session("tenant_acme", "sandbox_1")

    assert session.session_id == "sandbox_1"
    assert fetched.session_id == "sandbox_1"
    assert deleted.session_id == "sandbox_1"
    assert navigation.current_url == "https://example.com"
    assert screenshot.screenshot_uri == "provider://browser/sandbox_1.png"
    assert screenshot.screenshot_content == b"browser-image"
    assert [request["authorization"] for request in provider.requests] == [
        "Bearer provider_secret",
        "Bearer provider_secret",
        "Bearer provider_secret",
        "Bearer provider_secret",
        "Bearer provider_secret",
        "Bearer provider_secret",
        "Bearer provider_secret",
        "Bearer provider_secret",
    ]
    assert [request["path"] for request in provider.requests] == [
        "/capabilities",
        "/sessions",
        "/sessions",
        "/sessions/sandbox_1",
        "/actions",
        "/actions",
        "/sessions/sandbox_1",
        "/sessions",
    ]


def test_configured_browser_controller_rejects_cross_tenant_session_response():
    with RecordingBrowserProvider(
        response_overrides={"sessions": {"tenant_id": "tenant_other"}}
    ) as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            BrowserProviderUnavailableError,
            match="browser provider response context mismatch",
        ):
            controller.open_session(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id="sandbox_1",
            )


def test_configured_browser_controller_rejects_cross_session_action_response():
    with RecordingBrowserProvider(
        response_overrides={"actions": {"session_id": "sandbox_other"}}
    ) as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )
        controller.open_session(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="sandbox_1",
        )

        with pytest.raises(
            BrowserProviderUnavailableError,
            match="browser provider response context mismatch",
        ):
            controller.apply(
                BrowserAction(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    session_id="sandbox_1",
                    action_type=BrowserActionType.SCREENSHOT,
                )
            )


def test_configured_browser_controller_requires_controller_capabilities():
    with RecordingBrowserProvider(
        response_overrides={
            "capabilities": {
                "session_ttl_enforced": False,
                "max_session_ttl_seconds": 0,
            },
        }
    ) as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            BrowserProviderUnavailableError,
            match="browser controller capabilities are insufficient",
        ):
            controller.open_session(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id="sandbox_1",
            )

    assert [request["path"] for request in provider.requests] == ["/capabilities"]


def test_configured_browser_controller_enforces_global_session_capacity():
    with RecordingBrowserProvider(
        response_overrides={"capabilities": {"max_sessions": 1}}
    ) as provider:
        provider.sessions["browser_existing"] = {
            "session_id": "browser_existing",
            "tenant_id": "tenant_other",
            "workspace_id": "workspace_support",
            "run_id": "run_9",
            "current_url": None,
            "actions": [],
            "created_at": "2026-07-02T00:00:00+00:00",
        }
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            BrowserProviderUnavailableError,
            match="browser controller session capacity is full",
        ):
            controller.open_session(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id="sandbox_1",
            )

    assert [request["path"] for request in provider.requests] == [
        "/capabilities",
        "/sessions",
    ]


def test_configured_browser_controller_rejects_empty_delete_response():
    with RecordingBrowserProvider(
        response_overrides={"delete_status_code": 204}
    ) as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )
        controller.open_session(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="sandbox_1",
        )

        with pytest.raises(
            BrowserProviderUnavailableError,
            match="browser provider delete response must include session",
        ):
            controller.delete_session("tenant_acme", "sandbox_1")


def test_configured_browser_controller_rejects_delete_when_session_list_stays_active():
    with RecordingBrowserProvider(stale_delete_list=True) as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )
        controller.open_session(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="sandbox_1",
        )

        with pytest.raises(
            BrowserProviderUnavailableError,
            match="browser provider did not confirm deleted session",
        ):
            controller.delete_session("tenant_acme", "sandbox_1")


def test_configured_browser_controller_lists_tenant_scoped_provider_sessions():
    with RecordingBrowserProvider() as provider:
        controller = build_browser_controller(
            Settings(
                browser_provider="playwright",
                browser_controller_base_url=provider.url,
                browser_controller_api_key="provider_secret",
                browser_controller_timeout_seconds=3,
            )
        )
        controller.open_session(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="sandbox_1",
        )
        controller.open_session(
            tenant_id="tenant_other",
            workspace_id="workspace_support",
            run_id="run_2",
            session_id="sandbox_2",
        )

        sessions = controller.list_sessions("tenant_acme")

    assert [session.session_id for session in sessions] == ["sandbox_1"]
    assert provider.requests[-1]["method"] == "GET"
    assert provider.requests[-1]["path"] == "/sessions"


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


def test_sandbox_api_enforces_license_concurrency_limit():
    identity, operator, _creator = create_sandbox_identity()
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_private",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.SANDBOX_CONCURRENCY, limit=1),
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365),
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            license_service=license_service,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}
    payload = {
        "workspace_id": "workspace_sales",
        "run_id": "run_1",
        "image": "python:3.12",
        "network_mode": "disabled",
    }

    first = client.post("/api/sandbox/sessions", headers=headers, json=payload)
    second = client.post(
        "/api/sandbox/sessions",
        headers=headers,
        json=payload | {"run_id": "run_2"},
    )
    destroyed = client.delete(f"/api/sandbox/sessions/{first.json()['id']}", headers=headers)
    third = client.post(
        "/api/sandbox/sessions",
        headers=headers,
        json=payload | {"run_id": "run_3"},
    )

    assert first.status_code == 201
    assert second.status_code == 403
    assert second.json()["code"] == "license_entitlement_denied"
    assert "sandbox_concurrency" in second.json()["message"]
    assert destroyed.status_code == 200
    assert third.status_code == 201


def test_sandbox_secret_lease_resolve_endpoint_returns_value_without_audit_leakage():
    identity, operator, _creator = create_sandbox_identity()
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["sandbox.command"],
            actions=["read"],
        ),
    )
    lease = secret_service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="sandbox.command",
        actions=["read"],
        ttl_seconds=60,
        run_id="run_1",
        step_id="step_code",
        session_id="sandbox_1",
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            secret_service=secret_service,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}

    response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "step_id": "step_code",
            "session_id": "sandbox_1",
            "lease_token": lease.lease_token,
            "action": "read",
        },
    )
    audits = client.get(
        "/api/audit-events?event_type=secret.lease.resolved",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "raw-secret-value"
    assert body["lease_id"] == lease.id
    assert body["secret_ref_id"] == secret.id
    assert body["tool_name"] == "sandbox.command"
    assert audits.status_code == 200
    audit_events = audits.json()
    assert audit_events[0]["metadata"]["lease_id"] == lease.id
    assert audit_events[0]["metadata"]["secret_ref_id"] == secret.id
    assert lease.lease_token not in str(audit_events)
    assert "raw-secret-value" not in str(audit_events)


def test_sandbox_secret_lease_resolve_endpoint_requires_provider_token_when_configured():
    identity, operator, _creator = create_sandbox_identity()
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["sandbox.command"],
            actions=["read"],
        ),
    )
    lease = secret_service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="sandbox.command",
        actions=["read"],
        ttl_seconds=60,
        run_id="run_1",
        step_id="step_code",
        session_id="sandbox_1",
    )
    client = TestClient(
        create_app(
            settings=Settings(sandbox_secret_resolver_token="resolver_secret", _env_file=None),
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            secret_service=secret_service,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id}
    payload = {
        "workspace_id": "workspace_sales",
        "run_id": "run_1",
        "step_id": "step_code",
        "session_id": "sandbox_1",
        "lease_token": lease.lease_token,
        "action": "read",
    }

    missing_response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers=headers,
        json=payload,
    )
    wrong_response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers={**headers, "X-Sandbox-Resolver-Token": "wrong"},
        json=payload,
    )
    success_response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers={**headers, "X-Sandbox-Resolver-Token": "resolver_secret"},
        json=payload,
    )

    assert missing_response.status_code == 403
    assert missing_response.json()["code"] == "tenant_access_denied"
    assert wrong_response.status_code == 403
    assert wrong_response.json()["code"] == "tenant_access_denied"
    assert lease.lease_token not in missing_response.text
    assert lease.lease_token not in wrong_response.text
    assert "raw-secret-value" not in missing_response.text
    assert "raw-secret-value" not in wrong_response.text
    assert success_response.status_code == 200
    assert success_response.json()["value"] == "raw-secret-value"


def test_sandbox_secret_lease_resolve_endpoint_rejects_non_sandbox_tool_lease():
    identity, operator, _creator = create_sandbox_identity()
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
    )
    lease = secret_service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="crm.lookup",
        actions=["read"],
        ttl_seconds=60,
        run_id="run_1",
        step_id="step_code",
        session_id="sandbox_1",
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            secret_service=secret_service,
        )
    )

    response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id},
        json={
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "step_id": "step_code",
            "session_id": "sandbox_1",
            "lease_token": lease.lease_token,
            "action": "read",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "secret_access_denied"
    assert lease.lease_token not in response.text
    assert "raw-secret-value" not in response.text


def test_sandbox_secret_lease_resolve_endpoint_rejects_run_step_session_mismatch():
    identity, operator, _creator = create_sandbox_identity()
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["sandbox.command"],
            actions=["read"],
        ),
    )
    lease = secret_service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="sandbox.command",
        actions=["read"],
        ttl_seconds=60,
        run_id="run_1",
        step_id="step_code",
        session_id="sandbox_1",
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            secret_service=secret_service,
        )
    )

    response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id},
        json={
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "step_id": "step_other",
            "session_id": "sandbox_1",
            "lease_token": lease.lease_token,
            "action": "read",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "secret_access_denied"
    assert lease.lease_token not in response.text
    assert "raw-secret-value" not in response.text


def test_sandbox_secret_lease_resolve_endpoint_rejects_missing_session_for_session_bound_lease():
    identity, operator, _creator = create_sandbox_identity()
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="raw-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["sandbox.command"],
            actions=["read"],
        ),
    )
    lease = secret_service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="sandbox.command",
        actions=["read"],
        ttl_seconds=60,
        run_id="run_1",
        step_id="step_code",
        session_id="sandbox_1",
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            sandbox_adapter=InMemorySandboxAdapter(),
            secret_service=secret_service,
        )
    )

    response = client.post(
        "/api/sandbox/secret-leases/resolve",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": operator.id},
        json={
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "step_id": "step_code",
            "lease_token": lease.lease_token,
            "action": "read",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "secret_access_denied"
    assert lease.lease_token not in response.text
    assert "raw-secret-value" not in response.text


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

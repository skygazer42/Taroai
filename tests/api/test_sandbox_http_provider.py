import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from taroai.config import Settings
from taroai.sandbox import (
    SandboxCommand,
    SandboxCreateRequest,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSessionStatus,
    build_sandbox_adapter,
)
from taroai.sandbox.adapter import SandboxProviderUnavailableError


class RecordingSandboxProvider:
    def __init__(
        self,
        response_overrides: dict[str, dict] | None = None,
        stale_destroy_list: bool = False,
    ):
        self.requests: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.files: dict[tuple[str, str], dict] = {}
        self.response_overrides = response_overrides or {}
        self.stale_destroy_list = stale_destroy_list
        self.destroyed_session_ids: set[str] = set()
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
                        "id": "sandbox_1",
                        "tenant_id": body["tenant_id"],
                        "workspace_id": body["workspace_id"],
                        "run_id": body["run_id"],
                        "provider": "k8s",
                        "image": body["image"],
                        "network_mode": body["network_mode"],
                        "timeout_seconds": body["timeout_seconds"],
                        "status": "active",
                        "metadata": body.get("metadata", {}),
                        "created_at": "2026-07-02T00:00:00+00:00",
                    }
                    provider.sessions[session["id"]] = session
                    self._write_json(201, provider._response_body("sessions", session))
                    return
                if self.path == "/commands":
                    result = {
                        "tenant_id": body["tenant_id"],
                        "workspace_id": body["workspace_id"],
                        "run_id": body["run_id"],
                        "session_id": body["session_id"],
                        "command": body["command"],
                        "exit_code": 0,
                        "stdout": "hello from provider\n",
                        "stderr": "",
                        "output_uri": "s3://tenant/runs/run_1/sandbox-output.txt",
                        "created_at": "2026-07-02T00:00:01+00:00",
                    }
                    self._write_json(200, provider._response_body("commands", result))
                    return
                if self.path == "/files":
                    file_ref = {
                        "tenant_id": body["tenant_id"],
                        "workspace_id": body["workspace_id"],
                        "run_id": body["run_id"],
                        "session_id": body["session_id"],
                        "path": body["path"],
                        "content_type": body.get("content_type", "text/plain"),
                        "size_bytes": len(body.get("content", "").encode("utf-8")),
                        "content": body.get("content", ""),
                        "created_at": "2026-07-02T00:00:02+00:00",
                    }
                    provider.files[(body["session_id"], body["path"])] = file_ref
                    self._write_json(201, provider._response_body("files", file_ref))
                    return
                if self.path == "/snapshots":
                    snapshot = {
                        "id": "sandbox_snapshot_1",
                        "tenant_id": body["tenant_id"],
                        "workspace_id": body["workspace_id"],
                        "run_id": body["run_id"],
                        "session_id": body["session_id"],
                        "uri": f"s3://tenant/snapshots/{body['session_id']}.json",
                        "created_at": "2026-07-02T00:00:03+00:00",
                    }
                    self._write_json(201, provider._response_body("snapshots", snapshot))
                    return
                self._write_json(404, {"code": "not_found"})

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                provider.requests.append(
                    {
                        "method": "GET",
                        "path": parsed.path,
                        "query": query,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                if parsed.path.startswith("/sessions/"):
                    session_id = parsed.path.rsplit("/", 1)[-1]
                    session = provider.sessions.get(session_id)
                    tenant_id = query.get("tenant_id", [""])[0]
                    if session is None or session["tenant_id"] != tenant_id:
                        self._write_json(404, {"code": "not_found"})
                        return
                    self._write_json(200, provider._response_body("sessions", session))
                    return
                if parsed.path == "/sessions":
                    tenant_id = query.get("tenant_id", [None])[0]
                    sessions = []
                    for session in provider.sessions.values():
                        if tenant_id is not None and session["tenant_id"] != tenant_id:
                            continue
                        sessions.append(
                            session | {"status": "active", "destroyed_at": None}
                            if provider.stale_destroy_list
                            and session["id"] in provider.destroyed_session_ids
                            else session
                        )
                    self._write_json(
                        200,
                        provider._response_body(
                            "session_list",
                            {"sessions": sessions},
                        ),
                    )
                    return
                if parsed.path == "/capabilities":
                    self._write_json(
                        200,
                        provider._response_body(
                            "capabilities",
                            {
                                "provider": "k8s",
                                "network_isolation": True,
                                "filesystem_isolation": True,
                                "resource_limits": True,
                                "destroy_supported": True,
                                "session_ttl_enforced": True,
                                "runtime_isolation": True,
                                "image_policy_enforced": True,
                                "allowed_image_count": 1,
                                "max_session_ttl_seconds": 600,
                                "max_sessions": 5,
                                "max_sessions_per_tenant": 2,
                                "max_sessions_per_run": 1,
                            },
                        ),
                    )
                    return
                if parsed.path == "/files":
                    session_id = query.get("session_id", [""])[0]
                    path = query.get("path", [None])[0]
                    if path is not None:
                        file_ref = provider.files[(session_id, unquote(path))]
                        self._write_json(200, provider._response_body("files", file_ref))
                        return
                    files = [
                        file_ref
                        for (file_session_id, _path), file_ref in provider.files.items()
                        if file_session_id == session_id
                    ]
                    self._write_json(
                        200,
                        provider._response_body("file_list", {"files": files}),
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
                    session = provider.sessions[session_id]
                    session["status"] = "destroyed"
                    session["destroyed_at"] = "2026-07-02T00:00:04+00:00"
                    provider.destroyed_session_ids.add(session_id)
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

        return Handler

    def _response_body(self, key: str, body: dict) -> dict:
        return body | self.response_overrides.get(key, {})


def test_enterprise_sandbox_provider_uses_controller_protocol():
    with RecordingSandboxProvider() as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )
        capabilities = adapter.get_capabilities()
        session = adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=30,
            )
        )
        uploaded = adapter.upload_file(
            SandboxFileWrite(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=session.id,
                path="/workspace/artifacts/report.txt",
                content="hello artifact",
            )
        )
        files = adapter.list_files("tenant_acme", session.id)
        downloaded = adapter.download_file(
            "tenant_acme",
            session.id,
            "/workspace/artifacts/report.txt",
        )
        result = adapter.execute(
            SandboxCommand(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=session.id,
                command="cat /workspace/artifacts/report.txt",
            )
        )
        snapshot = adapter.snapshot("tenant_acme", session.id)
        fetched = adapter.get_session("tenant_acme", session.id)
        destroyed = adapter.destroy("tenant_acme", session.id)

    assert capabilities.provider == "k8s"
    assert capabilities.network_isolation is True
    assert capabilities.filesystem_isolation is True
    assert capabilities.resource_limits is True
    assert capabilities.destroy_supported is True
    assert capabilities.session_ttl_enforced is True
    assert capabilities.runtime_isolation is True
    assert capabilities.image_policy_enforced is True
    assert capabilities.allowed_image_count == 1
    assert capabilities.max_session_ttl_seconds == 600
    assert capabilities.max_sessions == 5
    assert capabilities.max_sessions_per_tenant == 2
    assert capabilities.max_sessions_per_run == 1
    assert session.provider == "k8s"
    assert uploaded.path == "/workspace/artifacts/report.txt"
    assert [file.path for file in files] == ["/workspace/artifacts/report.txt"]
    assert downloaded.content == "hello artifact"
    assert result.exit_code == 0
    assert result.stdout == "hello from provider\n"
    assert snapshot.uri.endswith("/sandbox_1.json")
    snapshot_request = next(
        request
        for request in provider.requests
        if request["method"] == "POST" and request["path"] == "/snapshots"
    )
    assert snapshot_request["body"] == {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
        "run_id": "run_1",
        "session_id": session.id,
    }
    assert fetched.id == session.id
    assert destroyed.status == SandboxSessionStatus.DESTROYED
    assert {request["authorization"] for request in provider.requests} == {
        "Bearer sandbox_secret"
    }
    file_read_queries = [
        request["query"]
        for request in provider.requests
        if request["method"] == "GET" and request["path"] == "/files"
    ]
    assert file_read_queries == [
        {
            "tenant_id": ["tenant_acme"],
            "session_id": ["sandbox_1"],
            "workspace_id": ["workspace_sales"],
            "run_id": ["run_1"],
        },
        {
            "tenant_id": ["tenant_acme"],
            "session_id": ["sandbox_1"],
            "workspace_id": ["workspace_sales"],
            "run_id": ["run_1"],
            "path": ["/workspace/artifacts/report.txt"],
        },
    ]
    assert [request["path"] for request in provider.requests] == [
        "/capabilities",
        "/sessions",
        "/sessions",
        "/files",
        "/sessions/sandbox_1",
        "/files",
        "/sessions/sandbox_1",
        "/files",
        "/commands",
        "/sessions/sandbox_1",
        "/snapshots",
        "/sessions/sandbox_1",
        "/sessions/sandbox_1",
        "/sessions",
    ]


def test_enterprise_sandbox_provider_rejects_cross_tenant_command_response():
    with RecordingSandboxProvider(
        response_overrides={"commands": {"tenant_id": "tenant_other"}}
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )
        session = adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=30,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox provider response context mismatch",
        ):
            adapter.execute(
                SandboxCommand(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    session_id=session.id,
                    command="cat /workspace/artifacts/report.txt",
                )
            )


def test_enterprise_sandbox_provider_rejects_destroy_response_when_session_stays_active():
    with RecordingSandboxProvider(
        response_overrides={"sessions": {"status": "active", "destroyed_at": None}}
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )
        session = adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=30,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox provider did not destroy session",
        ):
            adapter.destroy("tenant_acme", session.id)


def test_enterprise_sandbox_provider_rejects_destroy_when_session_list_stays_active():
    with RecordingSandboxProvider(stale_destroy_list=True) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )
        session = adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=30,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox provider did not confirm destroyed session",
        ):
            adapter.destroy("tenant_acme", session.id)


def test_enterprise_sandbox_provider_rejects_create_response_when_session_not_active():
    with RecordingSandboxProvider(
        response_overrides={"sessions": {"status": "destroyed"}}
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox provider did not create an active session",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=30,
                )
            )


def test_enterprise_sandbox_provider_rejects_create_response_with_wrong_provider():
    with RecordingSandboxProvider(
        response_overrides={"sessions": {"provider": "docker"}}
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox provider response context mismatch",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=30,
                )
            )


def test_enterprise_sandbox_provider_rejects_session_create_without_capabilities():
    with RecordingSandboxProvider(
        response_overrides={
            "capabilities": {
                "filesystem_isolation": False,
                "resource_limits": False,
            }
        }
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox controller capabilities are insufficient",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=30,
                )
            )

    assert [request["path"] for request in provider.requests] == ["/capabilities"]


def test_enterprise_sandbox_provider_rejects_create_without_runtime_or_image_policy():
    with RecordingSandboxProvider(
        response_overrides={
            "capabilities": {
                "runtime_isolation": False,
                "image_policy_enforced": False,
                "allowed_image_count": 0,
            }
        }
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="runtime_isolation, image_policy_enforced, allowed_image_count",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=30,
                )
            )

    assert [request["path"] for request in provider.requests] == ["/capabilities"]


def test_enterprise_sandbox_provider_rejects_capabilities_from_wrong_provider():
    with RecordingSandboxProvider(
        response_overrides={"capabilities": {"provider": "docker"}}
    ) as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox provider response context mismatch",
        ):
            adapter.get_capabilities()


def test_enterprise_sandbox_provider_rejects_create_when_ttl_exceeds_controller_limit():
    with RecordingSandboxProvider() as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox session timeout exceeds controller limit",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=601,
                )
            )

    assert [request["path"] for request in provider.requests] == ["/capabilities"]


def test_enterprise_sandbox_provider_rejects_create_when_tenant_capacity_is_full():
    with RecordingSandboxProvider(
        response_overrides={"capabilities": {"max_sessions_per_tenant": 1}}
    ) as provider:
        provider.sessions["sandbox_existing"] = {
            "id": "sandbox_existing",
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_existing",
            "provider": "k8s",
            "image": "python:3.12-slim",
            "network_mode": "disabled",
            "timeout_seconds": 30,
            "status": "active",
            "metadata": {},
            "created_at": "2026-07-02T00:00:00+00:00",
        }
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox controller tenant session capacity is full",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=30,
                )
            )

    assert [request["path"] for request in provider.requests] == [
        "/capabilities",
        "/sessions",
    ]


def test_enterprise_sandbox_provider_rejects_create_when_global_capacity_is_full():
    with RecordingSandboxProvider(
        response_overrides={"capabilities": {"max_sessions": 1}}
    ) as provider:
        provider.sessions["sandbox_existing"] = {
            "id": "sandbox_existing",
            "tenant_id": "tenant_other",
            "workspace_id": "workspace_support",
            "run_id": "run_existing",
            "provider": "k8s",
            "image": "python:3.12-slim",
            "network_mode": "disabled",
            "timeout_seconds": 30,
            "status": "active",
            "metadata": {},
            "created_at": "2026-07-02T00:00:00+00:00",
        }
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )

        with pytest.raises(
            SandboxProviderUnavailableError,
            match="sandbox controller session capacity is full",
        ):
            adapter.create(
                SandboxCreateRequest(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    image="python:3.12-slim",
                    network_mode=SandboxNetworkMode.DISABLED,
                    timeout_seconds=30,
                )
            )

    assert [request["path"] for request in provider.requests] == [
        "/capabilities",
        "/sessions",
    ]


def test_enterprise_sandbox_provider_lists_controller_sessions_for_concurrency():
    with RecordingSandboxProvider() as provider:
        adapter = build_sandbox_adapter(
            Settings(
                _env_file=None,
                sandbox_provider="k8s",
                sandbox_controller_base_url=provider.url,
                sandbox_controller_api_key="sandbox_secret",
                sandbox_controller_timeout_seconds=3,
            )
        )
        first_session = adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=30,
            )
        )
        provider.sessions["sandbox_destroyed"] = first_session.model_dump(
            mode="json"
        ) | {
            "id": "sandbox_destroyed",
            "status": "destroyed",
        }

        sessions = adapter.list_sessions("tenant_acme")

    assert [session.id for session in sessions] == ["sandbox_1", "sandbox_destroyed"]
    assert [session.status.value for session in sessions] == ["active", "destroyed"]
    assert provider.requests[-1]["method"] == "GET"
    assert provider.requests[-1]["path"] == "/sessions"
    assert provider.requests[-1]["authorization"] == "Bearer sandbox_secret"

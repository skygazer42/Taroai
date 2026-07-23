import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

from pydantic import PrivateAttr, ValidationError
import pytest

from taroai.deployment.install_evidence import SandboxLifecycleVerificationResult
from taroai.errors import NotFoundError
from taroai.sandbox.adapter import SandboxAdapter, SandboxProviderUnavailableError
from taroai.sandbox import lifecycle_verification as lifecycle_verification_module
from taroai.sandbox.lifecycle_verification import (
    SandboxLifecycleVerificationConfig,
    main,
    verify_sandbox_lifecycle,
)
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxSession,
    SandboxSessionStatus,
)


class RecordingSandboxAdapter(SandboxAdapter):
    provider: str = "k8s"

    _sessions: dict[str, SandboxSession] = PrivateAttr(default_factory=dict)
    _files: dict[str, SandboxFileRef] = PrivateAttr(default_factory=dict)
    _calls: list[tuple] = PrivateAttr(default_factory=list)

    @property
    def calls(self) -> list[tuple]:
        return self._calls

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        self._calls.append(("create", request.tenant_id, request.workspace_id, request.run_id))
        session = SandboxSession(
            id="sandbox_verify_1",
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            provider=self.provider,
            image=request.image,
            network_mode=request.network_mode,
            timeout_seconds=request.timeout_seconds,
        )
        self._sessions[session.id] = session
        return session

    def get_capabilities(self) -> dict:
        self._calls.append(("capabilities",))
        return {
            "provider": self.provider,
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
        }

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        self._calls.append(("execute", command.session_id, command.cwd))
        session = self._sessions[command.session_id]
        if session.status != SandboxSessionStatus.ACTIVE:
            raise NotFoundError(f"Sandbox session not found: {command.session_id}")
        if session.workspace_id != command.workspace_id or session.run_id != command.run_id:
            raise NotFoundError(f"Sandbox session not found: {command.session_id}")
        self._files["/workspace/artifacts/sandbox-lifecycle.txt"] = SandboxFileRef(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            path="/workspace/artifacts/sandbox-lifecycle.txt",
            size_bytes=len("sandbox lifecycle ok\n"),
            content="sandbox lifecycle ok\n",
        )
        return SandboxCommandResult(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            command=command.command,
            exit_code=0,
            stdout="customer-secret sandbox lifecycle ok",
        )

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        self._calls.append(
            ("upload_file", file_write.tenant_id, file_write.session_id, file_write.path)
        )
        session = self._sessions[file_write.session_id]
        if session.workspace_id != file_write.workspace_id or session.run_id != file_write.run_id:
            raise NotFoundError(f"Sandbox session not found: {file_write.session_id}")
        file_ref = SandboxFileRef(
            tenant_id=file_write.tenant_id,
            workspace_id=file_write.workspace_id,
            run_id=file_write.run_id,
            session_id=file_write.session_id,
            path=file_write.path,
            size_bytes=len(file_write.content),
            content=file_write.content,
            content_type=file_write.content_type,
        )
        self._files[file_write.path] = file_ref
        return file_ref

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        self._calls.append(("destroy", tenant_id, session_id))
        session = self._sessions[session_id]
        destroyed = session.model_copy(
            update={"status": SandboxSessionStatus.DESTROYED}
        )
        self._sessions[session_id] = destroyed
        return destroyed

    def list_sessions(self, tenant_id: str) -> list[SandboxSession]:
        self._calls.append(("list_sessions", tenant_id))
        return [
            session
            for session in self._sessions.values()
            if session.tenant_id == tenant_id
        ]

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        self._calls.append(("list_files", tenant_id, session_id))
        return [
            file_ref
            for file_ref in self._files.values()
            if file_ref.tenant_id == tenant_id and file_ref.session_id == session_id
        ]

    def download_file(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
    ) -> SandboxFileRef:
        self._calls.append(("download_file", tenant_id, session_id, path))
        file_ref = self._files[path]
        assert file_ref.tenant_id == tenant_id
        assert file_ref.session_id == session_id
        return file_ref


class FailingSandboxAdapter(RecordingSandboxAdapter):
    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        self._calls.append(("execute", command.session_id, command.cwd))
        return SandboxCommandResult(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            command=command.command,
            exit_code=2,
            stderr="customer-secret command failed",
        )

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        self._calls.append(("destroy", tenant_id, session_id))
        raise SandboxProviderUnavailableError("sandbox destroy failed")


class IncompleteCapabilitySandboxAdapter(RecordingSandboxAdapter):
    def get_capabilities(self) -> dict:
        self._calls.append(("capabilities",))
        return {
            "provider": self.provider,
            "network_isolation": True,
            "filesystem_isolation": False,
            "resource_limits": False,
            "destroy_supported": True,
            "session_ttl_enforced": False,
            "runtime_isolation": False,
            "image_policy_enforced": False,
            "allowed_image_count": 0,
            "max_session_ttl_seconds": None,
            "max_sessions": None,
            "max_sessions_per_tenant": None,
            "max_sessions_per_run": None,
        }


class ScopeProbeUnavailableSandboxAdapter(RecordingSandboxAdapter):
    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        if command.workspace_id.endswith("_scope_probe"):
            self._calls.append(("execute", command.session_id, command.cwd))
            raise SandboxProviderUnavailableError("sandbox command failed before scope check")
        return super().execute(command)

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        if file_write.workspace_id.endswith("_scope_probe"):
            self._calls.append(
                ("upload_file", file_write.tenant_id, file_write.session_id, file_write.path)
            )
            raise SandboxProviderUnavailableError("sandbox upload failed before scope check")
        return super().upload_file(file_write)


class StaleDestroyListSandboxAdapter(RecordingSandboxAdapter):
    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        self._calls.append(("destroy", tenant_id, session_id))
        return self._sessions[session_id].model_copy(
            update={"status": SandboxSessionStatus.DESTROYED}
        )


class PostDestroyPermissiveSandboxAdapter(RecordingSandboxAdapter):
    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        self._calls.append(("execute", command.session_id, command.cwd))
        session = self._sessions[command.session_id]
        if session.workspace_id != command.workspace_id or session.run_id != command.run_id:
            raise NotFoundError(f"Sandbox session not found: {command.session_id}")
        return SandboxCommandResult(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            command=command.command,
            exit_code=0,
            stdout="command still ran after destroy",
        )


def test_sandbox_lifecycle_verification_generates_install_validation_result(
    monkeypatch,
):
    adapter = RecordingSandboxAdapter(provider="k8s")
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_snapshot_scope",
        lambda _config, _session_id: True,
        raising=False,
    )
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_file_read_scope",
        lambda _config, _session_id: True,
        raising=False,
    )
    config = SandboxLifecycleVerificationConfig(
        base_url="http://sandbox.local",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        image="python:3.12-slim",
    )

    result = verify_sandbox_lifecycle(config, adapter=adapter)

    assert result == SandboxLifecycleVerificationResult(
        provider="k8s",
        session_id="sandbox_verify_1",
        session_created=True,
        command_executed=True,
        session_destroyed=True,
        session_destroy_confirmed=True,
        post_destroy_command_blocked=True,
        output_redacted=True,
        command_scope_enforced=True,
        file_scope_enforced=True,
        file_read_scope_enforced=True,
        snapshot_scope_enforced=True,
        artifact_path="/workspace/artifacts/sandbox-lifecycle.txt",
        artifact_listed=True,
        artifact_downloaded=True,
        downloaded_artifact_content_length=len("sandbox lifecycle ok\n"),
        capabilities_checked=True,
        network_isolation_declared=True,
        filesystem_isolation_declared=True,
        resource_limits_declared=True,
        destroy_supported_declared=True,
        session_ttl_enforced_declared=True,
        runtime_isolation_declared=True,
        image_policy_enforced_declared=True,
        allowed_image_count=1,
        max_session_ttl_seconds_declared=True,
        max_sessions_declared=True,
        max_sessions_per_tenant_declared=True,
        max_sessions_per_run_declared=True,
        session_listed=True,
        tenant_session_scope_enforced=True,
        auth_challenge_enforced=False,
    )
    assert adapter.calls == [
        ("capabilities",),
        ("create", "tenant_acme", "workspace_sales", "run_1"),
        ("execute", "sandbox_verify_1", "/workspace"),
        ("upload_file", "tenant_acme", "sandbox_verify_1", "/workspace/artifacts/scope-probe.txt"),
        ("list_sessions", "tenant_acme"),
        ("list_sessions", "tenant_sandbox_verify_denied"),
        ("execute", "sandbox_verify_1", "/workspace"),
        ("list_files", "tenant_acme", "sandbox_verify_1"),
        (
            "download_file",
            "tenant_acme",
            "sandbox_verify_1",
            "/workspace/artifacts/sandbox-lifecycle.txt",
        ),
        ("destroy", "tenant_acme", "sandbox_verify_1"),
        ("execute", "sandbox_verify_1", "/workspace"),
        ("list_sessions", "tenant_acme"),
    ]


def test_sandbox_lifecycle_verification_fails_without_isolation_capabilities():
    adapter = IncompleteCapabilitySandboxAdapter(provider="k8s")

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.session_created is True
    assert result.command_executed is True
    assert result.session_destroyed is True
    assert result.capabilities_checked is True
    assert result.network_isolation_declared is True
    assert result.filesystem_isolation_declared is False
    assert result.resource_limits_declared is False
    assert result.destroy_supported_declared is True
    assert result.session_ttl_enforced_declared is False
    assert result.runtime_isolation_declared is False
    assert result.image_policy_enforced_declared is False
    assert result.allowed_image_count == 0
    assert result.max_session_ttl_seconds_declared is False
    assert result.max_sessions_declared is False
    assert result.max_sessions_per_tenant_declared is False
    assert result.max_sessions_per_run_declared is False
    assert result.session_listed is True
    assert result.tenant_session_scope_enforced is True
    assert result.artifact_path == "/workspace/artifacts/sandbox-lifecycle.txt"
    assert result.artifact_listed is True
    assert result.artifact_downloaded is True
    assert result.downloaded_artifact_content_length == len("sandbox lifecycle ok\n")


def test_sandbox_lifecycle_verification_records_runtime_and_image_policy_capabilities(
    monkeypatch,
):
    class RuntimePolicySandboxAdapter(RecordingSandboxAdapter):
        def get_capabilities(self) -> dict:
            self._calls.append(("capabilities",))
            return {
                "provider": self.provider,
                "network_isolation": True,
                "filesystem_isolation": True,
                "resource_limits": True,
                "destroy_supported": True,
                "session_ttl_enforced": True,
                "runtime_isolation": True,
                "image_policy_enforced": True,
                "allowed_image_count": 2,
                "max_session_ttl_seconds": 600,
                "max_sessions": 5,
                "max_sessions_per_tenant": 2,
                "max_sessions_per_run": 1,
            }

    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_snapshot_scope",
        lambda _config, _session_id: True,
        raising=False,
    )
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_file_read_scope",
        lambda _config, _session_id: True,
        raising=False,
    )

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=RuntimePolicySandboxAdapter(provider="k8s"),
    )

    assert result.runtime_isolation_declared is True
    assert result.image_policy_enforced_declared is True
    assert result.allowed_image_count == 2
    assert lifecycle_verification_module.sandbox_lifecycle_verification_passed(
        result
    ) is True


def test_sandbox_lifecycle_verification_does_not_treat_provider_errors_as_scope_evidence():
    adapter = ScopeProbeUnavailableSandboxAdapter(provider="k8s")

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.command_executed is True
    assert result.command_scope_enforced is False
    assert result.file_scope_enforced is False


def test_sandbox_lifecycle_verification_requires_destroyed_session_absent_from_active_list():
    adapter = StaleDestroyListSandboxAdapter(provider="k8s")

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.session_destroyed is True
    assert result.model_dump().get("session_destroy_confirmed") is False
    assert lifecycle_verification_module.sandbox_lifecycle_verification_passed(
        result
    ) is False


def test_sandbox_lifecycle_verification_fails_when_post_destroy_command_runs():
    adapter = PostDestroyPermissiveSandboxAdapter(provider="k8s")

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.model_dump()["post_destroy_command_blocked"] is False
    assert lifecycle_verification_module.sandbox_lifecycle_verification_passed(
        result
    ) is False


def test_sandbox_lifecycle_verification_records_auth_challenge_when_api_key_configured(
    monkeypatch,
):
    adapter = RecordingSandboxAdapter(provider="k8s")
    monkeypatch.setattr(
        lifecycle_verification_module,
        "inspect_sandbox_controller_auth_challenge",
        lambda _config: {
            "auth_tenant_session_list_challenge_enforced": True,
            "auth_global_session_list_challenge_enforced": True,
            "auth_capabilities_challenge_enforced": True,
        },
        raising=False,
    )

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            api_key="sandbox_controller_secret_2026_long_key",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.auth_challenge_enforced is True
    assert result.auth_tenant_session_list_challenge_enforced is True
    assert result.auth_global_session_list_challenge_enforced is True
    assert result.auth_capabilities_challenge_enforced is True


def test_sandbox_lifecycle_verification_records_auth_challenge_probe_details(
    monkeypatch,
):
    adapter = RecordingSandboxAdapter(provider="k8s")

    def reject_by_path(_config, path: str) -> bool:
        return path != "/sessions"

    monkeypatch.setattr(
        lifecycle_verification_module,
        "sandbox_controller_unauthenticated_request_rejected",
        reject_by_path,
        raising=False,
    )

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            api_key="sandbox_controller_secret_2026_long_key",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.auth_challenge_enforced is False
    assert result.auth_tenant_session_list_challenge_enforced is True
    assert result.auth_global_session_list_challenge_enforced is False
    assert result.auth_capabilities_challenge_enforced is True


def test_sandbox_controller_auth_challenge_requires_capabilities_auth(monkeypatch):
    class SandboxResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class RecordingOpener:
        def __init__(self):
            self.requests: list[str] = []

        def open(self, request, timeout: int):
            parsed = urlparse(request.full_url)
            request_path = parsed.path
            if parsed.query:
                request_path = f"{request_path}?{parsed.query}"
            self.requests.append(request_path)
            if request_path in {
                "/sessions?tenant_id=taroai_auth_probe",
                "/sessions",
            }:
                raise HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    None,
                )
            return SandboxResponse()

    opener = RecordingOpener()
    monkeypatch.setattr(
        lifecycle_verification_module,
        "build_opener",
        lambda _proxy_handler: opener,
    )

    result = lifecycle_verification_module.verify_sandbox_controller_auth_challenge(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            api_key="sandbox_controller_secret_2026_long_key",
        )
    )

    assert result is False
    assert opener.requests == [
        "/sessions?tenant_id=taroai_auth_probe",
        "/sessions",
        "/capabilities",
    ]


def test_sandbox_controller_auth_challenge_requires_global_session_list_auth(monkeypatch):
    class SandboxResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class RecordingOpener:
        def __init__(self):
            self.requests: list[str] = []

        def open(self, request, timeout: int):
            parsed = urlparse(request.full_url)
            request_path = parsed.path
            if parsed.query:
                request_path = f"{request_path}?{parsed.query}"
            self.requests.append(request_path)
            if request_path in {
                "/sessions?tenant_id=taroai_auth_probe",
                "/capabilities",
            }:
                raise HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    None,
                )
            return SandboxResponse()

    opener = RecordingOpener()
    monkeypatch.setattr(
        lifecycle_verification_module,
        "build_opener",
        lambda _proxy_handler: opener,
    )

    result = lifecycle_verification_module.verify_sandbox_controller_auth_challenge(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            api_key="sandbox_controller_secret_2026_long_key",
        )
    )

    assert result is False
    assert opener.requests == [
        "/sessions?tenant_id=taroai_auth_probe",
        "/sessions",
        "/capabilities",
    ]


def test_sandbox_lifecycle_verification_records_snapshot_scope_evidence(
    monkeypatch,
):
    adapter = RecordingSandboxAdapter(provider="k8s")
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_snapshot_scope",
        lambda _config, _session_id: True,
        raising=False,
    )
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_file_read_scope",
        lambda _config, _session_id: True,
        raising=False,
    )

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.model_dump()["snapshot_scope_enforced"] is True


def test_sandbox_lifecycle_verification_reports_command_and_destroy_failures():
    adapter = FailingSandboxAdapter(provider="k8s")

    result = verify_sandbox_lifecycle(
        SandboxLifecycleVerificationConfig(
            base_url="http://sandbox.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
        ),
        adapter=adapter,
    )

    assert result.session_created is True
    assert result.command_executed is False
    assert result.session_destroyed is False
    assert result.output_redacted is True
    assert result.artifact_listed is False
    assert result.artifact_downloaded is False


def test_sandbox_lifecycle_verification_config_rejects_invalid_url():
    with pytest.raises(ValidationError):
        SandboxLifecycleVerificationConfig(base_url="sandbox.local")


def test_sandbox_lifecycle_verification_main_prints_redacted_json(
    capsys,
    monkeypatch,
):
    adapter = RecordingSandboxAdapter(provider="k8s")

    def build_adapter(_config: SandboxLifecycleVerificationConfig):
        return adapter

    monkeypatch.setattr(
        "taroai.sandbox.lifecycle_verification.build_sandbox_adapter",
        build_adapter,
    )
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_snapshot_scope",
        lambda _config, _session_id: True,
        raising=False,
    )
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_file_read_scope",
        lambda _config, _session_id: True,
        raising=False,
    )

    exit_code = main(
        [
            "--base-url",
            "http://sandbox.local",
            "--tenant-id",
            "tenant_acme",
            "--workspace-id",
            "workspace_sales",
            "--run-id",
            "run_1",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload == {
        "artifact_downloaded": True,
        "artifact_listed": True,
        "artifact_path": "/workspace/artifacts/sandbox-lifecycle.txt",
        "auth_capabilities_challenge_enforced": False,
        "auth_challenge_enforced": False,
        "auth_global_session_list_challenge_enforced": False,
        "auth_tenant_session_list_challenge_enforced": False,
        "capabilities_checked": True,
        "command_executed": True,
        "command_scope_enforced": True,
        "downloaded_artifact_content_length": len("sandbox lifecycle ok\n"),
        "destroy_supported_declared": True,
        "file_scope_enforced": True,
        "file_read_scope_enforced": True,
        "filesystem_isolation_declared": True,
        "max_session_ttl_seconds_declared": True,
        "max_sessions_declared": True,
        "max_sessions_per_run_declared": True,
        "max_sessions_per_tenant_declared": True,
        "network_isolation_declared": True,
        "output_redacted": True,
        "provider": "k8s",
        "resource_limits_declared": True,
        "runtime_isolation_declared": True,
        "image_policy_enforced_declared": True,
        "allowed_image_count": 1,
        "post_destroy_command_blocked": True,
        "session_created": True,
        "session_destroy_confirmed": True,
        "session_destroyed": True,
        "session_id": "sandbox_verify_1",
        "session_listed": True,
        "session_ttl_enforced_declared": True,
        "snapshot_scope_enforced": True,
        "tenant_session_scope_enforced": True,
    }
    assert "customer-secret" not in captured.out


def test_sandbox_lifecycle_verification_main_fails_when_api_key_auth_challenge_is_missing(
    capsys,
    monkeypatch,
):
    adapter = RecordingSandboxAdapter(provider="k8s")

    def build_adapter(_config: SandboxLifecycleVerificationConfig):
        return adapter

    monkeypatch.setattr(
        "taroai.sandbox.lifecycle_verification.build_sandbox_adapter",
        build_adapter,
    )
    monkeypatch.setattr(
        lifecycle_verification_module,
        "verify_sandbox_controller_auth_challenge",
        lambda _config: False,
    )

    exit_code = main(
        [
            "--base-url",
            "http://sandbox.local",
            "--api-key",
            "sandbox_controller_secret_2026_long_key",
            "--tenant-id",
            "tenant_acme",
            "--workspace-id",
            "workspace_sales",
            "--run-id",
            "run_1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["auth_challenge_enforced"] is False


def test_verify_sandbox_lifecycle_script_wraps_python_cli():
    script = Path("scripts/verify-sandbox-lifecycle.sh")

    text = script.read_text()

    assert "python -m taroai.sandbox.lifecycle_verification" in text
    assert "--base-url" in text
    assert "--api-key" in text
    assert "TAROAI_SANDBOX_CONTROLLER_API_KEY" in text

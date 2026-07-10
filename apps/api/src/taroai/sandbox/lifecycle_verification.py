import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from taroai.deployment.install_evidence import SandboxLifecycleVerificationResult
from taroai.errors import NotFoundError
from taroai.sandbox.http import HttpSandboxAdapter
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxControllerCapabilities,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSessionStatus,
)


DEFAULT_SANDBOX_VERIFY_ARTIFACT_PATH = "/workspace/artifacts/sandbox-lifecycle.txt"
DEFAULT_SANDBOX_VERIFY_ARTIFACT_CONTENT = "sandbox lifecycle ok\n"
DEFAULT_SANDBOX_VERIFY_COMMAND = (
    "python -c \"from pathlib import Path; "
    "Path('/workspace/artifacts').mkdir(parents=True, exist_ok=True); "
    "Path('/workspace/artifacts/sandbox-lifecycle.txt').write_text("
    "'sandbox lifecycle ok\\n', encoding='utf-8'"
    "); print('sandbox lifecycle ok')\""
)


class SandboxLifecycleVerificationConfig(BaseModel):
    base_url: str = Field(default="http://localhost:8002", min_length=1)
    api_key: str = Field(default="", repr=False)
    tenant_id: str = Field(default="tenant_sandbox_verify", min_length=1)
    denied_tenant_id: str = Field(default="tenant_sandbox_verify_denied", min_length=1)
    workspace_id: str = Field(default="workspace_sandbox_verify", min_length=1)
    run_id: str = Field(default_factory=lambda: f"run_sandbox_verify_{uuid4().hex[:12]}")
    image: str = Field(default="python:3.12-slim", min_length=1)
    command: str = Field(default=DEFAULT_SANDBOX_VERIFY_COMMAND, min_length=1)
    artifact_path: str = Field(default=DEFAULT_SANDBOX_VERIFY_ARTIFACT_PATH, min_length=1)
    expected_artifact_content: str = Field(
        default=DEFAULT_SANDBOX_VERIFY_ARTIFACT_CONTENT,
        min_length=1,
    )
    cwd: str = Field(default="/workspace", min_length=1)
    timeout_seconds: int = Field(default=30, ge=1)
    session_timeout_seconds: int = Field(default=300, ge=1)
    command_timeout_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def validate_base_url(self) -> "SandboxLifecycleVerificationConfig":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP URL")
        return self


def parse_args(argv: list[str] | None = None) -> SandboxLifecycleVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify a sandbox controller service against its HTTP lifecycle API."
    )
    parser.add_argument("--base-url", default="http://localhost:8002")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAROAI_SANDBOX_CONTROLLER_API_KEY", ""),
    )
    parser.add_argument("--tenant-id", default="tenant_sandbox_verify")
    parser.add_argument("--denied-tenant-id", default="tenant_sandbox_verify_denied")
    parser.add_argument("--workspace-id", default="workspace_sandbox_verify")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--command", default=DEFAULT_SANDBOX_VERIFY_COMMAND)
    parser.add_argument("--artifact-path", default=DEFAULT_SANDBOX_VERIFY_ARTIFACT_PATH)
    parser.add_argument(
        "--expected-artifact-content",
        default=DEFAULT_SANDBOX_VERIFY_ARTIFACT_CONTENT,
    )
    parser.add_argument("--cwd", default="/workspace")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--session-timeout-seconds", type=int, default=300)
    parser.add_argument("--command-timeout-seconds", type=int, default=30)
    parsed = parser.parse_args(argv)
    config_data: dict[str, Any] = {
        "base_url": parsed.base_url,
        "api_key": parsed.api_key,
        "tenant_id": parsed.tenant_id,
        "denied_tenant_id": parsed.denied_tenant_id,
        "workspace_id": parsed.workspace_id,
        "image": parsed.image,
        "command": parsed.command,
        "artifact_path": parsed.artifact_path,
        "expected_artifact_content": parsed.expected_artifact_content,
        "cwd": parsed.cwd,
        "timeout_seconds": parsed.timeout_seconds,
        "session_timeout_seconds": parsed.session_timeout_seconds,
        "command_timeout_seconds": parsed.command_timeout_seconds,
    }
    if parsed.run_id is not None:
        config_data["run_id"] = parsed.run_id
    return SandboxLifecycleVerificationConfig(**config_data)


def verify_sandbox_lifecycle(
    config: SandboxLifecycleVerificationConfig,
    adapter=None,
) -> SandboxLifecycleVerificationResult:
    sandbox_adapter = adapter or build_sandbox_adapter(config)
    auth_challenge_evidence = inspect_sandbox_controller_auth_challenge(config)
    auth_challenge_enforced = all(auth_challenge_evidence.values())
    provider = str(getattr(sandbox_adapter, "provider", "sandbox_controller"))
    session_id = "sandbox_session_not_created"
    session_created = False
    command_executed = False
    session_destroyed = False
    session_destroy_confirmed = False
    post_destroy_command_blocked = False
    session_listed = False
    tenant_session_scope_enforced = False
    command_scope_enforced = False
    file_scope_enforced = False
    file_read_scope_enforced = False
    snapshot_scope_enforced = False
    artifact_listed = False
    artifact_downloaded = False
    downloaded_artifact_content_length = 0
    capabilities = sandbox_lifecycle_capabilities_result()

    try:
        capabilities = sandbox_lifecycle_capabilities_result(
            sandbox_adapter.get_capabilities()
        )
        if capabilities["capabilities_checked"]:
            provider = capabilities["provider"]
    except Exception:
        capabilities = sandbox_lifecycle_capabilities_result()

    try:
        session = sandbox_adapter.create(
            SandboxCreateRequest(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                image=config.image,
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=config.session_timeout_seconds,
            )
        )
        provider = session.provider
        session_id = session.id
        session_created = True
    except Exception:
        return sandbox_lifecycle_verification_result(
            provider=provider,
            session_id=session_id,
            session_created=session_created,
            command_executed=command_executed,
            session_destroyed=session_destroyed,
            session_destroy_confirmed=session_destroy_confirmed,
            post_destroy_command_blocked=post_destroy_command_blocked,
            command_scope_enforced=command_scope_enforced,
            file_scope_enforced=file_scope_enforced,
            file_read_scope_enforced=file_read_scope_enforced,
            snapshot_scope_enforced=snapshot_scope_enforced,
            session_listed=session_listed,
            tenant_session_scope_enforced=tenant_session_scope_enforced,
            artifact_path=config.artifact_path,
            artifact_listed=artifact_listed,
            artifact_downloaded=artifact_downloaded,
            downloaded_artifact_content_length=downloaded_artifact_content_length,
            capabilities=capabilities,
            auth_challenge_enforced=auth_challenge_enforced,
            **auth_challenge_evidence,
        )

    try:
        sandbox_adapter.execute(
            SandboxCommand(
                tenant_id=config.tenant_id,
                workspace_id=f"{config.workspace_id}_scope_probe",
                run_id=f"{config.run_id}_scope_probe",
                session_id=session_id,
                command="true",
                cwd=config.cwd,
                timeout_seconds=config.command_timeout_seconds,
            )
        )
        command_scope_enforced = False
    except NotFoundError:
        command_scope_enforced = True
    except Exception:
        command_scope_enforced = False

    try:
        sandbox_adapter.upload_file(
            SandboxFileWrite(
                tenant_id=config.tenant_id,
                workspace_id=f"{config.workspace_id}_scope_probe",
                run_id=f"{config.run_id}_scope_probe",
                session_id=session_id,
                path="/workspace/artifacts/scope-probe.txt",
                content="scope probe",
                content_type="text/plain",
            )
        )
        file_scope_enforced = False
    except NotFoundError:
        file_scope_enforced = True
    except Exception:
        file_scope_enforced = False

    snapshot_scope_enforced = verify_sandbox_snapshot_scope(config, session_id)

    try:
        tenant_sessions = sandbox_adapter.list_sessions(config.tenant_id)
        session_listed = any(session.id == session_id for session in tenant_sessions)
    except Exception:
        session_listed = False

    try:
        denied_sessions = sandbox_adapter.list_sessions(config.denied_tenant_id)
        tenant_session_scope_enforced = not any(
            session.id == session_id for session in denied_sessions
        )
    except Exception:
        tenant_session_scope_enforced = False

    try:
        command_result = sandbox_adapter.execute(
            SandboxCommand(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                session_id=session_id,
                command=config.command,
                cwd=config.cwd,
                timeout_seconds=config.command_timeout_seconds,
            )
        )
        command_executed = command_result.exit_code == 0
    except Exception:
        command_executed = False

    if command_executed:
        try:
            files = sandbox_adapter.list_files(config.tenant_id, session_id)
            artifact_listed = sandbox_file_list_contains_path(
                files,
                config.artifact_path,
            )
        except Exception:
            artifact_listed = False
        try:
            downloaded = sandbox_adapter.download_file(
                config.tenant_id,
                session_id,
                config.artifact_path,
            )
            content = downloaded.content or ""
            downloaded_artifact_content_length = len(content)
            artifact_downloaded = config.expected_artifact_content in content
        except Exception:
            artifact_downloaded = False
            downloaded_artifact_content_length = 0
        file_read_scope_enforced = verify_sandbox_file_read_scope(
            config,
            session_id,
        )

    try:
        destroyed = sandbox_adapter.destroy(config.tenant_id, session_id)
        session_destroyed = destroyed.status == SandboxSessionStatus.DESTROYED
    except Exception:
        session_destroyed = False
    if session_destroyed:
        post_destroy_command_blocked = sandbox_post_destroy_command_blocked(
            sandbox_adapter,
            config,
            session_id,
        )
        session_destroy_confirmed = sandbox_session_destroy_confirmed(
            sandbox_adapter,
            config.tenant_id,
            session_id,
        )

    return sandbox_lifecycle_verification_result(
        provider=provider,
        session_id=session_id,
        session_created=session_created,
        command_executed=command_executed,
        session_destroyed=session_destroyed,
        session_destroy_confirmed=session_destroy_confirmed,
        post_destroy_command_blocked=post_destroy_command_blocked,
        command_scope_enforced=command_scope_enforced,
        file_scope_enforced=file_scope_enforced,
        file_read_scope_enforced=file_read_scope_enforced,
        snapshot_scope_enforced=snapshot_scope_enforced,
        session_listed=session_listed,
        tenant_session_scope_enforced=tenant_session_scope_enforced,
        artifact_path=config.artifact_path,
        artifact_listed=artifact_listed,
        artifact_downloaded=artifact_downloaded,
        downloaded_artifact_content_length=downloaded_artifact_content_length,
        capabilities=capabilities,
        auth_challenge_enforced=auth_challenge_enforced,
        **auth_challenge_evidence,
    )


def sandbox_file_list_contains_path(files: list[SandboxFileRef], path: str) -> bool:
    return any(file_ref.path == path for file_ref in files)


def sandbox_session_destroy_confirmed(
    sandbox_adapter,
    tenant_id: str,
    session_id: str,
) -> bool:
    try:
        sessions = sandbox_adapter.list_sessions(tenant_id)
    except Exception:
        return False
    return not any(
        session.id == session_id and session.status != SandboxSessionStatus.DESTROYED
        for session in sessions
    )


def sandbox_post_destroy_command_blocked(
    sandbox_adapter,
    config: SandboxLifecycleVerificationConfig,
    session_id: str,
) -> bool:
    try:
        sandbox_adapter.execute(
            SandboxCommand(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                session_id=session_id,
                command="true",
                cwd=config.cwd,
                timeout_seconds=config.command_timeout_seconds,
            )
        )
        return False
    except NotFoundError:
        return True
    except Exception:
        return False


def sandbox_lifecycle_verification_result(
    provider: str,
    session_id: str,
    session_created: bool,
    command_executed: bool,
    session_destroyed: bool,
    session_destroy_confirmed: bool = False,
    post_destroy_command_blocked: bool = False,
    command_scope_enforced: bool = False,
    file_scope_enforced: bool = False,
    file_read_scope_enforced: bool = False,
    snapshot_scope_enforced: bool = False,
    session_listed: bool = False,
    tenant_session_scope_enforced: bool = False,
    artifact_path: str | None = None,
    artifact_listed: bool = False,
    artifact_downloaded: bool = False,
    downloaded_artifact_content_length: int = 0,
    capabilities: dict[str, str | bool | int] | None = None,
    auth_challenge_enforced: bool = False,
    auth_tenant_session_list_challenge_enforced: bool = False,
    auth_global_session_list_challenge_enforced: bool = False,
    auth_capabilities_challenge_enforced: bool = False,
) -> SandboxLifecycleVerificationResult:
    resolved_capabilities = capabilities or sandbox_lifecycle_capabilities_result()
    return SandboxLifecycleVerificationResult(
        provider=provider,
        session_id=session_id,
        session_created=session_created,
        command_executed=command_executed,
        session_destroyed=session_destroyed,
        session_destroy_confirmed=session_destroy_confirmed,
        post_destroy_command_blocked=post_destroy_command_blocked,
        output_redacted=True,
        command_scope_enforced=command_scope_enforced,
        file_scope_enforced=file_scope_enforced,
        file_read_scope_enforced=file_read_scope_enforced,
        snapshot_scope_enforced=snapshot_scope_enforced,
        session_listed=session_listed,
        tenant_session_scope_enforced=tenant_session_scope_enforced,
        artifact_path=artifact_path,
        artifact_listed=artifact_listed,
        artifact_downloaded=artifact_downloaded,
        downloaded_artifact_content_length=downloaded_artifact_content_length,
        capabilities_checked=bool(resolved_capabilities["capabilities_checked"]),
        network_isolation_declared=bool(
            resolved_capabilities["network_isolation_declared"]
        ),
        filesystem_isolation_declared=bool(
            resolved_capabilities["filesystem_isolation_declared"]
        ),
        resource_limits_declared=bool(
            resolved_capabilities["resource_limits_declared"]
        ),
        destroy_supported_declared=bool(
            resolved_capabilities["destroy_supported_declared"]
        ),
        session_ttl_enforced_declared=bool(
            resolved_capabilities["session_ttl_enforced_declared"]
        ),
        runtime_isolation_declared=bool(
            resolved_capabilities["runtime_isolation_declared"]
        ),
        image_policy_enforced_declared=bool(
            resolved_capabilities["image_policy_enforced_declared"]
        ),
        allowed_image_count=int(resolved_capabilities["allowed_image_count"]),
        max_session_ttl_seconds_declared=bool(
            resolved_capabilities["max_session_ttl_seconds_declared"]
        ),
        max_sessions_declared=bool(
            resolved_capabilities["max_sessions_declared"]
        ),
        max_sessions_per_tenant_declared=bool(
            resolved_capabilities["max_sessions_per_tenant_declared"]
        ),
        max_sessions_per_run_declared=bool(
            resolved_capabilities["max_sessions_per_run_declared"]
        ),
        auth_challenge_enforced=auth_challenge_enforced,
        auth_tenant_session_list_challenge_enforced=(
            auth_tenant_session_list_challenge_enforced
        ),
        auth_global_session_list_challenge_enforced=(
            auth_global_session_list_challenge_enforced
        ),
        auth_capabilities_challenge_enforced=auth_capabilities_challenge_enforced,
    )


def verify_sandbox_controller_auth_challenge(
    config: SandboxLifecycleVerificationConfig,
) -> bool:
    return all(inspect_sandbox_controller_auth_challenge(config).values())


def inspect_sandbox_controller_auth_challenge(
    config: SandboxLifecycleVerificationConfig,
) -> dict[str, bool]:
    if not config.api_key.strip():
        return {
            "auth_tenant_session_list_challenge_enforced": False,
            "auth_global_session_list_challenge_enforced": False,
            "auth_capabilities_challenge_enforced": False,
        }
    return {
        "auth_tenant_session_list_challenge_enforced": (
            sandbox_controller_unauthenticated_request_rejected(
                config,
                "/sessions?tenant_id=taroai_auth_probe",
            )
        ),
        "auth_global_session_list_challenge_enforced": (
            sandbox_controller_unauthenticated_request_rejected(
                config,
                "/sessions",
            )
        ),
        "auth_capabilities_challenge_enforced": (
            sandbox_controller_unauthenticated_request_rejected(
                config,
                "/capabilities",
            )
        ),
    }


def sandbox_controller_unauthenticated_request_rejected(
    config: SandboxLifecycleVerificationConfig,
    path: str,
) -> bool:
    request = Request(
        f"{config.base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=config.timeout_seconds) as response:
            return response.status in {401, 403}
    except HTTPError as error:
        error.read()
        return error.code in {401, 403}
    except (TimeoutError, URLError):
        return False


def verify_sandbox_snapshot_scope(
    config: SandboxLifecycleVerificationConfig,
    session_id: str,
) -> bool:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = f"Bearer {config.api_key}"
    body = json.dumps(
        {
            "tenant_id": config.tenant_id,
            "workspace_id": f"{config.workspace_id}_scope_probe",
            "run_id": f"{config.run_id}_scope_probe",
            "session_id": session_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        f"{config.base_url.rstrip('/')}/snapshots",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=config.timeout_seconds):
            return False
    except HTTPError as error:
        error.read()
        return error.code in {403, 404}
    except (TimeoutError, URLError):
        return False


def verify_sandbox_file_read_scope(
    config: SandboxLifecycleVerificationConfig,
    session_id: str,
) -> bool:
    headers = {"Accept": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = f"Bearer {config.api_key}"
    query = urlencode(
        {
            "tenant_id": config.tenant_id,
            "session_id": session_id,
            "workspace_id": f"{config.workspace_id}_scope_probe",
            "run_id": f"{config.run_id}_scope_probe",
            "path": config.artifact_path,
        }
    )
    request = Request(
        f"{config.base_url.rstrip('/')}/files?{query}",
        headers=headers,
        method="GET",
    )
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=config.timeout_seconds):
            return False
    except HTTPError as error:
        error.read()
        return error.code in {403, 404}
    except (TimeoutError, URLError):
        return False


def sandbox_lifecycle_capabilities_result(
    capabilities: SandboxControllerCapabilities | dict[str, Any] | None = None,
) -> dict[str, str | bool | int]:
    if capabilities is None:
        return {
            "provider": "sandbox_controller",
            "capabilities_checked": False,
            "network_isolation_declared": False,
            "filesystem_isolation_declared": False,
            "resource_limits_declared": False,
            "destroy_supported_declared": False,
            "session_ttl_enforced_declared": False,
            "runtime_isolation_declared": False,
            "image_policy_enforced_declared": False,
            "allowed_image_count": 0,
            "max_session_ttl_seconds_declared": False,
            "max_sessions_declared": False,
            "max_sessions_per_tenant_declared": False,
            "max_sessions_per_run_declared": False,
        }
    capabilities = SandboxControllerCapabilities.model_validate(capabilities)
    return {
        "provider": capabilities.provider,
        "capabilities_checked": True,
        "network_isolation_declared": capabilities.network_isolation,
        "filesystem_isolation_declared": capabilities.filesystem_isolation,
        "resource_limits_declared": capabilities.resource_limits,
        "destroy_supported_declared": capabilities.destroy_supported,
        "session_ttl_enforced_declared": capabilities.session_ttl_enforced,
        "runtime_isolation_declared": capabilities.runtime_isolation,
        "image_policy_enforced_declared": capabilities.image_policy_enforced,
        "allowed_image_count": capabilities.allowed_image_count or 0,
        "max_session_ttl_seconds_declared": (
            capabilities.max_session_ttl_seconds is not None
        ),
        "max_sessions_declared": capabilities.max_sessions is not None,
        "max_sessions_per_tenant_declared": (
            capabilities.max_sessions_per_tenant is not None
        ),
        "max_sessions_per_run_declared": (
            capabilities.max_sessions_per_run is not None
        ),
    }


def build_sandbox_adapter(config: SandboxLifecycleVerificationConfig) -> HttpSandboxAdapter:
    return HttpSandboxAdapter(
        provider="http",
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
    )


def sandbox_lifecycle_verification_passed(
    result: SandboxLifecycleVerificationResult,
    auth_challenge_required: bool = False,
) -> bool:
    return (
        result.session_created
        and result.command_executed
        and result.session_destroyed
        and result.session_destroy_confirmed
        and result.post_destroy_command_blocked
        and result.command_scope_enforced
        and result.file_scope_enforced
        and result.file_read_scope_enforced
        and result.snapshot_scope_enforced
        and result.session_listed
        and result.tenant_session_scope_enforced
        and result.output_redacted
        and sandbox_lifecycle_artifact_verified(result)
        and result.capabilities_checked
        and result.network_isolation_declared
        and result.filesystem_isolation_declared
        and result.resource_limits_declared
        and result.destroy_supported_declared
        and result.session_ttl_enforced_declared
        and result.runtime_isolation_declared
        and result.image_policy_enforced_declared
        and result.allowed_image_count > 0
        and result.max_session_ttl_seconds_declared
        and result.max_sessions_declared
        and result.max_sessions_per_tenant_declared
        and result.max_sessions_per_run_declared
        and (
            not auth_challenge_required
            or result.auth_challenge_enforced
        )
    )


def sandbox_lifecycle_artifact_verified(
    result: SandboxLifecycleVerificationResult,
) -> bool:
    return (
        bool(result.artifact_path)
        and result.artifact_path.startswith("/workspace/artifacts/")
        and result.artifact_listed
        and result.artifact_downloaded
        and result.downloaded_artifact_content_length > 0
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_sandbox_lifecycle(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if sandbox_lifecycle_verification_passed(
        result,
        auth_challenge_required=bool(config.api_key.strip()),
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

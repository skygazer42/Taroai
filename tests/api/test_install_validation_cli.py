import json
import base64
import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from taroai.deployment import install_validation as install_validation_cli
from taroai.deployment.install_validation import (
    InstallValidationHttpResponse,
    InstallValidationRunConfig,
    parse_args,
    run_install_validation,
)
from taroai.deployment.install_evidence import (
    AuditWriteVerificationResult,
    BrowserControllerVerificationResult,
    EventStreamVerificationResult,
    RestoreDrillVerificationResult,
    SandboxLifecycleVerificationResult,
)
from taroai.deployment.release_package import ReleasePackageBuildConfig, build_release_package
from taroai.deployment.transfer_evidence import (
    ReleaseTransferEvidenceBuildConfig,
    build_release_transfer_evidence,
)
from taroai.deployment.validation import InstallValidationCheckName, InstallValidationStatus
from taroai.db.models import MigrationPlan
from taroai.model_gateway.verification import OpenAICompatibleModelGatewayVerificationResult
from taroai.observability.verification import TraceCollectorVerificationResult
from taroai.sandbox.kubernetes_verification import (
    KubernetesRuntimePolicyVerificationResult,
    KubernetesSandboxVerificationResult,
)
from taroai.secrets.verification import SecretManagerVerificationResult
from taroai.storage.object_storage_verification import ObjectStorageVerificationResult
from taroai.support.redaction import (
    SupportBundleRedactionConfig,
    redact_support_bundle_archive,
)
from taroai.workers.models import JobStatus
from taroai.workers.redis_verification import RedisQueueVerificationResult


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_zip(path: Path, entries: dict[str, bytes | str]) -> None:
    import zipfile

    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)


def rewrite_zip_entry_content(
    source: Path,
    target: Path,
    entry_name: str,
    content: bytes,
) -> None:
    import zipfile

    with zipfile.ZipFile(source) as source_archive:
        with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as target_archive:
            for item in source_archive.infolist():
                rewritten = zipfile.ZipInfo(item.filename)
                rewritten.date_time = item.date_time
                rewritten.compress_type = zipfile.ZIP_DEFLATED
                rewritten.external_attr = item.external_attr
                target_archive.writestr(
                    rewritten,
                    content if item.filename == entry_name else source_archive.read(item.filename),
                )


def remove_zip_entry(source: Path, target: Path, entry_name: str) -> None:
    import zipfile

    with zipfile.ZipFile(source) as source_archive:
        with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as target_archive:
            for item in source_archive.infolist():
                if item.filename == entry_name:
                    continue
                rewritten = zipfile.ZipInfo(item.filename)
                rewritten.date_time = item.date_time
                rewritten.compress_type = zipfile.ZIP_DEFLATED
                rewritten.external_attr = item.external_attr
                target_archive.writestr(rewritten, source_archive.read(item.filename))


def append_executable_zip_entry(
    path: Path,
    entry_name: str,
    content: str,
) -> None:
    import zipfile

    with zipfile.ZipFile(path, mode="a", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(entry_name)
        info.external_attr = (0o755 & 0xFFFF) << 16
        archive.writestr(info, content.encode("utf-8"))


def sign_release_package(package_path: Path) -> tuple[Path, str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    payload = {
        "algorithm": "ed25519",
        "key_id": "creao-release-2026-01",
        "package_sha256": sha256_file(package_path),
    }
    signature_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    signature_path = package_path.with_suffix(".zip.sig.json")
    signature_path.write_text(
        json.dumps(
            {
                **payload,
                "signature": base64.b64encode(
                    private_key.sign(signature_payload)
                ).decode("ascii"),
            }
        )
    )
    return (
        signature_path,
        payload["key_id"],
        base64.b64encode(public_key).decode("ascii"),
    )


def hardened_kubernetes_sandbox_fields() -> dict[str, object]:
    return {
        "workspace_volume_size_limit": "1Gi",
        "tmp_volume_size_limit": "1Gi",
        "pod_active_deadline_seconds": 300,
        "host_network": False,
        "host_pid": False,
        "host_ipc": False,
        "pod_run_as_non_root": True,
        "seccomp_profile_type": "RuntimeDefault",
        "privileged": False,
        "allow_privilege_escalation": False,
        "read_only_root_filesystem": True,
        "dropped_capabilities": ["ALL"],
        "automount_service_account_token": False,
        "service_links_enabled": False,
        "termination_grace_period_seconds": 5,
    }


def write_valid_kubernetes_lifecycle_evidence(path: Path) -> None:
    path.write_text(
        SandboxLifecycleVerificationResult(
            provider="kubernetes",
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
            session_listed=True,
            tenant_session_scope_enforced=True,
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
        ).model_dump_json()
    )


def valid_closed_loop_source_result_payload(
    *,
    sandbox_governance: bool = False,
) -> dict[str, object]:
    sandbox_governance_fields = {
        "sandbox_capabilities_checked": sandbox_governance,
        "sandbox_network_isolation_declared": sandbox_governance,
        "sandbox_filesystem_isolation_declared": sandbox_governance,
        "sandbox_resource_limits_declared": sandbox_governance,
        "sandbox_destroy_supported_declared": sandbox_governance,
        "sandbox_session_ttl_enforced_declared": sandbox_governance,
        "sandbox_runtime_isolation_declared": sandbox_governance,
        "sandbox_image_policy_enforced_declared": sandbox_governance,
        "sandbox_allowed_image_count": 1 if sandbox_governance else 0,
        "sandbox_max_session_ttl_seconds": 900 if sandbox_governance else 0,
        "sandbox_max_sessions": 50 if sandbox_governance else 0,
        "sandbox_max_sessions_per_tenant": 20 if sandbox_governance else 0,
        "sandbox_max_sessions_per_run": 3 if sandbox_governance else 0,
    }
    return {
        "api_base_url": "http://api.local",
        "browser_base_url": "http://browser.local",
        "web_base_url": "http://web.local",
        "api_health_ok": True,
        "browser_health_ok": True,
        "web_ok": True,
        "tenant_id": "tenant_acme",
        "owner_user_id": "user_owner",
        "tenant_ready": True,
        "model_gateway_configured": True,
        "sandbox_configured": True,
        "sandbox_provider": "k8s" if sandbox_governance else "local_process",
        "run_id": "run_1",
        "execute_status_code": 200,
        "run_status": "succeeded",
        "artifact_count": 1,
        "artifact_names": ["report.md"],
        "model_artifact_required_name_found": True,
        "model_artifact_storage_object_count": 1,
        "model_artifact_total_download_bytes": 72,
        "model_artifact_storage_object_id": "storage_report_1",
        "model_artifact_download_bytes": 72,
        "model_artifact_required_text_found": True,
        "model_sandbox_command_event_seen": True,
        "model_artifact_promoted_event_seen": True,
        "model_run_event_payload_safe": True,
        "model_sandbox_command_exit_code": 0,
        "model_sandbox_command_output_uri": (
            "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
            "sandbox-command-outputs/model_sandbox-output.json"
        ),
        "model_sandbox_command_output_storage_object_id": (
            "storage_model_sandbox_output_1"
        ),
        "model_artifact_event_matches_storage_object": True,
        "model_runtime_state_status": "succeeded",
        "model_runtime_sandbox_session_id": "runtime_sandbox_1",
        "model_runtime_completed_step_count": 1,
        "model_runtime_promoted_artifact_path_count": 1,
        "model_runtime_required_artifact_path_found": True,
        "model_trace_span_count": 3,
        "model_trace_event_count": 3,
        "model_trace_billing_meter_count": 1,
        "model_trace_audit_event_count": 1,
        "model_trace_runtime_tool_call_seen": True,
        "model_trace_billing_tool_call_seen": True,
        "model_trace_audit_tool_executed_seen": True,
        "model_trace_payload_safe": True,
        "sandbox_session_id": "sandbox_1",
        "sandbox_exit_code": 0,
        "sandbox_output_uri": (
            "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
            "sandbox-command-outputs/sandbox_1-output.json"
        ),
        "sandbox_output_storage_object_id": "storage_sandbox_output_1",
        "sandbox_output_download_bytes": 40,
        "sandbox_session_destroyed": True,
        "sandbox_destroy_status_confirmed": True,
        "sandbox_post_destroy_command_blocked": True,
        "browser_controller_auth_enforced": True,
        "browser_controller_auth_tenant_session_list_challenge_enforced": True,
        "browser_controller_auth_global_session_list_challenge_enforced": True,
        "browser_controller_auth_capabilities_challenge_enforced": True,
        "browser_controller_capabilities_checked": True,
        "browser_controller_auth_required": True,
        "browser_controller_session_ttl_enforced": True,
        "browser_controller_max_session_ttl_seconds": 900,
        "browser_controller_max_sessions": 50,
        "browser_controller_max_sessions_per_tenant": 20,
        "browser_controller_max_sessions_per_run": 3,
        "browser_screenshot_storage_object_id": "storage_browser_1",
        "browser_screenshot_download_bytes": 128,
        "browser_session_id": "browser_verify_1",
        "browser_session_listed": True,
        "browser_tenant_session_scope_enforced": True,
        "browser_session_read_scope_enforced": True,
        "browser_session_delete_scope_enforced": True,
        "browser_extract_text": "Browser smoke OK",
        "browser_workspace_submit_text": "succeeded",
        "browser_workspace_evidence_summary": "Artifact delivery proven",
        "browser_workspace_delivery_summary": "Artifact downloaded",
        "browser_workspace_delivery_chain_status": "Delivery chain complete",
        "browser_workspace_delivery_chain_run_id": "run_1",
        "browser_workspace_delivery_chain_sandbox_session_id": "runtime_sandbox_1",
        "browser_workspace_delivery_chain_artifact_storage_object_id": (
            "storage_report_1"
        ),
        "browser_workspace_delivery_chain_terminal_storage_object_id": (
            "storage_terminal_1"
        ),
        "browser_workspace_event_integrity_status": "Event stream verified",
        "browser_workspace_trace_status_text": "Loaded",
        "browser_workspace_trace_error_text": "No error",
        "browser_workspace_artifact_preview_text": "hello report",
        "browser_workspace_artifact_preview_storage_object_id": "storage_report_1",
        "browser_workspace_artifact_download_storage_object_id": "storage_report_1",
        "browser_workspace_artifact_download_status": "Downloaded",
        "browser_workspace_artifact_downloaded_storage_object_id": "storage_report_1",
        "browser_workspace_terminal_text": "exit_code=0",
        "browser_workspace_terminal_output_storage_object_id": "storage_terminal_1",
        "browser_workspace_feedback_api_seen": True,
        "browser_workspace_solution_pack_install_api_seen": True,
        "browser_workspace_solution_pack_install_skill_count": 1,
        "browser_workspace_skill_run_api_status": "succeeded",
        "browser_workspace_skill_run_artifact_count": 1,
        "browser_workspace_skill_run_artifact_download_bytes": 72,
        "browser_workspace_skill_run_required_text_found": True,
        "browser_workspace_skill_invocation_event_seen": True,
        "browser_workspace_skill_invocation_event_matches_skill": True,
        "browser_workspace_skill_run_sandbox_command_event_seen": True,
        "browser_workspace_skill_run_artifact_promoted_event_seen": True,
        "browser_workspace_skill_run_event_payload_safe": True,
        "browser_workspace_skill_runtime_state_status": "succeeded",
        "browser_workspace_skill_runtime_sandbox_session_id": (
            "runtime_skill_sandbox_1"
        ),
        "browser_workspace_skill_runtime_required_artifact_path_found": True,
        "browser_workspace_skill_trace_runtime_tool_call_seen": True,
        "browser_workspace_skill_trace_billing_tool_call_seen": True,
        "browser_workspace_skill_trace_audit_tool_executed_seen": True,
        "browser_workspace_skill_trace_payload_safe": True,
        "browser_workspace_skill_history_selection_trace_status": "Loaded",
        "browser_workspace_skill_history_selection_delivery_chain_status": (
            "Delivery chain complete"
        ),
        "browser_workspace_skill_history_selection_runtime_state_status": "succeeded",
        "browser_workspace_skill_history_selection_download_status": (
            "Downloaded report.md"
        ),
        "browser_workspace_skill_history_selection_feedback_api_seen": True,
        "solution_pack_reuse_marketplace_visible": True,
        "solution_pack_reuse_workspace_installed": True,
        "solution_pack_reuse_invocation_ready": True,
        **sandbox_governance_fields,
    }


def write_valid_closed_loop_demo_gate(
    path: Path,
    *,
    sandbox_governance: bool = False,
    with_source_result: bool = True,
) -> None:
    if with_source_result:
        source_result_path = (
            path.parent / "dist/local-cloud-poc-strict-e2e-result.json"
        )
        source_result_path.parent.mkdir(parents=True, exist_ok=True)
        source_result_path.write_text(
            json.dumps(
                valid_closed_loop_source_result_payload(
                    sandbox_governance=sandbox_governance
                )
            )
        )
    required_gates = [
        "demo_ready",
        "workspace_execution_ready",
        "skill_reuse_ready",
        "browser_controller_governance_ready",
    ]
    gate_results = {
        "demo_ready": True,
        "local_smoke_ready": True,
        "strict_model_ready": True,
        "workspace_execution_ready": True,
        "skill_reuse_ready": True,
        "browser_controller_governance_ready": True,
        "sandbox_governance_ready": sandbox_governance,
    }
    if sandbox_governance:
        required_gates.append("sandbox_governance_ready")
    path.write_text(
        json.dumps(
            {
                "status": "passed",
                "result_path": "dist/local-cloud-poc-strict-e2e-result.json",
                "demo_ready": True,
                "local_smoke_ready": True,
                "strict_model_ready": True,
                "workspace_execution_ready": True,
                "skill_reuse_ready": True,
                "browser_controller_governance_ready": True,
                "sandbox_governance_ready": sandbox_governance,
                "sandbox_runtime_isolation_declared": sandbox_governance,
                "sandbox_image_policy_enforced_declared": sandbox_governance,
                "sandbox_allowed_image_count": 1 if sandbox_governance else 0,
                "required_gates": required_gates,
                "failed_required_gates": [],
                "gate_results": gate_results,
                "summary": "strict workspace execution ready",
                "errors": [],
            }
        )
    )


def ready_browser_controller_readiness() -> dict[str, object]:
    return {
        "configured": True,
        "provider": "playwright",
        "controller_required": True,
        "controller_configured": True,
        "controller_endpoint_configured": True,
        "controller_auth_configured": True,
        "capabilities_checked": True,
        "auth_required_declared": True,
        "session_ttl_enforced_declared": True,
        "max_session_ttl_seconds": 900,
        "max_sessions": 25,
        "max_sessions_per_tenant": 10,
        "max_sessions_per_run": 2,
        "navigation_allowlist_enforced_declared": True,
        "navigation_allowed_host_count": 3,
        "missing": [],
    }


def valid_kubernetes_runtime_policy_evidence() -> KubernetesRuntimePolicyVerificationResult:
    return KubernetesRuntimePolicyVerificationResult(
        namespace="taroai",
        verified=True,
        namespace_labels={
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/warn": "restricted",
            "pod-security.kubernetes.io/enforce-version": "latest",
        },
        resource_quota_name="taroai-sandbox-runtime-quota",
        resource_quota_hard={
            "pods": "50",
            "requests.cpu": "20",
            "requests.memory": "40Gi",
            "limits.cpu": "40",
            "limits.memory": "80Gi",
            "requests.ephemeral-storage": "100Gi",
            "limits.ephemeral-storage": "200Gi",
        },
        limit_range_name="taroai-sandbox-runtime-limits",
        limit_range_default={
            "cpu": "1000m",
            "memory": "1Gi",
            "ephemeral-storage": "2Gi",
        },
        limit_range_default_request={
            "cpu": "500m",
            "memory": "512Mi",
            "ephemeral-storage": "1Gi",
        },
        limit_range_max={
            "memory": "4Gi",
            "ephemeral-storage": "8Gi",
        },
        network_policy_name="taroai-sandbox-runtime-default-deny",
        network_policy_pod_selector={
            "app.kubernetes.io/name": "taroai-sandbox-session",
        },
        network_policy_types=["Ingress", "Egress"],
        network_policy_default_deny=True,
        controller_service_account_name="sandbox-controller",
        controller_service_account_exists=True,
        runner_service_account_name="sandbox-runner",
        runner_service_account_token_automount_disabled=True,
        controller_role_name="sandbox-controller",
        controller_role_binding_name="sandbox-controller",
        controller_role_least_privilege=True,
        controller_role_binding_valid=True,
    )


def valid_kubernetes_sandbox_evidence(
    **overrides: object,
) -> KubernetesSandboxVerificationResult:
    runtime_image = "ghcr.io/customer/sandbox-runtime@sha256:" + ("a" * 64)
    data = {
        "provider": "kubernetes",
        "image": runtime_image,
        "namespace": "taroai",
        "session_id": "sandbox_kubernetes_verify",
        "pod_name": "taroai-sandbox-kubernetes-verify",
        "network_policy_name": "taroai-sandbox-kubernetes-verify-deny-all",
        "network_policy_default_deny": True,
        "network_policy_types": ["Ingress", "Egress"],
        "network_policy_session_selector": {
            "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
        },
        "exit_code": 0,
        "stdout_contains": "KUBERNETES VERIFY OK",
        "downloaded_content": "KUBERNETES VERIFY OK",
        "file_paths": ["/workspace/artifacts/report.txt"],
        "snapshot_uri": "kubernetes://taroai/pods/pod/snapshots/one",
        "destroyed": True,
        "service_account_name": "sandbox-runner",
        "runtime_class_name": "gvisor",
        "runtime_class_required": True,
        "allowed_images": ["ghcr.io/customer/sandbox-runtime@sha256:*"],
        "image_pull_policy": "IfNotPresent",
        "memory_limit": "512Mi",
        "cpu_limit": "500m",
        "ephemeral_storage_limit": "1Gi",
        **hardened_kubernetes_sandbox_fields(),
        "run_as_user": 65532,
        "run_as_group": 65532,
        "runtime_policy": valid_kubernetes_runtime_policy_evidence(),
    }
    data.update(overrides)
    return KubernetesSandboxVerificationResult(**data)


class RecordingInstallValidationHttpClient:
    def __init__(self):
        self.requests = []

    def get(self, url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        self.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "status": "ready",
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                        },
                    }
                ),
            )
        if url == "http://web.local/":
            return InstallValidationHttpResponse(
                status_code=200,
                body=(
                    "<title>Taroai Workspace</title>"
                    '<main data-testid="chat-column">'
                    '<div data-testid="conversation-log"></div>'
                    "Press Enter to send, Shift+Enter for a new line."
                    '<textarea id="composer-input"></textarea>'
                    '<button id="send-button"></button>'
                    '<input id="login-email" />'
                    '<input id="login-password" />'
                    '<button id="login-button"></button>'
                    '<button id="logout-button"></button>'
                    '<span data-auth-status>No token</span>'
                    '<span data-readiness-status>Preflight unchecked</span>'
                    '<span data-readiness-model>Model unchecked</span>'
                    '<span data-readiness-sandbox>Sandbox unchecked</span>'
                    '<dd data-browser-storage-object>--</dd>'
                    '<ul data-artifact-list></ul>'
                    '<script src="./assets/main.js" type="module"></script>'
                ),
            )
        if url == "http://web.local/assets/main.js":
            return InstallValidationHttpResponse(
                status_code=200,
                body=(
                    'sessionStorage.getItem("taroai.accessToken");'
                    'sessionStorage.setItem("taroai.accessToken", token);'
                    'sessionStorage.removeItem("taroai.accessToken");'
                    'fetch(`${state.apiBase}${"/api/auth/login"}`);'
                    'headers["Authorization"] = `${bearerPrefix}${state.accessToken}`;'
                    'const bearerPrefix = "Bearer ";'
                    'fetch(`${state.apiBase}${"/readyz"}`);'
                    "model_gateway; sandbox; missing.join;"
                    'fetch(`${state.apiBase}${"/api/storage/objects/"}${id}/content`);'
                ),
            )
        if url == "http://browser.local/healthz":
            return InstallValidationHttpResponse(
                status_code=200,
                body='{"status":"ok","service":"taroai-browser-controller"}',
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")


class BrowserReadyInstallValidationHttpClient(RecordingInstallValidationHttpClient):
    def get(self, url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "status": "ready",
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": ready_browser_controller_readiness(),
                        },
                    }
                ),
            )
        return super().get(url, headers)


class SensitiveWorkspaceUrlInstallValidationHttpClient(RecordingInstallValidationHttpClient):
    def get(self, url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "https://agent:probe-secret-value@web.local/":
            return InstallValidationHttpResponse(
                status_code=200,
                body=(
                    "<title>Taroai Workspace</title>"
                    '<main data-testid="chat-column">'
                    '<div data-testid="conversation-log"></div>'
                    "Press Enter to send, Shift+Enter for a new line."
                    '<textarea id="composer-input"></textarea>'
                    '<button id="send-button"></button>'
                    '<input id="login-email" />'
                    '<input id="login-password" />'
                    '<button id="login-button"></button>'
                    '<button id="logout-button"></button>'
                    '<span data-auth-status>No token</span>'
                    '<span data-readiness-status>Preflight unchecked</span>'
                    '<span data-readiness-model>Model unchecked</span>'
                    '<span data-readiness-sandbox>Sandbox unchecked</span>'
                    '<dd data-browser-storage-object>--</dd>'
                    '<ul data-artifact-list></ul>'
                    '<script src="./assets/main.js" type="module"></script>'
                ),
            )
        if url == "https://agent:probe-secret-value@web.local/assets/main.js":
            return InstallValidationHttpResponse(
                status_code=200,
                body=(
                    'sessionStorage.getItem("taroai.accessToken");'
                    'sessionStorage.setItem("taroai.accessToken", token);'
                    'sessionStorage.removeItem("taroai.accessToken");'
                    'fetch(`${state.apiBase}${"/api/auth/login"}`);'
                    'headers["Authorization"] = `${bearerPrefix}${state.accessToken}`;'
                    'const bearerPrefix = "Bearer ";'
                    'fetch(`${state.apiBase}${"/readyz"}`);'
                    "model_gateway; sandbox; missing.join;"
                    'fetch(`${state.apiBase}${"/api/storage/objects/"}${id}/content`);'
                ),
            )
        return super().get(url, headers)


def test_install_validation_runner_checks_api_model_sandbox_and_browser_controller():
    client = RecordingInstallValidationHttpClient()
    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            web_base_url="http://web.local",
            browser_controller_api_key="browser_secret",
        ),
        http_client=client,
    )

    checks = {check.name: check for check in report.checks}

    assert set(checks) == set(InstallValidationCheckName)
    assert report.status == InstallValidationStatus.FAILED
    assert checks[InstallValidationCheckName.API_HEALTH].status == "passed"
    assert checks[InstallValidationCheckName.MODEL_GATEWAY_HEALTH].status == "failed"
    assert (
        "--model-gateway-verification"
        in checks[InstallValidationCheckName.MODEL_GATEWAY_HEALTH].message
    )
    assert checks[InstallValidationCheckName.SANDBOX_HEALTH].status == "failed"
    assert "--sandbox-verification" in checks[InstallValidationCheckName.SANDBOX_HEALTH].message
    assert checks[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH].status == "failed"
    assert (
        "browser readiness missing from /readyz"
        in checks[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH].message
    )
    assert checks[InstallValidationCheckName.WEB_WORKSPACE_HEALTH].status == "passed"
    assert (
        checks[InstallValidationCheckName.WEB_WORKSPACE_HEALTH].metadata["web_base_url"]
        == "http://web.local"
    )
    assert checks[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY].status == "failed"
    assert checks[InstallValidationCheckName.DATABASE_MIGRATION].status == "failed"
    assert checks[InstallValidationCheckName.REDIS_CONNECTIVITY].status == "failed"
    assert checks[InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE].status == "failed"
    assert checks[InstallValidationCheckName.SECRET_MANAGER_READ].status == "failed"
    assert checks[InstallValidationCheckName.EVENT_STREAM].status == "failed"
    assert checks[InstallValidationCheckName.WORKER_QUEUE].status == "failed"
    assert checks[InstallValidationCheckName.AUDIT_WRITE].status == "failed"
    assert checks[InstallValidationCheckName.TRACE_COLLECTOR].status == "failed"
    assert checks[InstallValidationCheckName.BACKUP_RESTORE_DRILL].status == "failed"
    assert checks[InstallValidationCheckName.RUNTIME_CLOSED_LOOP].status == "failed"


def test_install_validation_runner_requires_closed_loop_demo_gate_for_cloud():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "--runtime-closed-loop-evidence" in check.message


def test_install_validation_runner_accepts_closed_loop_demo_gate_for_cloud(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "passed"
    assert check.metadata["demo_ready"] is True
    assert check.metadata["workspace_execution_ready"] is True
    assert check.metadata["skill_reuse_ready"] is True
    assert check.metadata["browser_controller_governance_ready"] is True


def test_install_validation_runner_rejects_closed_loop_without_skill_reuse_for_cloud(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    payload = json.loads(evidence_path.read_text())
    payload["skill_reuse_ready"] = False
    payload["required_gates"] = [
        gate for gate in payload["required_gates"] if gate != "skill_reuse_ready"
    ]
    payload["gate_results"]["skill_reuse_ready"] = False
    evidence_path.write_text(json.dumps(payload))

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "skill_reuse_ready" in check.message


def test_install_validation_runner_accepts_closed_loop_source_result_relative_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client = RecordingInstallValidationHttpClient()
    monkeypatch.chdir(tmp_path)
    dist_path = tmp_path / "dist"
    dist_path.mkdir()
    evidence_path = dist_path / "demo-gate.json"
    source_result_path = dist_path / "local-cloud-poc-strict-e2e-result.json"
    source_result_path.write_text(
        json.dumps(valid_closed_loop_source_result_payload())
    )
    write_valid_closed_loop_demo_gate(evidence_path, with_source_result=False)

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "passed"
    assert check.metadata["source_result_path"] == str(source_result_path)


def test_install_validation_runner_rejects_closed_loop_evidence_without_source_result(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path, with_source_result=False)

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "referenced local cloud PoC result is missing" in check.message


def test_install_validation_runner_rejects_closed_loop_evidence_that_does_not_match_source_result(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    source_result_path = tmp_path / "dist/local-cloud-poc-strict-e2e-result.json"
    source_result_path.write_text(json.dumps({"api_health_ok": False}))

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "runtime closed-loop evidence does not match source result" in check.message


def test_install_validation_runner_rejects_closed_loop_source_api_base_mismatch(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    source_result_path = tmp_path / "dist/local-cloud-poc-strict-e2e-result.json"
    source_result_path.write_text(
        json.dumps(
            valid_closed_loop_source_result_payload()
            | {"api_base_url": "https://other-api.local"}
        )
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert (
        "runtime closed-loop source API base URL did not match install validation API"
        in check.message
    )


def test_install_validation_runner_rejects_closed_loop_source_event_run_id_mismatch(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    event_result_path = tmp_path / "event-stream-verification.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    event_result_path.write_text(
        EventStreamVerificationResult(
            api_base_url="http://api.local",
            run_id="run_other",
            first_event_sequence=7,
            stream_opened=True,
            event_id_received=True,
            after_sequence_replay_succeeded=True,
            last_event_id_replay_succeeded=True,
            tenant_scope_enforced=True,
            safe_payload_confirmed=True,
        ).model_dump_json()
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
            event_stream_verification_path=str(event_result_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert (
        "runtime closed-loop source run id did not match event stream verification run id"
        in check.message
    )


def test_install_validation_runner_rejects_closed_loop_source_audit_run_id_mismatch(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    audit_result_path = tmp_path / "audit-write-verification.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    audit_result_path.write_text(
        AuditWriteVerificationResult(
            api_base_url="http://api.local",
            run_id="run_other",
            write_succeeded=True,
            read_back_succeeded=True,
            tenant_scope_enforced=True,
            sensitive_metadata_redacted=True,
        ).model_dump_json()
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
            audit_write_verification_path=str(audit_result_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert (
        "runtime closed-loop source run id did not match audit verification run id"
        in check.message
    )


def test_install_validation_runner_rejects_inconsistent_closed_loop_gate_evidence(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    payload = json.loads(evidence_path.read_text())
    payload["browser_controller_governance_ready"] = False
    evidence_path.write_text(json.dumps(payload))

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert (
        "required gates not passed: browser_controller_governance_ready"
        in check.message
    )


def test_install_validation_runner_rejects_closed_loop_report_with_errors(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    payload = json.loads(evidence_path.read_text())
    payload["errors"] = ["workspace_execution_ready=false"]
    evidence_path.write_text(json.dumps(payload))

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "evidence errors were present" in check.message


def test_install_validation_runner_rejects_closed_loop_demo_ready_without_supporting_fields(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path)
    payload = json.loads(evidence_path.read_text())
    payload["strict_model_ready"] = False
    evidence_path.write_text(json.dumps(payload))

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "strict_model_ready=false" in check.message


def test_install_validation_runner_rejects_closed_loop_sandbox_governance_without_supporting_fields(
    tmp_path: Path,
):
    client = RecordingInstallValidationHttpClient()
    evidence_path = tmp_path / "demo-gate.json"
    write_valid_closed_loop_demo_gate(evidence_path, sandbox_governance=True)
    payload = json.loads(evidence_path.read_text())
    payload["sandbox_runtime_isolation_declared"] = False
    payload["sandbox_image_policy_enforced_declared"] = False
    payload["sandbox_allowed_image_count"] = 0
    evidence_path.write_text(json.dumps(payload))

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            runtime_closed_loop_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        check.name: check
        for check in report.checks
    }[InstallValidationCheckName.RUNTIME_CLOSED_LOOP]
    assert check.status == "failed"
    assert "sandbox_runtime_isolation_declared=false" in check.message
    assert "sandbox_image_policy_enforced_declared=false" in check.message
    assert "sandbox_allowed_image_count=0" in check.message


def test_install_validation_runner_redacts_web_workspace_url_metadata():
    client = SensitiveWorkspaceUrlInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            web_base_url="https://agent:probe-secret-value@web.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.WEB_WORKSPACE_HEALTH]

    assert check.status == "passed"
    assert check.metadata["web_base_url"] == "https://web.local"
    assert "probe-secret-value" not in report.model_dump_json()


def test_install_validation_requires_model_gateway_evidence_when_configured_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--model-gateway-verification" in check.message
    assert "OpenAI-compatible" in check.remediation


def test_install_validation_requires_sandbox_evidence_when_configured_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--sandbox-verification" in check.message
    assert "sandbox lifecycle" in check.remediation


def test_install_validation_rejects_enterprise_sandbox_readiness_without_capabilities():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {
                                "configured": True,
                                "provider": "k8s",
                                "controller_required": True,
                                "controller_configured": True,
                                "controller_endpoint_configured": True,
                                "controller_auth_configured": True,
                                "missing": [],
                            },
                        },
                    }
                ),
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox_controller_capabilities" in check.message
    assert "/capabilities" in check.remediation


def test_install_validation_requires_support_bundle_redaction_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--support-bundle-redaction-evidence" in check.message
    assert "redaction evidence" in check.remediation


def test_install_validation_requires_restore_drill_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BACKUP_RESTORE_DRILL]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--restore-drill-verification" in check.message
    assert "restore drill" in check.remediation


def test_install_validation_requires_object_storage_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--object-storage-verification" in check.message
    assert "object storage" in check.remediation


def test_install_validation_requires_secret_manager_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SECRET_MANAGER_READ]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--secret-manager-verification" in check.message
    assert "secret manager" in check.remediation


def test_install_validation_requires_redis_queue_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    checks = {item.name: item for item in report.checks}

    assert report.status == InstallValidationStatus.FAILED
    assert checks[InstallValidationCheckName.REDIS_CONNECTIVITY].status == "failed"
    assert "--redis-queue-verification" in checks[
        InstallValidationCheckName.REDIS_CONNECTIVITY
    ].message
    assert "Redis queue" in checks[InstallValidationCheckName.REDIS_CONNECTIVITY].remediation
    assert checks[InstallValidationCheckName.WORKER_QUEUE].status == "failed"
    assert "--redis-queue-verification" in checks[
        InstallValidationCheckName.WORKER_QUEUE
    ].message
    assert "worker queue" in checks[InstallValidationCheckName.WORKER_QUEUE].remediation


def test_install_validation_requires_event_stream_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.EVENT_STREAM]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--event-stream-verification" in check.message
    assert "event stream" in check.remediation


def test_install_validation_requires_audit_write_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.AUDIT_WRITE]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--audit-write-verification" in check.message
    assert "audit" in check.remediation


def test_install_validation_requires_trace_collector_evidence_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.TRACE_COLLECTOR]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--trace-collector-verification" in check.message
    assert "trace collector" in check.remediation


def test_install_validation_requires_release_package_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--release-package" in check.message
    assert "release package" in check.remediation


def test_install_validation_requires_migration_plan_for_private():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.DATABASE_MIGRATION]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--migration-plan" in check.message
    assert "migration" in check.remediation


def test_install_validation_runner_skips_web_workspace_without_base_url():
    client = RecordingInstallValidationHttpClient()
    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.WEB_WORKSPACE_HEALTH]

    assert check.status == "skipped"
    assert "--web-base-url" in check.message
    assert all(not url.startswith("http://web.local") for url, _ in client.requests)


def test_install_validation_runner_fails_on_incomplete_web_workspace_contract():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": False,
                                "provider": "disabled",
                                "controller_required": False,
                                "controller_configured": False,
                                "controller_endpoint_configured": False,
                                "controller_auth_configured": False,
                                "missing": ["provider"],
                            },
                        },
                    }
                ),
            )
        if url == "http://web.local/":
            return InstallValidationHttpResponse(
                status_code=200,
                body='<main data-testid="chat-column"></main>',
            )
        if url == "http://web.local/assets/main.js":
            return InstallValidationHttpResponse(status_code=404, body="missing")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            web_base_url="http://web.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.WEB_WORKSPACE_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "readiness status" in check.message
    assert "workspace script" in check.message
    assert check.remediation


def test_install_validation_runner_fails_when_web_workspace_login_or_composer_is_missing():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": False,
                                "provider": "disabled",
                                "controller_required": False,
                                "controller_configured": False,
                                "controller_endpoint_configured": False,
                                "controller_auth_configured": False,
                                "missing": ["provider"],
                            },
                        },
                    }
                ),
            )
        if url == "http://web.local/":
            return InstallValidationHttpResponse(
                status_code=200,
                body=(
                    "<title>Taroai Workspace</title>"
                    '<main data-testid="chat-column">'
                    '<div data-testid="conversation-log"></div>'
                    '<span data-readiness-status>Preflight unchecked</span>'
                    '<span data-readiness-model>Model unchecked</span>'
                    '<span data-readiness-sandbox>Sandbox unchecked</span>'
                    '<dd data-browser-storage-object>--</dd>'
                    '<ul data-artifact-list></ul>'
                    '<script src="./assets/main.js" type="module"></script>'
                ),
            )
        if url == "http://web.local/assets/main.js":
            return InstallValidationHttpResponse(
                status_code=200,
                body=(
                    'sessionStorage.getItem("taroai.accessToken");'
                    'headers["Authorization"] = `${bearerPrefix}${state.accessToken}`;'
                    'fetch(`${state.apiBase}${"/readyz"}`);'
                    "model_gateway; sandbox; missing.join;"
                    'fetch(`${state.apiBase}${"/api/storage/objects/"}${id}/content`);'
                ),
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            web_base_url="http://web.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.WEB_WORKSPACE_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "login email input" in check.message
    assert "composer input" in check.message
    assert "login endpoint" in check.message
    assert "session token storage" in check.message


def test_install_validation_runner_marks_readiness_failures_with_remediation():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {
                                "configured": False,
                                "missing": ["model", "credential"],
                            },
                            "sandbox": {
                                "configured": False,
                                "missing": [
                                    "sandbox_controller_base_url",
                                    "sandbox_controller_api_key",
                                ],
                            },
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            return InstallValidationHttpResponse(status_code=503, body="down")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    checks = {check.name: check for check in report.checks}

    assert report.status == InstallValidationStatus.FAILED
    assert checks[InstallValidationCheckName.MODEL_GATEWAY_HEALTH].status == "failed"
    assert checks[InstallValidationCheckName.SANDBOX_HEALTH].status == "failed"
    assert checks[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH].status == "failed"
    assert "model, credential" in checks[InstallValidationCheckName.MODEL_GATEWAY_HEALTH].message
    assert (
        "sandbox_controller_base_url, sandbox_controller_api_key"
        in checks[InstallValidationCheckName.SANDBOX_HEALTH].message
    )
    assert (
        "TAROAI_SANDBOX_CONTROLLER_API_KEY"
        in checks[InstallValidationCheckName.SANDBOX_HEALTH].remediation
    )
    assert checks[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH].remediation


def test_install_validation_runner_does_not_echo_api_request_exceptions():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        raise RuntimeError("probe-secret-value")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )
    report_json = report.model_dump_json()
    check = {item.name: item for item in report.checks}[
        InstallValidationCheckName.API_HEALTH
    ]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "API health/readiness request failed" in check.message
    assert "probe-secret-value" not in report_json


def test_install_validation_runner_does_not_echo_browser_request_exceptions():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": True,
                                "provider": "playwright",
                            },
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            raise RuntimeError("probe-secret-value")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
        ),
        http_client=client,
    )
    report_json = report.model_dump_json()
    check = {item.name: item for item in report.checks}[
        InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH
    ]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser-controller health request failed" in check.message
    assert "probe-secret-value" not in report_json


def test_install_validation_runner_does_not_echo_web_request_exceptions():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": False,
                                "provider": "disabled",
                            },
                        },
                    }
                ),
            )
        if url == "http://web.local/":
            raise RuntimeError("probe-secret-value")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            web_base_url="http://web.local",
        ),
        http_client=client,
    )
    report_json = report.model_dump_json()
    check = {item.name: item for item in report.checks}[
        InstallValidationCheckName.WEB_WORKSPACE_HEALTH
    ]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "Web Workspace health request failed" in check.message
    assert "probe-secret-value" not in report_json


def test_install_validation_skips_browser_controller_when_browser_provider_disabled():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        client.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": False,
                                "provider": "disabled",
                                "controller_required": False,
                                "controller_configured": False,
                                "controller_endpoint_configured": False,
                                "controller_auth_configured": False,
                                "missing": ["provider"],
                            },
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            raise AssertionError("browser-controller health should not be called")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert check.status == "skipped"
    assert check.message == "browser provider is disabled"
    assert all(url != "http://browser.local/healthz" for url, _ in client.requests)


def test_install_validation_fails_when_enabled_browser_controller_is_missing():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": False,
                                "provider": "playwright",
                                "controller_required": True,
                                "controller_configured": False,
                                "controller_endpoint_configured": True,
                                "controller_auth_configured": False,
                                "missing": ["browser_controller_api_key"],
                            },
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            raise AssertionError("browser-controller health should not be called")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert check.status == "failed"
    assert "browser_controller_api_key" in check.message
    assert "TAROAI_BROWSER_CONTROLLER_API_KEY" in check.remediation


def test_install_validation_rejects_local_browser_controller_key():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        client.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": ready_browser_controller_readiness(),
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            raise AssertionError("browser-controller health should not be called")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="local_browser_controller_key_2026_dev_only",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser_controller_api_key uses a default value" in check.message
    assert "TAROAI_BROWSER_CONTROLLER_API_KEY" in check.remediation
    assert "local_browser_controller_key_2026_dev_only" not in check.message
    assert all(url != "http://browser.local/healthz" for url, _ in client.requests)


def test_install_validation_requires_browser_lifecycle_evidence_when_enabled_for_private():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        client.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": ready_browser_controller_readiness(),
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            return InstallValidationHttpResponse(
                status_code=200,
                body='{"status":"ok","service":"taroai-browser-controller"}',
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="private_browser_controller_key_2026",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--browser-controller-verification" in check.message
    assert check.remediation


def test_install_validation_requires_browser_lifecycle_evidence_when_enabled_for_cloud():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        client.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                                "browser": {
                                    "configured": True,
                                    "provider": "playwright",
                                    "controller_required": True,
                                    "controller_configured": True,
                                    "controller_endpoint_configured": True,
                                    "controller_auth_configured": True,
                                    "capabilities_checked": True,
                                    "auth_required_declared": True,
                                    "session_ttl_enforced_declared": True,
                                    "max_session_ttl_seconds": 900,
                                    "max_sessions": 50,
                                    "max_sessions_per_tenant": 20,
                                    "max_sessions_per_run": 3,
                                    "missing": [],
                                },
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            return InstallValidationHttpResponse(
                status_code=200,
                body='{"status":"ok","service":"taroai-browser-controller"}',
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="cloud_browser_controller_key_2026",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--browser-controller-verification" in check.message
    assert check.remediation


def test_install_validation_rejects_enabled_browser_controller_without_capabilities():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        client.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": {
                                "configured": True,
                                "provider": "playwright",
                                "controller_required": True,
                                "controller_configured": True,
                                "controller_endpoint_configured": True,
                                "controller_auth_configured": True,
                                "missing": [],
                            },
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            raise AssertionError("browser health should not run before capabilities pass")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="cloud_browser_controller_key_2026",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser_controller_capabilities" in check.message
    assert "/capabilities" in check.remediation


def test_install_validation_rejects_weak_browser_controller_readiness_capabilities():
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        client.requests.append((url, headers or {}))
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            browser = ready_browser_controller_readiness()
            browser.update(
                {
                    "auth_required_declared": False,
                    "session_ttl_enforced_declared": False,
                    "max_sessions": 0,
                    "max_sessions_per_run": 0,
                }
            )
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {"configured": True, "missing": []},
                            "browser": browser,
                        },
                    }
                ),
            )
        if url == "http://browser.local/healthz":
            raise AssertionError("browser health should not run before capabilities pass")
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="cloud_browser_controller_key_2026",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser_auth_required_declared" in check.message
    assert "browser_session_ttl_enforced_declared" in check.message
    assert "browser_max_sessions" in check.message
    assert "browser_max_sessions_per_run" in check.message


def test_install_validation_rejects_local_sandbox_controller_key():
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_controller_api_key="local_sandbox_controller_key_2026_dev_only",
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox_controller_api_key uses a default value" in check.message
    assert "TAROAI_SANDBOX_CONTROLLER_API_KEY" in check.remediation
    assert "local_sandbox_controller_key_2026_dev_only" not in check.message


def test_install_validation_cli_reads_sandbox_controller_api_key(monkeypatch):
    monkeypatch.setenv(
        "TAROAI_SANDBOX_CONTROLLER_API_KEY",
        "local_sandbox_controller_key_2026_dev_only",
    )

    config = parse_args([])

    assert (
        config.sandbox_controller_api_key
        == "local_sandbox_controller_key_2026_dev_only"
    )


def test_install_validation_cli_reads_output_path(monkeypatch):
    monkeypatch.setenv(
        "TAROAI_INSTALL_VALIDATION_OUTPUT",
        "install-validation.json",
    )

    config = parse_args([])

    assert config.output_path == "install-validation.json"


def test_install_validation_output_writer_creates_parent_directory(tmp_path: Path):
    output_path = tmp_path / "reports" / "install-validation.json"

    install_validation_cli.write_install_validation_output(output_path, '{"status":"failed"}')

    assert output_path.read_text() == '{"status":"failed"}\n'


def test_install_validation_output_writer_preserves_existing_report_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_path = tmp_path / "reports" / "install-validation.json"
    original_report = '{"status":"passed","existing":"keep"}\n'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(original_report, encoding="utf-8")

    original_write_text = Path.write_text

    def failing_write_text(self, data, *args, **kwargs):
        if self.parent == output_path.parent and output_path.name in self.name:
            original_write_text(self, '{"partial": ', *args, **kwargs)
            raise OSError("install validation write failed")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    with pytest.raises(OSError, match="install validation write failed"):
        install_validation_cli.write_install_validation_output(
            output_path,
            '{"status":"failed"}',
        )

    assert output_path.read_text(encoding="utf-8") == original_report
    assert not list(output_path.parent.glob(f".{output_path.name}*.tmp"))


def test_install_validation_runner_accepts_model_gateway_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "model-gateway-verification.json"
    result_path.write_text(
        OpenAICompatibleModelGatewayVerificationResult(
            verified=True,
            base_url="https://model.example.com/v1",
            model="gpt-4.1",
            provider_id="provider_sales",
            response_id="response_verify_1",
            planned_step_count=1,
            planned_tool_names=["planning.record"],
            input_tokens=12,
            output_tokens=16,
            total_tokens=28,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            model_gateway_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["base_url"] == "https://model.example.com/v1"
    assert check.metadata["model"] == "gpt-4.1"
    assert check.metadata["provider_id"] == "provider_sales"
    assert check.metadata["planned_step_count"] == 1
    assert check.metadata["total_tokens"] == 28


def test_install_validation_runner_rejects_model_gateway_evidence_with_api_key(
    tmp_path: Path,
):
    result_path = tmp_path / "model-gateway-verification.json"
    result_path.write_text(
        json.dumps(
            {
                "verified": True,
                "base_url": "https://model.example.com/v1",
                "model": "gpt-4.1",
                "provider_id": "provider_sales",
                "response_id": "response_verify_1",
                "planned_step_count": 1,
                "planned_tool_names": ["planning.record"],
                "input_tokens": 12,
                "output_tokens": 16,
                "total_tokens": 28,
                "api_key": "probe-secret-value",
            }
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            model_gateway_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert check.status == "failed"
    assert "could not be read or matched to the schema" in check.message
    assert "probe-secret-value" not in report.model_dump_json()


def test_install_validation_runner_rejects_model_gateway_direct_config_mismatch(
    tmp_path: Path,
):
    result_path = tmp_path / "model-gateway-verification.json"
    result_path.write_text(
        OpenAICompatibleModelGatewayVerificationResult(
            verified=True,
            base_url="https://other-model.example.com/v1",
            model="other-model",
            response_id="response_verify_1",
            planned_step_count=1,
            planned_tool_names=["planning.record"],
            input_tokens=12,
            output_tokens=16,
            total_tokens=28,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "status": "ready",
                        "checks": {
                            "model_gateway": {
                                "configured": True,
                                "gateway_type": "openai_compatible",
                                "base_url": "https://model.example.com/v1",
                                "model": "gpt-4.1",
                                "missing": [],
                            },
                            "sandbox": {"configured": True, "missing": []},
                        },
                    }
                ),
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            model_gateway_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "model gateway verification base_url did not match API readiness" in check.message
    assert "model gateway verification model did not match API readiness" in check.message


def test_install_validation_runner_rejects_model_gateway_provider_registry_mismatch(
    tmp_path: Path,
):
    result_path = tmp_path / "model-gateway-verification.json"
    result_path.write_text(
        OpenAICompatibleModelGatewayVerificationResult(
            verified=True,
            base_url="https://model.example.com/v1",
            model="gpt-4.1",
            provider_id="support-openai",
            response_id="response_verify_1",
            planned_step_count=1,
            planned_tool_names=["planning.record"],
            input_tokens=12,
            output_tokens=16,
            total_tokens=28,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "status": "ready",
                        "checks": {
                            "model_gateway": {
                                "configured": True,
                                "gateway_type": "provider_registry",
                                "provider_ids": ["sales-openai"],
                                "configured_provider_ids": ["sales-openai"],
                                "missing": [],
                            },
                            "sandbox": {"configured": True, "missing": []},
                        },
                    }
                ),
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            model_gateway_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "model gateway verification provider_id did not match API readiness" in check.message


def test_install_validation_runner_rejects_model_gateway_without_expected_plan_tool(
    tmp_path: Path,
):
    result_path = tmp_path / "model-gateway-verification.json"
    result_path.write_text(
        OpenAICompatibleModelGatewayVerificationResult(
            verified=True,
            base_url="https://model.example.com/v1",
            model="gpt-4.1",
            provider_id="provider_sales",
            response_id="response_verify_1",
            planned_step_count=1,
            planned_tool_names=["filesystem.delete"],
            input_tokens=12,
            output_tokens=16,
            total_tokens=28,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            model_gateway_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "model gateway did not return expected planning tool planning.record" in (
        check.message
    )


def test_install_validation_runner_fails_on_model_gateway_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "model-gateway-verification.json"
    result_path.write_text(
        OpenAICompatibleModelGatewayVerificationResult(
            verified=False,
            base_url="https://model.example.com/v1",
            model="gpt-4.1",
            provider_id="provider_sales",
            response_id="",
            planned_step_count=0,
            planned_tool_names=[],
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            model_gateway_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.MODEL_GATEWAY_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "model gateway verification did not pass" in check.message
    assert "model gateway response id was empty" in check.message
    assert "model gateway returned no planned steps" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_sandbox_lifecycle_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
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
            session_listed=True,
            tenant_session_scope_enforced=True,
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
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["provider"] == "e2b"
    assert check.metadata["session_id"] == "sandbox_verify_1"
    assert check.metadata["post_destroy_command_blocked"] is True
    assert check.metadata["command_scope_enforced"] is True
    assert check.metadata["file_scope_enforced"] is True
    assert check.metadata["file_read_scope_enforced"] is True
    assert check.metadata["snapshot_scope_enforced"] is True
    assert check.metadata["capabilities_checked"] is True
    assert check.metadata["network_isolation_declared"] is True
    assert check.metadata["session_ttl_enforced_declared"] is True
    assert check.metadata["runtime_isolation_declared"] is True
    assert check.metadata["image_policy_enforced_declared"] is True
    assert check.metadata["allowed_image_count"] == 1
    assert check.metadata["max_sessions_declared"] is True
    assert check.metadata["max_sessions_per_tenant_declared"] is True
    assert check.metadata["session_listed"] is True
    assert check.metadata["tenant_session_scope_enforced"] is True
    assert check.metadata["artifact_path"] == "/workspace/artifacts/sandbox-lifecycle.txt"
    assert check.metadata["artifact_listed"] is True
    assert check.metadata["artifact_downloaded"] is True
    assert check.metadata["downloaded_artifact_content_length"] == len(
        "sandbox lifecycle ok\n"
    )


def test_install_validation_rejects_weak_enterprise_sandbox_readiness_capabilities(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
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
            session_listed=True,
            tenant_session_scope_enforced=True,
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
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {
                                "configured": True,
                                "provider": "e2b",
                                "controller_required": True,
                                "controller_configured": True,
                                "controller_endpoint_configured": True,
                                "controller_auth_configured": True,
                                "capabilities_checked": True,
                                "network_isolation_declared": True,
                                "filesystem_isolation_declared": True,
                                "resource_limits_declared": True,
                                "destroy_supported_declared": True,
                                "session_ttl_enforced_declared": True,
                                "runtime_isolation_declared": False,
                                "image_policy_enforced_declared": False,
                                "allowed_image_count": 0,
                                "missing": [],
                            },
                        },
                    }
                ),
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox_runtime_isolation_declared" in check.message
    assert "sandbox_image_policy_enforced_declared" in check.message
    assert "sandbox_allowed_image_count" in check.message


def test_install_validation_runner_rejects_post_destroy_command_access(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            post_destroy_command_blocked=False,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            file_read_scope_enforced=True,
            snapshot_scope_enforced=True,
            artifact_path="/workspace/artifacts/sandbox-lifecycle.txt",
            artifact_listed=True,
            artifact_downloaded=True,
            downloaded_artifact_content_length=len("sandbox lifecycle ok\n"),
            session_listed=True,
            tenant_session_scope_enforced=True,
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
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox command was not blocked after session destroy" in check.message


def test_install_validation_runner_rejects_non_enterprise_sandbox_provider_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="docker",
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
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "private deployments require sandbox verification evidence from k8s or e2b"
        in check.message
    )
    assert "docker" in check.message


def test_install_validation_runner_rejects_e2b_sandbox_evidence_for_air_gapped(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
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
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="airgap-acme",
            deployment_mode="air_gapped",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "air_gapped deployments require sandbox verification evidence from k8s"
        in check.message
    )
    assert "e2b" in check.message


def test_install_validation_runner_rejects_sandbox_lifecycle_without_artifact_round_trip(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
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
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox artifact was not listed" in check.message
    assert "sandbox artifact was not downloaded" in check.message
    assert "sandbox downloaded artifact content was empty" in check.message


def test_install_validation_runner_rejects_sandbox_without_auth_challenge(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            file_read_scope_enforced=True,
            snapshot_scope_enforced=True,
            artifact_path="/workspace/artifacts/sandbox-lifecycle.txt",
            artifact_listed=True,
            artifact_downloaded=True,
            downloaded_artifact_content_length=len("sandbox lifecycle ok\n"),
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_controller_api_key="private_sandbox_controller_key_2026",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox controller auth challenge was not enforced" in check.message


def test_sandbox_lifecycle_failure_details_rejects_missing_global_auth_probe():
    result = SandboxLifecycleVerificationResult(
        provider="e2b",
        session_id="sandbox_verify_1",
        session_created=True,
        command_executed=True,
        session_destroyed=True,
        session_destroy_confirmed=True,
        output_redacted=True,
        command_scope_enforced=True,
        file_scope_enforced=True,
        snapshot_scope_enforced=True,
        artifact_path="/workspace/artifacts/sandbox-lifecycle.txt",
        artifact_listed=True,
        artifact_downloaded=True,
        downloaded_artifact_content_length=len("sandbox lifecycle ok\n"),
        session_listed=True,
        tenant_session_scope_enforced=True,
        capabilities_checked=True,
        network_isolation_declared=True,
        filesystem_isolation_declared=True,
        resource_limits_declared=True,
        destroy_supported_declared=True,
        session_ttl_enforced_declared=True,
        max_session_ttl_seconds_declared=True,
        max_sessions_declared=True,
        max_sessions_per_tenant_declared=True,
        max_sessions_per_run_declared=True,
        auth_challenge_enforced=True,
        auth_tenant_session_list_challenge_enforced=True,
        auth_global_session_list_challenge_enforced=False,
        auth_capabilities_challenge_enforced=True,
    )

    details = install_validation_cli.sandbox_lifecycle_verification_failure_details(
        result,
        auth_challenge_required=True,
    )

    assert "sandbox controller global session-list auth challenge was not enforced" in details


def test_sandbox_lifecycle_failure_details_rejects_missing_runtime_policy_evidence():
    result = SandboxLifecycleVerificationResult(
        provider="e2b",
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
        session_listed=True,
        tenant_session_scope_enforced=True,
        capabilities_checked=True,
        network_isolation_declared=True,
        filesystem_isolation_declared=True,
        resource_limits_declared=True,
        destroy_supported_declared=True,
        session_ttl_enforced_declared=True,
        max_session_ttl_seconds_declared=True,
        max_sessions_declared=True,
        max_sessions_per_tenant_declared=True,
        max_sessions_per_run_declared=True,
    )

    details = install_validation_cli.sandbox_lifecycle_verification_failure_details(
        result
    )

    assert "sandbox controller did not declare runtime isolation" in details
    assert "sandbox controller did not declare image policy enforcement" in details
    assert "sandbox controller did not declare allowed runtime images" in details


def test_install_validation_runner_rejects_sandbox_lifecycle_without_scope_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="e2b",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=False,
            file_scope_enforced=False,
            artifact_path="/workspace/artifacts/sandbox-lifecycle.txt",
            artifact_listed=True,
            artifact_downloaded=True,
            downloaded_artifact_content_length=len("sandbox lifecycle ok\n"),
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox command scope was not enforced" in check.message
    assert "sandbox file scope was not enforced" in check.message
    assert "sandbox file read scope was not enforced" in check.message


def test_install_validation_runner_rejects_sandbox_lifecycle_without_snapshot_scope_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        """
        {
          "provider": "e2b",
          "session_id": "sandbox_verify_1",
          "session_created": true,
          "command_executed": true,
          "session_destroyed": true,
          "output_redacted": true,
          "command_scope_enforced": true,
          "file_scope_enforced": true,
          "artifact_path": "/workspace/artifacts/sandbox-lifecycle.txt",
          "artifact_listed": true,
          "artifact_downloaded": true,
          "downloaded_artifact_content_length": 21,
          "session_listed": true,
          "tenant_session_scope_enforced": true,
          "capabilities_checked": true,
          "network_isolation_declared": true,
          "filesystem_isolation_declared": true,
          "resource_limits_declared": true,
          "destroy_supported_declared": true,
          "session_ttl_enforced_declared": true,
          "max_session_ttl_seconds_declared": true,
          "max_sessions_per_tenant_declared": true,
          "max_sessions_per_run_declared": true
        }
        """
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox snapshot scope was not enforced" in check.message


def test_install_validation_runner_rejects_sandbox_provider_evidence_mismatch(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(result_path)
    client = RecordingInstallValidationHttpClient()

    def get(url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        if url == "http://api.local/healthz":
            return InstallValidationHttpResponse(status_code=200, body='{"status":"ok"}')
        if url == "http://api.local/readyz":
            return InstallValidationHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "checks": {
                            "model_gateway": {"configured": True, "missing": []},
                            "sandbox": {
                                "configured": True,
                                "provider": "e2b",
                                "missing": [],
                            },
                        },
                    }
                ),
            )
        return InstallValidationHttpResponse(status_code=404, body="not found")

    client.get = get

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox verification provider did not match API readiness provider" in check.message


def test_install_validation_runner_requires_kubernetes_provider_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="k8s",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            file_read_scope_enforced=True,
            snapshot_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "--kubernetes-sandbox-verification" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_kubernetes_provider_evidence(
    tmp_path: Path,
):
    runtime_image = "ghcr.io/customer/sandbox-runtime@sha256:" + ("a" * 64)
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(
        KubernetesSandboxVerificationResult(
            provider="kubernetes",
            image=runtime_image,
            namespace="taroai",
            session_id="sandbox_kubernetes_verify",
            pod_name="taroai-sandbox-kubernetes-verify",
            network_policy_name="taroai-sandbox-kubernetes-verify-deny-all",
            network_policy_default_deny=True,
            network_policy_types=["Ingress", "Egress"],
            network_policy_session_selector={
                "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
            },
            exit_code=0,
            stdout_contains="KUBERNETES VERIFY OK",
            downloaded_content="KUBERNETES VERIFY OK",
            file_paths=[
                "/workspace/artifacts/report.txt",
                "/workspace/input.txt",
            ],
            snapshot_uri="kubernetes://taroai/pods/taroai-sandbox-kubernetes-verify/snapshots/one",
            destroyed=True,
            service_account_name="sandbox-runner",
            runtime_class_name="gvisor",
            runtime_class_required=True,
            allowed_images=["ghcr.io/customer/sandbox-runtime@sha256:*"],
            image_pull_policy="IfNotPresent",
            memory_limit="512Mi",
            cpu_limit="500m",
            ephemeral_storage_limit="1Gi",
            **hardened_kubernetes_sandbox_fields(),
            run_as_user=65532,
            run_as_group=65532,
            runtime_policy=KubernetesRuntimePolicyVerificationResult(
                namespace="taroai",
                verified=True,
                namespace_labels={
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                },
                resource_quota_name="taroai-sandbox-runtime-quota",
                resource_quota_hard={
                    "pods": "50",
                    "requests.cpu": "20",
                    "requests.memory": "40Gi",
                    "limits.cpu": "40",
                    "limits.memory": "80Gi",
                    "requests.ephemeral-storage": "100Gi",
                    "limits.ephemeral-storage": "200Gi",
                },
                limit_range_name="taroai-sandbox-runtime-limits",
                limit_range_default={
                    "cpu": "1000m",
                    "memory": "1Gi",
                    "ephemeral-storage": "2Gi",
                },
                limit_range_default_request={
                    "cpu": "500m",
                    "memory": "512Mi",
                    "ephemeral-storage": "1Gi",
                },
                limit_range_max={
                    "memory": "4Gi",
                    "ephemeral-storage": "8Gi",
                },
                network_policy_name="taroai-sandbox-runtime-default-deny",
                network_policy_pod_selector={
                    "app.kubernetes.io/name": "taroai-sandbox-session",
                },
                network_policy_types=["Ingress", "Egress"],
                network_policy_default_deny=True,
                controller_service_account_name="sandbox-controller",
                controller_service_account_exists=True,
                runner_service_account_name="sandbox-runner",
                runner_service_account_token_automount_disabled=True,
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
                controller_role_least_privilege=True,
                controller_role_binding_valid=True,
            ),
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert check.status == "passed"
    assert check.metadata["kubernetes_sandbox_verified"] is True
    assert check.metadata["kubernetes_namespace"] == "taroai"
    assert check.metadata["kubernetes_resource_quota_pods"] == "50"
    assert check.metadata["kubernetes_limit_range_default_memory"] == "1Gi"
    assert check.metadata["kubernetes_network_policy_default_deny"] is True
    assert check.metadata["kubernetes_session_network_policy_default_deny"] is True
    assert (
        check.metadata["kubernetes_session_network_policy_selector_session_id"]
        == "sandbox_kubernetes_verify"
    )
    assert check.metadata["kubernetes_allowed_image_count"] == 1
    assert check.metadata["kubernetes_image_digest_pinned"] is True


def test_install_validation_runner_rejects_weak_kubernetes_pod_hardening(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    evidence = json.loads(
        valid_kubernetes_sandbox_evidence(
            host_network=True,
            privileged=True,
            allow_privilege_escalation=True,
            automount_service_account_token=True,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "host network access was enabled" in check.message
    assert "container privileged mode was enabled" in check.message
    assert "container privilege escalation was allowed" in check.message
    assert "service account token automount was enabled" in check.message


def test_install_validation_runner_rejects_weak_kubernetes_controller_rbac(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    weak_runtime_policy = valid_kubernetes_runtime_policy_evidence().model_copy(
        update={
            "controller_role_least_privilege": False,
            "controller_role_binding_valid": False,
        }
    )
    evidence = json.loads(
        valid_kubernetes_sandbox_evidence(
            runtime_policy=weak_runtime_policy,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "controller Role was not least-privilege" in check.message
    assert "controller RoleBinding was not valid" in check.message


def test_install_validation_runner_rejects_kubernetes_runner_service_account_token(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    weak_runtime_policy = valid_kubernetes_runtime_policy_evidence().model_copy(
        update={"runner_service_account_token_automount_disabled": False}
    )
    evidence = json.loads(
        valid_kubernetes_sandbox_evidence(
            runtime_policy=weak_runtime_policy,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "runner ServiceAccount token automount was not disabled" in check.message


def test_install_validation_runner_rejects_kubernetes_session_service_account_mismatch(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    evidence = json.loads(
        valid_kubernetes_sandbox_evidence(
            service_account_name="sandbox-controller",
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "session ServiceAccount did not match verified runner ServiceAccount"
        in check.message
    )
    assert "session ServiceAccount used the controller ServiceAccount" in check.message


def test_install_validation_runner_rejects_kubernetes_runtime_policy_namespace_mismatch(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    evidence = json.loads(
        valid_kubernetes_sandbox_evidence(
            namespace="unverified-sandbox-namespace",
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "sandbox session namespace did not match verified runtime policy namespace"
        in check.message
    )


def test_install_validation_runner_rejects_kubernetes_session_network_policy_mismatch(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    write_valid_kubernetes_lifecycle_evidence(lifecycle_path)
    evidence = json.loads(
        valid_kubernetes_sandbox_evidence(
            network_policy_default_deny=False,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "session NetworkPolicy does not default-deny sandbox traffic"
        in check.message
    )


def test_install_validation_runner_rejects_kubernetes_evidence_with_service_token(
    tmp_path: Path,
):
    runtime_image = "ghcr.io/customer/sandbox-runtime@sha256:" + ("a" * 64)
    lifecycle_path = tmp_path / "sandbox-verification.json"
    lifecycle_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="kubernetes",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            snapshot_scope_enforced=True,
            artifact_path="/workspace/artifacts/sandbox-lifecycle.txt",
            artifact_listed=True,
            artifact_downloaded=True,
            downloaded_artifact_content_length=len("sandbox lifecycle ok\n"),
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    kubernetes_evidence = json.loads(
        KubernetesSandboxVerificationResult(
            provider="kubernetes",
            image=runtime_image,
            namespace="taroai",
            session_id="sandbox_kubernetes_verify",
            pod_name="taroai-sandbox-kubernetes-verify",
            network_policy_name="taroai-sandbox-kubernetes-verify-deny-all",
            network_policy_default_deny=True,
            network_policy_types=["Ingress", "Egress"],
            network_policy_session_selector={
                "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
            },
            exit_code=0,
            stdout_contains="KUBERNETES VERIFY OK",
            downloaded_content="KUBERNETES VERIFY OK",
            file_paths=["/workspace/artifacts/report.txt"],
            snapshot_uri="kubernetes://taroai/pods/pod/snapshots/one",
            destroyed=True,
            service_account_name="sandbox-runner",
            runtime_class_name="gvisor",
            runtime_class_required=True,
            allowed_images=["ghcr.io/customer/sandbox-runtime@sha256:*"],
            image_pull_policy="IfNotPresent",
            memory_limit="512Mi",
            cpu_limit="500m",
            ephemeral_storage_limit="1Gi",
            **hardened_kubernetes_sandbox_fields(),
            run_as_user=65532,
            run_as_group=65532,
            runtime_policy=KubernetesRuntimePolicyVerificationResult(
                namespace="taroai",
                verified=True,
                namespace_labels={
                    "pod-security.kubernetes.io/enforce": "restricted",
                },
                resource_quota_name="taroai-sandbox-runtime-quota",
                resource_quota_hard={"pods": "50"},
                limit_range_name="taroai-sandbox-runtime-limits",
                limit_range_default={"memory": "1Gi"},
                limit_range_default_request={"memory": "512Mi"},
                limit_range_max={"memory": "4Gi"},
                network_policy_name="taroai-sandbox-runtime-default-deny",
                network_policy_pod_selector={
                    "app.kubernetes.io/name": "taroai-sandbox-session",
                },
                network_policy_types=["Ingress", "Egress"],
                network_policy_default_deny=True,
                controller_service_account_name="sandbox-controller",
                controller_service_account_exists=True,
                runner_service_account_name="sandbox-runner",
                runner_service_account_token_automount_disabled=True,
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
                controller_role_least_privilege=True,
                controller_role_binding_valid=True,
            ),
        ).model_dump_json()
    )
    kubernetes_evidence["service_account_token"] = "probe-secret-value"
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(json.dumps(kubernetes_evidence))
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert check.status == "failed"
    assert (
        "kubernetes sandbox provider verification result could not be read or matched"
        in check.message
    )
    assert "probe-secret-value" not in report.model_dump_json()


def test_install_validation_runner_rejects_kubernetes_unapproved_runtime_image(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    lifecycle_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="kubernetes",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            snapshot_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(
        KubernetesSandboxVerificationResult(
            provider="kubernetes",
            image="python:3.12-slim",
            namespace="taroai",
            session_id="sandbox_kubernetes_verify",
            pod_name="taroai-sandbox-kubernetes-verify",
            network_policy_name="taroai-sandbox-kubernetes-verify-deny-all",
            network_policy_default_deny=True,
            network_policy_types=["Ingress", "Egress"],
            network_policy_session_selector={
                "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
            },
            exit_code=0,
            stdout_contains="KUBERNETES VERIFY OK",
            downloaded_content="KUBERNETES VERIFY OK",
            file_paths=["/workspace/artifacts/report.txt"],
            snapshot_uri="kubernetes://taroai/pods/pod/snapshots/one",
            destroyed=True,
            service_account_name="sandbox-runner",
            runtime_class_name="gvisor",
            runtime_class_required=True,
            allowed_images=["python:3.12-slim"],
            image_pull_policy="IfNotPresent",
            memory_limit="512Mi",
            cpu_limit="500m",
            ephemeral_storage_limit="1Gi",
            **hardened_kubernetes_sandbox_fields(),
            run_as_user=65532,
            run_as_group=65532,
            runtime_policy=KubernetesRuntimePolicyVerificationResult(
                namespace="taroai",
                verified=True,
                namespace_labels={
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                },
                resource_quota_name="taroai-sandbox-runtime-quota",
                resource_quota_hard={"pods": "50"},
                limit_range_name="taroai-sandbox-runtime-limits",
                limit_range_default={"memory": "1Gi"},
                limit_range_default_request={"memory": "512Mi"},
                limit_range_max={"memory": "4Gi"},
                network_policy_name="taroai-sandbox-runtime-default-deny",
                network_policy_pod_selector={
                    "app.kubernetes.io/name": "taroai-sandbox-session",
                },
                network_policy_types=["Ingress", "Egress"],
                network_policy_default_deny=True,
                controller_service_account_name="sandbox-controller",
                controller_service_account_exists=True,
                runner_service_account_name="sandbox-runner",
                runner_service_account_token_automount_disabled=True,
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
                controller_role_least_privilege=True,
                controller_role_binding_valid=True,
            ),
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "approved registry or digest" in check.message


def test_install_validation_runner_rejects_kubernetes_without_artifact_evidence(
    tmp_path: Path,
):
    runtime_image = "ghcr.io/customer/sandbox-runtime@sha256:" + ("a" * 64)
    lifecycle_path = tmp_path / "sandbox-verification.json"
    lifecycle_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="kubernetes",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            snapshot_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(
        KubernetesSandboxVerificationResult(
            provider="kubernetes",
            image=runtime_image,
            namespace="taroai",
            session_id="sandbox_kubernetes_verify",
            pod_name="taroai-sandbox-kubernetes-verify",
            network_policy_name="taroai-sandbox-kubernetes-verify-deny-all",
            network_policy_default_deny=True,
            network_policy_types=["Ingress", "Egress"],
            network_policy_session_selector={
                "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
            },
            exit_code=0,
            stdout_contains="KUBERNETES VERIFY OK",
            downloaded_content="KUBERNETES VERIFY OK",
            file_paths=["/workspace/input.txt"],
            snapshot_uri="kubernetes://taroai/pods/pod/snapshots/one",
            destroyed=True,
            service_account_name="sandbox-runner",
            runtime_class_name="gvisor",
            runtime_class_required=True,
            allowed_images=["ghcr.io/customer/sandbox-runtime@sha256:*"],
            image_pull_policy="IfNotPresent",
            memory_limit="512Mi",
            cpu_limit="500m",
            ephemeral_storage_limit="1Gi",
            **hardened_kubernetes_sandbox_fields(),
            run_as_user=65532,
            run_as_group=65532,
            runtime_policy=KubernetesRuntimePolicyVerificationResult(
                namespace="taroai",
                verified=True,
                namespace_labels={
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                },
                resource_quota_name="taroai-sandbox-runtime-quota",
                resource_quota_hard={"pods": "50"},
                limit_range_name="taroai-sandbox-runtime-limits",
                limit_range_default={"memory": "1Gi"},
                limit_range_default_request={"memory": "512Mi"},
                limit_range_max={"memory": "4Gi"},
                network_policy_name="taroai-sandbox-runtime-default-deny",
                network_policy_pod_selector={
                    "app.kubernetes.io/name": "taroai-sandbox-session",
                },
                network_policy_types=["Ingress", "Egress"],
                network_policy_default_deny=True,
                controller_service_account_name="sandbox-controller",
                controller_service_account_exists=True,
                runner_service_account_name="sandbox-runner",
                runner_service_account_token_automount_disabled=True,
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
                controller_role_least_privilege=True,
                controller_role_binding_valid=True,
            ),
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "publishable artifact" in check.message


def test_install_validation_runner_rejects_kubernetes_empty_downloaded_artifact(
    tmp_path: Path,
):
    runtime_image = "ghcr.io/customer/sandbox-runtime@sha256:" + ("a" * 64)
    lifecycle_path = tmp_path / "sandbox-verification.json"
    lifecycle_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="kubernetes",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            snapshot_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(
        KubernetesSandboxVerificationResult(
            provider="kubernetes",
            image=runtime_image,
            namespace="taroai",
            session_id="sandbox_kubernetes_verify",
            pod_name="taroai-sandbox-kubernetes-verify",
            network_policy_name="taroai-sandbox-kubernetes-verify-deny-all",
            network_policy_default_deny=True,
            network_policy_types=["Ingress", "Egress"],
            network_policy_session_selector={
                "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
            },
            exit_code=0,
            stdout_contains="KUBERNETES VERIFY OK",
            downloaded_content="",
            file_paths=["/workspace/artifacts/report.txt"],
            snapshot_uri="kubernetes://taroai/pods/pod/snapshots/one",
            destroyed=True,
            service_account_name="sandbox-runner",
            runtime_class_name="gvisor",
            runtime_class_required=True,
            allowed_images=["ghcr.io/customer/sandbox-runtime@sha256:*"],
            image_pull_policy="IfNotPresent",
            memory_limit="512Mi",
            cpu_limit="500m",
            ephemeral_storage_limit="1Gi",
            **hardened_kubernetes_sandbox_fields(),
            run_as_user=65532,
            run_as_group=65532,
            runtime_policy=KubernetesRuntimePolicyVerificationResult(
                namespace="taroai",
                verified=True,
                namespace_labels={
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                },
                resource_quota_name="taroai-sandbox-runtime-quota",
                resource_quota_hard={"pods": "50"},
                limit_range_name="taroai-sandbox-runtime-limits",
                limit_range_default={"memory": "1Gi"},
                limit_range_default_request={"memory": "512Mi"},
                limit_range_max={"memory": "4Gi"},
                network_policy_name="taroai-sandbox-runtime-default-deny",
                network_policy_pod_selector={
                    "app.kubernetes.io/name": "taroai-sandbox-session",
                },
                network_policy_types=["Ingress", "Egress"],
                network_policy_default_deny=True,
                controller_service_account_name="sandbox-controller",
                controller_service_account_exists=True,
                runner_service_account_name="sandbox-runner",
                runner_service_account_token_automount_disabled=True,
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
                controller_role_least_privilege=True,
                controller_role_binding_valid=True,
            ),
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "downloaded artifact content was empty" in check.message


def test_install_validation_runner_rejects_kubernetes_without_runtime_class(
    tmp_path: Path,
):
    lifecycle_path = tmp_path / "sandbox-verification.json"
    lifecycle_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="k8s",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=True,
            session_destroyed=True,
            session_destroy_confirmed=True,
            output_redacted=True,
            command_scope_enforced=True,
            file_scope_enforced=True,
            snapshot_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            capabilities_checked=True,
            network_isolation_declared=True,
            filesystem_isolation_declared=True,
            resource_limits_declared=True,
            destroy_supported_declared=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
        ).model_dump_json()
    )
    kubernetes_path = tmp_path / "kubernetes-sandbox-verification.json"
    kubernetes_path.write_text(
        KubernetesSandboxVerificationResult(
            provider="kubernetes",
            image="python:3.12-slim",
            namespace="taroai",
            session_id="sandbox_kubernetes_verify",
            pod_name="taroai-sandbox-kubernetes-verify",
            network_policy_name="taroai-sandbox-kubernetes-verify-deny-all",
            network_policy_default_deny=True,
            network_policy_types=["Ingress", "Egress"],
            network_policy_session_selector={
                "taroai.sandbox_session_id": "sandbox_kubernetes_verify",
            },
            exit_code=0,
            stdout_contains="KUBERNETES VERIFY OK",
            downloaded_content="KUBERNETES VERIFY OK",
            file_paths=["/workspace/artifacts/report.txt"],
            snapshot_uri="kubernetes://taroai/pods/pod/snapshots/one",
            destroyed=True,
            service_account_name="sandbox-runner",
            runtime_class_name="",
            runtime_class_required=False,
            allowed_images=["python:3.12-slim"],
            image_pull_policy="IfNotPresent",
            memory_limit="512Mi",
            cpu_limit="500m",
            ephemeral_storage_limit="1Gi",
            **hardened_kubernetes_sandbox_fields(),
            run_as_user=65532,
            run_as_group=65532,
            runtime_policy=KubernetesRuntimePolicyVerificationResult(
                namespace="taroai",
                verified=True,
                namespace_labels={
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                },
                resource_quota_name="taroai-sandbox-runtime-quota",
                resource_quota_hard={"pods": "50"},
                limit_range_name="taroai-sandbox-runtime-limits",
                limit_range_default={"memory": "1Gi"},
                limit_range_default_request={"memory": "512Mi"},
                limit_range_max={"memory": "4Gi"},
                network_policy_name="taroai-sandbox-runtime-default-deny",
                network_policy_pod_selector={
                    "app.kubernetes.io/name": "taroai-sandbox-session",
                },
                network_policy_types=["Ingress", "Egress"],
                network_policy_default_deny=True,
                controller_service_account_name="sandbox-controller",
                controller_service_account_exists=True,
                runner_service_account_name="sandbox-runner",
                runner_service_account_token_automount_disabled=True,
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
                controller_role_least_privilege=True,
                controller_role_binding_valid=True,
            ),
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(lifecycle_path),
            kubernetes_sandbox_verification_path=str(kubernetes_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "runtime class was not required" in check.message
    assert "runtime class name was empty" in check.message


def test_install_validation_runner_fails_on_sandbox_lifecycle_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "sandbox-verification.json"
    result_path.write_text(
        SandboxLifecycleVerificationResult(
            provider="k8s",
            session_id="sandbox_verify_1",
            session_created=True,
            command_executed=False,
            session_destroyed=False,
            output_redacted=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            sandbox_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SANDBOX_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "sandbox command was not executed" in check.message
    assert "sandbox session was not destroyed" in check.message
    assert "sandbox session was not listed for concurrency checks" in check.message
    assert "sandbox session list did not enforce tenant scope" in check.message
    assert "sandbox verification output was not redacted" in check.message
    assert "sandbox controller capabilities were not checked" in check.message
    assert "sandbox controller did not declare filesystem isolation" in check.message
    assert "sandbox controller did not declare session TTL enforcement" in check.message
    assert "sandbox controller did not declare global session capacity" in check.message
    assert "sandbox controller did not declare tenant session capacity" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_browser_controller_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "browser-controller-verification.json"
    result_path.write_text(
        BrowserControllerVerificationResult(
            provider="playwright",
            session_id="browser_verify_1",
            capabilities_checked=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
            navigation_allowlist_enforced_declared=True,
            navigation_allowed_host_count=2,
            session_opened=True,
            action_executed=True,
            session_deleted=True,
            session_delete_confirmed=True,
            duplicate_session_rejected=True,
            action_scope_enforced=True,
            session_read_scope_enforced=True,
            session_delete_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            screenshot_or_extract_verified=True,
            screenshot_uri=(
                "browser://tenant_acme/runs/run_1/sessions/"
                "browser_verify_1/screenshot.png"
            ),
            screenshot_content_length=len(b"browser-png"),
            extract_text_length=0,
            output_redacted=True,
        ).model_dump_json()
    )
    client = BrowserReadyInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["provider"] == "playwright"
    assert check.metadata["session_id"] == "browser_verify_1"
    assert check.metadata["session_delete_confirmed"] is True
    assert check.metadata["duplicate_session_rejected"] is True
    assert check.metadata["action_scope_enforced"] is True
    assert check.metadata["session_listed"] is True
    assert check.metadata["tenant_session_scope_enforced"] is True
    assert check.metadata["capabilities_checked"] is True
    assert check.metadata["session_ttl_enforced_declared"] is True
    assert check.metadata["max_sessions_per_tenant_declared"] is True
    assert check.metadata["max_sessions_per_run_declared"] is True
    assert check.metadata["screenshot_uri"] == (
        "browser://tenant_acme/runs/run_1/sessions/browser_verify_1/screenshot.png"
    )
    assert check.metadata["screenshot_content_length"] == len(b"browser-png")
    assert check.metadata["extract_text_length"] == 0


def test_install_validation_runner_rejects_browser_controller_without_capture_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "browser-controller-verification.json"
    result_path.write_text(
        BrowserControllerVerificationResult(
            provider="playwright",
            session_id="browser_verify_1",
            capabilities_checked=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
            navigation_allowlist_enforced_declared=True,
            navigation_allowed_host_count=2,
            session_opened=True,
            action_executed=True,
            session_deleted=True,
            session_delete_confirmed=True,
            duplicate_session_rejected=True,
            action_scope_enforced=True,
            session_read_scope_enforced=True,
            session_delete_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            screenshot_or_extract_verified=True,
            output_redacted=True,
        ).model_dump_json()
    )
    client = BrowserReadyInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser screenshot URI was not recorded" in check.message
    assert "browser screenshot content was empty" in check.message


def test_install_validation_runner_rejects_browser_controller_without_scope_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "browser-controller-verification.json"
    result_path.write_text(
        BrowserControllerVerificationResult(
            provider="playwright",
            session_id="browser_verify_1",
            capabilities_checked=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
            navigation_allowlist_enforced_declared=True,
            navigation_allowed_host_count=2,
            session_opened=True,
            action_executed=True,
            session_deleted=True,
            session_delete_confirmed=True,
            duplicate_session_rejected=False,
            action_scope_enforced=False,
            session_listed=True,
            tenant_session_scope_enforced=True,
            screenshot_or_extract_verified=True,
            screenshot_uri=(
                "browser://tenant_acme/runs/run_1/sessions/"
                "browser_verify_1/screenshot.png"
            ),
            screenshot_content_length=len(b"browser-png"),
            extract_text_length=0,
            output_redacted=True,
        ).model_dump_json()
    )
    client = BrowserReadyInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "duplicate browser session was not rejected" in check.message
    assert "browser action scope was not enforced" in check.message
    assert "browser session read scope was not enforced" in check.message
    assert "browser session delete scope was not enforced" in check.message


def test_install_validation_runner_rejects_browser_controller_without_auth_challenge(
    tmp_path: Path,
):
    result_path = tmp_path / "browser-controller-verification.json"
    result_path.write_text(
        BrowserControllerVerificationResult(
            provider="playwright",
            session_id="browser_verify_1",
            session_opened=True,
            action_executed=True,
            session_deleted=True,
            session_delete_confirmed=True,
            duplicate_session_rejected=True,
            action_scope_enforced=True,
            session_read_scope_enforced=True,
            session_delete_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            screenshot_or_extract_verified=True,
            screenshot_uri=(
                "browser://tenant_acme/runs/run_1/sessions/"
                "browser_verify_1/screenshot.png"
            ),
            screenshot_content_length=len(b"browser-png"),
            extract_text_length=0,
            output_redacted=True,
        ).model_dump_json()
    )
    client = BrowserReadyInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="private_browser_controller_key_2026",
            browser_controller_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser controller auth challenge was not enforced" in check.message


def test_install_validation_runner_rejects_browser_controller_provider_mismatch(
    tmp_path: Path,
):
    result_path = tmp_path / "browser-controller-verification.json"
    result_path.write_text(
        BrowserControllerVerificationResult(
            provider="browserbase",
            session_id="browser_verify_1",
            capabilities_checked=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
            session_opened=True,
            action_executed=True,
            session_deleted=True,
            session_delete_confirmed=True,
            duplicate_session_rejected=True,
            action_scope_enforced=True,
            session_read_scope_enforced=True,
            session_delete_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            screenshot_or_extract_verified=True,
            screenshot_uri=(
                "browser://tenant_acme/runs/run_1/sessions/"
                "browser_verify_1/screenshot.png"
            ),
            screenshot_content_length=len(b"browser-png"),
            extract_text_length=0,
            output_redacted=True,
        ).model_dump_json()
    )
    client = BrowserReadyInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "browser-controller verification provider did not match API readiness provider"
        in check.message
    )


def test_browser_controller_failure_details_rejects_missing_global_auth_probe():
    result = BrowserControllerVerificationResult(
        provider="playwright",
        session_id="browser_verify_1",
        session_opened=True,
        action_executed=True,
        session_deleted=True,
        session_delete_confirmed=True,
        duplicate_session_rejected=True,
        action_scope_enforced=True,
        session_read_scope_enforced=True,
        session_delete_scope_enforced=True,
        session_listed=True,
        tenant_session_scope_enforced=True,
        capabilities_checked=True,
        session_ttl_enforced_declared=True,
        max_session_ttl_seconds_declared=True,
        max_sessions_declared=True,
        max_sessions_per_tenant_declared=True,
        max_sessions_per_run_declared=True,
        screenshot_or_extract_verified=True,
        screenshot_uri=(
            "browser://tenant_acme/runs/run_1/sessions/"
            "browser_verify_1/screenshot.png"
        ),
        screenshot_content_length=len(b"browser-png"),
        extract_text_length=0,
        auth_challenge_enforced=True,
        auth_tenant_session_list_challenge_enforced=True,
        auth_global_session_list_challenge_enforced=False,
        auth_capabilities_challenge_enforced=True,
        output_redacted=True,
    )

    details = install_validation_cli.browser_controller_verification_failure_details(
        result,
        auth_challenge_required=True,
    )

    assert "browser controller global session-list auth challenge was not enforced" in details


def test_install_validation_runner_fails_on_browser_controller_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "browser-controller-verification.json"
    result_path.write_text(
        BrowserControllerVerificationResult(
            provider="playwright",
            session_id="browser_verify_1",
            session_opened=True,
            action_executed=False,
            session_deleted=False,
            session_delete_confirmed=False,
            session_listed=False,
            tenant_session_scope_enforced=False,
            screenshot_or_extract_verified=False,
            output_redacted=False,
        ).model_dump_json()
    )
    client = BrowserReadyInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "browser action was not executed" in check.message
    assert "browser session was not deleted" in check.message
    assert "browser session deletion was not confirmed" in check.message
    assert "browser session was not listed for concurrency checks" in check.message
    assert "browser session list did not enforce tenant scope" in check.message
    assert "browser screenshot or extract was not verified" in check.message
    assert "browser verification output was not redacted" in check.message
    assert check.remediation


def test_install_validation_runner_validates_release_package_integrity(tmp_path: Path):
    release_path = tmp_path / "taroai-release.zip"
    release_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package(release_path)
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            expected_release_package_checksum_sha256=release_result.checksum_sha256,
            release_package_signature_path=str(signature_path),
            release_package_trusted_public_keys={key_id: public_key},
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert check.status == "passed"
    assert check.metadata["package_path"] == str(release_path)
    assert check.metadata["checksum_sha256"] == release_result.checksum_sha256
    assert check.metadata["expected_checksum_sha256"] == release_result.checksum_sha256
    assert check.metadata["signature_valid"] is True
    assert check.metadata["signature_key_id"] == key_id


def test_install_validation_runner_rejects_unsigned_release_package_for_private(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    release_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            expected_release_package_checksum_sha256=release_result.checksum_sha256,
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "release package signature is required" in check.message
    assert check.remediation


def test_install_validation_runner_validates_signed_release_package(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    release_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package(release_path)
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            expected_release_package_checksum_sha256=release_result.checksum_sha256,
            release_package_signature_path=str(signature_path),
            release_package_trusted_public_keys={key_id: public_key},
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert check.status == "passed"
    assert check.metadata["signature_valid"] is True
    assert check.metadata["signature_key_id"] == key_id


def test_install_validation_runner_uses_release_transfer_evidence(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    evidence_path = tmp_path / "release-transfer-evidence.json"
    release_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package(release_path)
    build_release_transfer_evidence(
        ReleaseTransferEvidenceBuildConfig(
            package_path=release_path,
            signature_path=signature_path,
            key_id=key_id,
            public_key_base64=public_key,
            output_path=evidence_path,
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            release_transfer_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert check.status == "passed"
    assert check.metadata["package_path"] == str(release_path)
    assert check.metadata["release_transfer_evidence_path"] == str(evidence_path)
    assert check.metadata["checksum_sha256"] == release_result.checksum_sha256
    assert check.metadata["expected_checksum_sha256"] == release_result.checksum_sha256
    assert check.metadata["signature_valid"] is True
    assert check.metadata["signature_key_id"] == key_id
    assert check.metadata["transfer_evidence_package_version"] == "0.1.0"
    assert check.metadata["transfer_evidence_app_version"] == "0.1.0"
    assert check.metadata["transfer_evidence_migration_count"] == 45


def test_install_validation_runner_rejects_transfer_evidence_signature_outside_package_dir(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    evidence_path = tmp_path / "release-transfer-evidence.json"
    release_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package(release_path)
    report = build_release_transfer_evidence(
        ReleaseTransferEvidenceBuildConfig(
            package_path=release_path,
            signature_path=signature_path,
            key_id=key_id,
            public_key_base64=public_key,
            output_path=evidence_path,
        )
    )
    unsafe_report = report.model_copy(
        update={"signature_path": tmp_path.parent / "outside-release.sig.json"}
    )
    evidence_path.write_text(unsafe_report.model_dump_json())
    client = RecordingInstallValidationHttpClient()

    validation = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            expected_release_package_checksum_sha256=release_result.checksum_sha256,
            release_transfer_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in validation.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert validation.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "release transfer evidence signature path must stay with release package" in (
        check.message
    )


def test_install_validation_runner_rejects_transfer_evidence_package_outside_evidence_dir(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    evidence_path = tmp_path / "release-transfer-evidence.json"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package(release_path)
    report = build_release_transfer_evidence(
        ReleaseTransferEvidenceBuildConfig(
            package_path=release_path,
            signature_path=signature_path,
            key_id=key_id,
            public_key_base64=public_key,
            output_path=evidence_path,
        )
    )
    unsafe_report = report.model_copy(
        update={"package_path": tmp_path.parent / "outside-release.zip"}
    )
    evidence_path.write_text(unsafe_report.model_dump_json())
    client = RecordingInstallValidationHttpClient()

    validation = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_transfer_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in validation.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert validation.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "release transfer evidence package path must stay with transfer evidence" in (
        check.message
    )


def test_install_validation_runner_resolves_transfer_evidence_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    release_path = tmp_path / "taroai-release.zip"
    evidence_path = tmp_path / "release-transfer-evidence.json"
    release_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package(release_path)
    report = build_release_transfer_evidence(
        ReleaseTransferEvidenceBuildConfig(
            package_path=release_path,
            signature_path=signature_path,
            key_id=key_id,
            public_key_base64=public_key,
            output_path=evidence_path,
        )
    )
    portable_report = report.model_copy(
        update={
            "package_path": Path("taroai-release.zip"),
            "signature_path": Path("taroai-release.zip.sig.json"),
        }
    )
    evidence_path.write_text(portable_report.model_dump_json())
    monkeypatch.chdir(tmp_path.parent)
    client = RecordingInstallValidationHttpClient()

    validation = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_transfer_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in validation.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert check.status == "passed"
    assert check.metadata["package_path"] == str(release_path)
    assert check.metadata["checksum_sha256"] == release_result.checksum_sha256
    assert check.metadata["signature_valid"] is True
    assert check.metadata["signature_key_id"] == key_id


def test_install_validation_runner_rejects_bad_release_transfer_evidence(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    evidence_path = tmp_path / "release-transfer-evidence.json"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    evidence_path.write_text('{"valid": false, "private_key": "should-not-leak"}')
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            release_transfer_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "release transfer evidence could not be read" in check.message
    assert "should-not-leak" not in check.message
    assert check.metadata["release_transfer_evidence_path"] == str(evidence_path)


def test_install_validation_runner_fails_on_release_package_checksum_mismatch(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            expected_release_package_checksum_sha256="0" * 64,
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert check.metadata["package_path"] == str(release_path)
    assert "checksum does not match" in check.message
    assert check.remediation


def test_install_validation_runner_reports_stale_release_upgrade_matrix(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    rewrite_zip_entry_content(
        release_path,
        tampered_path,
        "infra/package/upgrade-matrix.md",
        b"| 0.1.0 | 0.1.0 | 001_initial to 011_license_validations |\n",
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(tampered_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "upgrade matrix must cover migration range "
            "001_initial to 046_agent_api_keys"
    ) in check.message
    assert check.metadata["upgrade_matrix_error_count"] == 1


def test_install_validation_runner_reports_invalid_release_python_source(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    rewrite_zip_entry_content(
        release_path,
        tampered_path,
        "apps/api/src/taroai/deployment/release_package.py",
        b"def broken(:\n",
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(tampered_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "apps/api/src/taroai/deployment/release_package.py" in check.message
    assert check.metadata["invalid_python_error_count"] == 1


def test_install_validation_runner_reports_release_import_dependency_gap(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        release_path,
        tampered_path,
        "apps/api/src/taroai/agent/__init__.py",
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(tampered_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "apps/api/src/taroai/agent/__init__.py" in check.message
    assert check.metadata["missing_import_dependency_count"] == 1


def test_install_validation_runner_reports_release_script_module_gap(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    append_executable_zip_entry(
        release_path,
        "scripts/verify-customer-health.sh",
        "#!/usr/bin/env sh\nexec python -m taroai.customer.health_check \"$@\"\n",
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "apps/api/src/taroai/customer/health_check.py" in check.message
    assert check.metadata["missing_script_module_count"] == 1


def test_install_validation_runner_reports_release_missing_required_count(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        release_path,
        tampered_path,
        "scripts/verify-release-package.sh",
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(tampered_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "scripts/verify-release-package.sh" in check.message
    assert check.metadata["missing_required_entry_count"] == 1


def test_install_validation_runner_reports_release_secret_pattern_count(
    tmp_path: Path,
):
    import zipfile

    release_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(release_path, mode="a") as archive:
        archive.writestr(
            "docs/operations/leaked-key.txt",
            "sk-test-release-package-secret-000000",
        )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="cloud-acme",
            deployment_mode="cloud",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "docs/operations/leaked-key.txt" in check.message
    assert check.metadata["secret_pattern_entry_count"] == 1


def test_install_validation_runner_fails_on_release_package_signature(
    tmp_path: Path,
):
    release_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=release_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, _, _ = sign_release_package(release_path)
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            release_package_path=str(release_path),
            release_package_signature_path=str(signature_path),
            release_package_trusted_public_keys={},
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "release package signing key is not trusted" in check.message
    assert check.metadata["signature_valid"] is False
    assert check.remediation


def test_install_validation_runner_accepts_clean_migration_plan(tmp_path: Path):
    migration_plan_path = tmp_path / "migration-plan.json"
    migration_plan_path.write_text(
        MigrationPlan(
            available_versions=["001_initial.sql", "002_next.sql"],
            applied_versions=["001_initial.sql", "002_next.sql"],
            pending_versions=[],
            unknown_applied_versions=[],
            up_to_date=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            migration_plan_path=str(migration_plan_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.DATABASE_MIGRATION]

    assert check.status == "passed"
    assert check.metadata["plan_path"] == str(migration_plan_path)
    assert check.metadata["available_count"] == 2
    assert check.metadata["applied_count"] == 2
    assert check.metadata["pending_count"] == 0
    assert check.metadata["unknown_applied_count"] == 0


def test_install_validation_runner_fails_on_pending_migration_plan(tmp_path: Path):
    migration_plan_path = tmp_path / "migration-plan.json"
    migration_plan_path.write_text(
        MigrationPlan(
            available_versions=["001_initial.sql", "002_next.sql"],
            applied_versions=["001_initial.sql"],
            pending_versions=["002_next.sql"],
            unknown_applied_versions=["999_outside_package.sql"],
            up_to_date=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            migration_plan_path=str(migration_plan_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.DATABASE_MIGRATION]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "pending migrations: 002_next.sql" in check.message
    assert "unknown applied migrations: 999_outside_package.sql" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_object_storage_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "object-storage-verification.json"
    result_path.write_text(
        ObjectStorageVerificationResult(
            bucket="taroai-artifacts",
            object_key="verify/object.txt",
            uploaded_bytes=128,
            downloaded_bytes=128,
            upload_etag="etag",
            read_signed_url_method="GET",
            write_signed_url_method="PUT",
            deleted=True,
            object_missing_after_delete=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            object_storage_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["bucket"] == "taroai-artifacts"
    assert check.metadata["uploaded_bytes"] == 128
    assert check.metadata["downloaded_bytes"] == 128


def test_install_validation_runner_rejects_object_storage_evidence_with_raw_signed_urls(
    tmp_path: Path,
):
    result_path = tmp_path / "object-storage-verification.json"
    result_path.write_text(
        json.dumps(
            {
                "bucket": "taroai-artifacts",
                "object_key": "verify/object.txt",
                "uploaded_bytes": 128,
                "downloaded_bytes": 128,
                "upload_etag": "etag",
                "read_signed_url_method": "GET",
                "write_signed_url_method": "PUT",
                "deleted": True,
                "object_missing_after_delete": True,
                "read_signed_url": (
                    "https://agent:probe-secret-value@storage.local/bucket/object.txt"
                    "?X-Amz-Signature=probe-secret-value"
                ),
            }
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            object_storage_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE]

    assert check.status == "failed"
    assert "could not be read or matched to the schema" in check.message
    assert "probe-secret-value" not in report.model_dump_json()


def test_install_validation_runner_fails_on_object_storage_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "object-storage-verification.json"
    result_path.write_text(
        ObjectStorageVerificationResult(
            bucket="taroai-artifacts",
            object_key="verify/object.txt",
            uploaded_bytes=128,
            downloaded_bytes=0,
            upload_etag="etag",
            read_signed_url_method="GET",
            write_signed_url_method="PUT",
            deleted=False,
            object_missing_after_delete=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            object_storage_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "downloaded bytes did not match uploaded bytes" in check.message
    assert "object was not deleted" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_redis_queue_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "redis-queue-verification.json"
    result_path.write_text(
        RedisQueueVerificationResult(
            key_prefix="taroai:verify:redis",
            ping_ok=True,
            acknowledged_job_id="job_ack",
            acknowledged_job_status=JobStatus.SUCCEEDED,
            recovered_job_id="job_recovered",
            recovered_job_status=JobStatus.RUNNING,
            recovered_job_attempts=2,
            dead_letter_job_id="job_dead",
            dead_letter_job_status=JobStatus.DEAD_LETTER,
            dead_letter_count=1,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            redis_queue_verification_path=str(result_path),
        ),
        http_client=client,
    )

    checks = {item.name: item for item in report.checks}
    redis_check = checks[InstallValidationCheckName.REDIS_CONNECTIVITY]
    worker_check = checks[InstallValidationCheckName.WORKER_QUEUE]

    assert redis_check.status == "passed"
    assert redis_check.metadata["result_path"] == str(result_path)
    assert redis_check.metadata["ping_ok"] is True
    assert worker_check.status == "passed"
    assert worker_check.metadata["acknowledged_job_status"] == "succeeded"
    assert worker_check.metadata["recovered_job_attempts"] == 2
    assert worker_check.metadata["dead_letter_count"] == 1


def test_install_validation_runner_rejects_redis_queue_evidence_with_raw_url(
    tmp_path: Path,
):
    result_path = tmp_path / "redis-queue-verification.json"
    result_path.write_text(
        json.dumps(
            {
                "key_prefix": "taroai:verify:redis",
                "ping_ok": True,
                "acknowledged_job_id": "job_ack",
                "acknowledged_job_status": "succeeded",
                "recovered_job_id": "job_recovered",
                "recovered_job_status": "running",
                "recovered_job_attempts": 2,
                "dead_letter_job_id": "job_dead",
                "dead_letter_job_status": "dead_letter",
                "dead_letter_count": 1,
                "redis_url": "redis://:probe-secret-value@redis.local:6379/0",
            }
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            redis_queue_verification_path=str(result_path),
        ),
        http_client=client,
    )

    checks = {item.name: item for item in report.checks}
    redis_check = checks[InstallValidationCheckName.REDIS_CONNECTIVITY]
    worker_check = checks[InstallValidationCheckName.WORKER_QUEUE]

    assert redis_check.status == "failed"
    assert worker_check.status == "failed"
    assert "could not be read or matched to the schema" in redis_check.message
    assert "could not be read or matched to the schema" in worker_check.message
    assert "probe-secret-value" not in report.model_dump_json()


def test_install_validation_runner_fails_on_redis_queue_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "redis-queue-verification.json"
    result_path.write_text(
        RedisQueueVerificationResult(
            key_prefix="taroai:verify:redis",
            ping_ok=False,
            acknowledged_job_id="job_ack",
            acknowledged_job_status=JobStatus.RUNNING,
            recovered_job_id="job_recovered",
            recovered_job_status=JobStatus.SUCCEEDED,
            recovered_job_attempts=1,
            dead_letter_job_id="job_dead",
            dead_letter_job_status=JobStatus.RUNNING,
            dead_letter_count=0,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            redis_queue_verification_path=str(result_path),
        ),
        http_client=client,
    )

    checks = {item.name: item for item in report.checks}
    redis_check = checks[InstallValidationCheckName.REDIS_CONNECTIVITY]
    worker_check = checks[InstallValidationCheckName.WORKER_QUEUE]

    assert report.status == InstallValidationStatus.FAILED
    assert redis_check.status == "failed"
    assert "Redis ping failed" in redis_check.message
    assert worker_check.status == "failed"
    assert "acknowledged job status was not succeeded" in worker_check.message
    assert "dead-letter job status was not dead_letter" in worker_check.message
    assert worker_check.remediation


def test_install_validation_runner_accepts_secret_manager_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "secret-manager-verification.json"
    result_path.write_text(
        SecretManagerVerificationResult(
            backend="aws_secrets_manager",
            reference_checked=True,
            lease_created=True,
            read_succeeded=True,
            scoped_context_enforced=True,
            output_redacted=True,
            secret_value_exposed=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            secret_manager_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SECRET_MANAGER_READ]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["backend"] == "aws_secrets_manager"
    assert check.metadata["read_succeeded"] is True
    assert check.metadata["scoped_context_enforced"] is True


def test_install_validation_runner_fails_on_secret_manager_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "secret-manager-verification.json"
    result_path.write_text(
        SecretManagerVerificationResult(
            backend="aws_secrets_manager",
            reference_checked=False,
            lease_created=False,
            read_succeeded=False,
            scoped_context_enforced=False,
            output_redacted=False,
            secret_value_exposed=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            secret_manager_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SECRET_MANAGER_READ]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "secret reference was not checked" in check.message
    assert "secret read did not succeed" in check.message
    assert "verification output exposed a secret value" in check.message
    assert check.remediation


def test_install_validation_runner_does_not_echo_secret_manager_evidence_parse_errors(
    tmp_path: Path,
):
    result_path = tmp_path / "secret-manager-verification.json"
    result_path.write_text(
        json.dumps(
            {
                "backend": "aws_secrets_manager",
                "reference_checked": True,
                "lease_created": True,
                "read_succeeded": True,
                "scoped_context_enforced": True,
                "output_redacted": True,
                "secret_value_exposed": False,
                "raw_secret_value": "probe-secret-value",
            }
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            secret_manager_verification_path=str(result_path),
        ),
        http_client=client,
    )
    report_json = report.model_dump_json()

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SECRET_MANAGER_READ]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "probe-secret-value" not in report_json
    assert "raw_secret_value" not in report_json


def test_install_validation_runner_does_not_echo_dependency_evidence_parse_errors(
    tmp_path: Path,
):
    migration_path = tmp_path / "migration-plan.json"
    migration_path.write_text(
        json.dumps(
            {
                "available_versions": ["001_initial.sql"],
                "applied_versions": ["001_initial.sql"],
                "pending_versions": [],
                "unknown_applied_versions": [],
                "up_to_date": "probe-secret-value",
            }
        )
    )
    object_storage_path = tmp_path / "object-storage-verification.json"
    object_storage_path.write_text(
        json.dumps(
            {
                "bucket": "taroai-artifacts",
                "object_key": "verify/object.txt",
                "uploaded_bytes": "probe-secret-value",
                "downloaded_bytes": 0,
                "read_signed_url_method": "GET",
                "write_signed_url_method": "PUT",
                "deleted": False,
                "object_missing_after_delete": False,
            }
        )
    )
    redis_path = tmp_path / "redis-queue-verification.json"
    redis_path.write_text(
        json.dumps(
            {
                "key_prefix": "taroai:verify:redis",
                "ping_ok": True,
                "acknowledged_job_id": "job_ack",
                "acknowledged_job_status": "probe-secret-value",
                "recovered_job_id": "job_recovered",
                "recovered_job_status": "running",
                "recovered_job_attempts": 2,
                "dead_letter_job_id": "job_dead",
                "dead_letter_job_status": "dead_letter",
                "dead_letter_count": 1,
            }
        )
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            migration_plan_path=str(migration_path),
            object_storage_verification_path=str(object_storage_path),
            redis_queue_verification_path=str(redis_path),
        ),
        http_client=RecordingInstallValidationHttpClient(),
    )
    report_json = report.model_dump_json()
    checks = {item.name: item for item in report.checks}

    assert report.status == InstallValidationStatus.FAILED
    assert checks[InstallValidationCheckName.DATABASE_MIGRATION].status == "failed"
    assert checks[InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE].status == "failed"
    assert checks[InstallValidationCheckName.REDIS_CONNECTIVITY].status == "failed"
    assert checks[InstallValidationCheckName.WORKER_QUEUE].status == "failed"
    assert "probe-secret-value" not in report_json


def test_install_validation_runner_accepts_event_stream_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "event-stream-verification.json"
    result_path.write_text(
        EventStreamVerificationResult(
            api_base_url="http://api.local",
            run_id="run_event_verify",
            first_event_sequence=7,
            stream_opened=True,
            event_id_received=True,
            after_sequence_replay_succeeded=True,
            last_event_id_replay_succeeded=True,
            tenant_scope_enforced=True,
            safe_payload_confirmed=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            event_stream_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.EVENT_STREAM]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["stream_opened"] is True
    assert check.metadata["last_event_id_replay_succeeded"] is True
    assert check.metadata["api_base_url"] == "http://api.local"
    assert check.metadata["run_id"] == "run_event_verify"
    assert check.metadata["first_event_sequence"] == 7


def test_install_validation_runner_fails_on_event_stream_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "event-stream-verification.json"
    result_path.write_text(
        EventStreamVerificationResult(
            stream_opened=False,
            event_id_received=False,
            after_sequence_replay_succeeded=False,
            last_event_id_replay_succeeded=False,
            tenant_scope_enforced=False,
            safe_payload_confirmed=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            event_stream_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.EVENT_STREAM]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "event stream did not open" in check.message
    assert "Last-Event-ID replay did not succeed" in check.message
    assert "event stream tenant scope was not enforced" in check.message
    assert check.remediation


def test_install_validation_runner_rejects_event_stream_api_base_mismatch(
    tmp_path: Path,
):
    result_path = tmp_path / "event-stream-verification.json"
    result_path.write_text(
        EventStreamVerificationResult(
            api_base_url="https://other-api.local",
            run_id="run_event_verify",
            first_event_sequence=7,
            stream_opened=True,
            event_id_received=True,
            after_sequence_replay_succeeded=True,
            last_event_id_replay_succeeded=True,
            tenant_scope_enforced=True,
            safe_payload_confirmed=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            event_stream_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.EVENT_STREAM]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "event stream api base URL did not match install validation API" in check.message


def test_install_validation_runner_accepts_audit_write_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "audit-write-verification.json"
    result_path.write_text(
        AuditWriteVerificationResult(
            api_base_url="http://api.local",
            run_id="run_audit_verify",
            write_succeeded=True,
            read_back_succeeded=True,
            tenant_scope_enforced=True,
            sensitive_metadata_redacted=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            audit_write_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.AUDIT_WRITE]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["write_succeeded"] is True
    assert check.metadata["sensitive_metadata_redacted"] is True
    assert check.metadata["api_base_url"] == "http://api.local"
    assert check.metadata["run_id"] == "run_audit_verify"


def test_install_validation_runner_rejects_audit_write_api_base_mismatch(
    tmp_path: Path,
):
    result_path = tmp_path / "audit-write-verification.json"
    result_path.write_text(
        AuditWriteVerificationResult(
            api_base_url="https://other-api.local",
            run_id="run_audit_verify",
            write_succeeded=True,
            read_back_succeeded=True,
            tenant_scope_enforced=True,
            sensitive_metadata_redacted=True,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            audit_write_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.AUDIT_WRITE]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "audit api base URL did not match install validation API" in check.message


def test_install_validation_runner_rejects_event_and_audit_run_id_mismatch(
    tmp_path: Path,
):
    event_result_path = tmp_path / "event-stream-verification.json"
    audit_result_path = tmp_path / "audit-write-verification.json"
    event_result_path.write_text(
        EventStreamVerificationResult(
            api_base_url="http://api.local",
            run_id="run_event_verify",
            first_event_sequence=7,
            stream_opened=True,
            event_id_received=True,
            after_sequence_replay_succeeded=True,
            last_event_id_replay_succeeded=True,
            tenant_scope_enforced=True,
            safe_payload_confirmed=True,
        ).model_dump_json()
    )
    audit_result_path.write_text(
        AuditWriteVerificationResult(
            api_base_url="http://api.local",
            run_id="run_audit_verify",
            write_succeeded=True,
            read_back_succeeded=True,
            tenant_scope_enforced=True,
            sensitive_metadata_redacted=True,
        ).model_dump_json()
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            event_stream_verification_path=str(event_result_path),
            audit_write_verification_path=str(audit_result_path),
        ),
        http_client=RecordingInstallValidationHttpClient(),
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.AUDIT_WRITE]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert (
        "audit run id did not match event stream verification run id"
        in check.message
    )


def test_install_validation_runner_rejects_legacy_event_and_audit_verification_results(
    tmp_path: Path,
):
    event_result_path = tmp_path / "legacy-event-stream-verification.json"
    audit_result_path = tmp_path / "legacy-audit-write-verification.json"
    event_result_path.write_text(
        """
        {
          "stream_opened": true,
          "event_id_received": true,
          "after_sequence_replay_succeeded": true,
          "last_event_id_replay_succeeded": true,
          "tenant_scope_enforced": true,
          "safe_payload_confirmed": true
        }
        """
    )
    audit_result_path.write_text(
        """
        {
          "write_succeeded": true,
          "read_back_succeeded": true,
          "tenant_scope_enforced": true,
          "sensitive_metadata_redacted": true
        }
        """
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            event_stream_verification_path=str(event_result_path),
            audit_write_verification_path=str(audit_result_path),
        ),
        http_client=BrowserReadyInstallValidationHttpClient(),
    )

    checks = {item.name: item for item in report.checks}

    assert report.status == InstallValidationStatus.FAILED
    assert checks[InstallValidationCheckName.EVENT_STREAM].status == "failed"
    assert "event stream run id was not recorded" in checks[
        InstallValidationCheckName.EVENT_STREAM
    ].message
    assert "event stream api base URL was not recorded" in checks[
        InstallValidationCheckName.EVENT_STREAM
    ].message
    assert "event stream first event sequence was not recorded" in checks[
        InstallValidationCheckName.EVENT_STREAM
    ].message
    assert checks[InstallValidationCheckName.AUDIT_WRITE].status == "failed"
    assert "audit run id was not recorded" in checks[
        InstallValidationCheckName.AUDIT_WRITE
    ].message
    assert "audit api base URL was not recorded" in checks[
        InstallValidationCheckName.AUDIT_WRITE
    ].message


def test_install_validation_runner_validates_support_bundle_redaction_evidence(
    tmp_path: Path,
):
    bundle_path = tmp_path / "support-bundle.zip"
    redacted_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    write_zip(
        bundle_path,
        {
            "logs/api.log": "Authorization: Bearer session-token-abcdefghijk\n",
            "logs/worker.jsonl": json.dumps(
                {
                    "prompt": "summarize confidential renewal",
                    "safe": "kept",
                }
            ),
        },
    )
    redact_support_bundle_archive(
        SupportBundleRedactionConfig(
            input_path=bundle_path,
            output_path=redacted_path,
            evidence_path=evidence_path,
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            support_bundle_redaction_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(evidence_path)
    assert check.metadata["file_count"] == 2
    assert check.metadata["redacted_entry_count"] == 2
    assert check.metadata["redaction_bearer_token_count"] == 1
    assert check.metadata["redaction_sensitive_field_count"] == 1


def test_install_validation_runner_rejects_bad_support_bundle_redaction_evidence(
    tmp_path: Path,
):
    evidence_path = tmp_path / "support-bundle-redaction.json"
    evidence_path.write_text('{"valid": false, "secret": "customer-secret"}')
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            support_bundle_redaction_evidence_path=str(evidence_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "support bundle redaction evidence could not be read" in check.message
    assert "customer-secret" not in check.message
    assert "customer-secret" not in check.model_dump_json()


def test_install_validation_runner_fails_on_audit_write_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "audit-write-verification.json"
    result_path.write_text(
        AuditWriteVerificationResult(
            write_succeeded=False,
            read_back_succeeded=False,
            tenant_scope_enforced=False,
            sensitive_metadata_redacted=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            audit_write_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.AUDIT_WRITE]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "audit write did not succeed" in check.message
    assert "audit read-back did not succeed" in check.message
    assert "audit tenant scope was not enforced" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_trace_collector_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "trace-collector-verification.json"
    result_path.write_text(
        TraceCollectorVerificationResult(
            status="exported",
            endpoint_url="http://collector:4318/v1/traces",
            trace_id="trace_collector_verify",
            span_count=1,
            resource_span_count=1,
            scope_span_count=1,
            authorization_header_sent=True,
            secret_value_exposed=False,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            trace_collector_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.TRACE_COLLECTOR]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["span_count"] == 1
    assert check.metadata["authorization_header_sent"] is True


def test_install_validation_runner_redacts_verification_url_metadata(
    tmp_path: Path,
):
    model_result_path = tmp_path / "model-gateway-verification.json"
    model_result_path.write_text(
        OpenAICompatibleModelGatewayVerificationResult(
            verified=True,
            base_url=(
                "https://agent:probe-secret-value@model.local/v1"
                "?token=probe-secret-value#frag"
            ),
            model="gpt-4.1",
            provider_id="provider_sales",
            response_id="response_verify_1",
            planned_step_count=1,
            planned_tool_names=["planning.record"],
        ).model_dump_json()
    )
    event_result_path = tmp_path / "event-stream-verification.json"
    event_result_path.write_text(
        EventStreamVerificationResult(
            api_base_url=(
                "https://agent:probe-secret-value@api.local"
                "?token=probe-secret-value#frag"
            ),
            run_id="run_event_verify",
            first_event_sequence=7,
            stream_opened=True,
            event_id_received=True,
            after_sequence_replay_succeeded=True,
            last_event_id_replay_succeeded=True,
            tenant_scope_enforced=True,
            safe_payload_confirmed=True,
        ).model_dump_json()
    )
    audit_result_path = tmp_path / "audit-write-verification.json"
    audit_result_path.write_text(
        AuditWriteVerificationResult(
            api_base_url=(
                "https://agent:probe-secret-value@api.local"
                "?token=probe-secret-value#frag"
            ),
            run_id="run_audit_verify",
            write_succeeded=True,
            read_back_succeeded=True,
            tenant_scope_enforced=True,
            sensitive_metadata_redacted=True,
        ).model_dump_json()
    )
    trace_result_path = tmp_path / "trace-collector-verification.json"
    trace_result_path.write_text(
        TraceCollectorVerificationResult(
            status="exported",
            endpoint_url=(
                "https://agent:probe-secret-value@collector.local:4318/v1/traces"
                "?token=probe-secret-value#frag"
            ),
            trace_id="trace_collector_verify",
            span_count=1,
            resource_span_count=1,
            scope_span_count=1,
            authorization_header_sent=True,
            secret_value_exposed=False,
        ).model_dump_json()
    )
    browser_result_path = tmp_path / "browser-controller-verification.json"
    browser_result_path.write_text(
        BrowserControllerVerificationResult(
            provider="playwright",
            session_id="browser_verify_1",
            capabilities_checked=True,
            session_ttl_enforced_declared=True,
            max_session_ttl_seconds_declared=True,
            max_sessions_declared=True,
            max_sessions_per_tenant_declared=True,
            max_sessions_per_run_declared=True,
            navigation_allowlist_enforced_declared=True,
            navigation_allowed_host_count=2,
            session_opened=True,
            action_executed=True,
            session_deleted=True,
            session_delete_confirmed=True,
            duplicate_session_rejected=True,
            action_scope_enforced=True,
            session_read_scope_enforced=True,
            session_delete_scope_enforced=True,
            session_listed=True,
            tenant_session_scope_enforced=True,
            screenshot_or_extract_verified=True,
            screenshot_uri=(
                "https://agent:probe-secret-value@browser.local/screens/1.png"
                "?token=probe-secret-value#frag"
            ),
            screenshot_content_length=128,
            output_redacted=True,
        ).model_dump_json()
    )

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            browser_controller_api_key="browser_secret",
            model_gateway_verification_path=str(model_result_path),
            event_stream_verification_path=str(event_result_path),
            audit_write_verification_path=str(audit_result_path),
            trace_collector_verification_path=str(trace_result_path),
            browser_controller_verification_path=str(browser_result_path),
        ),
        http_client=BrowserReadyInstallValidationHttpClient(),
    )
    checks = {item.name: item for item in report.checks}

    assert (
        checks[InstallValidationCheckName.MODEL_GATEWAY_HEALTH].metadata["base_url"]
        == "https://model.local/v1"
    )
    assert (
        checks[InstallValidationCheckName.EVENT_STREAM].metadata["api_base_url"]
        == "https://api.local"
    )
    assert (
        checks[InstallValidationCheckName.AUDIT_WRITE].metadata["api_base_url"]
        == "https://api.local"
    )
    assert (
        checks[InstallValidationCheckName.TRACE_COLLECTOR].metadata["endpoint_url"]
        == "https://collector.local:4318/v1/traces"
    )
    assert (
        checks[InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH].metadata[
            "screenshot_uri"
        ]
        == "https://browser.local/screens/1.png"
    )
    assert "probe-secret-value" not in report.model_dump_json()


def test_install_validation_runner_fails_on_trace_collector_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "trace-collector-verification.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "exported",
                "endpoint_url": "http://collector:4318/v1/traces",
                "trace_id": "trace_collector_verify",
                "span_count": 0,
                "resource_span_count": 0,
                "scope_span_count": 0,
                "authorization_header_sent": False,
                "secret_value_exposed": True,
            }
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            trace_collector_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.TRACE_COLLECTOR]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "trace collector did not receive a span" in check.message
    assert "verification output exposed a secret value" in check.message
    assert check.remediation


def test_install_validation_runner_accepts_restore_drill_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "restore-drill-verification.json"
    result_path.write_text(
        RestoreDrillVerificationResult(
            drill_id="restore_drill_2026_07",
            backup_manifest_generated=True,
            restore_order_executed=True,
            database_restore_verified=True,
            object_storage_restore_verified=True,
            redis_restore_or_rebuild_verified=True,
            config_restore_verified=True,
            post_restore_validation_passed=True,
            rpo_minutes=45,
            rto_minutes=25,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            restore_drill_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BACKUP_RESTORE_DRILL]

    assert check.status == "passed"
    assert check.metadata["result_path"] == str(result_path)
    assert check.metadata["drill_id"] == "restore_drill_2026_07"
    assert check.metadata["rpo_minutes"] == 45
    assert check.metadata["rto_minutes"] == 25


def test_install_validation_runner_fails_on_restore_drill_verification_result(
    tmp_path: Path,
):
    result_path = tmp_path / "restore-drill-verification.json"
    result_path.write_text(
        RestoreDrillVerificationResult(
            drill_id="restore_drill_2026_07",
            backup_manifest_generated=False,
            restore_order_executed=False,
            database_restore_verified=False,
            object_storage_restore_verified=False,
            redis_restore_or_rebuild_verified=False,
            config_restore_verified=False,
            post_restore_validation_passed=False,
            rpo_minutes=120,
            rto_minutes=90,
        ).model_dump_json()
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            restore_drill_verification_path=str(result_path),
        ),
        http_client=client,
    )

    check = {
        item.name: item for item in report.checks
    }[InstallValidationCheckName.BACKUP_RESTORE_DRILL]

    assert report.status == InstallValidationStatus.FAILED
    assert check.status == "failed"
    assert "backup manifest was not generated" in check.message
    assert "database restore was not verified" in check.message
    assert "post-restore validation did not pass" in check.message
    assert check.remediation


def test_restore_drill_verification_result_rejects_empty_id_and_negative_metrics():
    with pytest.raises(ValidationError) as error:
        RestoreDrillVerificationResult(
            drill_id="",
            backup_manifest_generated=True,
            restore_order_executed=True,
            database_restore_verified=True,
            object_storage_restore_verified=True,
            redis_restore_or_rebuild_verified=True,
            config_restore_verified=True,
            post_restore_validation_passed=True,
            rpo_minutes=-1,
            rto_minutes=-1,
        )

    assert "drill_id" in str(error.value)
    assert "rpo_minutes" in str(error.value)
    assert "rto_minutes" in str(error.value)


def test_install_validation_runner_does_not_echo_event_or_audit_evidence_parse_errors(
    tmp_path: Path,
):
    event_result_path = tmp_path / "event-stream-verification.json"
    event_result_path.write_text(
        json.dumps(
            {
                "stream_opened": True,
                "event_id_received": True,
                "after_sequence_replay_succeeded": True,
                "last_event_id_replay_succeeded": True,
                "tenant_scope_enforced": True,
                "safe_payload_confirmed": True,
                "raw_payload": "customer-secret",
            }
        )
    )
    audit_result_path = tmp_path / "audit-write-verification.json"
    audit_result_path.write_text(
        json.dumps(
            {
                "write_succeeded": True,
                "read_back_succeeded": True,
                "tenant_scope_enforced": True,
                "sensitive_metadata_redacted": True,
                "raw_metadata": "customer-secret",
            }
        )
    )
    client = RecordingInstallValidationHttpClient()

    report = run_install_validation(
        InstallValidationRunConfig(
            deployment_id="private-acme",
            deployment_mode="private",
            api_base_url="http://api.local",
            browser_base_url="http://browser.local",
            event_stream_verification_path=str(event_result_path),
            audit_write_verification_path=str(audit_result_path),
        ),
        http_client=client,
    )
    report_json = report.model_dump_json()

    assert report.status == InstallValidationStatus.FAILED
    assert "customer-secret" not in report_json
    assert "raw_payload" not in report_json
    assert "raw_metadata" not in report_json


def test_validate_install_script_wraps_python_cli():
    script = Path("scripts/validate-install.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.install_validation" in text
    assert "--mode" in text
    assert "--release-package" in text
    assert "--release-transfer-evidence" in text
    assert "--release-package-signature" in text
    assert "--release-package-trusted-public-key" in text
    assert "--migration-plan" in text
    assert "--object-storage-verification" in text
    assert "--redis-queue-verification" in text
    assert "--secret-manager-verification" in text
    assert "--model-gateway-verification" in text
    assert "--sandbox-verification" in text
    assert "--kubernetes-sandbox-verification" in text
    assert "--browser-controller-verification" in text
    assert "--event-stream-verification" in text
    assert "--audit-write-verification" in text
    assert "--trace-collector-verification" in text
    assert "--support-bundle-redaction-evidence" in text
    assert "--restore-drill-verification" in text
    assert "--output" in text

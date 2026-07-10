import argparse
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.sandbox.image_policy import (
    sandbox_runtime_image_policy_failure_details,
    sandbox_runtime_normalize_allowed_images,
)
from taroai.sandbox.kubernetes import KubernetesSandboxAdapter
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCreateRequest,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSessionStatus,
)


DEFAULT_KUBERNETES_VERIFY_COMMAND = (
    "python -c \"from pathlib import Path; "
    "text=Path('/workspace/input.txt').read_text().strip(); "
    "Path('/workspace/artifacts').mkdir(exist_ok=True); "
    "Path('/workspace/artifacts/report.txt').write_text(text.upper()); "
    "print(text.upper())\""
)
DEFAULT_KUBERNETES_SANDBOX_IMAGE = "ghcr.io/creao-ai/sandbox-runtime:2026-07"
KUBERNETES_CONTROLLER_REQUIRED_RBAC_RULES = [
    {
        "apiGroups": [""],
        "resources": ["pods"],
        "verbs": ["create", "get", "list", "watch", "delete"],
    },
    {
        "apiGroups": [""],
        "resources": ["pods/exec"],
        "verbs": ["create", "get"],
    },
    {
        "apiGroups": ["networking.k8s.io"],
        "resources": ["networkpolicies"],
        "verbs": ["create", "get", "list", "watch", "delete"],
    },
]


class KubernetesSandboxVerificationConfig(BaseModel):
    root_dir: Path = Field(default=Path("/tmp/taroai/kubernetes-sandbox-verify"))
    image: str = Field(default=DEFAULT_KUBERNETES_SANDBOX_IMAGE, min_length=1)
    namespace: str = Field(default="default", min_length=1)
    tenant_id: str = Field(default="tenant_kubernetes_verify", min_length=1)
    workspace_id: str = Field(default="workspace_kubernetes_verify", min_length=1)
    run_id: str = Field(default_factory=lambda: f"run_kubernetes_verify_{uuid4().hex[:12]}")
    input_path: str = Field(default="/workspace/input.txt", min_length=1)
    output_path: str = Field(default="/workspace/artifacts/report.txt", min_length=1)
    input_content: str = Field(default="kubernetes verify ok", min_length=1)
    expected_output: str = Field(default="KUBERNETES VERIFY OK", min_length=1)
    command: str = Field(default=DEFAULT_KUBERNETES_VERIFY_COMMAND, min_length=1)
    command_timeout_seconds: int = Field(default=30, ge=1)
    session_timeout_seconds: int = Field(default=300, ge=1)
    kubectl_binary: str = Field(default="kubectl", min_length=1)
    service_account_name: str = Field(default="sandbox-runner", min_length=1)
    runtime_class_name: str = ""
    runtime_class_required: bool = False
    allowed_images: list[str] = Field(
        default_factory=lambda: [DEFAULT_KUBERNETES_SANDBOX_IMAGE]
    )
    image_pull_policy: str = Field(default="IfNotPresent", min_length=1)
    pod_ready_timeout_seconds: int = Field(default=60, ge=1)
    memory_limit: str = Field(default="512Mi", min_length=1)
    cpu_limit: str = Field(default="500m", min_length=1)
    ephemeral_storage_limit: str = Field(default="1Gi", min_length=1)
    run_as_user: int = Field(default=65532, ge=1)
    run_as_group: int = Field(default=65532, ge=1)
    verify_runtime_policy: bool = False
    runtime_policy_resource_quota_name: str = Field(
        default="taroai-sandbox-runtime-quota",
        min_length=1,
    )
    runtime_policy_limit_range_name: str = Field(
        default="taroai-sandbox-runtime-limits",
        min_length=1,
    )
    runtime_policy_network_policy_name: str = Field(
        default="taroai-sandbox-runtime-default-deny",
        min_length=1,
    )
    runtime_policy_controller_service_account_name: str = Field(
        default="sandbox-controller",
        min_length=1,
    )
    runtime_policy_controller_role_name: str = Field(
        default="sandbox-controller",
        min_length=1,
    )
    runtime_policy_controller_role_binding_name: str = Field(
        default="sandbox-controller",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_image_policy(self) -> "KubernetesSandboxVerificationConfig":
        allowed_images = sandbox_runtime_normalize_allowed_images(self.allowed_images)
        details = sandbox_runtime_image_policy_failure_details(
            image=self.image,
            allowed_images=allowed_images,
            context="kubernetes sandbox verification",
        )
        if details:
            raise ValueError("; ".join(details))
        self.allowed_images = allowed_images
        return self


class KubernetesRuntimePolicyVerificationConfig(BaseModel):
    namespace: str = Field(default="taroai", min_length=1)
    kubectl_binary: str = Field(default="kubectl", min_length=1)
    resource_quota_name: str = Field(
        default="taroai-sandbox-runtime-quota",
        min_length=1,
    )
    limit_range_name: str = Field(
        default="taroai-sandbox-runtime-limits",
        min_length=1,
    )
    network_policy_name: str = Field(
        default="taroai-sandbox-runtime-default-deny",
        min_length=1,
    )
    expected_namespace_labels: dict[str, str] = Field(
        default_factory=lambda: {
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/warn": "restricted",
            "pod-security.kubernetes.io/enforce-version": "latest",
        }
    )
    expected_resource_quota_hard: dict[str, str] = Field(
        default_factory=lambda: {
            "pods": "50",
            "requests.cpu": "20",
            "requests.memory": "40Gi",
            "limits.cpu": "40",
            "limits.memory": "80Gi",
            "requests.ephemeral-storage": "100Gi",
            "limits.ephemeral-storage": "200Gi",
        }
    )
    expected_limit_range_default: dict[str, str] = Field(
        default_factory=lambda: {
            "cpu": "1000m",
            "memory": "1Gi",
            "ephemeral-storage": "2Gi",
        }
    )
    expected_limit_range_default_request: dict[str, str] = Field(
        default_factory=lambda: {
            "cpu": "500m",
            "memory": "512Mi",
            "ephemeral-storage": "1Gi",
        }
    )
    expected_limit_range_max: dict[str, str] = Field(
        default_factory=lambda: {
            "memory": "4Gi",
            "ephemeral-storage": "8Gi",
        }
    )
    controller_service_account_name: str = Field(
        default="sandbox-controller",
        min_length=1,
    )
    runner_service_account_name: str = Field(
        default="sandbox-runner",
        min_length=1,
    )
    controller_role_name: str = Field(
        default="sandbox-controller",
        min_length=1,
    )
    controller_role_binding_name: str = Field(
        default="sandbox-controller",
        min_length=1,
    )


class KubernetesRuntimePolicyVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    verified: bool
    namespace_labels: dict[str, str]
    resource_quota_name: str
    resource_quota_hard: dict[str, str]
    limit_range_name: str
    limit_range_default: dict[str, str]
    limit_range_default_request: dict[str, str]
    limit_range_max: dict[str, str]
    network_policy_name: str
    network_policy_pod_selector: dict[str, str]
    network_policy_types: list[str]
    network_policy_default_deny: bool
    controller_service_account_name: str = ""
    controller_service_account_exists: bool = False
    runner_service_account_name: str = ""
    runner_service_account_token_automount_disabled: bool = False
    controller_role_name: str = ""
    controller_role_binding_name: str = ""
    controller_role_least_privilege: bool = False
    controller_role_binding_valid: bool = False


class KubernetesSandboxVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    image: str
    namespace: str
    session_id: str
    pod_name: str
    network_policy_name: str
    network_policy_default_deny: bool
    network_policy_types: list[str] = Field(default_factory=list)
    network_policy_session_selector: dict[str, str] = Field(default_factory=dict)
    exit_code: int
    stdout_contains: str
    downloaded_content: str
    file_paths: list[str] = Field(default_factory=list)
    snapshot_uri: str
    destroyed: bool
    service_account_name: str
    runtime_class_name: str
    runtime_class_required: bool
    allowed_images: list[str] = Field(default_factory=list)
    image_pull_policy: str
    memory_limit: str
    cpu_limit: str
    ephemeral_storage_limit: str
    workspace_volume_size_limit: str
    tmp_volume_size_limit: str
    pod_active_deadline_seconds: int
    host_network: bool
    host_pid: bool
    host_ipc: bool
    pod_run_as_non_root: bool
    seccomp_profile_type: str
    privileged: bool
    allow_privilege_escalation: bool
    read_only_root_filesystem: bool
    dropped_capabilities: list[str] = Field(default_factory=list)
    automount_service_account_token: bool
    service_links_enabled: bool
    termination_grace_period_seconds: int
    run_as_user: int
    run_as_group: int
    runtime_policy: KubernetesRuntimePolicyVerificationResult | None = None


def parse_args(argv: list[str] | None = None) -> KubernetesSandboxVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify the Kubernetes sandbox provider against a real Kubernetes cluster."
    )
    parser.add_argument("--root-dir", type=Path, default=Path("/tmp/taroai/kubernetes-sandbox-verify"))
    parser.add_argument("--image", default=DEFAULT_KUBERNETES_SANDBOX_IMAGE)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--tenant-id", default="tenant_kubernetes_verify")
    parser.add_argument("--workspace-id", default="workspace_kubernetes_verify")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--kubectl-binary", default="kubectl")
    parser.add_argument("--service-account-name", default="sandbox-runner")
    parser.add_argument("--runtime-class-name", default="")
    parser.add_argument("--runtime-class-required", action="store_true")
    parser.add_argument(
        "--allowed-image",
        dest="allowed_images",
        action="append",
        default=None,
    )
    parser.add_argument("--image-pull-policy", default="IfNotPresent")
    parser.add_argument("--pod-ready-timeout-seconds", type=int, default=60)
    parser.add_argument("--memory-limit", default="512Mi")
    parser.add_argument("--cpu-limit", default="500m")
    parser.add_argument("--ephemeral-storage-limit", default="1Gi")
    parser.add_argument("--run-as-user", type=int, default=65532)
    parser.add_argument("--run-as-group", type=int, default=65532)
    parser.add_argument("--command-timeout-seconds", type=int, default=30)
    parser.add_argument("--verify-runtime-policy", action="store_true")
    parser.add_argument(
        "--runtime-policy-resource-quota-name",
        default="taroai-sandbox-runtime-quota",
    )
    parser.add_argument(
        "--runtime-policy-limit-range-name",
        default="taroai-sandbox-runtime-limits",
    )
    parser.add_argument(
        "--runtime-policy-network-policy-name",
        default="taroai-sandbox-runtime-default-deny",
    )
    parser.add_argument(
        "--runtime-policy-controller-service-account-name",
        default="sandbox-controller",
    )
    parser.add_argument(
        "--runtime-policy-controller-role-name",
        default="sandbox-controller",
    )
    parser.add_argument(
        "--runtime-policy-controller-role-binding-name",
        default="sandbox-controller",
    )
    parsed = parser.parse_args(argv)
    config_data = {
        "root_dir": parsed.root_dir,
        "image": parsed.image,
        "namespace": parsed.namespace,
        "tenant_id": parsed.tenant_id,
        "workspace_id": parsed.workspace_id,
        "kubectl_binary": parsed.kubectl_binary,
        "service_account_name": parsed.service_account_name,
        "runtime_class_name": parsed.runtime_class_name,
        "runtime_class_required": parsed.runtime_class_required,
        "allowed_images": parsed.allowed_images or [parsed.image],
        "image_pull_policy": parsed.image_pull_policy,
        "pod_ready_timeout_seconds": parsed.pod_ready_timeout_seconds,
        "memory_limit": parsed.memory_limit,
        "cpu_limit": parsed.cpu_limit,
        "ephemeral_storage_limit": parsed.ephemeral_storage_limit,
        "run_as_user": parsed.run_as_user,
        "run_as_group": parsed.run_as_group,
        "command_timeout_seconds": parsed.command_timeout_seconds,
        "verify_runtime_policy": parsed.verify_runtime_policy,
        "runtime_policy_resource_quota_name": parsed.runtime_policy_resource_quota_name,
        "runtime_policy_limit_range_name": parsed.runtime_policy_limit_range_name,
        "runtime_policy_network_policy_name": parsed.runtime_policy_network_policy_name,
        "runtime_policy_controller_service_account_name": (
            parsed.runtime_policy_controller_service_account_name
        ),
        "runtime_policy_controller_role_name": parsed.runtime_policy_controller_role_name,
        "runtime_policy_controller_role_binding_name": (
            parsed.runtime_policy_controller_role_binding_name
        ),
    }
    if parsed.run_id is not None:
        config_data["run_id"] = parsed.run_id
    return KubernetesSandboxVerificationConfig(**config_data)


def verify_kubernetes_sandbox(
    config: KubernetesSandboxVerificationConfig,
    adapter=None,
    runtime_policy_command_runner=None,
) -> KubernetesSandboxVerificationResult:
    kubernetes_adapter = adapter or build_kubernetes_sandbox_adapter(config)
    runtime_policy = None
    if config.verify_runtime_policy:
        runtime_policy = verify_kubernetes_runtime_policy(
            KubernetesRuntimePolicyVerificationConfig(
                namespace=config.namespace,
                kubectl_binary=config.kubectl_binary,
                resource_quota_name=config.runtime_policy_resource_quota_name,
                limit_range_name=config.runtime_policy_limit_range_name,
                network_policy_name=config.runtime_policy_network_policy_name,
                controller_service_account_name=(
                    config.runtime_policy_controller_service_account_name
                ),
                runner_service_account_name=config.service_account_name,
                controller_role_name=config.runtime_policy_controller_role_name,
                controller_role_binding_name=(
                    config.runtime_policy_controller_role_binding_name
                ),
            ),
            command_runner=runtime_policy_command_runner,
        )
    session: SandboxSession | None = None
    try:
        session = kubernetes_adapter.create(
            SandboxCreateRequest(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                image=config.image,
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=config.session_timeout_seconds,
            )
        )
        service_account_name = str(session.metadata.get("service_account_name") or "")
        runtime_class_name = str(session.metadata.get("runtime_class_name") or "")
        expected_runtime_class_name = config.runtime_class_name.strip()
        if (
            service_account_name != config.service_account_name
            or (
                bool(expected_runtime_class_name)
                and runtime_class_name != expected_runtime_class_name
            )
        ):
            raise RuntimeError(
                "kubernetes sandbox pod identity mismatch: "
                f"serviceAccountName expected {config.service_account_name} "
                f"got {service_account_name or '<missing>'}; "
                f"runtimeClassName expected "
                f"{expected_runtime_class_name or '<empty>'} "
                f"got {runtime_class_name or '<empty>'}"
            )
        workspace_volume_size_limit = str(
            session.metadata.get("workspace_volume_size_limit") or ""
        )
        tmp_volume_size_limit = str(session.metadata.get("tmp_volume_size_limit") or "")
        if workspace_volume_size_limit != config.ephemeral_storage_limit:
            raise RuntimeError(
                "kubernetes sandbox workspace volume sizeLimit mismatch: "
                f"expected {config.ephemeral_storage_limit} "
                f"got {workspace_volume_size_limit or '<missing>'}"
            )
        if tmp_volume_size_limit != config.ephemeral_storage_limit:
            raise RuntimeError(
                "kubernetes sandbox tmp volume sizeLimit mismatch: "
                f"expected {config.ephemeral_storage_limit} "
                f"got {tmp_volume_size_limit or '<missing>'}"
            )
        try:
            pod_active_deadline_seconds = int(
                session.metadata.get("pod_active_deadline_seconds") or 0
            )
        except (TypeError, ValueError):
            pod_active_deadline_seconds = 0
        if pod_active_deadline_seconds != config.session_timeout_seconds:
            raise RuntimeError(
                "kubernetes sandbox activeDeadlineSeconds mismatch: "
                f"expected {config.session_timeout_seconds} "
                f"got {pod_active_deadline_seconds or '<missing>'}"
            )
        cpu_limit = str(session.metadata.get("cpu_limit") or "")
        memory_limit = str(session.metadata.get("memory_limit") or "")
        ephemeral_storage_resource_limit = str(
            session.metadata.get("ephemeral_storage_limit") or ""
        )
        try:
            run_as_user = int(session.metadata.get("run_as_user") or 0)
        except (TypeError, ValueError):
            run_as_user = 0
        try:
            run_as_group = int(session.metadata.get("run_as_group") or 0)
        except (TypeError, ValueError):
            run_as_group = 0
        host_network = session.metadata.get("host_network")
        host_pid = session.metadata.get("host_pid")
        host_ipc = session.metadata.get("host_ipc")
        if host_network is not False or host_pid is not False or host_ipc is not False:
            raise RuntimeError("kubernetes sandbox host namespace isolation mismatch")
        pod_run_as_non_root = session.metadata.get("pod_run_as_non_root")
        seccomp_profile_type = str(session.metadata.get("seccomp_profile_type") or "")
        privileged = session.metadata.get("privileged")
        allow_privilege_escalation = session.metadata.get("allow_privilege_escalation")
        read_only_root_filesystem = session.metadata.get("read_only_root_filesystem")
        raw_dropped_capabilities = session.metadata.get("dropped_capabilities")
        dropped_capabilities = (
            [str(capability) for capability in raw_dropped_capabilities]
            if isinstance(raw_dropped_capabilities, list)
            else []
        )
        if (
            pod_run_as_non_root is not True
            or seccomp_profile_type != "RuntimeDefault"
            or privileged is not False
            or allow_privilege_escalation is not False
            or read_only_root_filesystem is not True
            or dropped_capabilities != ["ALL"]
        ):
            raise RuntimeError("kubernetes sandbox securityContext mismatch")
        automount_service_account_token = session.metadata.get(
            "automount_service_account_token"
        )
        service_links_enabled = session.metadata.get("service_links_enabled")
        if (
            automount_service_account_token is not False
            or service_links_enabled is not False
        ):
            raise RuntimeError("kubernetes sandbox credential isolation mismatch")
        try:
            termination_grace_period_seconds = int(
                session.metadata.get("termination_grace_period_seconds") or -1
            )
        except (TypeError, ValueError):
            termination_grace_period_seconds = -1
        if termination_grace_period_seconds > 5 or termination_grace_period_seconds < 0:
            raise RuntimeError(
                "kubernetes sandbox terminationGracePeriodSeconds mismatch"
            )
        if (
            cpu_limit != config.cpu_limit
            or memory_limit != config.memory_limit
            or ephemeral_storage_resource_limit != config.ephemeral_storage_limit
            or run_as_user != config.run_as_user
            or run_as_group != config.run_as_group
        ):
            raise RuntimeError(
                "kubernetes sandbox resource and user policy mismatch"
            )
        network_policy_default_deny = (
            session.metadata.get("network_policy_default_deny") is True
        )
        raw_network_policy_types = session.metadata.get("network_policy_types")
        network_policy_types = (
            [str(policy_type) for policy_type in raw_network_policy_types]
            if isinstance(raw_network_policy_types, list)
            else []
        )
        raw_network_policy_session_selector = session.metadata.get(
            "network_policy_session_selector"
        )
        network_policy_session_selector = (
            {
                str(key): str(value)
                for key, value in raw_network_policy_session_selector.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(raw_network_policy_session_selector, dict)
            else {}
        )
        if (
            not network_policy_default_deny
            or sorted(network_policy_types) != ["Egress", "Ingress"]
            or network_policy_session_selector.get("taroai.sandbox_session_id")
            != session.id
        ):
            raise RuntimeError(
                "kubernetes sandbox session NetworkPolicy mismatch"
            )
        kubernetes_adapter.upload_file(
            SandboxFileWrite(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                session_id=session.id,
                path=config.input_path,
                content=config.input_content,
                content_type="text/plain",
            )
        )
        command_result = kubernetes_adapter.execute(
            SandboxCommand(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                session_id=session.id,
                command=config.command,
                cwd="/workspace",
                timeout_seconds=config.command_timeout_seconds,
            )
        )
        if command_result.exit_code != 0:
            raise RuntimeError(
                "kubernetes sandbox command failed with "
                f"exit code {command_result.exit_code}"
            )
        if config.expected_output not in command_result.stdout:
            raise RuntimeError(
                "kubernetes sandbox command output did not include expected text"
            )
        file_paths = sorted(
            file_ref.path
            for file_ref in kubernetes_adapter.list_files(config.tenant_id, session.id)
        )
        downloaded = kubernetes_adapter.download_file(
            config.tenant_id,
            session.id,
            config.output_path,
        )
        if downloaded.content != config.expected_output:
            raise RuntimeError(
                "kubernetes sandbox downloaded artifact content did not match"
            )
        snapshot = kubernetes_adapter.snapshot(config.tenant_id, session.id)
        destroyed = kubernetes_adapter.destroy(config.tenant_id, session.id)
        result = KubernetesSandboxVerificationResult(
            provider=session.provider,
            image=session.image,
            namespace=str(session.metadata.get("namespace") or config.namespace),
            session_id=session.id,
            pod_name=str(session.metadata.get("pod_name") or ""),
            network_policy_name=str(session.metadata.get("network_policy_name") or ""),
            network_policy_default_deny=network_policy_default_deny,
            network_policy_types=network_policy_types,
            network_policy_session_selector=network_policy_session_selector,
            exit_code=command_result.exit_code,
            stdout_contains=config.expected_output,
            downloaded_content=downloaded.content or "",
            file_paths=file_paths,
            snapshot_uri=snapshot.uri,
            destroyed=destroyed.status == SandboxSessionStatus.DESTROYED,
            service_account_name=service_account_name,
            runtime_class_name=runtime_class_name,
            runtime_class_required=config.runtime_class_required,
            allowed_images=list(config.allowed_images),
            image_pull_policy=config.image_pull_policy,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            ephemeral_storage_limit=config.ephemeral_storage_limit,
            workspace_volume_size_limit=workspace_volume_size_limit,
            tmp_volume_size_limit=tmp_volume_size_limit,
            pod_active_deadline_seconds=pod_active_deadline_seconds,
            host_network=host_network,
            host_pid=host_pid,
            host_ipc=host_ipc,
            pod_run_as_non_root=pod_run_as_non_root,
            seccomp_profile_type=seccomp_profile_type,
            privileged=privileged,
            allow_privilege_escalation=allow_privilege_escalation,
            read_only_root_filesystem=read_only_root_filesystem,
            dropped_capabilities=dropped_capabilities,
            automount_service_account_token=automount_service_account_token,
            service_links_enabled=service_links_enabled,
            termination_grace_period_seconds=termination_grace_period_seconds,
            run_as_user=run_as_user,
            run_as_group=run_as_group,
            runtime_policy=runtime_policy,
        )
        session = None
        return result
    finally:
        if session is not None:
            cleanup_session(kubernetes_adapter, config.tenant_id, session)


def build_kubernetes_sandbox_adapter(
    config: KubernetesSandboxVerificationConfig,
) -> KubernetesSandboxAdapter:
    return KubernetesSandboxAdapter(
        root_dir=config.root_dir,
        namespace=config.namespace,
        kubectl_binary=config.kubectl_binary,
        service_account_name=config.service_account_name,
        runtime_class_name=config.runtime_class_name,
        runtime_class_required=config.runtime_class_required,
        allowed_images=config.allowed_images,
        image_pull_policy=config.image_pull_policy,
        pod_ready_timeout_seconds=config.pod_ready_timeout_seconds,
        memory_limit=config.memory_limit,
        cpu_limit=config.cpu_limit,
        ephemeral_storage_limit=config.ephemeral_storage_limit,
        run_as_user=config.run_as_user,
        run_as_group=config.run_as_group,
    )


def verify_kubernetes_runtime_policy(
    config: KubernetesRuntimePolicyVerificationConfig,
    command_runner=None,
) -> KubernetesRuntimePolicyVerificationResult:
    namespace_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    quota_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "resourcequota",
            config.resource_quota_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    limit_range_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "limitrange",
            config.limit_range_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    network_policy_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "networkpolicy",
            config.network_policy_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    controller_service_account_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "serviceaccount",
            config.controller_service_account_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    runner_service_account_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "serviceaccount",
            config.runner_service_account_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    role_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "role",
            config.controller_role_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )
    role_binding_document = run_kubectl_json(
        [
            config.kubectl_binary,
            "get",
            "rolebinding",
            config.controller_role_binding_name,
            "--namespace",
            config.namespace,
            "-o",
            "json",
        ],
        command_runner=command_runner,
    )

    namespace_labels = namespace_document.get("metadata", {}).get("labels", {})
    resource_quota_hard = quota_document.get("spec", {}).get("hard", {})
    container_limit = find_container_limit(limit_range_document)
    limit_range_default = container_limit.get("default", {})
    limit_range_default_request = container_limit.get("defaultRequest", {})
    limit_range_max = container_limit.get("max", {})
    network_policy_spec = network_policy_document.get("spec", {})
    network_policy_pod_selector = network_policy_spec.get("podSelector", {}).get(
        "matchLabels",
        {},
    )
    network_policy_types = list(network_policy_spec.get("policyTypes", []))
    network_policy_default_deny = (
        network_policy_pod_selector.get("app.kubernetes.io/name")
        == "taroai-sandbox-session"
        and sorted(network_policy_types) == ["Egress", "Ingress"]
        and "ingress" not in network_policy_spec
        and "egress" not in network_policy_spec
    )
    controller_role_least_privilege = kubernetes_controller_role_least_privilege(
        role_document
    )
    controller_service_account_exists = kubernetes_service_account_exists(
        controller_service_account_document,
        config.controller_service_account_name,
    )
    runner_service_account_token_automount_disabled = (
        kubernetes_service_account_token_automount_disabled(
            runner_service_account_document
        )
    )
    controller_role_binding_valid = kubernetes_controller_role_binding_valid(
        role_binding_document,
        namespace=config.namespace,
        service_account_name=config.controller_service_account_name,
        role_name=config.controller_role_name,
    )

    require_expected_values(
        namespace_labels,
        config.expected_namespace_labels,
        "kubernetes runtime namespace labels",
    )
    require_expected_values(
        resource_quota_hard,
        config.expected_resource_quota_hard,
        "kubernetes runtime ResourceQuota",
    )
    require_expected_values(
        limit_range_default,
        config.expected_limit_range_default,
        "kubernetes runtime LimitRange default",
    )
    require_expected_values(
        limit_range_default_request,
        config.expected_limit_range_default_request,
        "kubernetes runtime LimitRange defaultRequest",
    )
    require_expected_values(
        limit_range_max,
        config.expected_limit_range_max,
        "kubernetes runtime LimitRange max",
    )
    if not network_policy_default_deny:
        raise RuntimeError(
            "kubernetes runtime NetworkPolicy must default-deny sandbox session traffic"
        )
    if not controller_service_account_exists:
        raise RuntimeError(
            "kubernetes sandbox controller ServiceAccount was not verified"
        )
    if not runner_service_account_token_automount_disabled:
        raise RuntimeError(
            "kubernetes sandbox runner ServiceAccount token automount must be disabled"
        )
    if not controller_role_least_privilege:
        raise RuntimeError(
            "kubernetes sandbox controller Role must match least-privilege rules"
        )
    if not controller_role_binding_valid:
        raise RuntimeError(
            "kubernetes sandbox controller RoleBinding must bind the controller "
            "ServiceAccount to the controller Role"
        )

    return KubernetesRuntimePolicyVerificationResult(
        namespace=config.namespace,
        verified=True,
        namespace_labels=dict(namespace_labels),
        resource_quota_name=config.resource_quota_name,
        resource_quota_hard=dict(resource_quota_hard),
        limit_range_name=config.limit_range_name,
        limit_range_default=dict(limit_range_default),
        limit_range_default_request=dict(limit_range_default_request),
        limit_range_max=dict(limit_range_max),
        network_policy_name=config.network_policy_name,
        network_policy_pod_selector=dict(network_policy_pod_selector),
        network_policy_types=network_policy_types,
        network_policy_default_deny=network_policy_default_deny,
        controller_service_account_name=config.controller_service_account_name,
        controller_service_account_exists=controller_service_account_exists,
        runner_service_account_name=config.runner_service_account_name,
        runner_service_account_token_automount_disabled=(
            runner_service_account_token_automount_disabled
        ),
        controller_role_name=config.controller_role_name,
        controller_role_binding_name=config.controller_role_binding_name,
        controller_role_least_privilege=controller_role_least_privilege,
        controller_role_binding_valid=controller_role_binding_valid,
    )


def run_kubectl_json(command: list[str], command_runner=None) -> dict:
    runner = command_runner or subprocess.run
    completed = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "kubectl command failed while verifying kubernetes runtime policy: "
            + (completed.stderr or "").strip()
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("kubectl returned invalid JSON") from error


def find_container_limit(limit_range_document: dict) -> dict:
    for limit in limit_range_document.get("spec", {}).get("limits", []):
        if limit.get("type") == "Container":
            return limit
    raise RuntimeError("kubernetes runtime LimitRange is missing Container limits")


def kubernetes_service_account_exists(
    service_account_document: dict,
    expected_name: str,
) -> bool:
    return service_account_document.get("metadata", {}).get("name") == expected_name


def kubernetes_service_account_token_automount_disabled(
    service_account_document: dict,
) -> bool:
    return service_account_document.get("automountServiceAccountToken") is False


def kubernetes_controller_role_least_privilege(role_document: dict) -> bool:
    return normalized_kubernetes_rbac_rules(
        role_document.get("rules", [])
    ) == normalized_kubernetes_rbac_rules(KUBERNETES_CONTROLLER_REQUIRED_RBAC_RULES)


def normalized_kubernetes_rbac_rules(rules: list[dict]) -> set[tuple]:
    normalized: set[tuple] = set()
    for rule in rules:
        normalized.add(
            (
                tuple(sorted(str(item) for item in rule.get("apiGroups", []))),
                tuple(sorted(str(item) for item in rule.get("resources", []))),
                tuple(sorted(str(item) for item in rule.get("verbs", []))),
            )
        )
    return normalized


def kubernetes_controller_role_binding_valid(
    role_binding_document: dict,
    *,
    namespace: str,
    service_account_name: str,
    role_name: str,
) -> bool:
    role_ref = role_binding_document.get("roleRef", {})
    subjects = role_binding_document.get("subjects", [])
    expected_subject = {
        "kind": "ServiceAccount",
        "name": service_account_name,
    }
    subject_namespace = subjects[0].get("namespace") if subjects else None
    return (
        role_ref.get("apiGroup") == "rbac.authorization.k8s.io"
        and role_ref.get("kind") == "Role"
        and role_ref.get("name") == role_name
        and len(subjects) == 1
        and (subject_namespace is None or subject_namespace == namespace)
        and {
            "kind": subjects[0].get("kind"),
            "name": subjects[0].get("name"),
        }
        == expected_subject
    )


def require_expected_values(
    actual: dict[str, str],
    expected: dict[str, str],
    label: str,
) -> None:
    mismatches = [
        f"{key} expected {value} got {actual.get(key)}"
        for key, value in expected.items()
        if actual.get(key) != value
    ]
    if mismatches:
        raise RuntimeError(f"{label} mismatch: {', '.join(mismatches)}")


def cleanup_session(adapter, tenant_id: str, session: SandboxSession) -> None:
    current = adapter.sessions.get(session.id) if hasattr(adapter, "sessions") else None
    if current is not None and current.status == SandboxSessionStatus.DESTROYED:
        return
    try:
        adapter.destroy(tenant_id, session.id)
    except Exception:
        return


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_kubernetes_sandbox(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

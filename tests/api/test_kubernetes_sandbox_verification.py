from pathlib import Path

import pytest
from pydantic import ValidationError

from taroai.sandbox.kubernetes_verification import (
    DEFAULT_KUBERNETES_SANDBOX_IMAGE,
    KubernetesSandboxVerificationConfig,
    KubernetesRuntimePolicyVerificationConfig,
    parse_args,
    verify_kubernetes_sandbox,
    verify_kubernetes_runtime_policy,
)
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSessionStatus,
    SandboxSnapshot,
)


class RecordingKubernetesSandboxAdapter:
    def __init__(self):
        self.calls: list[str] = []
        self.session = SandboxSession(
            id="sandbox_k8s_verify",
            tenant_id="tenant_verify",
            workspace_id="workspace_verify",
            run_id="run_verify",
            provider="kubernetes",
            image=DEFAULT_KUBERNETES_SANDBOX_IMAGE,
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=300,
            status=SandboxSessionStatus.ACTIVE,
            metadata={
                "namespace": "tenant-sandboxes",
                "pod_name": "taroai-tenant-verify-sandbox",
                "network_policy_name": "taroai-tenant-verify-sandbox-deny-all",
                "network_policy_default_deny": True,
                "network_policy_types": ["Ingress", "Egress"],
                "network_policy_session_selector": {
                    "taroai.sandbox_session_id": "sandbox_k8s_verify"
                },
                "service_account_name": "sandbox-runner",
                "runtime_class_name": "gvisor",
                "cpu_limit": "500m",
                "memory_limit": "512Mi",
                "ephemeral_storage_limit": "1Gi",
                "cpu_request": "500m",
                "memory_request": "512Mi",
                "ephemeral_storage_request": "1Gi",
                "run_as_user": 10001,
                "run_as_group": 10001,
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
            },
        )

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        self.calls.append("create")
        assert request.network_mode == SandboxNetworkMode.DISABLED
        assert request.image == DEFAULT_KUBERNETES_SANDBOX_IMAGE
        return self.session

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        self.calls.append("upload")
        assert file_write.path == "/workspace/input.txt"
        return SandboxFileRef(
            tenant_id=file_write.tenant_id,
            workspace_id=file_write.workspace_id,
            run_id=file_write.run_id,
            session_id=file_write.session_id,
            path=file_write.path,
            content_type=file_write.content_type,
            size_bytes=len(file_write.content.encode("utf-8")),
            content=file_write.content,
        )

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        self.calls.append("execute")
        assert command.command
        return SandboxCommandResult(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            command=command.command,
            exit_code=0,
            stdout="KUBERNETES VERIFY OK\n",
            stderr="",
        )

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        self.calls.append("list")
        return [
            SandboxFileRef(
                tenant_id=tenant_id,
                workspace_id="workspace_verify",
                run_id="run_verify",
                session_id=session_id,
                path="/workspace/input.txt",
                content_type="text/plain",
                size_bytes=20,
            ),
            SandboxFileRef(
                tenant_id=tenant_id,
                workspace_id="workspace_verify",
                run_id="run_verify",
                session_id=session_id,
                path="/workspace/artifacts/report.txt",
                content_type="text/plain",
                size_bytes=21,
            ),
        ]

    def download_file(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
    ) -> SandboxFileRef:
        self.calls.append("download")
        assert path == "/workspace/artifacts/report.txt"
        return SandboxFileRef(
            tenant_id=tenant_id,
            workspace_id="workspace_verify",
            run_id="run_verify",
            session_id=session_id,
            path=path,
            content_type="text/plain",
            size_bytes=len("KUBERNETES VERIFY OK"),
            content="KUBERNETES VERIFY OK",
        )

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        self.calls.append("snapshot")
        return SandboxSnapshot(
            tenant_id=tenant_id,
            workspace_id="workspace_verify",
            run_id="run_verify",
            session_id=session_id,
            uri="kubernetes://tenant-sandboxes/taroai-tenant-verify-sandbox/snapshots/snapshot_1",
        )

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        self.calls.append("destroy")
        return self.session.model_copy(
            update={"status": SandboxSessionStatus.DESTROYED}
        )


class RecordingKubectlPolicyRunner:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs):
        self.commands.append(command)
        resource = command[2]
        if resource == "namespace":
            payload = {
                "metadata": {
                    "name": "taroai",
                    "labels": {
                        "pod-security.kubernetes.io/enforce": "restricted",
                        "pod-security.kubernetes.io/audit": "restricted",
                        "pod-security.kubernetes.io/warn": "restricted",
                        "pod-security.kubernetes.io/enforce-version": "latest",
                    },
                }
            }
        elif resource == "resourcequota":
            payload = {
                "metadata": {"name": "taroai-sandbox-runtime-quota"},
                "spec": {
                    "hard": {
                        "pods": "50",
                        "requests.cpu": "20",
                        "requests.memory": "40Gi",
                        "limits.cpu": "40",
                        "limits.memory": "80Gi",
                        "requests.ephemeral-storage": "100Gi",
                        "limits.ephemeral-storage": "200Gi",
                    }
                },
            }
        elif resource == "limitrange":
            payload = {
                "metadata": {"name": "taroai-sandbox-runtime-limits"},
                "spec": {
                    "limits": [
                        {
                            "type": "Container",
                            "default": {
                                "cpu": "1000m",
                                "memory": "1Gi",
                                "ephemeral-storage": "2Gi",
                            },
                            "defaultRequest": {
                                "cpu": "500m",
                                "memory": "512Mi",
                                "ephemeral-storage": "1Gi",
                            },
                            "max": {
                                "cpu": "2000m",
                                "memory": "4Gi",
                                "ephemeral-storage": "8Gi",
                            },
                        }
                    ]
                },
            }
        elif resource == "networkpolicy":
            payload = {
                "metadata": {"name": "taroai-sandbox-runtime-default-deny"},
                "spec": {
                    "podSelector": {
                        "matchLabels": {
                            "app.kubernetes.io/name": "taroai-sandbox-session",
                        }
                    },
                    "policyTypes": ["Ingress", "Egress"],
                },
            }
        elif resource == "serviceaccount":
            name = command[3]
            payload = {
                "metadata": {"name": name},
                "automountServiceAccountToken": name != "sandbox-runner",
            }
        elif resource == "role":
            payload = {
                "metadata": {"name": "sandbox-controller"},
                "rules": [
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
                ],
            }
        elif resource == "rolebinding":
            payload = {
                "metadata": {"name": "sandbox-controller"},
                "subjects": [
                    {
                        "kind": "ServiceAccount",
                        "name": "sandbox-controller",
                    }
                ],
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": "sandbox-controller",
                },
            }
        else:
            raise AssertionError(f"unexpected kubectl resource: {resource}")
        return CompletedKubectlCommand(payload)


class ExtraSubjectRoleBindingPolicyRunner(RecordingKubectlPolicyRunner):
    def __call__(self, command: list[str], **kwargs):
        completed = super().__call__(command, **kwargs)
        if command[2] != "rolebinding":
            return completed
        payload = __import__("json").loads(completed.stdout)
        payload["subjects"].append(
            {
                "kind": "Group",
                "name": "system:authenticated",
            }
        )
        return CompletedKubectlCommand(payload)


class ForeignNamespaceRoleBindingPolicyRunner(RecordingKubectlPolicyRunner):
    def __call__(self, command: list[str], **kwargs):
        completed = super().__call__(command, **kwargs)
        if command[2] != "rolebinding":
            return completed
        payload = __import__("json").loads(completed.stdout)
        payload["subjects"][0]["namespace"] = "other-namespace"
        return CompletedKubectlCommand(payload)


class CompletedKubectlCommand:
    def __init__(self, payload: dict):
        self.returncode = 0
        self.stdout = __import__("json").dumps(payload)
        self.stderr = ""


def test_kubernetes_sandbox_verification_cli_parses_core_inputs(tmp_path: Path):
    config = parse_args(
        [
            "--root-dir",
            str(tmp_path),
            "--namespace",
            "tenant-sandboxes",
            "--service-account-name",
            "sandbox-runner",
            "--runtime-class-name",
            "gvisor",
            "--runtime-class-required",
            "--allowed-image",
            DEFAULT_KUBERNETES_SANDBOX_IMAGE,
            "--memory-limit",
            "512Mi",
            "--cpu-limit",
            "500m",
            "--ephemeral-storage-limit",
            "1Gi",
            "--run-as-user",
            "10001",
            "--run-as-group",
            "10001",
            "--verify-runtime-policy",
            "--runtime-policy-resource-quota-name",
            "taroai-sandbox-runtime-quota",
            "--runtime-policy-limit-range-name",
            "taroai-sandbox-runtime-limits",
            "--runtime-policy-network-policy-name",
            "taroai-sandbox-runtime-default-deny",
        ]
    )

    assert config.root_dir == tmp_path
    assert config.namespace == "tenant-sandboxes"
    assert config.service_account_name == "sandbox-runner"
    assert config.runtime_class_name == "gvisor"
    assert config.runtime_class_required is True
    assert config.allowed_images == [DEFAULT_KUBERNETES_SANDBOX_IMAGE]
    assert config.memory_limit == "512Mi"
    assert config.cpu_limit == "500m"
    assert config.ephemeral_storage_limit == "1Gi"
    assert config.run_as_user == 10001
    assert config.run_as_group == 10001
    assert config.verify_runtime_policy is True
    assert config.runtime_policy_resource_quota_name == "taroai-sandbox-runtime-quota"
    assert config.runtime_policy_limit_range_name == "taroai-sandbox-runtime-limits"
    assert config.runtime_policy_network_policy_name == "taroai-sandbox-runtime-default-deny"


def test_kubernetes_sandbox_verification_rejects_weak_image_policy():
    with pytest.raises(ValidationError, match="broad"):
        KubernetesSandboxVerificationConfig(
            allowed_images=["ghcr.io/creao-ai/sandbox-runtime:*"]
        )


def test_kubernetes_runtime_policy_verification_reads_namespace_quota_and_limits():
    runner = RecordingKubectlPolicyRunner()

    result = verify_kubernetes_runtime_policy(
        KubernetesRuntimePolicyVerificationConfig(
            namespace="taroai",
            kubectl_binary="kubectl",
            resource_quota_name="taroai-sandbox-runtime-quota",
            limit_range_name="taroai-sandbox-runtime-limits",
            network_policy_name="taroai-sandbox-runtime-default-deny",
            controller_service_account_name="sandbox-controller",
            runner_service_account_name="sandbox-runner",
            controller_role_name="sandbox-controller",
            controller_role_binding_name="sandbox-controller",
        ),
        command_runner=runner,
    )

    assert result.namespace == "taroai"
    assert result.verified is True
    assert result.namespace_labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert result.resource_quota_hard["pods"] == "50"
    assert result.limit_range_default["memory"] == "1Gi"
    assert result.limit_range_default_request["ephemeral-storage"] == "1Gi"
    assert result.limit_range_max["ephemeral-storage"] == "8Gi"
    assert result.network_policy_name == "taroai-sandbox-runtime-default-deny"
    assert result.network_policy_pod_selector == {
        "app.kubernetes.io/name": "taroai-sandbox-session"
    }
    assert result.network_policy_types == ["Ingress", "Egress"]
    assert result.network_policy_default_deny is True
    assert result.controller_service_account_name == "sandbox-controller"
    assert result.controller_service_account_exists is True
    assert result.runner_service_account_name == "sandbox-runner"
    assert result.runner_service_account_token_automount_disabled is True
    assert result.controller_role_name == "sandbox-controller"
    assert result.controller_role_binding_name == "sandbox-controller"
    assert result.controller_role_least_privilege is True
    assert result.controller_role_binding_valid is True
    assert runner.commands == [
        ["kubectl", "get", "namespace", "taroai", "-o", "json"],
        [
            "kubectl",
            "get",
            "resourcequota",
            "taroai-sandbox-runtime-quota",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "get",
            "limitrange",
            "taroai-sandbox-runtime-limits",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "get",
            "networkpolicy",
            "taroai-sandbox-runtime-default-deny",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "get",
            "serviceaccount",
            "sandbox-controller",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "get",
            "serviceaccount",
            "sandbox-runner",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "get",
            "role",
            "sandbox-controller",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
        [
            "kubectl",
            "get",
            "rolebinding",
            "sandbox-controller",
            "--namespace",
            "taroai",
            "-o",
            "json",
        ],
    ]


def test_kubernetes_runtime_policy_rejects_extra_rolebinding_subjects():
    runner = ExtraSubjectRoleBindingPolicyRunner()

    with pytest.raises(RuntimeError, match="RoleBinding"):
        verify_kubernetes_runtime_policy(
            KubernetesRuntimePolicyVerificationConfig(
                namespace="taroai",
                kubectl_binary="kubectl",
                resource_quota_name="taroai-sandbox-runtime-quota",
                limit_range_name="taroai-sandbox-runtime-limits",
                network_policy_name="taroai-sandbox-runtime-default-deny",
                controller_service_account_name="sandbox-controller",
                runner_service_account_name="sandbox-runner",
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
            ),
            command_runner=runner,
        )


def test_kubernetes_runtime_policy_rejects_foreign_namespace_rolebinding_subject():
    runner = ForeignNamespaceRoleBindingPolicyRunner()

    with pytest.raises(RuntimeError, match="RoleBinding"):
        verify_kubernetes_runtime_policy(
            KubernetesRuntimePolicyVerificationConfig(
                namespace="taroai",
                kubectl_binary="kubectl",
                resource_quota_name="taroai-sandbox-runtime-quota",
                limit_range_name="taroai-sandbox-runtime-limits",
                network_policy_name="taroai-sandbox-runtime-default-deny",
                controller_service_account_name="sandbox-controller",
                runner_service_account_name="sandbox-runner",
                controller_role_name="sandbox-controller",
                controller_role_binding_name="sandbox-controller",
            ),
            command_runner=runner,
        )


def test_kubernetes_sandbox_verification_exercises_full_adapter_lifecycle(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    runner = RecordingKubectlPolicyRunner()
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        image=DEFAULT_KUBERNETES_SANDBOX_IMAGE,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        service_account_name="sandbox-runner",
        runtime_class_name="gvisor",
        runtime_class_required=True,
        allowed_images=[DEFAULT_KUBERNETES_SANDBOX_IMAGE],
        memory_limit="512Mi",
        cpu_limit="500m",
        ephemeral_storage_limit="1Gi",
        run_as_user=10001,
        run_as_group=10001,
        verify_runtime_policy=True,
    )

    result = verify_kubernetes_sandbox(
        config,
        adapter=adapter,
        runtime_policy_command_runner=runner,
    )

    assert result.provider == "kubernetes"
    assert result.image == DEFAULT_KUBERNETES_SANDBOX_IMAGE
    assert result.namespace == "tenant-sandboxes"
    assert result.service_account_name == "sandbox-runner"
    assert result.runtime_class_name == "gvisor"
    assert result.runtime_class_required is True
    assert result.allowed_images == [DEFAULT_KUBERNETES_SANDBOX_IMAGE]
    assert result.session_id == "sandbox_k8s_verify"
    assert result.pod_name == "taroai-tenant-verify-sandbox"
    assert result.network_policy_name == "taroai-tenant-verify-sandbox-deny-all"
    assert result.network_policy_default_deny is True
    assert result.network_policy_types == ["Ingress", "Egress"]
    assert result.network_policy_session_selector == {
        "taroai.sandbox_session_id": "sandbox_k8s_verify"
    }
    assert result.exit_code == 0
    assert result.stdout_contains == "KUBERNETES VERIFY OK"
    assert result.downloaded_content == "KUBERNETES VERIFY OK"
    assert result.file_paths == [
        "/workspace/artifacts/report.txt",
        "/workspace/input.txt",
    ]
    assert result.snapshot_uri.endswith("/snapshots/snapshot_1")
    assert result.destroyed is True
    assert result.memory_limit == "512Mi"
    assert result.cpu_limit == "500m"
    assert result.ephemeral_storage_limit == "1Gi"
    assert result.workspace_volume_size_limit == "1Gi"
    assert result.tmp_volume_size_limit == "1Gi"
    assert result.pod_active_deadline_seconds == 300
    assert result.host_network is False
    assert result.host_pid is False
    assert result.host_ipc is False
    assert result.pod_run_as_non_root is True
    assert result.seccomp_profile_type == "RuntimeDefault"
    assert result.privileged is False
    assert result.allow_privilege_escalation is False
    assert result.read_only_root_filesystem is True
    assert result.dropped_capabilities == ["ALL"]
    assert result.automount_service_account_token is False
    assert result.service_links_enabled is False
    assert result.termination_grace_period_seconds == 5
    assert result.run_as_user == 10001
    assert result.run_as_group == 10001
    assert result.runtime_policy is not None
    assert result.runtime_policy.resource_quota_hard["pods"] == "50"
    assert result.runtime_policy.network_policy_default_deny is True
    assert adapter.calls == [
        "create",
        "upload",
        "execute",
        "list",
        "download",
        "snapshot",
        "destroy",
    ]


def test_kubernetes_sandbox_verification_rejects_missing_volume_size_evidence(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "workspace_volume_size_limit": "512Mi",
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        ephemeral_storage_limit="1Gi",
    )

    with pytest.raises(RuntimeError, match="workspace volume sizeLimit mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]


def test_kubernetes_sandbox_verification_rejects_host_namespace_access(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "host_network": True,
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        memory_limit="512Mi",
        cpu_limit="500m",
        ephemeral_storage_limit="1Gi",
        run_as_user=10001,
        run_as_group=10001,
    )

    with pytest.raises(RuntimeError, match="host namespace isolation mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]


def test_kubernetes_sandbox_verification_rejects_weak_security_context(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "allow_privilege_escalation": True,
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        memory_limit="512Mi",
        cpu_limit="500m",
        ephemeral_storage_limit="1Gi",
        run_as_user=10001,
        run_as_group=10001,
    )

    with pytest.raises(RuntimeError, match="securityContext mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]


def test_kubernetes_sandbox_verification_rejects_service_account_token_mount(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "automount_service_account_token": True,
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
    )

    with pytest.raises(RuntimeError, match="credential isolation mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]


def test_kubernetes_sandbox_verification_rejects_resource_and_user_mismatch(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "memory_limit": "768Mi",
                "cpu_limit": "750m",
                "run_as_user": 10002,
                "run_as_group": 10002,
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        memory_limit="512Mi",
        cpu_limit="500m",
        run_as_user=10001,
        run_as_group=10001,
    )

    with pytest.raises(RuntimeError, match="resource and user policy mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]


def test_kubernetes_sandbox_verification_rejects_session_network_policy_mismatch(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "network_policy_default_deny": False,
                "network_policy_types": ["Ingress", "Egress"],
                "network_policy_session_selector": {
                    "taroai.sandbox_session_id": "sandbox_k8s_verify"
                },
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        memory_limit="512Mi",
        cpu_limit="500m",
        ephemeral_storage_limit="1Gi",
        run_as_user=10001,
        run_as_group=10001,
    )

    with pytest.raises(RuntimeError, match="session NetworkPolicy mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]


def test_kubernetes_sandbox_verification_rejects_session_identity_mismatch(
    tmp_path: Path,
):
    adapter = RecordingKubernetesSandboxAdapter()
    adapter.session = adapter.session.model_copy(
        update={
            "metadata": {
                **adapter.session.metadata,
                "service_account_name": "default",
                "runtime_class_name": "runc",
            }
        }
    )
    config = KubernetesSandboxVerificationConfig(
        root_dir=tmp_path,
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        namespace="tenant-sandboxes",
        expected_output="KUBERNETES VERIFY OK",
        service_account_name="sandbox-runner",
        runtime_class_name="gvisor",
        runtime_class_required=True,
    )

    with pytest.raises(RuntimeError, match="pod identity mismatch"):
        verify_kubernetes_sandbox(config, adapter=adapter)

    assert adapter.calls == ["create", "destroy"]

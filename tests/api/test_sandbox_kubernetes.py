import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from taroai.domain import utc_now
from taroai.sandbox.adapter import SandboxExecutionError, SandboxProviderUnavailableError
from taroai.sandbox.kubernetes import KubernetesSandboxAdapter
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCreateRequest,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSessionStatus,
)


class RecordingKubectlRunner:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str] = []
        self.created_pod: dict | None = None
        self.created_network_policy: dict | None = None
        self.deleted_pod_names: set[str] = set()
        self.deleted_network_policy_names: set[str] = set()

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if kwargs.get("input"):
            self.inputs.append(kwargs["input"])
        if command[1] == "apply":
            if kwargs.get("input"):
                manifest = json.loads(kwargs["input"])
                self.created_pod = next(
                    item for item in manifest["items"] if item["kind"] == "Pod"
                )
                self.created_network_policy = next(
                    item
                    for item in manifest["items"]
                    if item["kind"] == "NetworkPolicy"
                )
            return subprocess.CompletedProcess(command, 0, stdout="created\n", stderr="")
        if command[1] == "wait":
            return subprocess.CompletedProcess(command, 0, stdout="ready\n", stderr="")
        if command[1:3] == ["get", "pod"] and self.created_pod is not None:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(self.created_pod),
                stderr="",
            )
        if (
            command[1:3] == ["get", "networkpolicy"]
        ):
            network_policy_name = str(command[3])
            if network_policy_name in self.deleted_network_policy_names:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="networkpolicy not found",
                )
            if self.created_network_policy is None:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="networkpolicy not found",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(self.created_network_policy),
                stderr="",
            )
        if command[1] == "exec":
            shell_command = command[-1]
            if "find /workspace" in shell_command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="/workspace/input.txt\t5\n/workspace/artifacts/report.md\t6\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="hello from kubernetes\n",
                stderr="",
            )
        if command[1] == "cp":
            if command[2].startswith("tenant-sandboxes/"):
                Path(command[3]).write_text("downloaded from pod", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "get":
            items = [
                {
                    "metadata": {
                        "name": "taroai-tenant-acme-orphan",
                        "labels": {
                            "taroai.sandbox_session_id": "sandbox_orphan",
                            "taroai.network_policy_name": "taroai-tenant-acme-orphan-deny-all",
                        },
                    }
                },
                {
                    "metadata": {
                        "name": "taroai-tenant-acme-active",
                        "annotations": {
                            "taroai.expires_at": "2999-01-01T00:00:00+00:00"
                        },
                        "labels": {
                            "taroai.sandbox_session_id": "sandbox_active",
                            "taroai.network_policy_name": "taroai-tenant-acme-active-deny-all",
                        },
                    }
                },
                {
                    "metadata": {
                        "name": "taroai-tenant-acme-expired",
                        "annotations": {
                            "taroai.expires_at": "2000-01-01T00:00:00+00:00"
                        },
                        "labels": {
                            "taroai.sandbox_session_id": "sandbox_expired",
                            "taroai.network_policy_name": "taroai-tenant-acme-expired-deny-all",
                        },
                    }
                },
            ]
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "items": [
                            item
                            for item in items
                            if item["metadata"]["name"] not in self.deleted_pod_names
                        ]
                    }
                ),
                stderr="",
            )
        if command[1] == "delete":
            if len(command) > 3 and command[2] == "pod":
                self.deleted_pod_names.add(str(command[3]))
            if len(command) > 5 and command[4] == "networkpolicy":
                self.deleted_network_policy_names.add(str(command[5]))
            return subprocess.CompletedProcess(command, 0, stdout="deleted\n", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="unexpected kubectl command",
        )


class ActiveAfterDeleteKubectlRunner(RecordingKubectlRunner):
    def __init__(self):
        super().__init__()
        self.created_pod: dict | None = None
        self.delete_seen = False

    def __call__(self, command, **kwargs):
        if command[1] == "apply" and kwargs.get("input"):
            manifest = json.loads(kwargs["input"])
            self.created_pod = next(
                item for item in manifest["items"] if item["kind"] == "Pod"
            )
        if command[1] == "delete":
            self.delete_seen = True
            self.calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, stdout="deleted\n", stderr="")
        if command[1] == "get" and self.delete_seen and self.created_pod is not None:
            self.calls.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"items": [self.created_pod]}),
                stderr="",
            )
        return super().__call__(command, **kwargs)


class ExpiredSessionStillActiveAfterDeleteKubectlRunner(RecordingKubectlRunner):
    def __init__(self):
        super().__init__()
        self.delete_seen = False

    def __call__(self, command, **kwargs):
        if command[1] == "delete":
            self.delete_seen = True
            self.calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, stdout="deleted\n", stderr="")
        if command[1:3] == ["get", "pods"] and self.delete_seen:
            self.calls.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": "taroai-tenant-acme-expired-exec",
                                    "labels": {
                                        "taroai.sandbox_session_id": (
                                            "sandbox_expired_exec"
                                        )
                                    },
                                }
                            }
                        ]
                    }
                ),
                stderr="",
            )
        return super().__call__(command, **kwargs)


class CleanupStillActiveAfterDeleteKubectlRunner(RecordingKubectlRunner):
    def __init__(self):
        super().__init__()
        self.delete_seen = False

    def __call__(self, command, **kwargs):
        if command[1] == "delete":
            self.delete_seen = True
            self.calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, stdout="deleted\n", stderr="")
        if command[1:3] == ["get", "pods"]:
            self.calls.append(list(command))
            payload = {
                "items": [
                    {
                        "metadata": {
                            "name": "taroai-tenant-acme-expired",
                            "annotations": {
                                "taroai.expires_at": "2000-01-01T00:00:00+00:00"
                            },
                            "labels": {
                                "taroai.sandbox_session_id": "sandbox_expired",
                                "taroai.network_policy_name": (
                                    "taroai-tenant-acme-expired-deny-all"
                                ),
                            },
                        }
                    }
                ]
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        return super().__call__(command, **kwargs)


class CleanupNetworkPolicyStillActiveAfterDeleteKubectlRunner(RecordingKubectlRunner):
    def __init__(self):
        super().__init__()
        self.deleted_pod_names.add("taroai-tenant-acme-expired")

    def __call__(self, command, **kwargs):
        if command[1:3] == ["get", "networkpolicy"]:
            self.calls.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "metadata": {
                            "name": command[3],
                            "namespace": "tenant-sandboxes",
                        },
                        "spec": {
                            "podSelector": {
                                "matchLabels": {
                                    "taroai.sandbox_session_id": "sandbox_expired"
                                }
                            },
                            "policyTypes": ["Ingress", "Egress"],
                        },
                    }
                ),
                stderr="",
            )
        return super().__call__(command, **kwargs)


class CreatedPodReadbackKubectlRunner(RecordingKubectlRunner):
    def __call__(self, command, **kwargs):
        if command[1:3] == ["get", "pod"] and self.created_pod is not None:
            self.calls.append(list(command))
            actual_pod = json.loads(json.dumps(self.created_pod))
            actual_pod["spec"]["serviceAccountName"] = "actual-sandbox-runner"
            actual_pod["spec"]["runtimeClassName"] = "kata-containers"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(actual_pod),
                stderr="",
            )
        return super().__call__(command, **kwargs)


class EgressAllowedNetworkPolicyReadbackKubectlRunner(RecordingKubectlRunner):
    def __call__(self, command, **kwargs):
        if (
            command[1:3] == ["get", "networkpolicy"]
            and self.created_network_policy is not None
        ):
            self.calls.append(list(command))
            actual_network_policy = json.loads(json.dumps(self.created_network_policy))
            actual_network_policy["spec"]["egress"] = [
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}
            ]
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(actual_network_policy),
                stderr="",
            )
        return super().__call__(command, **kwargs)


def test_kubernetes_sandbox_creates_network_isolated_pod_and_executes_commands(
    tmp_path: Path,
):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        service_account_name="sandbox-runner",
        runtime_class_name="gvisor",
        image_pull_policy="IfNotPresent",
        pod_ready_timeout_seconds=45,
        memory_limit="512Mi",
        cpu_limit="500m",
        ephemeral_storage_limit="1Gi",
        run_as_user=10001,
        run_as_group=10001,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )

    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=300,
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
    result = adapter.execute(
        SandboxCommand(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.id,
            command="cat input.txt",
            cwd="/workspace",
            env={"REPORT_MODE": "safe"},
            timeout_seconds=30,
        )
    )
    files = adapter.list_files("tenant_acme", session.id)
    downloaded = adapter.download_file(
        "tenant_acme",
        session.id,
        "/workspace/input.txt",
    )
    snapshot = adapter.snapshot("tenant_acme", session.id)
    destroyed = adapter.destroy("tenant_acme", session.id)

    manifest = json.loads(runner.inputs[0])
    resources = {item["kind"]: item for item in manifest["items"]}
    pod = resources["Pod"]
    network_policy = resources["NetworkPolicy"]
    container = pod["spec"]["containers"][0]

    assert session.provider == "kubernetes"
    assert session.metadata["namespace"] == "tenant-sandboxes"
    assert session.metadata["pod_name"].startswith("taroai-tenant-acme-")
    assert session.metadata["network_policy_default_deny"] is True
    assert session.metadata["network_policy_types"] == ["Ingress", "Egress"]
    assert session.metadata["network_policy_session_selector"] == {
        "taroai.sandbox_session_id": session.id
    }
    assert session.metadata["service_account_name"] == "sandbox-runner"
    assert session.metadata["runtime_class_name"] == "gvisor"
    assert session.metadata["cpu_limit"] == "500m"
    assert session.metadata["memory_limit"] == "512Mi"
    assert session.metadata["ephemeral_storage_limit"] == "1Gi"
    assert session.metadata["cpu_request"] == "500m"
    assert session.metadata["memory_request"] == "512Mi"
    assert session.metadata["ephemeral_storage_request"] == "1Gi"
    assert session.metadata["workspace_volume_size_limit"] == "1Gi"
    assert session.metadata["tmp_volume_size_limit"] == "1Gi"
    assert session.metadata["pod_active_deadline_seconds"] == 300
    assert session.metadata["host_network"] is False
    assert session.metadata["host_pid"] is False
    assert session.metadata["host_ipc"] is False
    assert session.metadata["pod_run_as_non_root"] is True
    assert session.metadata["run_as_user"] == 10001
    assert session.metadata["run_as_group"] == 10001
    assert session.metadata["fs_group"] == 10001
    assert session.metadata["seccomp_profile_type"] == "RuntimeDefault"
    assert session.metadata["privileged"] is False
    assert session.metadata["allow_privilege_escalation"] is False
    assert session.metadata["read_only_root_filesystem"] is True
    assert session.metadata["dropped_capabilities"] == ["ALL"]
    assert session.metadata["automount_service_account_token"] is False
    assert session.metadata["service_links_enabled"] is False
    assert session.metadata["termination_grace_period_seconds"] == 5
    assert pod["metadata"]["namespace"] == "tenant-sandboxes"
    assert pod["metadata"]["labels"]["taroai.sandbox_session_id"] == session.id
    assert pod["metadata"]["annotations"]["taroai.timeout_seconds"] == "300"
    assert pod["metadata"]["annotations"]["taroai.expires_at"]
    assert pod["spec"]["serviceAccountName"] == "sandbox-runner"
    assert pod["spec"]["runtimeClassName"] == "gvisor"
    assert pod["spec"]["activeDeadlineSeconds"] == 300
    assert pod["spec"]["terminationGracePeriodSeconds"] == 5
    assert pod["spec"]["hostNetwork"] is False
    assert pod["spec"]["hostPID"] is False
    assert pod["spec"]["hostIPC"] is False
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["enableServiceLinks"] is False
    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    assert pod["spec"]["securityContext"]["runAsUser"] == 10001
    assert pod["spec"]["securityContext"]["runAsGroup"] == 10001
    assert pod["spec"]["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert container["image"] == "ghcr.io/creao-ai/sandbox-runtime:2026-07"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["workingDir"] == "/workspace"
    assert container["resources"]["limits"] == {
        "cpu": "500m",
        "memory": "512Mi",
        "ephemeral-storage": "1Gi",
    }
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["privileged"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    volumes = {volume["name"]: volume for volume in pod["spec"]["volumes"]}
    assert volumes["workspace"]["emptyDir"]["sizeLimit"] == "1Gi"
    assert volumes["tmp"]["emptyDir"] == {"medium": "Memory", "sizeLimit": "1Gi"}
    assert network_policy["spec"]["podSelector"]["matchLabels"] == {
        "taroai.sandbox_session_id": session.id
    }
    assert network_policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in network_policy["spec"]
    assert "egress" not in network_policy["spec"]
    assert ["kubectl", "wait", "--for=condition=Ready", f"pod/{session.metadata['pod_name']}", "-n", "tenant-sandboxes", "--timeout=45s"] in runner.calls
    assert uploaded.path == "/workspace/input.txt"
    assert result.exit_code == 0
    assert result.stdout == "hello from kubernetes\n"
    assert any(
        len(call) > 1
        and call[1] == "exec"
        and "REPORT_MODE=safe" in call[-1]
        for call in runner.calls
    )
    assert [file.path for file in files] == [
        "/workspace/artifacts/report.md",
        "/workspace/input.txt",
    ]
    assert downloaded.content == "downloaded from pod"
    assert snapshot.uri == f"kubernetes://tenant-sandboxes/{session.metadata['pod_name']}/snapshots/{snapshot.id}"
    assert destroyed.status == SandboxSessionStatus.DESTROYED
    assert [
        "kubectl",
        "delete",
        "pod",
        session.metadata["pod_name"],
        "networkpolicy",
        session.metadata["network_policy_name"],
        "-n",
        "tenant-sandboxes",
        "--ignore-not-found=true",
        "--wait=false",
    ] in runner.calls
    assert any(call[1:3] == ["get", "pods"] for call in runner.calls)
    assert any(call[1:3] == ["get", "networkpolicy"] for call in runner.calls)


def test_kubernetes_sandbox_refreshes_session_metadata_from_created_pod(
    tmp_path: Path,
):
    runner = CreatedPodReadbackKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        service_account_name="sandbox-runner",
        runtime_class_name="gvisor",
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )

    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=300,
        )
    )

    assert session.metadata["service_account_name"] == "actual-sandbox-runner"
    assert session.metadata["runtime_class_name"] == "kata-containers"
    assert session.metadata["network_policy_default_deny"] is True
    assert session.metadata["network_policy_types"] == ["Ingress", "Egress"]
    assert session.metadata["network_policy_session_selector"] == {
        "taroai.sandbox_session_id": session.id
    }
    assert [
        "kubectl",
        "get",
        "pod",
        session.metadata["pod_name"],
        "-n",
        "tenant-sandboxes",
        "-o",
        "json",
    ] in runner.calls
    assert [
        "kubectl",
        "get",
        "networkpolicy",
        session.metadata["network_policy_name"],
        "-n",
        "tenant-sandboxes",
        "-o",
        "json",
    ] in runner.calls


def test_kubernetes_sandbox_reads_actual_session_network_policy(tmp_path: Path):
    runner = EgressAllowedNetworkPolicyReadbackKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )

    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=300,
        )
    )

    assert session.metadata["network_policy_default_deny"] is False
    assert session.metadata["network_policy_types"] == ["Ingress", "Egress"]
    assert session.metadata["network_policy_session_selector"] == {
        "taroai.sandbox_session_id": session.id
    }


def test_kubernetes_sandbox_destroy_rejects_active_session_after_delete(
    tmp_path: Path,
):
    runner = ActiveAfterDeleteKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=300,
        )
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="did not confirm destroyed session",
    ):
        adapter.destroy("tenant_acme", session.id)

    assert adapter.sessions[session.id].status == SandboxSessionStatus.ACTIVE
    assert runner.calls[-1][1] == "get"


def test_kubernetes_sandbox_declares_hardening_capabilities(tmp_path: Path):
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=RecordingKubectlRunner(),
        max_sessions=7,
        max_sessions_per_tenant=3,
        max_sessions_per_run=2,
    )

    capabilities = adapter.get_capabilities()

    assert capabilities.provider == "kubernetes"
    assert capabilities.network_isolation is True
    assert capabilities.filesystem_isolation is True
    assert capabilities.resource_limits is True
    assert capabilities.destroy_supported is True
    assert capabilities.session_ttl_enforced is True
    assert capabilities.max_session_ttl_seconds == 600
    assert capabilities.max_sessions == 7
    assert capabilities.max_sessions_per_tenant == 3
    assert capabilities.max_sessions_per_run == 2


def test_kubernetes_sandbox_capabilities_do_not_overstate_weak_image_policy(
    tmp_path: Path,
):
    weak_policy_adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=RecordingKubectlRunner(),
        runtime_class_required=True,
        runtime_class_name="gvisor",
        allowed_images=["*"],
    )
    hardened_policy_adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=RecordingKubectlRunner(),
        runtime_class_required=True,
        runtime_class_name="gvisor",
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime@sha256:*"],
    )

    weak_capabilities = weak_policy_adapter.get_capabilities()
    hardened_capabilities = hardened_policy_adapter.get_capabilities()

    assert weak_capabilities.runtime_isolation is True
    assert weak_capabilities.image_policy_enforced is False
    assert weak_capabilities.allowed_image_count == 1
    assert hardened_capabilities.runtime_isolation is True
    assert hardened_capabilities.image_policy_enforced is True
    assert hardened_capabilities.allowed_image_count == 1


def test_kubernetes_sandbox_rejects_session_timeout_above_ttl(tmp_path: Path):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        max_session_ttl_seconds=60,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="session timeout exceeds provider TTL",
    ):
        adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=120,
            )
        )

    assert not any(call[1] == "apply" for call in runner.calls)


def test_kubernetes_sandbox_rejects_expired_session_before_exec(tmp_path: Path):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )
    expired_session = SandboxSession(
        id="sandbox_expired_exec",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="kubernetes",
        image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=300,
        created_at=utc_now() - timedelta(seconds=301),
        metadata={
            "namespace": "tenant-sandboxes",
            "pod_name": "taroai-tenant-acme-expired-exec",
            "network_policy_name": "taroai-tenant-acme-expired-exec-deny-all",
        },
    )
    adapter.sessions[expired_session.id] = expired_session

    with pytest.raises(SandboxExecutionError, match="expired"):
        adapter.execute(
            SandboxCommand(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=expired_session.id,
                command="echo should-not-run",
            )
        )

    assert adapter.sessions[expired_session.id].status == SandboxSessionStatus.DESTROYED
    assert adapter.sessions[expired_session.id].destroyed_at is not None
    assert [
        "kubectl",
        "delete",
        "pod",
        "taroai-tenant-acme-expired-exec",
        "networkpolicy",
        "taroai-tenant-acme-expired-exec-deny-all",
        "-n",
        "tenant-sandboxes",
        "--ignore-not-found=true",
        "--wait=false",
    ] in runner.calls
    assert not any(call[1] == "exec" for call in runner.calls)


def test_kubernetes_sandbox_rejects_expired_session_when_cleanup_leaves_active_pod(
    tmp_path: Path,
):
    runner = ExpiredSessionStillActiveAfterDeleteKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )
    expired_session = SandboxSession(
        id="sandbox_expired_exec",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="kubernetes",
        image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=300,
        created_at=utc_now() - timedelta(seconds=301),
        metadata={
            "namespace": "tenant-sandboxes",
            "pod_name": "taroai-tenant-acme-expired-exec",
            "network_policy_name": "taroai-tenant-acme-expired-exec-deny-all",
        },
    )
    adapter.sessions[expired_session.id] = expired_session

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="did not confirm expired session cleanup",
    ):
        adapter.execute(
            SandboxCommand(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=expired_session.id,
                command="echo should-not-run",
            )
        )

    assert adapter.sessions[expired_session.id].status == SandboxSessionStatus.ACTIVE
    assert any(call[1:3] == ["get", "pods"] for call in runner.calls)
    assert not any(call[1] == "exec" for call in runner.calls)


def test_kubernetes_sandbox_rejects_session_capacity_before_creating_pod(
    tmp_path: Path,
):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
        max_sessions_per_run=1,
    )

    adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
            network_mode=SandboxNetworkMode.DISABLED,
        )
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="sandbox session limit reached",
    ):
        adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )

    apply_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "apply"]
    assert len(apply_calls) == 1


def test_kubernetes_sandbox_rejects_non_disabled_network_mode(tmp_path: Path):
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=RecordingKubectlRunner(),
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="only supports disabled network mode",
    ):
        adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.OPEN,
            )
        )


def test_kubernetes_sandbox_rejects_unsafe_env_names_before_shell_exec(
    tmp_path: Path,
):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
            network_mode=SandboxNetworkMode.DISABLED,
        )
    )

    with pytest.raises(
        SandboxExecutionError,
        match="invalid sandbox environment variable name",
    ):
        adapter.execute(
            SandboxCommand(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=session.id,
                command="echo safe",
                env={"BAD; touch /workspace/pwned": "1"},
            )
        )

    exec_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "exec"]
    assert exec_calls == []


def test_kubernetes_sandbox_requires_image_allowlist(tmp_path: Path):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        runtime_class_name="gvisor",
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="allowed image list must not be empty",
    ):
        adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )

    assert runner.calls == []


def test_kubernetes_sandbox_enforces_image_allowlist_and_runtime_class(
    tmp_path: Path,
):
    runner = RecordingKubectlRunner()
    runtime_class_adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        runtime_class_required=True,
        runtime_class_name="",
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )
    image_policy_adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
        runtime_class_required=True,
        runtime_class_name="gvisor",
        allowed_images=["ghcr.io/creao-ai/sandbox-runtime:2026-07"],
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="requires runtime class",
    ):
        runtime_class_adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="ghcr.io/creao-ai/sandbox-runtime:2026-07",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="sandbox image is not allowed",
    ):
        image_policy_adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="approved registry or digest",
    ):
        KubernetesSandboxAdapter(
            namespace="tenant-sandboxes",
            root_dir=tmp_path,
            kubectl_runner=runner,
            runtime_class_required=True,
            runtime_class_name="gvisor",
            allowed_images=["python:3.12-slim"],
        ).create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="latest",
    ):
        KubernetesSandboxAdapter(
            namespace="tenant-sandboxes",
            root_dir=tmp_path,
            kubectl_runner=runner,
            runtime_class_required=True,
            runtime_class_name="gvisor",
            allowed_images=["ghcr.io/creao-ai/sandbox-runtime:latest"],
        ).create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="ghcr.io/creao-ai/sandbox-runtime:latest",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )

    assert runner.calls == []


def test_kubernetes_sandbox_cleans_orphan_session_pods(tmp_path: Path):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )

    cleaned = adapter.cleanup_orphaned_sessions(
        known_active_session_ids={"sandbox_active", "sandbox_expired"}
    )

    assert cleaned == ["sandbox_expired", "sandbox_orphan"]
    assert [
        "kubectl",
        "get",
        "pods",
        "-n",
        "tenant-sandboxes",
        "-l",
        "app.kubernetes.io/name=taroai-sandbox-session",
        "-o",
        "json",
    ] in runner.calls
    assert [
        "kubectl",
        "delete",
        "pod",
        "taroai-tenant-acme-orphan",
        "networkpolicy",
        "taroai-tenant-acme-orphan-deny-all",
        "-n",
        "tenant-sandboxes",
        "--ignore-not-found=true",
        "--wait=false",
    ] in runner.calls
    assert [
        "kubectl",
        "delete",
        "pod",
        "taroai-tenant-acme-expired",
        "networkpolicy",
        "taroai-tenant-acme-expired-deny-all",
        "-n",
        "tenant-sandboxes",
        "--ignore-not-found=true",
        "--wait=false",
    ] in runner.calls


def test_kubernetes_sandbox_cleanup_marks_tracked_expired_sessions_destroyed(
    tmp_path: Path,
):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )
    adapter.sessions["sandbox_expired"] = SandboxSession(
        id="sandbox_expired",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="kubernetes",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=300,
        metadata={
            "namespace": "tenant-sandboxes",
            "pod_name": "taroai-tenant-acme-expired",
            "network_policy_name": "taroai-tenant-acme-expired-deny-all",
        },
    )
    session_path = tmp_path / "tenant_acme" / "sandbox_expired"
    session_path.mkdir(parents=True)

    cleaned = adapter.cleanup_orphaned_sessions(
        known_active_session_ids={"sandbox_active", "sandbox_expired"}
    )

    assert cleaned == ["sandbox_expired", "sandbox_orphan"]
    assert adapter.sessions["sandbox_expired"].status == SandboxSessionStatus.DESTROYED
    assert adapter.sessions["sandbox_expired"].destroyed_at is not None
    assert not session_path.exists()


def test_kubernetes_sandbox_cleanup_rejects_still_active_deleted_session(
    tmp_path: Path,
):
    runner = CleanupStillActiveAfterDeleteKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )
    adapter.sessions["sandbox_expired"] = SandboxSession(
        id="sandbox_expired",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="kubernetes",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=300,
        metadata={
            "namespace": "tenant-sandboxes",
            "pod_name": "taroai-tenant-acme-expired",
            "network_policy_name": "taroai-tenant-acme-expired-deny-all",
        },
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="did not confirm cleaned session",
    ):
        adapter.cleanup_orphaned_sessions(
            known_active_session_ids={"sandbox_expired"}
        )

    assert adapter.sessions["sandbox_expired"].status == SandboxSessionStatus.ACTIVE
    assert any(call[1:3] == ["get", "pods"] for call in runner.calls)


def test_kubernetes_sandbox_cleanup_rejects_still_active_network_policy(
    tmp_path: Path,
):
    runner = CleanupNetworkPolicyStillActiveAfterDeleteKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )
    adapter.sessions["sandbox_expired"] = SandboxSession(
        id="sandbox_expired",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="kubernetes",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=300,
        metadata={
            "namespace": "tenant-sandboxes",
            "pod_name": "taroai-tenant-acme-expired",
            "network_policy_name": "taroai-tenant-acme-expired-deny-all",
        },
    )

    with pytest.raises(
        SandboxProviderUnavailableError,
        match="did not confirm cleaned NetworkPolicy",
    ):
        adapter.cleanup_orphaned_sessions(
            known_active_session_ids={"sandbox_expired"}
        )

    assert adapter.sessions["sandbox_expired"].status == SandboxSessionStatus.ACTIVE
    assert any(call[1:3] == ["get", "networkpolicy"] for call in runner.calls)


def test_kubernetes_sandbox_treats_empty_active_session_set_as_no_active_pods(
    tmp_path: Path,
):
    runner = RecordingKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )
    adapter.sessions["sandbox_active"] = SandboxSession(
        id="sandbox_active",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="kubernetes",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=300,
    )

    cleaned = adapter.cleanup_orphaned_sessions(known_active_session_ids=set())

    assert cleaned == ["sandbox_active", "sandbox_expired", "sandbox_orphan"]


def test_kubernetes_sandbox_discovers_existing_session_pods_after_controller_restart(
    tmp_path: Path,
):
    class DiscoveryKubectlRunner:
        def __init__(self):
            self.calls: list[list[str]] = []

        def __call__(self, command, **kwargs):
            self.calls.append(list(command))
            if command[1:3] == ["get", "networkpolicy"]:
                network_policy_name = command[3]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "metadata": {
                                "name": network_policy_name,
                                "namespace": "tenant-sandboxes",
                            },
                            "spec": {
                                "podSelector": {
                                    "matchLabels": {
                                        "taroai.sandbox_session_id": (
                                            "sandbox_existing"
                                        )
                                    }
                                },
                                "policyTypes": ["Ingress", "Egress"],
                            },
                        }
                    ),
                    stderr="",
                )
            if command[1] == "get":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "items": [
                                {
                                    "metadata": {
                                        "name": "taroai-tenant-acme-existing",
                                        "annotations": {
                                            "taroai.created_at": (
                                                "2026-07-05T00:00:00+00:00"
                                            ),
                                            "taroai.expires_at": (
                                                "2999-01-01T00:00:00+00:00"
                                            ),
                                            "taroai.timeout_seconds": "600",
                                        },
                                        "labels": {
                                            "taroai.tenant_id": "tenant_acme",
                                            "taroai.workspace_id": "workspace_sales",
                                            "taroai.run_id": "run_1",
                                            "taroai.sandbox_session_id": "sandbox_existing",
                                            "taroai.network_policy_name": (
                                                "taroai-tenant-acme-existing-deny-all"
                                            ),
                                        },
                                    },
                                    "spec": {
                                        "serviceAccountName": "sandbox-runner",
                                        "runtimeClassName": "gvisor",
                                        "activeDeadlineSeconds": 600,
                                        "terminationGracePeriodSeconds": 5,
                                        "automountServiceAccountToken": False,
                                        "enableServiceLinks": False,
                                        "hostNetwork": False,
                                        "hostPID": False,
                                        "hostIPC": False,
                                        "securityContext": {
                                            "runAsNonRoot": True,
                                            "runAsUser": 10001,
                                            "runAsGroup": 10001,
                                            "fsGroup": 10001,
                                            "seccompProfile": {
                                                "type": "RuntimeDefault"
                                            },
                                        },
                                        "containers": [
                                            {
                                                "name": "workspace",
                                                "image": (
                                                    "ghcr.io/creao-ai/"
                                                    "sandbox-runtime:2026-07"
                                                ),
                                                "resources": {
                                                    "limits": {
                                                        "cpu": "500m",
                                                        "memory": "512Mi",
                                                        "ephemeral-storage": "1Gi",
                                                    },
                                                    "requests": {
                                                        "cpu": "500m",
                                                        "memory": "512Mi",
                                                        "ephemeral-storage": "1Gi",
                                                    },
                                                },
                                                "securityContext": {
                                                    "allowPrivilegeEscalation": False,
                                                    "readOnlyRootFilesystem": True,
                                                    "capabilities": {"drop": ["ALL"]},
                                                },
                                            }
                                        ],
                                        "volumes": [
                                            {
                                                "name": "workspace",
                                                "emptyDir": {"sizeLimit": "1Gi"},
                                            },
                                            {
                                                "name": "tmp",
                                                "emptyDir": {
                                                    "medium": "Memory",
                                                    "sizeLimit": "1Gi",
                                                },
                                            },
                                        ],
                                    },
                                }
                            ]
                        }
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    runner = DiscoveryKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )

    sessions = adapter.list_sessions("tenant_acme")

    assert [session.id for session in sessions] == ["sandbox_existing"]
    assert sessions[0].workspace_id == "workspace_sales"
    assert sessions[0].run_id == "run_1"
    assert sessions[0].image == "ghcr.io/creao-ai/sandbox-runtime:2026-07"
    assert sessions[0].timeout_seconds == 600
    assert sessions[0].metadata["pod_name"] == "taroai-tenant-acme-existing"
    assert sessions[0].metadata["network_policy_name"] == (
        "taroai-tenant-acme-existing-deny-all"
    )
    assert sessions[0].metadata["network_policy_default_deny"] is True
    assert sessions[0].metadata["network_policy_types"] == ["Ingress", "Egress"]
    assert sessions[0].metadata["network_policy_session_selector"] == {
        "taroai.sandbox_session_id": "sandbox_existing"
    }
    assert sessions[0].metadata["service_account_name"] == "sandbox-runner"
    assert sessions[0].metadata["runtime_class_name"] == "gvisor"
    assert sessions[0].metadata["cpu_limit"] == "500m"
    assert sessions[0].metadata["memory_limit"] == "512Mi"
    assert sessions[0].metadata["ephemeral_storage_limit"] == "1Gi"
    assert sessions[0].metadata["cpu_request"] == "500m"
    assert sessions[0].metadata["memory_request"] == "512Mi"
    assert sessions[0].metadata["ephemeral_storage_request"] == "1Gi"
    assert sessions[0].metadata["workspace_volume_size_limit"] == "1Gi"
    assert sessions[0].metadata["tmp_volume_size_limit"] == "1Gi"
    assert sessions[0].metadata["pod_active_deadline_seconds"] == 600
    assert sessions[0].metadata["host_network"] is False
    assert sessions[0].metadata["host_pid"] is False
    assert sessions[0].metadata["host_ipc"] is False
    assert sessions[0].metadata["pod_run_as_non_root"] is True
    assert sessions[0].metadata["run_as_user"] == 10001
    assert sessions[0].metadata["run_as_group"] == 10001
    assert sessions[0].metadata["fs_group"] == 10001
    assert sessions[0].metadata["seccomp_profile_type"] == "RuntimeDefault"
    assert sessions[0].metadata["allow_privilege_escalation"] is False
    assert sessions[0].metadata["read_only_root_filesystem"] is True
    assert sessions[0].metadata["dropped_capabilities"] == ["ALL"]
    assert sessions[0].metadata["automount_service_account_token"] is False
    assert sessions[0].metadata["service_links_enabled"] is False
    assert sessions[0].metadata["termination_grace_period_seconds"] == 5
    assert adapter.sessions["sandbox_existing"].tenant_id == "tenant_acme"
    assert [
        "kubectl",
        "get",
        "pods",
        "-n",
        "tenant-sandboxes",
        "-l",
        "app.kubernetes.io/name=taroai-sandbox-session",
        "-o",
        "json",
    ] in runner.calls
    assert [
        "kubectl",
        "get",
        "networkpolicy",
        "taroai-tenant-acme-existing-deny-all",
        "-n",
        "tenant-sandboxes",
        "-o",
        "json",
    ] in runner.calls


def test_kubernetes_sandbox_gets_existing_session_after_controller_restart(
    tmp_path: Path,
):
    class DiscoveryKubectlRunner:
        def __init__(self):
            self.calls: list[list[str]] = []

        def __call__(self, command, **kwargs):
            self.calls.append(list(command))
            if command[1] == "get":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "items": [
                                {
                                    "metadata": {
                                        "name": "taroai-tenant-acme-existing",
                                        "annotations": {
                                            "taroai.created_at": (
                                                "2026-07-05T00:00:00+00:00"
                                            ),
                                            "taroai.expires_at": (
                                                "2999-01-01T00:00:00+00:00"
                                            ),
                                            "taroai.timeout_seconds": "600",
                                        },
                                        "labels": {
                                            "taroai.tenant_id": "tenant_acme",
                                            "taroai.workspace_id": "workspace_sales",
                                            "taroai.run_id": "run_1",
                                            "taroai.sandbox_session_id": "sandbox_existing",
                                            "taroai.network_policy_name": (
                                                "taroai-tenant-acme-existing-deny-all"
                                            ),
                                        },
                                    },
                                    "spec": {
                                        "serviceAccountName": "sandbox-runner",
                                        "runtimeClassName": "gvisor",
                                        "activeDeadlineSeconds": 600,
                                        "terminationGracePeriodSeconds": 5,
                                        "automountServiceAccountToken": False,
                                        "enableServiceLinks": False,
                                        "hostNetwork": False,
                                        "hostPID": False,
                                        "hostIPC": False,
                                        "securityContext": {
                                            "runAsNonRoot": True,
                                            "runAsUser": 10001,
                                            "runAsGroup": 10001,
                                            "fsGroup": 10001,
                                            "seccompProfile": {
                                                "type": "RuntimeDefault"
                                            },
                                        },
                                        "containers": [
                                            {
                                                "name": "workspace",
                                                "image": (
                                                    "ghcr.io/creao-ai/"
                                                    "sandbox-runtime:2026-07"
                                                ),
                                                "resources": {
                                                    "limits": {
                                                        "cpu": "500m",
                                                        "memory": "512Mi",
                                                        "ephemeral-storage": "1Gi",
                                                    },
                                                    "requests": {
                                                        "cpu": "500m",
                                                        "memory": "512Mi",
                                                        "ephemeral-storage": "1Gi",
                                                    },
                                                },
                                                "securityContext": {
                                                    "allowPrivilegeEscalation": False,
                                                    "readOnlyRootFilesystem": True,
                                                    "capabilities": {"drop": ["ALL"]},
                                                },
                                            }
                                        ],
                                        "volumes": [
                                            {
                                                "name": "workspace",
                                                "emptyDir": {"sizeLimit": "1Gi"},
                                            },
                                            {
                                                "name": "tmp",
                                                "emptyDir": {
                                                    "medium": "Memory",
                                                    "sizeLimit": "1Gi",
                                                },
                                            },
                                        ],
                                    },
                                }
                            ]
                        }
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    runner = DiscoveryKubectlRunner()
    adapter = KubernetesSandboxAdapter(
        namespace="tenant-sandboxes",
        root_dir=tmp_path,
        kubectl_runner=runner,
    )

    session = adapter.get_session("tenant_acme", "sandbox_existing")

    assert session.id == "sandbox_existing"
    assert session.workspace_id == "workspace_sales"
    assert session.run_id == "run_1"
    assert session.metadata["pod_name"] == "taroai-tenant-acme-existing"
    assert session.metadata["service_account_name"] == "sandbox-runner"
    assert session.metadata["runtime_class_name"] == "gvisor"
    assert session.metadata["cpu_limit"] == "500m"
    assert session.metadata["memory_limit"] == "512Mi"
    assert session.metadata["ephemeral_storage_limit"] == "1Gi"
    assert session.metadata["cpu_request"] == "500m"
    assert session.metadata["memory_request"] == "512Mi"
    assert session.metadata["ephemeral_storage_request"] == "1Gi"
    assert session.metadata["workspace_volume_size_limit"] == "1Gi"
    assert session.metadata["tmp_volume_size_limit"] == "1Gi"
    assert session.metadata["pod_active_deadline_seconds"] == 600
    assert session.metadata["host_network"] is False
    assert session.metadata["host_pid"] is False
    assert session.metadata["host_ipc"] is False
    assert session.metadata["pod_run_as_non_root"] is True
    assert session.metadata["run_as_user"] == 10001
    assert session.metadata["run_as_group"] == 10001
    assert session.metadata["fs_group"] == 10001
    assert session.metadata["seccomp_profile_type"] == "RuntimeDefault"
    assert session.metadata["allow_privilege_escalation"] is False
    assert session.metadata["read_only_root_filesystem"] is True
    assert session.metadata["dropped_capabilities"] == ["ALL"]
    assert session.metadata["automount_service_account_token"] is False
    assert session.metadata["service_links_enabled"] is False
    assert session.metadata["termination_grace_period_seconds"] == 5

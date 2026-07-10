import json
import mimetypes
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field

from taroai.domain import utc_now
from taroai.errors import NotFoundError
from taroai.sandbox.image_policy import (
    sandbox_runtime_allowed_image_policy_failure_details,
    sandbox_runtime_image_policy_failure_details,
    sandbox_runtime_normalize_allowed_images,
)
from taroai.sandbox.adapter import (
    SandboxAdapter,
    SandboxExecutionError,
    SandboxProviderUnavailableError,
)
from taroai.sandbox.env import invalid_sandbox_env_names
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
    SandboxControllerCapabilities,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSessionStatus,
    SandboxSnapshot,
)


class KubernetesSandboxAdapter(SandboxAdapter):
    provider: str = "kubernetes"
    namespace: str = Field(default="default", min_length=1)
    root_dir: Path = Field(default=Path("/tmp/taroai/kubernetes-sandboxes"))
    kubectl_binary: str = Field(default="kubectl", min_length=1)
    pod_ready_timeout_seconds: int = Field(default=60, ge=1)
    max_output_chars: int = Field(default=65536, ge=1024)
    service_account_name: str = Field(default="sandbox-runner", min_length=1)
    runtime_class_name: str = ""
    runtime_class_required: bool = False
    allowed_images: list[str] = Field(default_factory=list)
    image_pull_policy: str = Field(default="IfNotPresent", min_length=1)
    memory_limit: str = Field(default="1Gi", min_length=1)
    cpu_limit: str = Field(default="1000m", min_length=1)
    ephemeral_storage_limit: str = Field(default="2Gi", min_length=1)
    max_session_ttl_seconds: int = Field(default=600, ge=1)
    max_sessions: int = Field(default=50, ge=1)
    max_sessions_per_tenant: int = Field(default=20, ge=1)
    max_sessions_per_run: int = Field(default=3, ge=1)
    run_as_user: int = Field(default=65532, ge=1)
    run_as_group: int = Field(default=65532, ge=1)
    kubectl_runner: Any | None = Field(default=None, exclude=True, repr=False)
    sessions: dict[str, SandboxSession] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_capabilities(self) -> SandboxControllerCapabilities:
        allowed_images = sandbox_runtime_normalize_allowed_images(
            self.allowed_images
        )
        image_policy_failures = sandbox_runtime_allowed_image_policy_failure_details(
            allowed_images,
            context="kubernetes_allowed_images",
        )
        return SandboxControllerCapabilities(
            provider=self.provider,
            network_isolation=True,
            filesystem_isolation=True,
            resource_limits=True,
            destroy_supported=True,
            session_ttl_enforced=True,
            runtime_isolation=(
                self.runtime_class_required and bool(self.runtime_class_name.strip())
            ),
            image_policy_enforced=not image_policy_failures,
            allowed_image_count=len(allowed_images),
            max_session_ttl_seconds=self.max_session_ttl_seconds,
            max_sessions=self.max_sessions,
            max_sessions_per_tenant=self.max_sessions_per_tenant,
            max_sessions_per_run=self.max_sessions_per_run,
        )

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        if request.network_mode != SandboxNetworkMode.DISABLED:
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox only supports disabled network mode"
            )
        self._validate_create_policy(request)
        self._sync_sessions_from_cluster()
        self._enforce_session_limits(request)
        session = SandboxSession(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            provider=self.provider,
            image=request.image,
            network_mode=request.network_mode,
            timeout_seconds=request.timeout_seconds,
            metadata=request.metadata,
        )
        pod_name = self._pod_name(session)
        network_policy_name = f"{pod_name}-deny-all"
        session = session.model_copy(
            update={
                "metadata": dict(session.metadata)
                | {
                    "namespace": self.namespace,
                    "pod_name": pod_name,
                    "network_policy_name": network_policy_name,
                    "network_policy_default_deny": True,
                    "network_policy_types": ["Ingress", "Egress"],
                    "network_policy_session_selector": {
                        "taroai.sandbox_session_id": session.id,
                    },
                    "service_account_name": self.service_account_name,
                    "runtime_class_name": self.runtime_class_name.strip(),
                    "cpu_limit": self.cpu_limit,
                    "memory_limit": self.memory_limit,
                    "ephemeral_storage_limit": self.ephemeral_storage_limit,
                    "cpu_request": self.cpu_limit,
                    "memory_request": self.memory_limit,
                    "ephemeral_storage_request": self.ephemeral_storage_limit,
                    "workspace_volume_size_limit": self.ephemeral_storage_limit,
                    "tmp_volume_size_limit": self.ephemeral_storage_limit,
                    "pod_active_deadline_seconds": request.timeout_seconds,
                    "host_network": False,
                    "host_pid": False,
                    "host_ipc": False,
                    "pod_run_as_non_root": True,
                    "run_as_user": self.run_as_user,
                    "run_as_group": self.run_as_group,
                    "fs_group": self.run_as_group,
                    "seccomp_profile_type": "RuntimeDefault",
                    "privileged": False,
                    "allow_privilege_escalation": False,
                    "read_only_root_filesystem": True,
                    "dropped_capabilities": ["ALL"],
                    "automount_service_account_token": False,
                    "service_links_enabled": False,
                    "termination_grace_period_seconds": 5,
                }
            }
        )
        self._session_path(session).mkdir(parents=True, exist_ok=True)
        manifest = self._session_manifest(
            session=session,
            pod_name=pod_name,
            network_policy_name=network_policy_name,
        )
        apply_result = self._run_kubectl(
            ["apply", "-f", "-"],
            input_text=json.dumps(manifest, sort_keys=True),
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if apply_result.returncode != 0:
            shutil.rmtree(self._session_path(session), ignore_errors=True)
            message = apply_result.stderr.strip() or apply_result.stdout.strip()
            raise SandboxProviderUnavailableError(
                f"kubernetes sandbox provider failed to create session: {message}"
            )
        wait_result = self._run_kubectl(
            [
                "wait",
                "--for=condition=Ready",
                f"pod/{pod_name}",
                "-n",
                self.namespace,
                f"--timeout={self.pod_ready_timeout_seconds}s",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if wait_result.returncode != 0:
            self._run_kubectl(
                [
                    "delete",
                    "pod",
                    pod_name,
                    "networkpolicy",
                    network_policy_name,
                    "-n",
                    self.namespace,
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                timeout_seconds=self.pod_ready_timeout_seconds,
            )
            shutil.rmtree(self._session_path(session), ignore_errors=True)
            message = wait_result.stderr.strip() or wait_result.stdout.strip()
            raise SandboxProviderUnavailableError(
                f"kubernetes sandbox provider failed to start session: {message}"
            )
        try:
            session = self._read_created_session(
                fallback_session=session,
                pod_name=pod_name,
                network_policy_name=network_policy_name,
            )
        except SandboxProviderUnavailableError:
            self._run_kubectl(
                [
                    "delete",
                    "pod",
                    pod_name,
                    "networkpolicy",
                    network_policy_name,
                    "-n",
                    self.namespace,
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                timeout_seconds=self.pod_ready_timeout_seconds,
            )
            shutil.rmtree(self._session_path(session), ignore_errors=True)
            raise
        self.sessions[session.id] = session
        return session

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        session = self._get_active_session(command.tenant_id, command.session_id)
        self._assert_scope(session, command.workspace_id, command.run_id)
        shell_command = self._shell_command(command)
        try:
            completed = self._run_kubectl(
                [
                    "exec",
                    "-n",
                    self._namespace_for_session(session),
                    self._pod_name_for_session(session),
                    "-c",
                    "workspace",
                    "--",
                    "/bin/sh",
                    "-lc",
                    shell_command,
                ],
                timeout_seconds=command.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            stdout = self._coerce_process_output(error.stdout)
            stderr = self._coerce_process_output(error.stderr)
            if stderr:
                stderr = f"{stderr}\n"
            stderr = f"{stderr}command timed out after {command.timeout_seconds} seconds"
            return self._command_result(command, 124, stdout, stderr)
        return self._command_result(
            command,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        session = self._get_active_session(file_write.tenant_id, file_write.session_id)
        self._assert_scope(session, file_write.workspace_id, file_write.run_id)
        display_path = self._workspace_display_path(file_write.path)
        local_path = self._local_workspace_path(session, display_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        content_bytes = file_write.content.encode("utf-8")
        local_path.write_bytes(content_bytes)
        directory = str(Path(display_path).parent)
        mkdir_result = self._run_workspace_shell(
            session,
            f"mkdir -p {shlex.quote(directory)}",
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if mkdir_result.returncode != 0:
            raise SandboxExecutionError(
                "kubernetes sandbox provider failed to prepare upload directory"
            )
        cp_result = self._run_kubectl(
            [
                "cp",
                str(local_path),
                (
                    f"{self._namespace_for_session(session)}/"
                    f"{self._pod_name_for_session(session)}:{display_path}"
                ),
                "-c",
                "workspace",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if cp_result.returncode != 0:
            message = cp_result.stderr.strip() or cp_result.stdout.strip()
            raise SandboxExecutionError(
                f"kubernetes sandbox provider failed to upload file: {message}"
            )
        return SandboxFileRef(
            tenant_id=file_write.tenant_id,
            workspace_id=file_write.workspace_id,
            run_id=file_write.run_id,
            session_id=file_write.session_id,
            path=display_path,
            content_type=file_write.content_type,
            size_bytes=len(content_bytes),
            content=file_write.content,
        )

    def download_file(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
    ) -> SandboxFileRef:
        session = self._get_active_session(tenant_id, session_id)
        display_path = self._workspace_display_path(path)
        local_path = self._download_path(session, display_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        cp_result = self._run_kubectl(
            [
                "cp",
                (
                    f"{self._namespace_for_session(session)}/"
                    f"{self._pod_name_for_session(session)}:{display_path}"
                ),
                str(local_path),
                "-c",
                "workspace",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if cp_result.returncode != 0:
            message = cp_result.stderr.strip() or cp_result.stdout.strip()
            raise NotFoundError(f"Sandbox file not found: {path}. {message}".strip())
        if not local_path.exists() or not local_path.is_file():
            raise NotFoundError(f"Sandbox file not found: {path}")
        content = local_path.read_text(encoding="utf-8")
        return SandboxFileRef(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            path=display_path,
            content_type=self._content_type(display_path),
            size_bytes=len(content.encode("utf-8")),
            content=content,
        )

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        session = self._get_active_session(tenant_id, session_id)
        completed = self._run_workspace_shell(
            session,
            "find /workspace -type f -printf '%p\\t%s\\n'",
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise SandboxExecutionError(
                f"kubernetes sandbox provider failed to list files: {message}"
            )
        files: list[SandboxFileRef] = []
        for raw_line in completed.stdout.splitlines():
            if not raw_line.strip():
                continue
            raw_path, raw_size = self._split_find_line(raw_line)
            display_path = self._workspace_display_path(raw_path)
            files.append(
                SandboxFileRef(
                    tenant_id=session.tenant_id,
                    workspace_id=session.workspace_id,
                    run_id=session.run_id,
                    session_id=session.id,
                    path=display_path,
                    content_type=self._content_type(display_path),
                    size_bytes=raw_size,
                )
            )
        return sorted(files, key=lambda item: item.path)

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        session = self._get_active_session(tenant_id, session_id)
        snapshot = SandboxSnapshot(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            uri="kubernetes://pending",
        )
        snapshot = snapshot.model_copy(
            update={
                "uri": (
                    f"kubernetes://{self._namespace_for_session(session)}/"
                    f"{self._pod_name_for_session(session)}/snapshots/{snapshot.id}"
                )
            }
        )
        snapshot_path = self._session_path(session) / "snapshots" / "snapshot.json"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot.id,
                    "tenant_id": session.tenant_id,
                    "workspace_id": session.workspace_id,
                    "run_id": session.run_id,
                    "session_id": session.id,
                    "namespace": self._namespace_for_session(session),
                    "pod_name": self._pod_name_for_session(session),
                    "created_at": snapshot.created_at.isoformat(),
                    "files": [
                        {
                            "path": file.path,
                            "size_bytes": file.size_bytes,
                        }
                        for file in self.list_files(tenant_id, session_id)
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return snapshot

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self._get_session(tenant_id, session_id)
        self._delete_session_resources(session)
        if self._cluster_session_pod_active(session_id):
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider did not confirm destroyed session"
            )
        if self._cluster_network_policy_active(
            self._network_policy_name_for_session(session),
            self._namespace_for_session(session),
        ):
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider did not confirm destroyed NetworkPolicy"
            )
        destroyed = session.model_copy(
            update={
                "status": SandboxSessionStatus.DESTROYED,
                "destroyed_at": utc_now(),
            }
        )
        self.sessions[session_id] = destroyed
        shutil.rmtree(self._session_path(session), ignore_errors=True)
        return destroyed

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        return self._get_session(tenant_id, session_id)

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        self._sync_sessions_from_cluster()
        sessions = sorted(
            self.sessions.values(),
            key=lambda item: (item.created_at, item.id),
        )
        if tenant_id is None:
            return sessions
        return [session for session in sessions if session.tenant_id == tenant_id]

    def _cluster_session_pod_active(self, session_id: str) -> bool:
        result = self._run_kubectl(
            [
                "get",
                "pods",
                "-n",
                self.namespace,
                "-l",
                "app.kubernetes.io/name=taroai-sandbox-session",
                "-o",
                "json",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider could not confirm destroyed session: "
                f"{message}"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider returned invalid pod list JSON"
            ) from error
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict) or metadata.get("deletionTimestamp"):
                continue
            labels = metadata.get("labels", {})
            if not isinstance(labels, dict):
                continue
            if labels.get("taroai.sandbox_session_id") == session_id:
                return True
        return False

    def _read_created_session(
        self,
        fallback_session: SandboxSession,
        pod_name: str,
        network_policy_name: str,
    ) -> SandboxSession:
        result = self._run_kubectl(
            [
                "get",
                "pod",
                pod_name,
                "-n",
                self.namespace,
                "-o",
                "json",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider failed to read created session pod: "
                f"{message}"
            )
        try:
            pod = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider returned invalid created pod JSON"
            ) from error
        session = self._session_from_pod_item(pod)
        if session is None or session.id != fallback_session.id:
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider could not confirm created session pod"
            )
        network_policy_metadata = self._read_created_network_policy_metadata(
            network_policy_name,
            session.id,
        )
        return session.model_copy(
            update={"metadata": dict(session.metadata) | network_policy_metadata}
        )

    def _read_created_network_policy_metadata(
        self,
        network_policy_name: str,
        session_id: str,
    ) -> dict[str, Any]:
        result = self._run_kubectl(
            [
                "get",
                "networkpolicy",
                network_policy_name,
                "-n",
                self.namespace,
                "-o",
                "json",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider failed to read created NetworkPolicy: "
                f"{message}"
            )
        try:
            network_policy = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider returned invalid NetworkPolicy JSON"
            ) from error
        spec = network_policy.get("spec", {})
        if not isinstance(spec, dict):
            spec = {}
        pod_selector = spec.get("podSelector", {})
        if not isinstance(pod_selector, dict):
            pod_selector = {}
        match_labels = pod_selector.get("matchLabels", {})
        if not isinstance(match_labels, dict):
            match_labels = {}
        session_selector = {
            str(key): str(value)
            for key, value in match_labels.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        raw_policy_types = spec.get("policyTypes", [])
        policy_types = (
            [str(policy_type) for policy_type in raw_policy_types]
            if isinstance(raw_policy_types, list)
            else []
        )
        default_deny = (
            session_selector.get("taroai.sandbox_session_id") == session_id
            and sorted(policy_types) == ["Egress", "Ingress"]
            and "ingress" not in spec
            and "egress" not in spec
        )
        return {
            "network_policy_default_deny": default_deny,
            "network_policy_types": policy_types,
            "network_policy_session_selector": session_selector,
        }

    def cleanup_orphaned_sessions(
        self,
        known_active_session_ids: set[str] | None = None,
    ) -> list[str]:
        if known_active_session_ids is None:
            active_ids = set(self.sessions.keys())
        else:
            active_ids = set(known_active_session_ids)
        result = self._run_kubectl(
            [
                "get",
                "pods",
                "-n",
                self.namespace,
                "-l",
                "app.kubernetes.io/name=taroai-sandbox-session",
                "-o",
                "json",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise SandboxExecutionError(
                f"kubernetes sandbox provider failed to list session pods: {message}"
            )
        cleaned_session_ids: list[str] = []
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise SandboxExecutionError(
                "kubernetes sandbox provider returned invalid pod list JSON"
            ) from error
        for item in payload.get("items", []):
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            labels = metadata.get("labels", {})
            annotations = metadata.get("annotations", {})
            session_id = labels.get("taroai.sandbox_session_id")
            pod_name = metadata.get("name")
            if not session_id or not pod_name:
                continue
            if (
                session_id in active_ids
                and not self._pod_session_is_expired(annotations)
            ):
                continue
            network_policy_name = (
                labels.get("taroai.network_policy_name")
                or f"{pod_name}-deny-all"
            )
            delete_result = self._run_kubectl(
                [
                    "delete",
                    "pod",
                    str(pod_name),
                    "networkpolicy",
                    str(network_policy_name),
                    "-n",
                    self.namespace,
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                timeout_seconds=self.pod_ready_timeout_seconds,
            )
            if delete_result.returncode != 0:
                message = delete_result.stderr.strip() or delete_result.stdout.strip()
                raise SandboxExecutionError(
                    "kubernetes sandbox provider failed to clean orphaned session "
                    f"{session_id}: {message}"
                )
            if self._cluster_session_pod_active(str(session_id)):
                raise SandboxProviderUnavailableError(
                    "kubernetes sandbox provider did not confirm cleaned session "
                    f"{session_id}"
                )
            if self._cluster_network_policy_active(
                str(network_policy_name),
                self.namespace,
            ):
                raise SandboxProviderUnavailableError(
                    "kubernetes sandbox provider did not confirm cleaned NetworkPolicy "
                    f"{network_policy_name}"
                )
            tracked_session = self.sessions.get(str(session_id))
            if tracked_session is not None:
                destroyed_session = tracked_session.model_copy(
                    update={
                        "status": SandboxSessionStatus.DESTROYED,
                        "destroyed_at": utc_now(),
                    }
                )
                self.sessions[destroyed_session.id] = destroyed_session
                shutil.rmtree(self._session_path(tracked_session), ignore_errors=True)
            cleaned_session_ids.append(str(session_id))
        return sorted(cleaned_session_ids)

    def _enforce_session_limits(self, request: SandboxCreateRequest) -> None:
        active_sessions = [
            session
            for session in self.sessions.values()
            if session.status == SandboxSessionStatus.ACTIVE
        ]
        tenant_sessions = [
            session
            for session in active_sessions
            if session.tenant_id == request.tenant_id
        ]
        run_sessions = [
            session
            for session in tenant_sessions
            if session.run_id == request.run_id
        ]
        if (
            len(active_sessions) >= self.max_sessions
            or len(tenant_sessions) >= self.max_sessions_per_tenant
            or len(run_sessions) >= self.max_sessions_per_run
        ):
            raise SandboxProviderUnavailableError("sandbox session limit reached")

    def _sync_sessions_from_cluster(self) -> None:
        result = self._run_kubectl(
            [
                "get",
                "pods",
                "-n",
                self.namespace,
                "-l",
                "app.kubernetes.io/name=taroai-sandbox-session",
                "-o",
                "json",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise SandboxExecutionError(
                f"kubernetes sandbox provider failed to list session pods: {message}"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise SandboxExecutionError(
                "kubernetes sandbox provider returned invalid pod list JSON"
            ) from error
        for item in payload.get("items", []):
            session = self._session_from_pod_item(item)
            if session is None:
                continue
            existing = self.sessions.get(session.id)
            if (
                existing is not None
                and existing.status == SandboxSessionStatus.DESTROYED
            ):
                continue
            network_policy_metadata = self._read_created_network_policy_metadata(
                self._network_policy_name_for_session(session),
                session.id,
            )
            session = session.model_copy(
                update={"metadata": dict(session.metadata) | network_policy_metadata}
            )
            self.sessions[session.id] = session

    def _session_from_pod_item(self, item: Any) -> SandboxSession | None:
        if not isinstance(item, dict):
            return None
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            return None
        labels = metadata.get("labels", {})
        annotations = metadata.get("annotations", {})
        if not isinstance(labels, dict) or not isinstance(annotations, dict):
            return None
        if metadata.get("deletionTimestamp"):
            return None
        session_id = labels.get("taroai.sandbox_session_id")
        tenant_id = labels.get("taroai.tenant_id")
        workspace_id = labels.get("taroai.workspace_id")
        run_id = labels.get("taroai.run_id")
        pod_name = metadata.get("name")
        required_values = [session_id, tenant_id, workspace_id, run_id, pod_name]
        if not all(isinstance(value, str) and value for value in required_values):
            return None
        network_policy_name = (
            labels.get("taroai.network_policy_name") or f"{pod_name}-deny-all"
        )
        if not isinstance(network_policy_name, str) or not network_policy_name:
            network_policy_name = f"{pod_name}-deny-all"
        session_metadata = {
            "namespace": self.namespace,
            "pod_name": pod_name,
            "network_policy_name": network_policy_name,
        }
        service_account_name = self._pod_spec_string(item, "serviceAccountName")
        if service_account_name:
            session_metadata["service_account_name"] = service_account_name
        runtime_class_name = self._pod_spec_string(item, "runtimeClassName")
        if runtime_class_name:
            session_metadata["runtime_class_name"] = runtime_class_name
        workspace_volume_size_limit = self._pod_empty_dir_size_limit(item, "workspace")
        tmp_volume_size_limit = self._pod_empty_dir_size_limit(item, "tmp")
        if workspace_volume_size_limit:
            session_metadata["workspace_volume_size_limit"] = workspace_volume_size_limit
        if tmp_volume_size_limit:
            session_metadata["tmp_volume_size_limit"] = tmp_volume_size_limit
        pod_active_deadline_seconds = self._pod_active_deadline_seconds(item)
        if pod_active_deadline_seconds is not None:
            session_metadata["pod_active_deadline_seconds"] = pod_active_deadline_seconds
        for metadata_key, spec_key in (
            ("host_network", "hostNetwork"),
            ("host_pid", "hostPID"),
            ("host_ipc", "hostIPC"),
        ):
            spec_value = self._pod_spec_bool(item, spec_key)
            if spec_value is not None:
                session_metadata[metadata_key] = spec_value
        pod_run_as_non_root = self._pod_security_context_bool(item, "runAsNonRoot")
        if pod_run_as_non_root is not None:
            session_metadata["pod_run_as_non_root"] = pod_run_as_non_root
        for metadata_key, spec_key in (
            ("run_as_user", "runAsUser"),
            ("run_as_group", "runAsGroup"),
            ("fs_group", "fsGroup"),
        ):
            spec_value = self._pod_security_context_positive_int(item, spec_key)
            if spec_value is not None:
                session_metadata[metadata_key] = spec_value
        seccomp_profile_type = self._pod_seccomp_profile_type(item)
        if seccomp_profile_type:
            session_metadata["seccomp_profile_type"] = seccomp_profile_type
        for metadata_key, section, resource_name in (
            ("cpu_limit", "limits", "cpu"),
            ("memory_limit", "limits", "memory"),
            ("ephemeral_storage_limit", "limits", "ephemeral-storage"),
            ("cpu_request", "requests", "cpu"),
            ("memory_request", "requests", "memory"),
            ("ephemeral_storage_request", "requests", "ephemeral-storage"),
        ):
            resource_value = self._workspace_resource_value(
                item,
                section,
                resource_name,
            )
            if resource_value:
                session_metadata[metadata_key] = resource_value
        allow_privilege_escalation = self._workspace_security_context_bool(
            item,
            "allowPrivilegeEscalation",
        )
        privileged = self._workspace_security_context_bool(item, "privileged")
        if privileged is not None:
            session_metadata["privileged"] = privileged
        if allow_privilege_escalation is not None:
            session_metadata["allow_privilege_escalation"] = allow_privilege_escalation
        read_only_root_filesystem = self._workspace_security_context_bool(
            item,
            "readOnlyRootFilesystem",
        )
        if read_only_root_filesystem is not None:
            session_metadata["read_only_root_filesystem"] = read_only_root_filesystem
        dropped_capabilities = self._workspace_dropped_capabilities(item)
        if dropped_capabilities:
            session_metadata["dropped_capabilities"] = dropped_capabilities
        automount_service_account_token = self._pod_spec_bool(
            item,
            "automountServiceAccountToken",
        )
        if automount_service_account_token is not None:
            session_metadata["automount_service_account_token"] = (
                automount_service_account_token
            )
        service_links_enabled = self._pod_spec_bool(item, "enableServiceLinks")
        if service_links_enabled is not None:
            session_metadata["service_links_enabled"] = service_links_enabled
        termination_grace_period_seconds = self._pod_spec_positive_int(
            item,
            "terminationGracePeriodSeconds",
        )
        if termination_grace_period_seconds is not None:
            session_metadata["termination_grace_period_seconds"] = (
                termination_grace_period_seconds
            )
        return SandboxSession(
            id=session_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            provider=self.provider,
            image=self._pod_workspace_image(item),
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=self._pod_timeout_seconds(annotations),
            metadata=session_metadata,
            created_at=self._pod_created_at(annotations),
        )

    def _pod_workspace_image(self, item: dict[str, Any]) -> str:
        image = self._workspace_container(item).get("image")
        if isinstance(image, str) and image:
            return image
        return "unknown"

    def _pod_active_deadline_seconds(self, item: dict[str, Any]) -> int | None:
        return self._pod_spec_positive_int(item, "activeDeadlineSeconds")

    def _pod_spec_positive_int(self, item: dict[str, Any], key: str) -> int | None:
        spec = item.get("spec", {})
        value = spec.get(key) if isinstance(spec, dict) else None
        if isinstance(value, int) and value >= 1:
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = int(value)
            except ValueError:
                return None
            if parsed >= 1:
                return parsed
        return None

    def _pod_spec_bool(self, item: dict[str, Any], key: str) -> bool | None:
        spec = item.get("spec", {})
        value = spec.get(key) if isinstance(spec, dict) else None
        if isinstance(value, bool):
            return value
        return None

    def _pod_spec_string(self, item: dict[str, Any], key: str) -> str:
        spec = item.get("spec", {})
        value = spec.get(key) if isinstance(spec, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""

    def _pod_security_context_bool(
        self,
        item: dict[str, Any],
        key: str,
    ) -> bool | None:
        security_context = self._pod_security_context(item)
        value = security_context.get(key)
        if isinstance(value, bool):
            return value
        return None

    def _pod_security_context_positive_int(
        self,
        item: dict[str, Any],
        key: str,
    ) -> int | None:
        security_context = self._pod_security_context(item)
        value = security_context.get(key)
        if isinstance(value, int) and value >= 1:
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = int(value)
            except ValueError:
                return None
            if parsed >= 1:
                return parsed
        return None

    def _pod_seccomp_profile_type(self, item: dict[str, Any]) -> str:
        security_context = self._pod_security_context(item)
        seccomp_profile = security_context.get("seccompProfile", {})
        if not isinstance(seccomp_profile, dict):
            return ""
        profile_type = seccomp_profile.get("type")
        if isinstance(profile_type, str) and profile_type.strip():
            return profile_type
        return ""

    def _pod_security_context(self, item: dict[str, Any]) -> dict[str, Any]:
        spec = item.get("spec", {})
        security_context = (
            spec.get("securityContext", {}) if isinstance(spec, dict) else {}
        )
        if isinstance(security_context, dict):
            return security_context
        return {}

    def _workspace_security_context_bool(
        self,
        item: dict[str, Any],
        key: str,
    ) -> bool | None:
        security_context = self._workspace_security_context(item)
        value = security_context.get(key)
        if isinstance(value, bool):
            return value
        return None

    def _workspace_dropped_capabilities(self, item: dict[str, Any]) -> list[str]:
        security_context = self._workspace_security_context(item)
        capabilities = security_context.get("capabilities", {})
        if not isinstance(capabilities, dict):
            return []
        dropped = capabilities.get("drop", [])
        if not isinstance(dropped, list):
            return []
        return [str(capability) for capability in dropped]

    def _workspace_security_context(self, item: dict[str, Any]) -> dict[str, Any]:
        container = self._workspace_container(item)
        security_context = container.get("securityContext", {})
        if isinstance(security_context, dict):
            return security_context
        return {}

    def _workspace_resource_value(
        self,
        item: dict[str, Any],
        section_name: str,
        resource_name: str,
    ) -> str:
        container = self._workspace_container(item)
        resources = container.get("resources", {})
        if not isinstance(resources, dict):
            return ""
        section = resources.get(section_name, {})
        if not isinstance(section, dict):
            return ""
        value = section.get(resource_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        return ""

    def _workspace_container(self, item: dict[str, Any]) -> dict[str, Any]:
        spec = item.get("spec", {})
        containers = spec.get("containers", []) if isinstance(spec, dict) else []
        if not isinstance(containers, list):
            return {}
        fallback_container: dict[str, Any] = {}
        for container in containers:
            if not isinstance(container, dict):
                continue
            if not fallback_container:
                fallback_container = container
            if container.get("name") == "workspace":
                return container
        return fallback_container

    def _pod_empty_dir_size_limit(self, item: dict[str, Any], volume_name: str) -> str:
        spec = item.get("spec", {})
        volumes = spec.get("volumes", []) if isinstance(spec, dict) else []
        if not isinstance(volumes, list):
            return ""
        for volume in volumes:
            if not isinstance(volume, dict) or volume.get("name") != volume_name:
                continue
            empty_dir = volume.get("emptyDir", {})
            if not isinstance(empty_dir, dict):
                return ""
            size_limit = empty_dir.get("sizeLimit")
            if isinstance(size_limit, str) and size_limit.strip():
                return size_limit
            return ""
        return ""

    def _pod_timeout_seconds(self, annotations: dict[str, Any]) -> int:
        value = annotations.get("taroai.timeout_seconds")
        if isinstance(value, str) and value.strip():
            try:
                parsed = int(value)
            except ValueError:
                parsed = 300
            if parsed >= 1:
                return parsed
        return 300

    def _pod_created_at(self, annotations: dict[str, Any]) -> datetime:
        value = annotations.get("taroai.created_at")
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return utc_now()
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return utc_now()

    def _session_manifest(
        self,
        session: SandboxSession,
        pod_name: str,
        network_policy_name: str,
    ) -> dict[str, Any]:
        labels = {
            "app.kubernetes.io/name": "taroai-sandbox-session",
            "app.kubernetes.io/part-of": "taroai",
            "taroai.tenant_id": session.tenant_id,
            "taroai.workspace_id": session.workspace_id,
            "taroai.run_id": session.run_id,
            "taroai.sandbox_session_id": session.id,
            "taroai.network_policy_name": network_policy_name,
        }
        annotations = self._session_annotations(session)
        pod_spec: dict[str, Any] = {
            "serviceAccountName": self.service_account_name,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "activeDeadlineSeconds": session.timeout_seconds,
            "terminationGracePeriodSeconds": 5,
            "hostNetwork": False,
            "hostPID": False,
            "hostIPC": False,
            "restartPolicy": "Never",
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": self.run_as_user,
                "runAsGroup": self.run_as_group,
                "fsGroup": self.run_as_group,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "workspace",
                    "image": session.image,
                    "imagePullPolicy": self.image_pull_policy,
                    "workingDir": "/workspace",
                    "command": ["sleep", "infinity"],
                    "resources": {
                        "limits": {
                            "cpu": self.cpu_limit,
                            "memory": self.memory_limit,
                            "ephemeral-storage": self.ephemeral_storage_limit,
                        },
                        "requests": {
                            "cpu": self.cpu_limit,
                            "memory": self.memory_limit,
                            "ephemeral-storage": self.ephemeral_storage_limit,
                        },
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "privileged": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": "/workspace"},
                        {"name": "tmp", "mountPath": "/tmp"},
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "workspace",
                    "emptyDir": {"sizeLimit": self.ephemeral_storage_limit},
                },
                {
                    "name": "tmp",
                    "emptyDir": {
                        "medium": "Memory",
                        "sizeLimit": self.ephemeral_storage_limit,
                    },
                },
            ],
        }
        if self.runtime_class_name.strip():
            pod_spec["runtimeClassName"] = self.runtime_class_name.strip()
        return {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": pod_name,
                        "namespace": self.namespace,
                        "labels": labels,
                        "annotations": annotations,
                    },
                    "spec": pod_spec,
                },
                {
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "NetworkPolicy",
                    "metadata": {
                        "name": network_policy_name,
                        "namespace": self.namespace,
                        "labels": labels,
                        "annotations": annotations,
                    },
                    "spec": {
                        "podSelector": {
                            "matchLabels": {
                                "taroai.sandbox_session_id": session.id,
                            }
                        },
                        "policyTypes": ["Ingress", "Egress"],
                    },
                },
            ],
        }

    def _session_annotations(self, session: SandboxSession) -> dict[str, str]:
        expires_at = session.created_at + timedelta(seconds=session.timeout_seconds)
        return {
            "taroai.created_at": session.created_at.isoformat(),
            "taroai.expires_at": expires_at.isoformat(),
            "taroai.timeout_seconds": str(session.timeout_seconds),
        }

    def _pod_session_is_expired(self, annotations: dict[str, Any]) -> bool:
        expires_at = annotations.get("taroai.expires_at")
        if not isinstance(expires_at, str) or not expires_at.strip():
            return False
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= utc_now()

    def _validate_create_policy(self, request: SandboxCreateRequest) -> None:
        if self.runtime_class_required and not self.runtime_class_name.strip():
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox provider requires runtime class"
            )
        if request.timeout_seconds > self.max_session_ttl_seconds:
            raise SandboxProviderUnavailableError(
                "kubernetes sandbox session timeout exceeds provider TTL"
            )
        policy_details = sandbox_runtime_image_policy_failure_details(
            image=request.image,
            allowed_images=self.allowed_images,
            context="kubernetes sandbox",
        )
        if policy_details:
            raise SandboxProviderUnavailableError("; ".join(policy_details))

    def _run_workspace_shell(
        self,
        session: SandboxSession,
        shell_command: str,
        timeout_seconds: int | None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run_kubectl(
            [
                "exec",
                "-n",
                self._namespace_for_session(session),
                self._pod_name_for_session(session),
                "-c",
                "workspace",
                "--",
                "/bin/sh",
                "-lc",
                shell_command,
            ],
            timeout_seconds=timeout_seconds,
        )

    def _run_kubectl(
        self,
        args: list[str],
        timeout_seconds: int | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.kubectl_binary, *args]
        runner = self.kubectl_runner or subprocess.run
        try:
            return runner(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                input=input_text,
                check=False,
            )
        except FileNotFoundError as error:
            raise SandboxProviderUnavailableError(
                f"kubectl binary is not available: {self.kubectl_binary}"
            ) from error
        except OSError as error:
            raise SandboxProviderUnavailableError(
                f"kubernetes sandbox provider is unavailable: {error}"
            ) from error

    def _shell_command(self, command: SandboxCommand) -> str:
        cwd = self._workspace_display_path(command.cwd)
        exports = " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in sorted(self._command_env(command.env).items())
        )
        return f"cd {shlex.quote(cwd)} && export {exports}; {command.command}"

    def _command_result(
        self,
        command: SandboxCommand,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> SandboxCommandResult:
        return SandboxCommandResult(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            command=command.command,
            exit_code=exit_code,
            stdout=self._limit_output(stdout),
            stderr=self._limit_output(stderr),
        )

    def _get_active_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self._get_session(tenant_id, session_id)
        if session.status != SandboxSessionStatus.ACTIVE:
            raise SandboxExecutionError(f"Sandbox session is not active: {session_id}")
        if self._session_expired(session):
            try:
                self._delete_session_resources(session)
            except SandboxExecutionError as error:
                raise SandboxExecutionError(
                    f"Sandbox session expired and cleanup failed: {session_id}"
                ) from error
            if self._cluster_session_pod_active(session_id):
                raise SandboxProviderUnavailableError(
                    "kubernetes sandbox provider did not confirm expired session cleanup"
                )
            if self._cluster_network_policy_active(
                self._network_policy_name_for_session(session),
                self._namespace_for_session(session),
            ):
                raise SandboxProviderUnavailableError(
                    "kubernetes sandbox provider did not confirm expired NetworkPolicy cleanup"
                )
            expired_session = session.model_copy(
                update={
                    "status": SandboxSessionStatus.DESTROYED,
                    "destroyed_at": utc_now(),
                }
            )
            self.sessions[session_id] = expired_session
            shutil.rmtree(self._session_path(session), ignore_errors=True)
            raise SandboxExecutionError(f"Sandbox session expired: {session_id}")
        return session

    def _delete_session_resources(self, session: SandboxSession) -> None:
        result = self._run_kubectl(
            [
                "delete",
                "pod",
                self._pod_name_for_session(session),
                "networkpolicy",
                self._network_policy_name_for_session(session),
                "-n",
                self._namespace_for_session(session),
                "--ignore-not-found=true",
                "--wait=false",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise SandboxExecutionError(
                f"kubernetes sandbox provider failed to destroy session: {message}"
            )

    def _cluster_network_policy_active(
        self,
        network_policy_name: str,
        namespace: str,
    ) -> bool:
        result = self._run_kubectl(
            [
                "get",
                "networkpolicy",
                network_policy_name,
                "-n",
                namespace,
                "-o",
                "json",
            ],
            timeout_seconds=self.pod_ready_timeout_seconds,
        )
        if result.returncode == 0:
            return True
        message = (result.stderr or result.stdout or "").lower()
        if "not found" in message or "notfound" in message:
            return False
        raise SandboxProviderUnavailableError(
            "kubernetes sandbox provider could not confirm NetworkPolicy cleanup: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    def _session_expired(self, session: SandboxSession) -> bool:
        created_at = session.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at + timedelta(seconds=session.timeout_seconds) <= utc_now()

    def _get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self.sessions.get(session_id)
        if session is None:
            self._sync_sessions_from_cluster()
            session = self.sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise NotFoundError(f"Sandbox session not found: {session_id}")
        return session

    def _assert_scope(
        self,
        session: SandboxSession,
        workspace_id: str,
        run_id: str,
    ) -> None:
        if session.workspace_id != workspace_id or session.run_id != run_id:
            raise NotFoundError(f"Sandbox session not found: {session.id}")

    def _workspace_display_path(self, requested_path: str) -> str:
        if requested_path == "/workspace":
            return "/workspace"
        if requested_path.startswith("/workspace/"):
            candidate = Path(requested_path)
        elif Path(requested_path).is_absolute():
            raise SandboxExecutionError("sandbox path is outside sandbox workspace")
        else:
            candidate = Path("/workspace") / requested_path
        normalized_parts: list[str] = []
        for part in candidate.parts:
            if part in ("", "/"):
                continue
            if part == ".":
                continue
            if part == "..":
                if normalized_parts:
                    normalized_parts.pop()
                continue
            normalized_parts.append(part)
        normalized = "/" + "/".join(normalized_parts)
        if normalized == "/workspace" or normalized.startswith("/workspace/"):
            return normalized
        raise SandboxExecutionError("sandbox path is outside sandbox workspace")

    def _local_workspace_path(self, session: SandboxSession, display_path: str) -> Path:
        relative_path = display_path.removeprefix("/workspace/").strip("/")
        return self._session_path(session) / "uploads" / relative_path

    def _download_path(self, session: SandboxSession, display_path: str) -> Path:
        relative_path = display_path.removeprefix("/workspace/").strip("/")
        return self._session_path(session) / "downloads" / relative_path

    def _session_path(self, session: SandboxSession) -> Path:
        return (
            self.root_dir
            / self._safe_path_part(session.tenant_id)
            / self._safe_path_part(session.id)
        )

    def _pod_name(self, session: SandboxSession) -> str:
        name = (
            f"taroai-{self._safe_kubernetes_name_part(session.tenant_id)}-"
            f"{self._safe_kubernetes_name_part(session.id)}"
        )
        return name[:63].strip("-") or f"taroai-{session.id[-8:]}"

    def _namespace_for_session(self, session: SandboxSession) -> str:
        return str(session.metadata.get("namespace") or self.namespace)

    def _pod_name_for_session(self, session: SandboxSession) -> str:
        return str(session.metadata.get("pod_name") or self._pod_name(session))

    def _network_policy_name_for_session(self, session: SandboxSession) -> str:
        return str(
            session.metadata.get("network_policy_name")
            or f"{self._pod_name_for_session(session)}-deny-all"
        )

    def _command_env(self, custom_env: dict[str, str]) -> dict[str, str]:
        invalid_names = invalid_sandbox_env_names(custom_env)
        if invalid_names:
            raise SandboxExecutionError(
                "invalid sandbox environment variable name: "
                + ", ".join(invalid_names)
            )
        env = dict(custom_env)
        env["PYTHONUNBUFFERED"] = "1"
        env["TAROAI_SANDBOX_WORKSPACE"] = "/workspace"
        return env

    def _content_type(self, path: str) -> str:
        return mimetypes.guess_type(Path(path).name)[0] or "text/plain"

    def _split_find_line(self, raw_line: str) -> tuple[str, int]:
        if "\t" not in raw_line:
            return raw_line, 0
        raw_path, raw_size = raw_line.rsplit("\t", 1)
        try:
            size = int(raw_size)
        except ValueError:
            size = 0
        return raw_path, size

    def _safe_path_part(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "value"

    def _safe_kubernetes_name_part(self, value: str) -> str:
        safe_value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
        return safe_value or "value"

    def _limit_output(self, value: str) -> str:
        if len(value) <= self.max_output_chars:
            return value
        return f"{value[:self.max_output_chars]}\n[output truncated]"

    def _coerce_process_output(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

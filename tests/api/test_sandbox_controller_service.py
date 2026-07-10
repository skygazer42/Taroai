from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import PrivateAttr, ValidationError
import pytest

from taroai.domain import utc_now
from taroai.errors import NotFoundError
from taroai.sandbox.adapter import SandboxAdapter
from taroai.sandbox.controller_service import (
    SandboxControllerServiceSettings,
    build_sandbox_controller_adapter,
    create_sandbox_controller_app,
)
from taroai.sandbox.docker import DockerSandboxAdapter
from taroai.sandbox.kubernetes import KubernetesSandboxAdapter
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


class RecordingSandboxAdapter(SandboxAdapter):
    provider: str = "docker"

    _sessions: dict[str, SandboxSession] = PrivateAttr(default_factory=dict)
    _files: dict[tuple[str, str], SandboxFileRef] = PrivateAttr(default_factory=dict)
    _created_count: int = PrivateAttr(default=0)

    @property
    def sessions(self) -> dict[str, SandboxSession]:
        return self._sessions

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        self._created_count += 1
        session = SandboxSession(
            id=f"sandbox_{self._created_count}",
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            provider=self.provider,
            image=request.image,
            network_mode=request.network_mode,
            timeout_seconds=request.timeout_seconds,
            metadata=request.metadata,
        )
        self._sessions[session.id] = session
        return session

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        session = self.get_session(command.tenant_id, command.session_id)
        return SandboxCommandResult(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            command=command.command,
            exit_code=0,
            stdout="sandbox controller ok\n",
        )

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        session = self.get_session(file_write.tenant_id, file_write.session_id)
        file_ref = SandboxFileRef(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            path=file_write.path,
            content_type=file_write.content_type,
            size_bytes=len(file_write.content.encode("utf-8")),
            content=file_write.content,
        )
        self._files[(session.id, file_write.path)] = file_ref
        return file_ref

    def download_file(self, tenant_id: str, session_id: str, path: str) -> SandboxFileRef:
        self.get_session(tenant_id, session_id)
        file_ref = self._files.get((session_id, path))
        if file_ref is None:
            raise NotFoundError(f"Sandbox file not found: {path}")
        return file_ref

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        self.get_session(tenant_id, session_id)
        return [
            file_ref
            for (file_session_id, _path), file_ref in self._files.items()
            if file_session_id == session_id
        ]

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        session = self.get_session(tenant_id, session_id)
        return SandboxSnapshot(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            uri=f"s3://tenant/{session.id}/snapshot.json",
        )

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self.get_session(tenant_id, session_id)
        destroyed = session.model_copy(
            update={
                "status": SandboxSessionStatus.DESTROYED,
                "destroyed_at": utc_now(),
            }
        )
        self._sessions[session_id] = destroyed
        return destroyed

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self._sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise NotFoundError(f"Sandbox session not found: {session_id}")
        return session

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda session: (session.created_at, session.id),
        )
        if tenant_id is None:
            return sessions
        return [session for session in sessions if session.tenant_id == tenant_id]


class RecordingCleanupSandboxAdapter(RecordingSandboxAdapter):
    _cleanup_calls: list[set[str]] = PrivateAttr(default_factory=list)

    @property
    def cleanup_calls(self) -> list[set[str]]:
        return self._cleanup_calls

    def cleanup_orphaned_sessions(
        self,
        known_active_session_ids: set[str] | None = None,
    ) -> list[str]:
        self._cleanup_calls.append(set(known_active_session_ids or set()))
        return ["sandbox_orphan"]


class ProviderGlobalListSandboxAdapter(RecordingSandboxAdapter):
    _tenant_filtered_calls: int = PrivateAttr(default=0)

    @property
    def tenant_filtered_calls(self) -> int:
        return self._tenant_filtered_calls

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        if tenant_id is not None:
            self._tenant_filtered_calls += 1
            return []
        return sorted(
            self.sessions.values(),
            key=lambda session: (session.created_at, session.id),
        )


def controller_client(
    adapter: RecordingSandboxAdapter,
    settings: SandboxControllerServiceSettings | None = None,
) -> TestClient:
    return TestClient(
        create_sandbox_controller_app(
            adapter=adapter,
            settings=settings
            or SandboxControllerServiceSettings(
                api_key="sandbox_controller_secret_2026_long_key",
                session_ttl_seconds=120,
                max_sessions=2,
                max_sessions_per_tenant=2,
                max_sessions_per_run=2,
            ),
        )
    )


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer sandbox_controller_secret_2026_long_key"}


def session_payload(run_id: str = "run_1") -> dict:
    return {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
        "run_id": run_id,
        "image": "python:3.12-slim",
        "network_mode": SandboxNetworkMode.DISABLED.value,
        "timeout_seconds": 60,
        "metadata": {"purpose": "verification"},
    }


def test_sandbox_controller_service_matches_http_provider_contract():
    adapter = RecordingSandboxAdapter()
    client = controller_client(adapter)

    health = client.get("/healthz")
    unauthenticated = client.post("/sessions", json=session_payload())
    capabilities = client.get("/capabilities", headers=auth_headers())
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]
    listed = client.get("/sessions?tenant_id=tenant_acme", headers=auth_headers())
    uploaded = client.post(
        "/files",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
            "path": "/workspace/artifacts/report.txt",
            "content": "hello artifact",
            "content_type": "text/plain",
        },
    )
    files = client.get(
        (
            f"/files?tenant_id=tenant_acme&session_id={session_id}"
            "&workspace_id=workspace_sales&run_id=run_1"
        ),
        headers=auth_headers(),
    )
    downloaded = client.get(
        (
            f"/files?tenant_id=tenant_acme&session_id={session_id}"
            "&workspace_id=workspace_sales&run_id=run_1"
            "&path=/workspace/artifacts/report.txt"
        ),
        headers=auth_headers(),
    )
    command = client.post(
        "/commands",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
            "command": "cat /workspace/artifacts/report.txt",
            "cwd": "/workspace",
            "timeout_seconds": 30,
            "env": {},
        },
    )
    snapshot = client.post(
        "/snapshots",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
        },
    )
    fetched = client.get(
        f"/sessions/{session_id}?tenant_id=tenant_acme",
        headers=auth_headers(),
    )
    destroyed = client.delete(
        f"/sessions/{session_id}?tenant_id=tenant_acme",
        headers=auth_headers(),
    )

    assert health.status_code == 200
    assert unauthenticated.status_code == 401
    assert capabilities.status_code == 200
    assert capabilities.json()["provider"] == "docker"
    assert capabilities.json()["network_isolation"] is True
    assert capabilities.json()["filesystem_isolation"] is True
    assert capabilities.json()["resource_limits"] is True
    assert capabilities.json()["destroy_supported"] is True
    assert capabilities.json()["session_ttl_enforced"] is True
    assert capabilities.json()["max_session_ttl_seconds"] == 120
    assert capabilities.json()["max_sessions"] == 2
    assert capabilities.json()["max_sessions_per_tenant"] == 2
    assert capabilities.json()["max_sessions_per_run"] == 2
    assert created.status_code == 201
    assert listed.json()["sessions"][0]["id"] == session_id
    assert uploaded.json()["path"] == "/workspace/artifacts/report.txt"
    assert files.json()["files"][0]["path"] == "/workspace/artifacts/report.txt"
    assert downloaded.json()["content"] == "hello artifact"
    assert command.json()["stdout"] == "sandbox controller ok\n"
    assert snapshot.json()["uri"].endswith(f"/{session_id}/snapshot.json")
    assert fetched.json()["id"] == session_id
    assert destroyed.status_code == 200
    assert destroyed.json()["status"] == SandboxSessionStatus.DESTROYED.value


def test_sandbox_controller_service_enforces_active_session_capacity():
    adapter = RecordingSandboxAdapter()
    client = controller_client(
        adapter,
        SandboxControllerServiceSettings(
            api_key="sandbox_controller_secret_2026_long_key",
            session_ttl_seconds=120,
            max_sessions=5,
            max_sessions_per_tenant=5,
            max_sessions_per_run=1,
        ),
    )

    first = client.post("/sessions", json=session_payload(), headers=auth_headers())
    second = client.post("/sessions", json=session_payload(), headers=auth_headers())

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["detail"] == "sandbox session limit reached"


def test_sandbox_controller_service_rejects_cross_workspace_run_operations():
    adapter = RecordingSandboxAdapter()
    client = controller_client(adapter)
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]

    command = client.post(
        "/commands",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_finance",
            "run_id": "run_2",
            "session_id": session_id,
            "command": "cat /workspace/artifacts/report.txt",
            "cwd": "/workspace",
            "timeout_seconds": 30,
            "env": {},
        },
    )
    upload = client.post(
        "/files",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_finance",
            "run_id": "run_2",
            "session_id": session_id,
            "path": "/workspace/artifacts/report.txt",
            "content": "wrong scope",
            "content_type": "text/plain",
        },
    )
    files = client.get(
        (
            f"/files?tenant_id=tenant_acme&session_id={session_id}"
            "&workspace_id=workspace_sales&run_id=run_1"
        ),
        headers=auth_headers(),
    )

    assert command.status_code == 404
    assert command.json()["detail"] == f"Sandbox session not found: {session_id}"
    assert upload.status_code == 404
    assert upload.json()["detail"] == f"Sandbox session not found: {session_id}"
    assert files.json()["files"] == []


def test_sandbox_controller_service_rejects_cross_workspace_run_snapshots():
    adapter = RecordingSandboxAdapter()
    client = controller_client(adapter)
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]

    snapshot = client.post(
        "/snapshots",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_finance",
            "run_id": "run_2",
            "session_id": session_id,
        },
    )

    assert snapshot.status_code == 404
    assert snapshot.json()["detail"] == f"Sandbox session not found: {session_id}"


def test_sandbox_controller_service_rejects_cross_workspace_run_file_reads():
    adapter = RecordingSandboxAdapter()
    client = controller_client(adapter)
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]
    client.post(
        "/files",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
            "path": "/workspace/artifacts/report.txt",
            "content": "sales artifact",
            "content_type": "text/plain",
        },
    )

    listed = client.get(
        (
            f"/files?tenant_id=tenant_acme&session_id={session_id}"
            "&workspace_id=workspace_finance&run_id=run_2"
        ),
        headers=auth_headers(),
    )
    downloaded = client.get(
        (
            f"/files?tenant_id=tenant_acme&session_id={session_id}"
            "&workspace_id=workspace_finance&run_id=run_2"
            "&path=/workspace/artifacts/report.txt"
        ),
        headers=auth_headers(),
    )

    assert listed.status_code == 404
    assert listed.json()["detail"] == f"Sandbox session not found: {session_id}"
    assert downloaded.status_code == 404
    assert downloaded.json()["detail"] == f"Sandbox session not found: {session_id}"


def test_sandbox_controller_service_rejects_operations_after_destroy():
    adapter = RecordingSandboxAdapter()
    client = controller_client(adapter)
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]

    destroyed = client.delete(
        f"/sessions/{session_id}?tenant_id=tenant_acme",
        headers=auth_headers(),
    )
    command = client.post(
        "/commands",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
            "command": "echo should-not-run",
            "cwd": "/workspace",
            "timeout_seconds": 30,
            "env": {},
        },
    )
    upload = client.post(
        "/files",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
            "path": "/workspace/artifacts/after-destroy.txt",
            "content": "should not write",
            "content_type": "text/plain",
        },
    )
    files = client.get(
        (
            f"/files?tenant_id=tenant_acme&session_id={session_id}"
            "&workspace_id=workspace_sales&run_id=run_1"
        ),
        headers=auth_headers(),
    )
    snapshot = client.post(
        "/snapshots",
        headers=auth_headers(),
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": session_id,
        },
    )

    assert destroyed.status_code == 200
    for response in (command, upload, files, snapshot):
        assert response.status_code == 404
        assert response.json()["detail"] == f"Sandbox session not found: {session_id}"
    assert adapter._files == {}


def test_sandbox_controller_service_cleans_expired_sessions_before_listing():
    adapter = RecordingSandboxAdapter()
    client = controller_client(
        adapter,
        SandboxControllerServiceSettings(
            api_key="sandbox_controller_secret_2026_long_key",
            session_ttl_seconds=60,
            max_sessions=5,
            max_sessions_per_tenant=5,
            max_sessions_per_run=5,
        ),
    )
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]
    adapter.sessions[session_id] = adapter.sessions[session_id].model_copy(
        update={"created_at": utc_now() - timedelta(seconds=61)}
    )

    listed = client.get("/sessions?tenant_id=tenant_acme", headers=auth_headers())

    assert listed.status_code == 200
    assert listed.json()["sessions"][0]["status"] == SandboxSessionStatus.DESTROYED.value


def test_sandbox_controller_service_lists_all_known_sessions_without_tenant_filter():
    adapter = RecordingSandboxAdapter()
    client = controller_client(adapter)
    first = client.post("/sessions", json=session_payload(), headers=auth_headers())
    second = client.post(
        "/sessions",
        json=session_payload(run_id="run_2")
        | {
            "tenant_id": "tenant_other",
            "workspace_id": "workspace_support",
        },
        headers=auth_headers(),
    )

    listed = client.get("/sessions", headers=auth_headers())

    assert first.status_code == 201
    assert second.status_code == 201
    assert listed.status_code == 200
    assert [session["tenant_id"] for session in listed.json()["sessions"]] == [
        "tenant_acme",
        "tenant_other",
    ]


def test_sandbox_controller_service_uses_provider_global_session_list_after_restart():
    adapter = ProviderGlobalListSandboxAdapter()
    existing_session = SandboxSession(
        id="sandbox_existing",
        tenant_id="tenant_existing",
        workspace_id="workspace_existing",
        run_id="run_existing",
        provider="docker",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=60,
    )
    adapter.sessions[existing_session.id] = existing_session
    client = controller_client(adapter)

    listed = client.get("/sessions", headers=auth_headers())

    assert listed.status_code == 200
    assert [session["id"] for session in listed.json()["sessions"]] == [
        "sandbox_existing"
    ]
    assert adapter.tenant_filtered_calls == 0


def test_sandbox_controller_service_create_limit_counts_provider_global_sessions():
    adapter = ProviderGlobalListSandboxAdapter()
    existing_session = SandboxSession(
        id="sandbox_existing",
        tenant_id="tenant_existing",
        workspace_id="workspace_existing",
        run_id="run_existing",
        provider="docker",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=60,
    )
    adapter.sessions[existing_session.id] = existing_session
    client = controller_client(
        adapter,
        SandboxControllerServiceSettings(
            api_key="sandbox_controller_secret_2026_long_key",
            max_sessions=1,
            max_sessions_per_tenant=1,
            max_sessions_per_run=1,
        ),
    )

    response = client.post("/sessions", json=session_payload(), headers=auth_headers())

    assert response.status_code == 429
    assert response.json()["detail"] == "sandbox session limit reached"


def test_sandbox_controller_service_cleans_expired_sessions_before_create_limit():
    adapter = RecordingSandboxAdapter()
    client = controller_client(
        adapter,
        SandboxControllerServiceSettings(
            api_key="sandbox_controller_secret_2026_long_key",
            session_ttl_seconds=60,
            max_sessions=5,
            max_sessions_per_tenant=5,
            max_sessions_per_run=1,
        ),
    )
    expired = SandboxSession(
        id="sandbox_expired",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        provider="docker",
        image="python:3.12-slim",
        network_mode=SandboxNetworkMode.DISABLED,
        timeout_seconds=60,
        created_at=utc_now() - timedelta(seconds=61),
    )
    adapter.sessions[expired.id] = expired

    created = client.post("/sessions", json=session_payload(), headers=auth_headers())

    assert created.status_code == 201
    assert adapter.sessions["sandbox_expired"].status == SandboxSessionStatus.DESTROYED


def test_sandbox_controller_service_runs_gated_orphan_cleanup_with_active_sessions():
    adapter = RecordingCleanupSandboxAdapter()
    client = controller_client(
        adapter,
        SandboxControllerServiceSettings(
            api_key="sandbox_controller_secret_2026_long_key",
            session_ttl_seconds=120,
            max_sessions=5,
            max_sessions_per_tenant=5,
            max_sessions_per_run=5,
            kubernetes_orphan_cleanup_enabled=True,
        ),
    )
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]

    listed = client.get("/sessions?tenant_id=tenant_acme", headers=auth_headers())

    assert listed.status_code == 200
    assert adapter.cleanup_calls == [{session_id}]


def test_sandbox_controller_service_runs_orphan_cleanup_for_known_tenant_without_active_sessions():
    adapter = RecordingCleanupSandboxAdapter()
    client = controller_client(
        adapter,
        SandboxControllerServiceSettings(
            api_key="sandbox_controller_secret_2026_long_key",
            session_ttl_seconds=120,
            max_sessions=5,
            max_sessions_per_tenant=5,
            max_sessions_per_run=5,
            kubernetes_orphan_cleanup_enabled=True,
        ),
    )
    created = client.post("/sessions", json=session_payload(), headers=auth_headers())
    session_id = created.json()["id"]
    client.delete(f"/sessions/{session_id}?tenant_id=tenant_acme", headers=auth_headers())
    adapter.cleanup_calls.clear()

    listed = client.get("/sessions?tenant_id=tenant_acme", headers=auth_headers())

    assert listed.status_code == 200
    assert adapter.cleanup_calls == [set()]


def test_sandbox_controller_service_settings_rejects_short_api_key():
    with pytest.raises(ValidationError):
        SandboxControllerServiceSettings(api_key="short_sandbox_secret")


def test_sandbox_controller_service_settings_require_kubernetes_runtime_class_policy():
    with pytest.raises(ValidationError, match="kubernetes_runtime_class_required"):
        SandboxControllerServiceSettings(
            provider="kubernetes",
            kubernetes_runtime_class_required=False,
            kubernetes_runtime_class_name="gvisor",
            kubernetes_allowed_images=[
                "ghcr.io/creao-ai/sandbox-runtime@sha256:*"
            ],
        )

    with pytest.raises(ValidationError, match="kubernetes_runtime_class_name"):
        SandboxControllerServiceSettings(
            provider="k8s",
            kubernetes_runtime_class_required=True,
            kubernetes_runtime_class_name=" ",
            kubernetes_allowed_images=[
                "ghcr.io/creao-ai/sandbox-runtime@sha256:*"
            ],
        )


def test_sandbox_controller_service_settings_rejects_weak_kubernetes_image_policy():
    with pytest.raises(ValidationError, match="kubernetes_allowed_images"):
        SandboxControllerServiceSettings(
            provider="kubernetes",
            kubernetes_runtime_class_name="gvisor",
            kubernetes_runtime_class_required=True,
            kubernetes_allowed_images=[],
        )

    with pytest.raises(ValidationError, match="broad"):
        SandboxControllerServiceSettings(
            provider="kubernetes",
            kubernetes_runtime_class_name="gvisor",
            kubernetes_runtime_class_required=True,
            kubernetes_allowed_images=["*"],
        )

    with pytest.raises(ValidationError, match="approved registry or digest"):
        SandboxControllerServiceSettings(
            provider="kubernetes",
            kubernetes_runtime_class_name="gvisor",
            kubernetes_runtime_class_required=True,
            kubernetes_allowed_images=["python:3.12-slim"],
        )

    with pytest.raises(ValidationError, match="latest"):
        SandboxControllerServiceSettings(
            provider="kubernetes",
            kubernetes_runtime_class_name="gvisor",
            kubernetes_runtime_class_required=True,
            kubernetes_allowed_images=["ghcr.io/creao-ai/sandbox-runtime:latest"],
        )

    with pytest.raises(ValidationError, match="broad"):
        SandboxControllerServiceSettings(
            provider="kubernetes",
            kubernetes_runtime_class_name="gvisor",
            kubernetes_runtime_class_required=True,
            kubernetes_allowed_images=["ghcr.io/creao-ai/sandbox-runtime:*"],
        )


def test_sandbox_controller_builds_kubernetes_provider_from_settings(tmp_path: Path):
    adapter = build_sandbox_controller_adapter(
        SandboxControllerServiceSettings(
            provider="kubernetes",
            root_dir=tmp_path,
            session_ttl_seconds=300,
            max_sessions=17,
            max_sessions_per_tenant=9,
            max_sessions_per_run=4,
            kubernetes_namespace="tenant-sandboxes",
            kubernetes_service_account_name="sandbox-runner",
            kubernetes_runtime_class_name="gvisor",
            kubernetes_runtime_class_required=True,
            kubernetes_allowed_images=[
                "ghcr.io/creao-ai/sandbox-runtime@sha256:*"
            ],
            kubernetes_image_pull_policy="IfNotPresent",
            kubernetes_memory_limit="512Mi",
            kubernetes_cpu_limit="500m",
            kubernetes_ephemeral_storage_limit="1Gi",
        )
    )

    assert isinstance(adapter, KubernetesSandboxAdapter)
    assert adapter.namespace == "tenant-sandboxes"
    assert adapter.root_dir == tmp_path
    assert adapter.service_account_name == "sandbox-runner"
    assert adapter.runtime_class_name == "gvisor"
    assert adapter.runtime_class_required is True
    assert adapter.allowed_images == ["ghcr.io/creao-ai/sandbox-runtime@sha256:*"]
    assert adapter.image_pull_policy == "IfNotPresent"
    assert adapter.memory_limit == "512Mi"
    assert adapter.cpu_limit == "500m"
    assert adapter.ephemeral_storage_limit == "1Gi"
    assert adapter.max_session_ttl_seconds == 300
    assert adapter.max_sessions == 17
    assert adapter.max_sessions_per_tenant == 9
    assert adapter.max_sessions_per_run == 4


def test_sandbox_controller_builds_docker_provider_capacity_from_settings(
    tmp_path: Path,
):
    adapter = build_sandbox_controller_adapter(
        SandboxControllerServiceSettings(
            provider="docker",
            root_dir=tmp_path,
            max_sessions=17,
            max_sessions_per_tenant=9,
            max_sessions_per_run=4,
        )
    )

    assert isinstance(adapter, DockerSandboxAdapter)
    assert adapter.max_sessions == 17
    assert adapter.max_sessions_per_tenant == 9
    assert adapter.max_sessions_per_run == 4

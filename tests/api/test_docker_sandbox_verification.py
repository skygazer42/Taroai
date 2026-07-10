from pathlib import Path

from taroai.sandbox.docker_verification import (
    DockerSandboxVerificationConfig,
    parse_args,
    verify_docker_sandbox,
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


class RecordingDockerSandboxAdapter:
    def __init__(self):
        self.calls: list[str] = []
        self.session = SandboxSession(
            id="sandbox_verify",
            tenant_id="tenant_verify",
            workspace_id="workspace_verify",
            run_id="run_verify",
            provider="docker",
            image="python:3.12-slim",
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=300,
            status=SandboxSessionStatus.ACTIVE,
            metadata={"container_name": "taroai_verify"},
        )

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        self.calls.append("create")
        assert request.network_mode == SandboxNetworkMode.DISABLED
        assert request.image == "python:3.12-slim"
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
            stdout="DOCKER VERIFY OK\n",
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
                size_bytes=16,
            ),
            SandboxFileRef(
                tenant_id=tenant_id,
                workspace_id="workspace_verify",
                run_id="run_verify",
                session_id=session_id,
                path="/workspace/artifacts/report.txt",
                content_type="text/plain",
                size_bytes=17,
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
            size_bytes=len("DOCKER VERIFY OK"),
            content="DOCKER VERIFY OK",
        )

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        self.calls.append("snapshot")
        return SandboxSnapshot(
            tenant_id=tenant_id,
            workspace_id="workspace_verify",
            run_id="run_verify",
            session_id=session_id,
            uri="file:///tmp/taroai/snapshot.json",
        )

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        self.calls.append("destroy")
        return self.session.model_copy(
            update={"status": SandboxSessionStatus.DESTROYED}
        )


def test_docker_sandbox_verification_cli_parses_core_inputs(tmp_path: Path):
    config = parse_args(
        [
            "--root-dir",
            str(tmp_path),
            "--image",
            "python:3.12-slim",
            "--memory-limit",
            "512m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "96",
            "--container-user",
            "10001:10001",
        ]
    )

    assert config.root_dir == tmp_path
    assert config.image == "python:3.12-slim"
    assert config.memory_limit == "512m"
    assert config.cpus == 0.5
    assert config.pids_limit == 96
    assert config.container_user == "10001:10001"


def test_docker_sandbox_verification_exercises_full_adapter_lifecycle(tmp_path: Path):
    adapter = RecordingDockerSandboxAdapter()
    config = DockerSandboxVerificationConfig(
        root_dir=tmp_path,
        image="python:3.12-slim",
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        run_id="run_verify",
        expected_output="DOCKER VERIFY OK",
    )

    result = verify_docker_sandbox(config, adapter=adapter)

    assert result.provider == "docker"
    assert result.image == "python:3.12-slim"
    assert result.container_user == "65532:65532"
    assert result.session_id == "sandbox_verify"
    assert result.container_name == "taroai_verify"
    assert result.exit_code == 0
    assert result.stdout_contains == "DOCKER VERIFY OK"
    assert result.downloaded_content == "DOCKER VERIFY OK"
    assert result.file_paths == [
        "/workspace/artifacts/report.txt",
        "/workspace/input.txt",
    ]
    assert result.snapshot_uri == "file:///tmp/taroai/snapshot.json"
    assert result.destroyed is True
    assert adapter.calls == [
        "create",
        "upload",
        "execute",
        "list",
        "download",
        "snapshot",
        "destroy",
    ]

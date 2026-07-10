import argparse
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from taroai.sandbox.docker import DockerSandboxAdapter
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCreateRequest,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSessionStatus,
)


DEFAULT_DOCKER_VERIFY_COMMAND = (
    "python -c \"from pathlib import Path; "
    "text=Path('/workspace/input.txt').read_text().strip(); "
    "Path('/workspace/artifacts').mkdir(exist_ok=True); "
    "Path('/workspace/artifacts/report.txt').write_text(text.upper()); "
    "print(text.upper())\""
)


class DockerSandboxVerificationConfig(BaseModel):
    root_dir: Path = Field(default=Path("/tmp/taroai/docker-sandbox-verify"))
    image: str = Field(default="python:3.12-slim", min_length=1)
    tenant_id: str = Field(default="tenant_docker_verify", min_length=1)
    workspace_id: str = Field(default="workspace_docker_verify", min_length=1)
    run_id: str = Field(default_factory=lambda: f"run_docker_verify_{uuid4().hex[:12]}")
    input_path: str = Field(default="/workspace/input.txt", min_length=1)
    output_path: str = Field(default="/workspace/artifacts/report.txt", min_length=1)
    input_content: str = Field(default="docker verify ok", min_length=1)
    expected_output: str = Field(default="DOCKER VERIFY OK", min_length=1)
    command: str = Field(default=DEFAULT_DOCKER_VERIFY_COMMAND, min_length=1)
    command_timeout_seconds: int = Field(default=30, ge=1)
    session_timeout_seconds: int = Field(default=300, ge=1)
    docker_binary: str = Field(default="docker", min_length=1)
    memory_limit: str = Field(default="512m", min_length=1)
    cpus: float = Field(default=0.5, gt=0)
    pids_limit: int = Field(default=96, ge=1)
    container_user: str = Field(default="65532:65532", min_length=1)
    read_only_rootfs: bool = True
    drop_all_capabilities: bool = True
    security_opts: list[str] = Field(default_factory=lambda: ["no-new-privileges:true"])
    tmpfs_mounts: list[str] = Field(
        default_factory=lambda: ["/tmp:rw,noexec,nosuid,size=128m"]
    )


class DockerSandboxVerificationResult(BaseModel):
    provider: str
    image: str
    session_id: str
    container_name: str
    exit_code: int
    stdout_contains: str
    downloaded_content: str
    file_paths: list[str] = Field(default_factory=list)
    snapshot_uri: str
    destroyed: bool
    memory_limit: str
    cpus: float
    pids_limit: int
    container_user: str
    read_only_rootfs: bool
    drop_all_capabilities: bool
    security_opts: list[str] = Field(default_factory=list)
    tmpfs_mounts: list[str] = Field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> DockerSandboxVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify the Docker sandbox provider against a real Docker engine."
    )
    parser.add_argument("--root-dir", type=Path, default=Path("/tmp/taroai/docker-sandbox-verify"))
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--tenant-id", default="tenant_docker_verify")
    parser.add_argument("--workspace-id", default="workspace_docker_verify")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--memory-limit", default="512m")
    parser.add_argument("--cpus", type=float, default=0.5)
    parser.add_argument("--pids-limit", type=int, default=96)
    parser.add_argument("--container-user", default="65532:65532")
    parser.add_argument("--command-timeout-seconds", type=int, default=30)
    parsed = parser.parse_args(argv)
    config_data = {
        "root_dir": parsed.root_dir,
        "image": parsed.image,
        "tenant_id": parsed.tenant_id,
        "workspace_id": parsed.workspace_id,
        "docker_binary": parsed.docker_binary,
        "memory_limit": parsed.memory_limit,
        "cpus": parsed.cpus,
        "pids_limit": parsed.pids_limit,
        "container_user": parsed.container_user,
        "command_timeout_seconds": parsed.command_timeout_seconds,
    }
    if parsed.run_id is not None:
        config_data["run_id"] = parsed.run_id
    return DockerSandboxVerificationConfig(**config_data)


def verify_docker_sandbox(
    config: DockerSandboxVerificationConfig,
    adapter=None,
) -> DockerSandboxVerificationResult:
    docker_adapter = adapter or build_docker_sandbox_adapter(config)
    session: SandboxSession | None = None
    try:
        session = docker_adapter.create(
            SandboxCreateRequest(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                image=config.image,
                network_mode=SandboxNetworkMode.DISABLED,
                timeout_seconds=config.session_timeout_seconds,
            )
        )
        docker_adapter.upload_file(
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
        command_result = docker_adapter.execute(
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
                f"docker sandbox command failed with exit code {command_result.exit_code}"
            )
        if config.expected_output not in command_result.stdout:
            raise RuntimeError("docker sandbox command output did not include expected text")
        file_paths = sorted(
            file_ref.path
            for file_ref in docker_adapter.list_files(config.tenant_id, session.id)
        )
        downloaded = docker_adapter.download_file(
            config.tenant_id,
            session.id,
            config.output_path,
        )
        if downloaded.content != config.expected_output:
            raise RuntimeError("docker sandbox downloaded artifact content did not match")
        snapshot = docker_adapter.snapshot(config.tenant_id, session.id)
        destroyed = docker_adapter.destroy(config.tenant_id, session.id)
        result = DockerSandboxVerificationResult(
            provider=session.provider,
            image=session.image,
            session_id=session.id,
            container_name=str(session.metadata.get("container_name") or ""),
            exit_code=command_result.exit_code,
            stdout_contains=config.expected_output,
            downloaded_content=downloaded.content or "",
            file_paths=file_paths,
            snapshot_uri=snapshot.uri,
            destroyed=destroyed.status == SandboxSessionStatus.DESTROYED,
            memory_limit=config.memory_limit,
            cpus=config.cpus,
            pids_limit=config.pids_limit,
            container_user=config.container_user,
            read_only_rootfs=config.read_only_rootfs,
            drop_all_capabilities=config.drop_all_capabilities,
            security_opts=list(config.security_opts),
            tmpfs_mounts=list(config.tmpfs_mounts),
        )
        session = None
        return result
    finally:
        if session is not None:
            cleanup_session(docker_adapter, config.tenant_id, session)


def build_docker_sandbox_adapter(
    config: DockerSandboxVerificationConfig,
) -> DockerSandboxAdapter:
    return DockerSandboxAdapter(
        root_dir=config.root_dir,
        docker_binary=config.docker_binary,
        memory_limit=config.memory_limit,
        cpus=config.cpus,
        pids_limit=config.pids_limit,
        container_user=config.container_user,
        read_only_rootfs=config.read_only_rootfs,
        drop_all_capabilities=config.drop_all_capabilities,
        security_opts=config.security_opts,
        tmpfs_mounts=config.tmpfs_mounts,
    )


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
    result = verify_docker_sandbox(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

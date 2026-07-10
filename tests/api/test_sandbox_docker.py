import subprocess
import stat
from pathlib import Path

from taroai.config import Settings
from taroai.sandbox import (
    DockerSandboxAdapter,
    SandboxCommand,
    SandboxCreateRequest,
    SandboxExecutionError,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxProviderUnavailableError,
    SandboxSessionStatus,
    build_sandbox_adapter,
)


class RecordingDockerRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if command[1] == "run":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="container_123\n",
                stderr="",
            )
        if command[1] == "exec":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="hello\n",
                stderr="",
            )
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="unexpected docker command",
        )


def test_docker_sandbox_runs_container_with_disabled_network_and_workspace_mount(
    tmp_path: Path,
):
    runner = RecordingDockerRunner()
    adapter = DockerSandboxAdapter(
        root_dir=tmp_path,
        command_runner=runner,
        memory_limit="768m",
        cpus=0.5,
        pids_limit=128,
        container_user="10001:10001",
        read_only_rootfs=True,
        drop_all_capabilities=True,
        security_opts=["no-new-privileges:true"],
        tmpfs_mounts=["/tmp:rw,noexec,nosuid,size=128m"],
    )

    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12-slim",
            network_mode=SandboxNetworkMode.DISABLED,
            timeout_seconds=5,
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
            timeout_seconds=5,
        )
    )
    files = adapter.list_files("tenant_acme", session.id)
    downloaded = adapter.download_file(
        "tenant_acme",
        session.id,
        "/workspace/input.txt",
    )
    run_call = runner.calls[0]
    workspace_host_path = next(
        Path(item.split(":", 1)[0])
        for item in run_call
        if item.endswith(":/workspace")
    )
    workspace_mode = stat.S_IMODE(workspace_host_path.stat().st_mode)
    destroyed = adapter.destroy("tenant_acme", session.id)

    exec_call = runner.calls[1]
    rm_call = runner.calls[2]

    assert session.provider == "docker"
    assert "--network" in run_call
    assert "none" in run_call
    assert "--memory" in run_call
    assert "768m" in run_call
    assert "--cpus" in run_call
    assert "0.5" in run_call
    assert "--pids-limit" in run_call
    assert "128" in run_call
    assert "--user" in run_call
    assert "10001:10001" in run_call
    assert "--read-only" in run_call
    assert run_call.count("--cap-drop") == 1
    assert "ALL" in run_call
    assert "--security-opt" in run_call
    assert "no-new-privileges:true" in run_call
    assert "--tmpfs" in run_call
    assert "/tmp:rw,noexec,nosuid,size=128m" in run_call
    assert any(item.endswith(":/workspace") for item in run_call)
    assert workspace_mode == 0o777
    assert uploaded.path == "/workspace/input.txt"
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert [file.path for file in files] == ["/workspace/input.txt"]
    assert downloaded.content == "hello"
    assert "--workdir" in exec_call
    assert "/workspace" in exec_call
    assert "TAROAI_SANDBOX_WORKSPACE=/workspace" in exec_call
    assert rm_call[1:3] == ["rm", "-f"]
    assert destroyed.status == SandboxSessionStatus.DESTROYED


def test_docker_sandbox_declares_hardening_capabilities(tmp_path: Path):
    adapter = DockerSandboxAdapter(
        root_dir=tmp_path,
        command_runner=RecordingDockerRunner(),
        max_sessions=7,
        max_sessions_per_tenant=3,
        max_sessions_per_run=2,
    )

    capabilities = adapter.get_capabilities()

    assert capabilities.provider == "docker"
    assert capabilities.network_isolation is True
    assert capabilities.filesystem_isolation is True
    assert capabilities.resource_limits is True
    assert capabilities.destroy_supported is True
    assert capabilities.session_ttl_enforced is False
    assert capabilities.max_session_ttl_seconds is None
    assert capabilities.max_sessions == 7
    assert capabilities.max_sessions_per_tenant == 3
    assert capabilities.max_sessions_per_run == 2


def test_docker_sandbox_rejects_session_capacity_before_creating_container(
    tmp_path: Path,
):
    runner = RecordingDockerRunner()
    adapter = DockerSandboxAdapter(
        root_dir=tmp_path,
        command_runner=runner,
        max_sessions_per_run=1,
    )

    adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12-slim",
            network_mode=SandboxNetworkMode.DISABLED,
        )
    )

    try:
        adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.DISABLED,
            )
        )
    except SandboxProviderUnavailableError as error:
        assert "sandbox session limit reached" in str(error)
    else:
        raise AssertionError("docker sandbox should enforce per-run capacity")

    assert [call[1] for call in runner.calls].count("run") == 1


def test_docker_sandbox_rejects_non_disabled_network_mode(tmp_path: Path):
    adapter = DockerSandboxAdapter(
        root_dir=tmp_path,
        command_runner=RecordingDockerRunner(),
    )

    try:
        adapter.create(
            SandboxCreateRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                image="python:3.12-slim",
                network_mode=SandboxNetworkMode.OPEN,
            )
        )
    except SandboxProviderUnavailableError as error:
        assert "only supports disabled network mode" in str(error)
    else:
        raise AssertionError("docker sandbox must not claim unmanaged networking support")


def test_docker_sandbox_rejects_invalid_env_name_before_exec(tmp_path: Path):
    runner = RecordingDockerRunner()
    adapter = DockerSandboxAdapter(
        root_dir=tmp_path,
        command_runner=runner,
    )
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12-slim",
            network_mode=SandboxNetworkMode.DISABLED,
        )
    )

    try:
        adapter.execute(
            SandboxCommand(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=session.id,
                command="printf ok",
                cwd="/workspace",
                timeout_seconds=5,
                env={"BAD-NAME": "1"},
            )
        )
    except SandboxExecutionError as error:
        assert "invalid sandbox environment variable name: BAD-NAME" in str(error)
    else:
        raise AssertionError("invalid sandbox env name should be rejected")

    assert all(call[1] != "exec" for call in runner.calls)


def test_build_sandbox_adapter_uses_docker_provider(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        sandbox_provider="docker",
        sandbox_root_dir=str(tmp_path),
        sandbox_docker_memory_limit="512m",
        sandbox_docker_cpus=0.75,
        sandbox_docker_pids_limit=96,
        sandbox_max_sessions=17,
        sandbox_max_sessions_per_tenant=9,
        sandbox_max_sessions_per_run=4,
        sandbox_docker_user="10002:10002",
        sandbox_docker_read_only_rootfs=False,
        sandbox_docker_drop_all_capabilities=False,
        sandbox_docker_security_opts=["no-new-privileges:true"],
        sandbox_docker_tmpfs_mounts=["/tmp:rw,size=64m"],
    )

    adapter = build_sandbox_adapter(settings)

    assert isinstance(adapter, DockerSandboxAdapter)
    assert adapter.root_dir == tmp_path
    assert adapter.memory_limit == "512m"
    assert adapter.cpus == 0.75
    assert adapter.pids_limit == 96
    assert adapter.max_sessions == 17
    assert adapter.max_sessions_per_tenant == 9
    assert adapter.max_sessions_per_run == 4
    assert adapter.container_user == "10002:10002"
    assert adapter.read_only_rootfs is False
    assert adapter.drop_all_capabilities is False
    assert adapter.security_opts == ["no-new-privileges:true"]
    assert adapter.tmpfs_mounts == ["/tmp:rw,size=64m"]

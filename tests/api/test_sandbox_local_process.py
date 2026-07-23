import base64
import shlex
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.identity import InMemoryIdentityService, PasswordHasher, Permission, Role, UserAccountCreate
from taroai.sandbox import (
    LocalProcessSandboxAdapter,
    SandboxCommand,
    SandboxCreateRequest,
    SandboxExecutionError,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxProviderUnavailableError,
    SandboxSessionStatus,
    build_sandbox_adapter,
)
from taroai.storage import InMemoryStorageCatalog
from taroai.store import InMemoryControlPlaneStore


def test_local_process_sandbox_executes_commands_in_session_workspace(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
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
            command="python -c \"from pathlib import Path; Path('output.txt').write_text(Path('input.txt').read_text().upper()); print(Path('output.txt').read_text())\"",
            cwd="/workspace",
            timeout_seconds=5,
        )
    )
    downloaded = adapter.download_file("tenant_acme", session.id, "/workspace/output.txt")
    binary_content = b"\x89PNG\r\n\x1a\n\x00\xff"
    binary_upload = adapter.upload_file(
        SandboxFileWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.id,
            path="/workspace/image.png",
            content_base64=base64.b64encode(binary_content).decode("ascii"),
            content_type="image/png",
        )
    )
    binary_download = adapter.download_file(
        "tenant_acme", session.id, "/workspace/image.png"
    )
    snapshot = adapter.snapshot("tenant_acme", session.id)
    destroyed = adapter.destroy("tenant_acme", session.id)

    assert session.provider == "local_process"
    assert uploaded.size_bytes == 5
    assert result.exit_code == 0
    assert result.stdout.strip() == "HELLO"
    assert downloaded.content == "HELLO"
    assert binary_upload.content_bytes() == binary_content
    assert binary_download.content is None
    assert binary_download.content_bytes() == binary_content
    assert snapshot.uri.startswith("file://")
    assert destroyed.status == SandboxSessionStatus.DESTROYED


def test_local_process_destroy_terminates_running_process_group(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            timeout_seconds=30,
        )
    )
    started = tmp_path / "started"
    leaked = tmp_path / "leaked"
    script = (
        f"from pathlib import Path; import time; "
        f"Path({str(started)!r}).write_text('1'); time.sleep(30); "
        f"Path({str(leaked)!r}).write_text('1')"
    )
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            adapter.execute(
                SandboxCommand(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    run_id="run_1",
                    session_id=session.id,
                    command=f"python -c {shlex.quote(script)}",
                    timeout_seconds=30,
                )
            )
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 3
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    destroyed = adapter.destroy("tenant_acme", session.id)
    worker.join(timeout=3)

    assert started.exists()
    assert not worker.is_alive()
    assert destroyed.status == SandboxSessionStatus.DESTROYED
    assert results[0].exit_code != 0
    assert not leaked.exists()


def test_local_process_sandbox_supports_workspace_absolute_paths_in_commands(
    tmp_path: Path,
):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
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

    result = adapter.execute(
        SandboxCommand(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.id,
            command=(
                "mkdir -p /workspace/artifacts && "
                "printf 'hello from absolute workspace' > /workspace/artifacts/report.txt"
            ),
            cwd="/workspace",
            timeout_seconds=5,
        )
    )
    downloaded = adapter.download_file(
        "tenant_acme",
        session.id,
        "/workspace/artifacts/report.txt",
    )

    assert result.exit_code == 0
    assert downloaded.content == "hello from absolute workspace"


def test_local_process_sandbox_keeps_workspace_env_authoritative(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
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

    result = adapter.execute(
        SandboxCommand(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id=session.id,
            command='test "$TAROAI_SANDBOX_WORKSPACE" = "$PWD"',
            cwd="/workspace",
            timeout_seconds=5,
            env={"TAROAI_SANDBOX_WORKSPACE": "/tmp/outside"},
        )
    )

    assert result.exit_code == 0


def test_local_process_sandbox_rejects_invalid_env_name(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
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


def test_local_process_sandbox_blocks_paths_outside_workspace(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)
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
        adapter.upload_file(
            SandboxFileWrite(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                run_id="run_1",
                session_id=session.id,
                path="/etc/passwd",
                content="blocked",
            )
        )
    except SandboxExecutionError as error:
        assert "outside sandbox workspace" in str(error)
    else:
        raise AssertionError("path outside workspace should be rejected")


def test_local_process_sandbox_rejects_non_disabled_network_mode(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(root_dir=tmp_path)

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
        raise AssertionError("local process sandbox must not claim open networking support")


def test_local_process_sandbox_enforces_active_session_capacity(tmp_path: Path):
    adapter = LocalProcessSandboxAdapter(
        root_dir=tmp_path,
        max_sessions_per_run=1,
    )
    first = adapter.create(
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
        raise AssertionError("local process sandbox should enforce per-run capacity")

    adapter.destroy("tenant_acme", first.id)
    replacement = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            image="python:3.12-slim",
            network_mode=SandboxNetworkMode.DISABLED,
        )
    )

    assert replacement.status == SandboxSessionStatus.ACTIVE


def test_local_process_sandbox_declares_poc_capabilities_without_security_claims(
    tmp_path: Path,
):
    adapter = LocalProcessSandboxAdapter(
        root_dir=tmp_path,
        max_sessions=7,
        max_sessions_per_tenant=3,
        max_sessions_per_run=2,
    )

    capabilities = adapter.get_capabilities()

    assert capabilities.provider == "local_process"
    assert capabilities.network_isolation is False
    assert capabilities.filesystem_isolation is False
    assert capabilities.resource_limits is False
    assert capabilities.destroy_supported is True
    assert capabilities.session_ttl_enforced is False
    assert capabilities.max_session_ttl_seconds is None
    assert capabilities.max_sessions == 7
    assert capabilities.max_sessions_per_tenant == 3
    assert capabilities.max_sessions_per_run == 2


def test_build_sandbox_adapter_uses_local_process_capacity_settings(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        sandbox_provider="local_process",
        sandbox_root_dir=str(tmp_path),
        sandbox_max_sessions=17,
        sandbox_max_sessions_per_tenant=9,
        sandbox_max_sessions_per_run=4,
    )

    adapter = build_sandbox_adapter(settings)

    assert isinstance(adapter, LocalProcessSandboxAdapter)
    assert adapter.root_dir == tmp_path
    assert adapter.max_sessions == 17
    assert adapter.max_sessions_per_tenant == 9
    assert adapter.max_sessions_per_run == 4


def test_create_app_uses_local_process_sandbox_provider(tmp_path: Path):
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    user = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="operator@example.com",
            display_name="Operator",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sandbox_operator",
            name="Sandbox Operator",
            permissions=[
                Permission(action="sandbox.create", resource="tenant:tenant_acme"),
                Permission(action="sandbox.execute", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", user.id, "role_sandbox_operator")
    settings = Settings(
        _env_file=None,
        sandbox_provider="local_process",
        sandbox_root_dir=str(tmp_path),
        object_storage_access_key_id="local_access",
        object_storage_secret_access_key="local_secret",
    )
    app = create_app(
        settings=settings,
        store=InMemoryControlPlaneStore(),
        identity_service=identity,
        storage_catalog=InMemoryStorageCatalog(bucket="taroai-artifacts"),
    )
    client = TestClient(app)
    token = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "operator@example.com",
            "password": "correct horse battery staple",
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    session_response = client.post(
        "/api/sandbox/sessions",
        json={"workspace_id": "workspace_sales", "run_id": "run_1"},
        headers=headers,
    )
    command_response = client.post(
        f"/api/sandbox/sessions/{session_response.json()['id']}/commands",
        json={"command": "python -c \"print('local-provider-ready')\""},
        headers=headers,
    )

    assert session_response.status_code == 201
    assert session_response.json()["provider"] == "local_process"
    assert command_response.status_code == 200
    assert command_response.json()["stdout"].strip() == "local-provider-ready"

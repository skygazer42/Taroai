import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from pydantic import ConfigDict, Field

from taroai.domain import utc_now
from taroai.sandbox.adapter import (
    SandboxAdapter,
    SandboxExecutionError,
    SandboxProviderUnavailableError,
)
from taroai.errors import NotFoundError
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


class LocalProcessSandboxAdapter(SandboxAdapter):
    provider: str = "local_process"
    root_dir: Path = Field(default=Path("/tmp/taroai/sandboxes"))
    max_output_chars: int = Field(default=65536, ge=1024)
    max_sessions: int = Field(default=50, ge=1)
    max_sessions_per_tenant: int = Field(default=20, ge=1)
    max_sessions_per_run: int = Field(default=3, ge=1)
    sessions: dict[str, SandboxSession] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_capabilities(self) -> SandboxControllerCapabilities:
        return SandboxControllerCapabilities(
            provider=self.provider,
            network_isolation=False,
            filesystem_isolation=False,
            resource_limits=False,
            destroy_supported=True,
            session_ttl_enforced=False,
            max_session_ttl_seconds=None,
            max_sessions=self.max_sessions,
            max_sessions_per_tenant=self.max_sessions_per_tenant,
            max_sessions_per_run=self.max_sessions_per_run,
        )

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        if request.network_mode != SandboxNetworkMode.DISABLED:
            raise SandboxProviderUnavailableError(
                "local process sandbox only supports disabled network mode"
            )
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
        self._workspace_path(session).mkdir(parents=True, exist_ok=True)
        self.sessions[session.id] = session
        return session

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        session = self._get_active_session(command.tenant_id, command.session_id)
        self._assert_scope(session, command.workspace_id, command.run_id)
        cwd = self._resolve_workspace_path(session, command.cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        local_command = self._local_workspace_command(command.command, session)
        try:
            completed = subprocess.run(
                ["/bin/sh", "-lc", local_command],
                cwd=cwd,
                env=self._command_env(command.env, session),
                capture_output=True,
                text=True,
                timeout=command.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            stdout = self._coerce_process_output(error.stdout)
            stderr = self._coerce_process_output(error.stderr)
            if stderr:
                stderr = f"{stderr}\n"
            stderr = f"{stderr}command timed out after {command.timeout_seconds} seconds"
            return self._command_result(command, 124, stdout, stderr)
        except OSError as error:
            raise SandboxExecutionError(f"local process sandbox command failed: {error}") from error
        return self._command_result(
            command,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        session = self._get_active_session(file_write.tenant_id, file_write.session_id)
        self._assert_scope(session, file_write.workspace_id, file_write.run_id)
        path = self._resolve_workspace_path(session, file_write.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        content_bytes = file_write.content_bytes()
        path.write_bytes(content_bytes)
        return SandboxFileRef(
            tenant_id=file_write.tenant_id,
            workspace_id=file_write.workspace_id,
            run_id=file_write.run_id,
            session_id=file_write.session_id,
            path=self._workspace_display_path(path, session),
            content_type=file_write.content_type,
            size_bytes=len(content_bytes),
            content=file_write.content if file_write.content_base64 is None else None,
        )

    def download_file(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
    ) -> SandboxFileRef:
        session = self._get_active_session(tenant_id, session_id)
        resolved_path = self._resolve_workspace_path(session, path)
        if not resolved_path.exists() or not resolved_path.is_file():
            raise NotFoundError(f"Sandbox file not found: {path}")
        content = resolved_path.read_text(encoding="utf-8")
        return SandboxFileRef(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            path=self._workspace_display_path(resolved_path, session),
            content_type=self._content_type(resolved_path),
            size_bytes=len(content.encode("utf-8")),
            content=content,
        )

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        session = self._get_active_session(tenant_id, session_id)
        workspace_path = self._workspace_path(session)
        files: list[SandboxFileRef] = []
        for path in sorted(workspace_path.rglob("*")):
            if not path.is_file():
                continue
            files.append(
                SandboxFileRef(
                    tenant_id=session.tenant_id,
                    workspace_id=session.workspace_id,
                    run_id=session.run_id,
                    session_id=session.id,
                    path=self._workspace_display_path(path, session),
                    content_type=self._content_type(path),
                    size_bytes=path.stat().st_size,
                )
            )
        return files

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        session = self._get_active_session(tenant_id, session_id)
        session_path = self._session_path(session)
        workspace_path = self._workspace_path(session)
        snapshot_path = session_path / "snapshots" / "snapshot.json"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        files = [
            {
                "path": self._workspace_display_path(path, session),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(workspace_path.rglob("*"))
            if path.is_file()
        ]
        snapshot = SandboxSnapshot(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            uri=f"file://{snapshot_path}",
        )
        snapshot_path.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot.id,
                    "tenant_id": session.tenant_id,
                    "workspace_id": session.workspace_id,
                    "run_id": session.run_id,
                    "session_id": session.id,
                    "files": files,
                    "created_at": snapshot.created_at.isoformat(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return snapshot

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self._get_session(tenant_id, session_id)
        destroyed = session.model_copy(
            update={
                "status": SandboxSessionStatus.DESTROYED,
                "destroyed_at": utc_now(),
            }
        )
        self.sessions[session_id] = destroyed
        shutil.rmtree(self._workspace_path(session), ignore_errors=True)
        return destroyed

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        return self._get_session(tenant_id, session_id)

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        sessions = sorted(
            self.sessions.values(),
            key=lambda item: (item.created_at, item.id),
        )
        if tenant_id is None:
            return sessions
        return [session for session in sessions if session.tenant_id == tenant_id]

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
        return session

    def _get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self.sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise NotFoundError(f"Sandbox session not found: {session_id}")
        return session

    def _assert_scope(self, session: SandboxSession, workspace_id: str, run_id: str) -> None:
        if session.workspace_id != workspace_id or session.run_id != run_id:
            raise NotFoundError(f"Sandbox session not found: {session.id}")

    def _command_env(self, custom_env: dict[str, str], session: SandboxSession) -> dict[str, str]:
        workspace_path = self._workspace_path(session)
        invalid_names = invalid_sandbox_env_names(custom_env)
        if invalid_names:
            raise SandboxExecutionError(
                "invalid sandbox environment variable name: "
                + ", ".join(invalid_names)
            )
        env = dict(custom_env)
        env.update(
            {
                "HOME": str(workspace_path),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "PYTHONUNBUFFERED": "1",
                "TAROAI_SANDBOX_WORKSPACE": str(workspace_path),
            }
        )
        return env

    def _local_workspace_command(self, command: str, session: SandboxSession) -> str:
        workspace_path = shlex.quote(str(self._workspace_path(session).resolve()))
        return command.replace("/workspace", workspace_path)

    def _workspace_display_path(self, path: Path, session: SandboxSession) -> str:
        relative_path = path.resolve().relative_to(self._workspace_path(session).resolve())
        if str(relative_path) == ".":
            return "/workspace"
        return f"/workspace/{relative_path}"

    def _content_type(self, path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "text/plain"

    def _resolve_workspace_path(self, session: SandboxSession, requested_path: str) -> Path:
        workspace = self._workspace_path(session).resolve()
        if requested_path == "/workspace":
            target = workspace
        elif requested_path.startswith("/workspace/"):
            target = workspace / requested_path.removeprefix("/workspace/")
        elif Path(requested_path).is_absolute():
            raise SandboxExecutionError("sandbox path is outside sandbox workspace")
        else:
            target = workspace / requested_path
        resolved = target.resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as error:
            raise SandboxExecutionError("sandbox path is outside sandbox workspace") from error
        return resolved

    def _workspace_path(self, session: SandboxSession) -> Path:
        return self._session_path(session) / "workspace"

    def _session_path(self, session: SandboxSession) -> Path:
        return self.root_dir / self._safe_path_part(session.tenant_id) / self._safe_path_part(session.id)

    def _safe_path_part(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "value"

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

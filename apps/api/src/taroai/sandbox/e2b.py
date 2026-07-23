import base64
import hashlib
import mimetypes
import shlex
from datetime import timedelta
from pathlib import PurePosixPath

from e2b import (
    CommandExitException,
    NotFoundException,
    Sandbox,
    SandboxException,
    TimeoutException,
)
from e2b.sandbox.sandbox_api import SandboxInfo, SandboxQuery, SandboxState
from pydantic import ConfigDict, Field

from taroai.domain import utc_now
from taroai.errors import NotFoundError
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


class E2BSandboxAdapter(SandboxAdapter):
    provider: str = "e2b"
    api_key: str = Field(min_length=1, repr=False)
    template: str = ""
    default_runtime_image: str = "python:3.12-slim"
    request_timeout_seconds: int = Field(default=30, ge=1)
    max_output_chars: int = Field(default=65536, ge=1024)
    max_session_ttl_seconds: int = Field(default=3600, ge=1)
    max_sessions: int = Field(default=50, ge=1)
    max_sessions_per_tenant: int = Field(default=20, ge=1)
    max_sessions_per_run: int = Field(default=3, ge=1)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_capabilities(self) -> SandboxControllerCapabilities:
        return SandboxControllerCapabilities(
            provider=self.provider,
            network_isolation=True,
            filesystem_isolation=True,
            resource_limits=True,
            destroy_supported=True,
            command_cancellation_supported=True,
            session_ttl_enforced=True,
            runtime_isolation=True,
            image_policy_enforced=True,
            allowed_image_count=1,
            max_session_ttl_seconds=self.max_session_ttl_seconds,
            max_sessions=self.max_sessions,
            max_sessions_per_tenant=self.max_sessions_per_tenant,
            max_sessions_per_run=self.max_sessions_per_run,
        )

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        if request.network_mode == SandboxNetworkMode.ALLOWLIST:
            raise SandboxProviderUnavailableError(
                "e2b sandbox allowlist network mode is not configured"
            )
        if request.timeout_seconds > self.max_session_ttl_seconds:
            raise SandboxProviderUnavailableError(
                "sandbox session timeout exceeds e2b limit"
            )
        self._enforce_session_limits(request)
        metadata = {
            **{str(key): str(value) for key, value in request.metadata.items()},
            "taroai": "1",
            "taroai_tenant_id": request.tenant_id,
            "taroai_workspace_id": request.workspace_id,
            "taroai_run_id": request.run_id,
            **(
                {"taroai_thread_id": request.thread_id}
                if request.thread_id is not None
                else {}
            ),
            "taroai_network_mode": request.network_mode.value,
            "taroai_timeout_seconds": str(request.timeout_seconds),
        }
        template = (
            request.image
            if "image" in request.model_fields_set
            and request.image != self.default_runtime_image
            else self.template or None
        )
        try:
            sandbox = Sandbox.create(
                template=template,
                timeout=request.timeout_seconds,
                metadata=metadata,
                allow_internet_access=request.network_mode == SandboxNetworkMode.OPEN,
                api_key=self.api_key,
                request_timeout=self.request_timeout_seconds,
            )
            sandbox.commands.run(
                "sudo mkdir -p /workspace/inputs /workspace/artifacts && "
                "sudo chown -R \"$(id -u):$(id -g)\" /workspace",
                timeout=min(30, request.timeout_seconds),
                request_timeout=self.request_timeout_seconds,
            )
            return self._session_from_info(sandbox.get_info(), request.tenant_id)
        except Exception as error:
            if "sandbox" in locals():
                try:
                    sandbox.kill()
                except Exception:
                    pass
            raise SandboxProviderUnavailableError(
                f"e2b sandbox creation failed: {error}"
            ) from error

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        session = self.get_session(command.tenant_id, command.session_id)
        self._assert_scope(
            session,
            command.workspace_id,
            command.run_id,
            command.thread_id,
        )
        cwd = self._workspace_path(command.cwd)
        env = self._command_env(command.env)
        timeout_seconds = min(command.timeout_seconds, session.timeout_seconds)
        sandbox = self._connect(session)
        pid_path = self._command_pid_path(command.id)
        try:
            handle = sandbox.commands.run(
                command.command,
                background=True,
                envs=env,
                cwd=cwd,
                timeout=timeout_seconds,
                request_timeout=max(
                    self.request_timeout_seconds,
                    timeout_seconds + 5,
                ),
            )
            try:
                sandbox.files.write(
                    pid_path,
                    str(handle.pid).encode("ascii"),
                    request_timeout=self.request_timeout_seconds,
                )
            except Exception:
                try:
                    handle.kill()
                except Exception:
                    pass
                raise
            result = handle.wait()
            return self._command_result(
                command, result.exit_code, result.stdout, result.stderr
            )
        except CommandExitException as error:
            return self._command_result(
                command, error.exit_code, error.stdout, error.stderr
            )
        except TimeoutException:
            return self._command_result(
                command,
                124,
                "",
                f"command timed out after {timeout_seconds} seconds",
            )
        except SandboxException as error:
            raise SandboxExecutionError(f"e2b sandbox command failed: {error}") from error
        finally:
            try:
                sandbox.files.remove(
                    pid_path,
                    request_timeout=self.request_timeout_seconds,
                )
            except Exception:
                pass

    def cancel_command(
        self,
        tenant_id: str,
        session_id: str,
        command_id: str,
    ) -> bool:
        session = self.get_session(tenant_id, session_id)
        sandbox = self._connect(session)
        pid_path = self._command_pid_path(command_id)
        try:
            pid = int(
                sandbox.files.read(
                    pid_path,
                    format="text",
                    request_timeout=self.request_timeout_seconds,
                ).strip()
            )
            return bool(
                sandbox.commands.kill(
                    pid,
                    request_timeout=self.request_timeout_seconds,
                )
            )
        except (SandboxException, TypeError, ValueError):
            return False
        finally:
            try:
                sandbox.files.remove(
                    pid_path,
                    request_timeout=self.request_timeout_seconds,
                )
            except Exception:
                pass

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        session = self.get_session(file_write.tenant_id, file_write.session_id)
        self._assert_scope(
            session,
            file_write.workspace_id,
            file_write.run_id,
            file_write.thread_id,
        )
        path = self._workspace_path(file_write.path)
        content = file_write.content_bytes()
        try:
            sandbox = self._connect(session)
            sandbox.files.write(
                path,
                content,
                request_timeout=self.request_timeout_seconds,
            )
            if file_write.mode is not None:
                chmod = sandbox.commands.run(
                    f"chmod {file_write.mode:o} {shlex.quote(path)}",
                    timeout=min(30, session.timeout_seconds),
                    request_timeout=self.request_timeout_seconds,
                )
                if chmod.exit_code != 0:
                    raise SandboxExecutionError(
                        "e2b sandbox failed to set uploaded file mode"
                    )
        except SandboxException as error:
            raise SandboxExecutionError(f"e2b sandbox file upload failed: {error}") from error
        return SandboxFileRef(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=file_write.run_id,
            session_id=session.id,
            path=path,
            content_type=file_write.content_type,
            size_bytes=len(content),
            content=file_write.content if file_write.content_base64 is None else None,
            content_base64=file_write.content_base64,
        )

    def download_file(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
    ) -> SandboxFileRef:
        session = self.get_session(tenant_id, session_id)
        resolved_path = self._workspace_path(path)
        try:
            content = self._connect(session).files.read(
                resolved_path,
                format="bytes",
                request_timeout=self.request_timeout_seconds,
            )
        except SandboxException as error:
            raise NotFoundError(f"Sandbox file not found: {path}") from error
        content_bytes = bytes(content)
        try:
            text_content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_content = None
        return SandboxFileRef(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            path=resolved_path,
            content_type=mimetypes.guess_type(resolved_path)[0] or "text/plain",
            size_bytes=len(content_bytes),
            content=text_content,
            content_base64=(
                base64.b64encode(content_bytes).decode("ascii")
                if text_content is None
                else None
            ),
        )

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        session = self.get_session(tenant_id, session_id)
        try:
            entries = self._connect(session).files.list(
                "/workspace",
                depth=20,
                request_timeout=self.request_timeout_seconds,
            )
        except SandboxException as error:
            raise SandboxExecutionError(f"e2b sandbox file listing failed: {error}") from error
        return [
            SandboxFileRef(
                tenant_id=session.tenant_id,
                workspace_id=session.workspace_id,
                run_id=session.run_id,
                session_id=session.id,
                path=entry.path,
                content_type=mimetypes.guess_type(entry.path)[0] or "text/plain",
                size_bytes=entry.size,
            )
            for entry in entries
            if getattr(entry.type, "value", None) == "file"
        ]

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        session = self.get_session(tenant_id, session_id)
        # ponytail: 运行中沙盒引用足够支撑当前检查点；需要跨 Run 恢复时再创建持久 E2B snapshot。
        return SandboxSnapshot(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            uri=f"e2b://sandboxes/{session.id}",
        )

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self.get_session(tenant_id, session_id)
        try:
            Sandbox.kill(
                session_id,
                api_key=self.api_key,
                request_timeout=self.request_timeout_seconds,
            )
        except SandboxException as error:
            raise SandboxExecutionError(f"e2b sandbox destroy failed: {error}") from error
        return session.model_copy(
            update={
                "status": SandboxSessionStatus.DESTROYED,
                "destroyed_at": utc_now(),
            }
        )

    def pause(self, tenant_id: str, session_id: str) -> SandboxSession:
        cleanup_timeout = min(5, self.request_timeout_seconds)
        try:
            info = Sandbox.get_info(
                session_id,
                api_key=self.api_key,
                request_timeout=cleanup_timeout,
            )
        except Exception as error:
            raise SandboxExecutionError(f"e2b sandbox pause failed: {error}") from error
        session = self._session_from_info(info, tenant_id)
        try:
            Sandbox.beta_pause(
                session.id,
                api_key=self.api_key,
                request_timeout=cleanup_timeout,
            )
        except Exception as error:
            raise SandboxExecutionError(f"e2b sandbox pause failed: {error}") from error
        return session

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        try:
            info = Sandbox.get_info(
                session_id,
                api_key=self.api_key,
                request_timeout=self.request_timeout_seconds,
            )
        except NotFoundException as error:
            raise NotFoundError(f"Sandbox session not found: {session_id}") from error
        except SandboxException as error:
            raise SandboxProviderUnavailableError(
                f"e2b sandbox lookup failed: {error}"
            ) from error
        return self._session_from_info(info, tenant_id)

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        metadata = {"taroai": "1"}
        if tenant_id is not None:
            metadata["taroai_tenant_id"] = tenant_id
        try:
            paginator = Sandbox.list(
                query=SandboxQuery(
                    metadata=metadata,
                    state=[SandboxState.RUNNING, SandboxState.PAUSED],
                ),
                limit=self.max_sessions,
                api_key=self.api_key,
                request_timeout=self.request_timeout_seconds,
            )
            infos: list[SandboxInfo] = []
            while paginator.has_next:
                infos.extend(paginator.next_items())
        except SandboxException as error:
            raise SandboxProviderUnavailableError(
                f"e2b sandbox listing failed: {error}"
            ) from error
        sessions = [
            self._session_from_info(info, tenant_id)
            for info in infos
            if tenant_id is None or info.metadata.get("taroai_tenant_id") == tenant_id
        ]
        return sorted(sessions, key=lambda item: (item.created_at, item.id))

    def _connect(self, session: SandboxSession) -> Sandbox:
        try:
            return Sandbox.connect(
                session.id,
                timeout=session.timeout_seconds,
                api_key=self.api_key,
                request_timeout=self.request_timeout_seconds,
            )
        except SandboxException as error:
            raise SandboxProviderUnavailableError(
                f"e2b sandbox connection failed: {error}"
            ) from error

    def _command_pid_path(self, command_id: str) -> str:
        digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()
        return f"/tmp/taroai-command-{digest}.pid"

    def _session_from_info(
        self,
        info: SandboxInfo,
        tenant_id: str | None,
    ) -> SandboxSession:
        metadata = dict(info.metadata)
        actual_tenant_id = metadata.get("taroai_tenant_id")
        if (
            metadata.get("taroai") != "1"
            or not actual_tenant_id
            or (tenant_id is not None and actual_tenant_id != tenant_id)
        ):
            raise NotFoundError(f"Sandbox session not found: {info.sandbox_id}")
        return SandboxSession(
            id=info.sandbox_id,
            tenant_id=actual_tenant_id,
            workspace_id=metadata["taroai_workspace_id"],
            run_id=metadata["taroai_run_id"],
            provider=self.provider,
            image=info.template_id or self.template or "base",
            network_mode=SandboxNetworkMode(
                metadata.get("taroai_network_mode", SandboxNetworkMode.DISABLED.value)
            ),
            timeout_seconds=int(
                metadata.get("taroai_timeout_seconds", self.max_session_ttl_seconds)
            ),
            metadata=metadata,
            created_at=info.started_at,
        )

    def _enforce_session_limits(self, request: SandboxCreateRequest) -> None:
        cutoff = utc_now() - timedelta(seconds=self.max_session_ttl_seconds)
        sessions = []
        for session in self.list_sessions():
            if session.created_at > cutoff:
                sessions.append(session)
                continue
            try:
                Sandbox.kill(
                    session.id,
                    api_key=self.api_key,
                    request_timeout=self.request_timeout_seconds,
                )
            except NotFoundException:
                pass
            except SandboxException:
                sessions.append(session)
        tenant_sessions = [
            session for session in sessions if session.tenant_id == request.tenant_id
        ]
        run_sessions = [
            session for session in tenant_sessions if session.run_id == request.run_id
        ]
        if (
            len(sessions) >= self.max_sessions
            or len(tenant_sessions) >= self.max_sessions_per_tenant
            or len(run_sessions) >= self.max_sessions_per_run
        ):
            raise SandboxProviderUnavailableError("sandbox session limit reached")

    def _assert_scope(
        self,
        session: SandboxSession,
        workspace_id: str,
        run_id: str,
        thread_id: str | None,
    ) -> None:
        same_run = session.run_id == run_id
        same_thread = (
            thread_id is not None
            and session.metadata.get("taroai_thread_id") == thread_id
        )
        if session.workspace_id != workspace_id or not (same_run or same_thread):
            raise NotFoundError(f"Sandbox session not found: {session.id}")

    def _workspace_path(self, requested_path: str) -> str:
        path = PurePosixPath(requested_path)
        if not path.is_absolute():
            path = PurePosixPath("/workspace") / path
        if ".." in path.parts or (path != PurePosixPath("/workspace") and path.parts[:2] != ("/", "workspace")):
            raise SandboxExecutionError("sandbox path is outside sandbox workspace")
        return str(path)

    def _command_env(self, custom_env: dict[str, str]) -> dict[str, str]:
        invalid_names = invalid_sandbox_env_names(custom_env)
        if invalid_names:
            raise SandboxExecutionError(
                "invalid sandbox environment variable name: "
                + ", ".join(invalid_names)
            )
        return {
            **custom_env,
            "PYTHONUNBUFFERED": "1",
            "TAROAI_SANDBOX_WORKSPACE": "/workspace",
        }

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

    def _limit_output(self, value: str) -> str:
        if len(value) <= self.max_output_chars:
            return value
        return f"{value[:self.max_output_chars]}\n[output truncated]"

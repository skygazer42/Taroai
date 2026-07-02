from pydantic import Field

from taroai.domain import utc_now
from taroai.sandbox import (
    BrowserAction,
    BrowserActionType,
    BrowserController,
    BrowserObservation,
    BrowserSession,
    SandboxAdapter,
    SandboxCommand,
    SandboxCommandResult,
    SandboxCreateRequest,
    SandboxExecutionError,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxSession,
    SandboxSessionStatus,
    SandboxSnapshot,
)
from taroai.store import NotFoundError


def empty_png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a"
        "0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db4"
        "0000000049454e44ae426082"
    )


class InMemorySandboxAdapter(SandboxAdapter):
    provider: str = "in_memory"
    sessions: dict[str, SandboxSession] = Field(default_factory=dict)
    files: dict[str, SandboxFileRef] = Field(default_factory=dict)

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
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
        self.sessions[session.id] = session
        return session

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        session = self._get_active_session(command.tenant_id, command.session_id)
        if session.workspace_id != command.workspace_id or session.run_id != command.run_id:
            raise NotFoundError(f"Sandbox session not found: {command.session_id}")
        return SandboxCommandResult(
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
            session_id=command.session_id,
            command=command.command,
            exit_code=0,
            stdout=f"accepted: {command.command}",
        )

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        session = self._get_active_session(file_write.tenant_id, file_write.session_id)
        if session.workspace_id != file_write.workspace_id or session.run_id != file_write.run_id:
            raise NotFoundError(f"Sandbox session not found: {file_write.session_id}")
        file_ref = SandboxFileRef(
            tenant_id=file_write.tenant_id,
            workspace_id=file_write.workspace_id,
            run_id=file_write.run_id,
            session_id=file_write.session_id,
            path=file_write.path,
            content_type=file_write.content_type,
            size_bytes=len(file_write.content.encode("utf-8")),
            content=file_write.content,
        )
        self.files[self._file_key(file_write.tenant_id, file_write.session_id, file_write.path)] = file_ref
        return file_ref

    def download_file(self, tenant_id: str, session_id: str, path: str) -> SandboxFileRef:
        self._get_active_session(tenant_id, session_id)
        file_ref = self.files.get(self._file_key(tenant_id, session_id, path))
        if file_ref is None:
            raise NotFoundError(f"Sandbox file not found: {path}")
        return file_ref

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        session = self._get_active_session(tenant_id, session_id)
        return SandboxSnapshot(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session.id,
            uri=f"s3://{session.tenant_id}/runs/{session.run_id}/sandbox/{session.id}/snapshot.json",
        )

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        session = self._get_session(tenant_id, session_id)
        destroyed = session.model_copy(
            update={
                "status": SandboxSessionStatus.DESTROYED,
                "destroyed_at": utc_now(),
            }
        )
        self.sessions[session_id] = destroyed
        return destroyed

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        return self._get_session(tenant_id, session_id)

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

    def _file_key(self, tenant_id: str, session_id: str, path: str) -> str:
        return f"{tenant_id}:{session_id}:{path}"


class InMemoryBrowserController(BrowserController):
    provider: str = "in_memory"
    sessions: dict[str, BrowserSession] = Field(default_factory=dict)

    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
    ) -> BrowserSession:
        session = BrowserSession(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, tenant_id: str, session_id: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise NotFoundError(f"Browser session not found: {session_id}")
        return session

    def apply(self, action: BrowserAction) -> BrowserObservation:
        session = self.get_session(action.tenant_id, action.session_id)
        if session.workspace_id != action.workspace_id or session.run_id != action.run_id:
            raise NotFoundError(f"Browser session not found: {action.session_id}")

        current_url = action.url if action.action_type == BrowserActionType.NAVIGATE else session.current_url
        updated_session = session.model_copy(
            update={
                "current_url": current_url,
                "actions": [*session.actions, action],
            }
        )
        self.sessions[action.session_id] = updated_session
        screenshot_uri = None
        screenshot_content = None
        text = None
        if action.action_type == BrowserActionType.SCREENSHOT:
            screenshot_uri = f"s3://{action.tenant_id}/runs/{action.run_id}/browser/{action.session_id}.png"
            screenshot_content = empty_png()
        if action.action_type == BrowserActionType.EXTRACT:
            text = f"extracted from {current_url or 'about:blank'}"
        return BrowserObservation(
            tenant_id=action.tenant_id,
            workspace_id=action.workspace_id,
            run_id=action.run_id,
            session_id=action.session_id,
            action_type=action.action_type,
            current_url=current_url,
            text=text,
            screenshot_uri=screenshot_uri,
            screenshot_content=screenshot_content,
            metadata=action.metadata,
        )

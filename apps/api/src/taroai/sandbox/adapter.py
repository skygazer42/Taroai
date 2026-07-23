from pydantic import BaseModel

from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
    SandboxControllerCapabilities,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSnapshot,
)


class SandboxProviderUnavailableError(RuntimeError):
    pass


class SandboxExecutionError(RuntimeError):
    pass


class SandboxAdapter(BaseModel):
    provider: str = "disabled"

    def get_capabilities(self) -> SandboxControllerCapabilities:
        raise SandboxProviderUnavailableError("sandbox provider capabilities are unavailable")

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def cancel_command(
        self,
        tenant_id: str,
        session_id: str,
        command_id: str,
    ) -> bool:
        return False

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def download_file(self, tenant_id: str, session_id: str, path: str) -> SandboxFileRef:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        return []

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        return []


def sandbox_network_mode_from_string(value: str) -> SandboxNetworkMode:
    return SandboxNetworkMode(value)

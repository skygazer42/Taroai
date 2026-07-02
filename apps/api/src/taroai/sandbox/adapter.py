from pydantic import BaseModel

from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
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

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def download_file(self, tenant_id: str, session_id: str, path: str) -> SandboxFileRef:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        raise SandboxProviderUnavailableError("sandbox provider is disabled")


def sandbox_network_mode_from_string(value: str) -> SandboxNetworkMode:
    return SandboxNetworkMode(value)

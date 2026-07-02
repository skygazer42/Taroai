from pydantic import BaseModel

from taroai.sandbox.models import (
    BrowserAction,
    BrowserObservation,
    BrowserSession,
)


class BrowserProviderUnavailableError(RuntimeError):
    pass


class BrowserController(BaseModel):
    provider: str = "disabled"

    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
    ) -> BrowserSession:
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def get_session(self, tenant_id: str, session_id: str) -> BrowserSession:
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def apply(self, action: BrowserAction) -> BrowserObservation:
        raise BrowserProviderUnavailableError("browser provider is disabled")

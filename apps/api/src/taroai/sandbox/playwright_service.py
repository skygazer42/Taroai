import base64
from contextlib import asynccontextmanager
import json
from secrets import compare_digest
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taroai.domain import utc_now
from taroai.errors import NotFoundError
from taroai.sandbox.browser import (
    BrowserControllerCapabilities,
    BrowserProviderUnavailableError,
    PlaywrightBrowserController,
)
from taroai.sandbox.models import (
    BrowserAction,
    BrowserActionType,
    BrowserObservation,
    BrowserSession,
)


BROWSER_CONTROLLER_API_KEY_MIN_LENGTH = 32


class BrowserSessionOpenRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class BrowserControllerServiceSettings(BaseSettings):
    api_key: str = Field(default="", repr=False)
    session_ttl_seconds: int = Field(default=1800, ge=0)
    max_sessions: int = Field(default=50, ge=1)
    max_sessions_per_tenant: int = Field(default=20, ge=1)
    max_sessions_per_run: int = Field(default=3, ge=1)
    navigation_allowed_hosts: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_prefix="TAROAI_BROWSER_CONTROLLER_",
        extra="forbid",
    )

    @field_validator("navigation_allowed_hosts", mode="before")
    @classmethod
    def parse_navigation_allowed_hosts(cls, value):
        if isinstance(value, str):
            return parse_env_list(value)
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key_length(cls, value: str) -> str:
        if value.strip() and len(value.strip()) < BROWSER_CONTROLLER_API_KEY_MIN_LENGTH:
            raise ValueError(
                f"api_key must be at least {BROWSER_CONTROLLER_API_KEY_MIN_LENGTH} characters when configured"
            )
        return value

    @classmethod
    def from_env(cls) -> "BrowserControllerServiceSettings":
        return cls()


def create_playwright_browser_app(
    controller: PlaywrightBrowserController | None = None,
    settings: BrowserControllerServiceSettings | None = None,
) -> FastAPI:
    browser_controller = controller or PlaywrightBrowserController()
    service_settings = settings or BrowserControllerServiceSettings.from_env()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            browser_controller.close()

    browser_app = FastAPI(title="Taroai Browser Controller", lifespan=lifespan)
    browser_app.state.browser_controller = browser_controller
    browser_app.state.settings = service_settings

    def require_controller_auth(
        authorization: str | None = Header(default=None),
    ) -> None:
        expected_api_key = service_settings.api_key.strip()
        if not expected_api_key:
            return
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="browser controller authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided_api_key = authorization[len(prefix):]
        if not compare_digest(provided_api_key, expected_api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="browser controller authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def prepare_controller_request(
        _auth: None = Depends(require_controller_auth),
    ) -> None:
        cleanup_expired_browser_sessions(browser_controller, service_settings)

    def prepare_session_creation(
        _request: None = Depends(prepare_controller_request),
    ) -> None:
        return None

    @browser_app.exception_handler(NotFoundError)
    async def not_found_handler(_request, error: NotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))

    @browser_app.exception_handler(BrowserProviderUnavailableError)
    async def provider_error_handler(_request, error: BrowserProviderUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        )

    @browser_app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "taroai-browser-controller"}

    @browser_app.get("/capabilities")
    def capabilities(
        _request: None = Depends(prepare_controller_request),
    ) -> BrowserControllerCapabilities:
        allowed_hosts = normalize_allowed_hosts(
            service_settings.navigation_allowed_hosts
        )
        return BrowserControllerCapabilities(
            provider=browser_controller.provider,
            auth_required=bool(service_settings.api_key.strip()),
            session_ttl_enforced=service_settings.session_ttl_seconds > 0,
            max_session_ttl_seconds=service_settings.session_ttl_seconds,
            max_sessions=service_settings.max_sessions,
            max_sessions_per_tenant=service_settings.max_sessions_per_tenant,
            max_sessions_per_run=service_settings.max_sessions_per_run,
            navigation_allowlist_enforced=bool(allowed_hosts),
            navigation_allowed_host_count=len(allowed_hosts),
        )

    @browser_app.post("/sessions", status_code=status.HTTP_201_CREATED)
    def open_session(
        request: BrowserSessionOpenRequest,
        _request: None = Depends(prepare_session_creation),
    ) -> BrowserSession:
        if browser_session_exists(browser_controller, request.session_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Browser session already exists: {request.session_id}",
            )
        enforce_browser_session_limits(
            browser_controller,
            service_settings,
            tenant_id=request.tenant_id,
            run_id=request.run_id,
        )
        return browser_controller.open_session(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            session_id=request.session_id,
        )

    @browser_app.get("/sessions")
    def list_sessions(
        tenant_id: str | None = Query(default=None, min_length=1),
        _request: None = Depends(prepare_controller_request),
    ) -> dict[str, list[BrowserSession]]:
        return {"sessions": browser_controller.list_sessions(tenant_id)}

    @browser_app.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        tenant_id: str = Query(min_length=1),
        workspace_id: str = Query(min_length=1),
        run_id: str = Query(min_length=1),
        _request: None = Depends(prepare_controller_request),
    ) -> BrowserSession:
        return require_browser_session_scope(
            browser_controller,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )

    @browser_app.delete("/sessions/{session_id}")
    def delete_session(
        session_id: str,
        tenant_id: str = Query(min_length=1),
        workspace_id: str = Query(min_length=1),
        run_id: str = Query(min_length=1),
        _request: None = Depends(prepare_controller_request),
    ) -> BrowserSession:
        require_browser_session_scope(
            browser_controller,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )
        return browser_controller.delete_session(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )

    @browser_app.post("/actions", status_code=status.HTTP_201_CREATED)
    def apply_action(
        action: BrowserAction,
        _request: None = Depends(prepare_controller_request),
    ) -> dict:
        enforce_browser_navigation_policy(action, service_settings)
        return _serialize_observation(browser_controller.apply(action))

    return browser_app


def cleanup_expired_browser_sessions(
    controller: PlaywrightBrowserController,
    settings: BrowserControllerServiceSettings,
) -> None:
    if settings.session_ttl_seconds <= 0:
        return
    now = utc_now()
    for session in controller.list_sessions():
        age_seconds = (now - session.created_at).total_seconds()
        if age_seconds <= settings.session_ttl_seconds:
            continue
        try:
            controller.delete_session(
                tenant_id=session.tenant_id,
                session_id=session.session_id,
            )
        except NotFoundError:
            continue


def browser_session_exists(
    controller: PlaywrightBrowserController,
    session_id: str,
) -> bool:
    return any(session.session_id == session_id for session in controller.list_sessions())


def require_browser_session_scope(
    controller: PlaywrightBrowserController,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    session_id: str,
) -> BrowserSession:
    session = controller.get_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        session_id=session_id,
    )
    return session


def enforce_browser_session_limits(
    controller: PlaywrightBrowserController,
    settings: BrowserControllerServiceSettings,
    tenant_id: str,
    run_id: str,
) -> None:
    sessions = controller.list_sessions()
    tenant_sessions = [
        session for session in sessions if session.tenant_id == tenant_id
    ]
    run_sessions = [
        session
        for session in tenant_sessions
        if session.run_id == run_id
    ]
    if (
        len(sessions) >= settings.max_sessions
        or len(tenant_sessions) >= settings.max_sessions_per_tenant
        or len(run_sessions) >= settings.max_sessions_per_run
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="browser session limit reached",
        )


def enforce_browser_navigation_policy(
    action: BrowserAction,
    settings: BrowserControllerServiceSettings,
) -> None:
    if action.action_type != BrowserActionType.NAVIGATE:
        return
    allowed_hosts = normalize_allowed_hosts(settings.navigation_allowed_hosts)
    if not allowed_hosts:
        return
    parsed = urlparse(action.url or "")
    host = (parsed.hostname or "").lower()
    if not host or not browser_host_allowed(host, allowed_hosts):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="browser navigation host is not allowed",
        )


def browser_host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    for pattern in allowed_hosts:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and host != pattern[2:]:
                return True
            continue
        if host == pattern:
            return True
    return False


def normalize_allowed_hosts(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        host = value.strip().lower()
        if host:
            normalized.append(host)
    return normalized


def parse_env_list(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("browser navigation allowed hosts must be a list")
        return [str(item) for item in parsed]
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _serialize_observation(observation: BrowserObservation) -> dict:
    payload = observation.model_dump(mode="json")
    if observation.screenshot_content is not None:
        payload["screenshot_content_base64"] = base64.b64encode(
            observation.screenshot_content
        ).decode("ascii")
    return payload


app = create_playwright_browser_app()

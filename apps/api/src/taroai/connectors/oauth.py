import json
import threading
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.connectors.models import ConnectorAuthMode, ConnectorDefinition
from taroai.domain import new_id, utc_now
from taroai.secrets import (
    SecretAccessDeniedError,
    SecretLeaseExpiredError,
    SecretNotFoundError,
    SecretService,
)


class ConnectorOAuthError(RuntimeError):
    pass


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client() -> httpx.Client:
    """Process-wide pooled HTTP client for OAuth token calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(30.0),
                    follow_redirects=True,
                )
    return _HTTP_CLIENT


class OAuthConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorize_url: str = Field(min_length=1)
    token_url: str = Field(min_length=1)
    callback_url: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=list)
    client_id_secret_ref_id: str = Field(min_length=1)
    client_secret_secret_ref_id: str = Field(min_length=1)
    access_token_secret_ref_id: str = Field(min_length=1)
    refresh_token_secret_ref_id: str | None = None
    state_ttl_seconds: int = Field(default=600, ge=1)
    lease_ttl_seconds: int = Field(default=60, ge=1)

    @field_validator("authorize_url", "token_url", "callback_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OAuth URLs must use HTTP or HTTPS")
        return value


class OAuthAuthorizationSession(BaseModel):
    tenant_id: str
    workspace_id: str
    connector_id: str
    requested_by_user_id: str
    expires_at: datetime
    reconnect_thread_id: str | None = None
    reconnect_run_id: str | None = None
    reconnect_action_id: str | None = None
    opener_origin: str | None = None


class RedisOAuthAuthorizationStateStore(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    key_prefix: str = "taroai:connector-oauth:state"
    client: Any | None = Field(default=None, exclude=True)

    def save(self, state: str, session: OAuthAuthorizationSession) -> None:
        ttl = max(1, int((session.expires_at - utc_now()).total_seconds()))
        self._client().set(self._key(state), session.model_dump_json(), ex=ttl)

    def get(self, state: str) -> OAuthAuthorizationSession | None:
        return self._decode(self._client().get(self._key(state)))

    def pop(self, state: str) -> OAuthAuthorizationSession | None:
        return self._decode(self._client().getdel(self._key(state)))

    def _client(self):
        if self.client is None:
            import redis

            object.__setattr__(
                self,
                "client",
                redis.Redis.from_url(self.url, decode_responses=True),
            )
        return self.client

    def _key(self, state: str) -> str:
        return f"{self.key_prefix}:{state}"

    @staticmethod
    def _decode(value: str | bytes | None) -> OAuthAuthorizationSession | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            return OAuthAuthorizationSession.model_validate_json(value)
        except ValueError:
            return None


class ConnectorOAuthAuthorizeResult(BaseModel):
    connector_id: str
    authorization_url: str
    state: str
    expires_at: datetime
    reconnect_thread_id: str | None = None
    reconnect_run_id: str | None = None
    reconnect_action_id: str | None = None


class ConnectorOAuthCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


class OAuthCodeExchangeRequest(BaseModel):
    token_url: str
    code: str
    redirect_uri: str
    client_id: str
    client_secret: str


class OAuthRefreshRequest(BaseModel):
    token_url: str
    refresh_token: str
    client_id: str
    client_secret: str


class OAuthTokenResponse(BaseModel):
    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_in: int | None = Field(default=None, ge=1)
    token_type: str | None = None


class ConnectorOAuthTokenResult(BaseModel):
    connector_id: str
    status: str
    access_token_secret_ref_id: str
    refresh_token_secret_ref_id: str | None = None
    expires_in: int | None = None
    token_type: str | None = None
    reconnect_thread_id: str | None = None
    reconnect_run_id: str | None = None
    reconnect_action_id: str | None = None


class UrlLibOAuthTokenClient(BaseModel):
    def exchange_code(self, request: OAuthCodeExchangeRequest) -> dict[str, Any]:
        return self._post_form(
            request.token_url,
            {
                "grant_type": "authorization_code",
                "code": request.code,
                "redirect_uri": request.redirect_uri,
                "client_id": request.client_id,
                "client_secret": request.client_secret,
            },
        )

    def refresh(self, request: OAuthRefreshRequest) -> dict[str, Any]:
        return self._post_form(
            request.token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": request.refresh_token,
                "client_id": request.client_id,
                "client_secret": request.client_secret,
            },
        )

    def _post_form(self, url: str, payload: dict[str, str]) -> dict[str, Any]:
        body = urlencode(payload).encode("utf-8")
        try:
            response = _shared_http_client().post(
                url,
                content=body,
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            # urllib 的 HTTPError 是 URLError 子类：非 2xx 也映射为
            # ConnectorOAuthError；raise_for_status 保持相同行为。
            response.raise_for_status()
            return json.loads(response.content.decode("utf-8"))
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ConnectorOAuthError("OAuth token request failed") from error


class ConnectorOAuthService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    secret_service: SecretService | None = None
    token_client: Any = Field(default_factory=UrlLibOAuthTokenClient)
    state_store: Any | None = Field(default=None, exclude=True)
    pending_states: dict[str, OAuthAuthorizationSession] = Field(default_factory=dict)

    def pending_authorization(self, state: str) -> OAuthAuthorizationSession:
        session = (
            self.state_store.get(state)
            if self.state_store is not None
            else self.pending_states.get(state)
        )
        if session is None:
            raise ConnectorOAuthError("OAuth state is invalid or already consumed")
        if session.expires_at <= utc_now():
            if self.state_store is not None:
                self.state_store.pop(state)
            else:
                self.pending_states.pop(state, None)
            raise ConnectorOAuthError("OAuth state has expired")
        return session.model_copy(deep=True)

    def build_authorization_url(
        self,
        connector: ConnectorDefinition,
        requested_by_user_id: str,
        reconnect_thread_id: str | None = None,
        reconnect_run_id: str | None = None,
        reconnect_action_id: str | None = None,
        opener_origin: str | None = None,
        now: datetime | None = None,
    ) -> ConnectorOAuthAuthorizeResult:
        config = self._config(connector)
        resolved_now = now or utc_now()
        state = new_id("oauth_state")
        session = OAuthAuthorizationSession(
            tenant_id=connector.tenant_id,
            workspace_id=connector.workspace_id,
            connector_id=connector.id,
            requested_by_user_id=requested_by_user_id,
            expires_at=resolved_now + timedelta(seconds=config.state_ttl_seconds),
            reconnect_thread_id=reconnect_thread_id,
            reconnect_run_id=reconnect_run_id,
            reconnect_action_id=reconnect_action_id,
            opener_origin=opener_origin,
        )
        if self.state_store is not None:
            self.state_store.save(state, session)
        else:
            self.pending_states[state] = session
        client_id = self._secret_value(
            connector=connector,
            secret_id=config.client_id_secret_ref_id,
            action="connector.oauth2.client_id",
            ttl_seconds=config.lease_ttl_seconds,
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": config.callback_url,
                "scope": " ".join(config.scopes),
                "state": state,
            }
        )
        return ConnectorOAuthAuthorizeResult(
            connector_id=connector.id,
            authorization_url=f"{config.authorize_url}?{query}",
            state=state,
            expires_at=session.expires_at,
            reconnect_thread_id=reconnect_thread_id,
            reconnect_run_id=reconnect_run_id,
            reconnect_action_id=reconnect_action_id,
        )

    def complete_callback(
        self,
        connector: ConnectorDefinition,
        request: ConnectorOAuthCallbackRequest,
        now: datetime | None = None,
    ) -> ConnectorOAuthTokenResult:
        config = self._config(connector)
        session = self._consume_state(connector, request.state, now or utc_now())
        client_id, client_secret = self._client_credentials(connector, config)
        response = OAuthTokenResponse.model_validate(
            self.token_client.exchange_code(
                OAuthCodeExchangeRequest(
                    token_url=config.token_url,
                    code=request.code,
                    redirect_uri=config.callback_url,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            )
        )
        result = self._store_tokens(
            connector=connector,
            config=config,
            response=response,
            status="completed",
        )
        return result.model_copy(
            update={
                "reconnect_thread_id": session.reconnect_thread_id,
                "reconnect_run_id": session.reconnect_run_id,
                "reconnect_action_id": session.reconnect_action_id,
            }
        )

    def refresh(
        self,
        connector: ConnectorDefinition,
    ) -> ConnectorOAuthTokenResult:
        config = self._config(connector)
        if config.refresh_token_secret_ref_id is None:
            raise ConnectorOAuthError("refresh token reference is required")
        client_id, client_secret = self._client_credentials(connector, config)
        refresh_token = self._secret_value(
            connector=connector,
            secret_id=config.refresh_token_secret_ref_id,
            action="connector.oauth2.refresh",
            ttl_seconds=config.lease_ttl_seconds,
        )
        response = OAuthTokenResponse.model_validate(
            self.token_client.refresh(
                OAuthRefreshRequest(
                    token_url=config.token_url,
                    refresh_token=refresh_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            )
        )
        return self._store_tokens(
            connector=connector,
            config=config,
            response=response,
            status="refreshed",
        )

    def _config(self, connector: ConnectorDefinition) -> OAuthConnectorConfig:
        if connector.auth_mode != ConnectorAuthMode.OAUTH2:
            raise ConnectorOAuthError("connector is not configured for OAuth2")
        raw_config = connector.metadata.get("oauth2")
        if not isinstance(raw_config, dict):
            raise ConnectorOAuthError("OAuth2 connector config is required")
        config = OAuthConnectorConfig.model_validate(raw_config)
        if connector.credential_ref is None:
            raise ConnectorOAuthError("connector credential reference is required")
        if connector.credential_ref.secret_ref_id != config.access_token_secret_ref_id:
            raise ConnectorOAuthError("connector credential reference must point to access token secret")
        return config

    def _client_credentials(
        self,
        connector: ConnectorDefinition,
        config: OAuthConnectorConfig,
    ) -> tuple[str, str]:
        return (
            self._secret_value(
                connector=connector,
                secret_id=config.client_id_secret_ref_id,
                action="connector.oauth2.client_id",
                ttl_seconds=config.lease_ttl_seconds,
            ),
            self._secret_value(
                connector=connector,
                secret_id=config.client_secret_secret_ref_id,
                action="connector.oauth2.client_secret",
                ttl_seconds=config.lease_ttl_seconds,
            ),
        )

    def _secret_value(
        self,
        connector: ConnectorDefinition,
        secret_id: str,
        action: str,
        ttl_seconds: int,
    ) -> str:
        if self.secret_service is None:
            raise ConnectorOAuthError("secret service is not configured")
        try:
            lease = self.secret_service.create_lease(
                tenant_id=connector.tenant_id,
                workspace_id=connector.workspace_id,
                secret_id=secret_id,
                tool_name="connector.oauth2",
                actions=[action],
                ttl_seconds=ttl_seconds,
            )
            return self.secret_service.resolve_lease_value(
                tenant_id=connector.tenant_id,
                lease_token=lease.lease_token,
            )
        except (
            SecretAccessDeniedError,
            SecretLeaseExpiredError,
            SecretNotFoundError,
        ) as error:
            raise ConnectorOAuthError("OAuth secret is not available") from error

    def _consume_state(
        self,
        connector: ConnectorDefinition,
        state: str,
        now: datetime,
    ) -> OAuthAuthorizationSession:
        session = (
            self.state_store.pop(state)
            if self.state_store is not None
            else self.pending_states.pop(state, None)
        )
        if session is None:
            raise ConnectorOAuthError("OAuth state is invalid")
        if (
            session.tenant_id != connector.tenant_id
            or session.workspace_id != connector.workspace_id
            or session.connector_id != connector.id
        ):
            raise ConnectorOAuthError("OAuth state does not match connector")
        if session.expires_at <= now:
            raise ConnectorOAuthError("OAuth state expired")
        return session

    def _store_tokens(
        self,
        connector: ConnectorDefinition,
        config: OAuthConnectorConfig,
        response: OAuthTokenResponse,
        status: str,
    ) -> ConnectorOAuthTokenResult:
        if self.secret_service is None:
            raise ConnectorOAuthError("secret service is not configured")
        self.secret_service.rotate_secret_value(
            tenant_id=connector.tenant_id,
            secret_id=config.access_token_secret_ref_id,
            value=response.access_token,
        )
        refresh_ref = config.refresh_token_secret_ref_id
        if refresh_ref is not None and response.refresh_token is not None:
            self.secret_service.rotate_secret_value(
                tenant_id=connector.tenant_id,
                secret_id=refresh_ref,
                value=response.refresh_token,
            )
        return ConnectorOAuthTokenResult(
            connector_id=connector.id,
            status=status,
            access_token_secret_ref_id=config.access_token_secret_ref_id,
            refresh_token_secret_ref_id=refresh_ref,
            expires_in=response.expires_in,
            token_type=response.token_type,
        )

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from taroai.auth.models import AuthLoginResult, AuthLogoutResult, AuthTokenClaims
from taroai.auth.sessions import AuthSessionStore, InMemoryAuthSessionStore
from taroai.domain import utc_now
from taroai.identity import InMemoryIdentityService, SqlIdentityService, UserAccount
from taroai.store import NotFoundError


class AuthRequiredError(PermissionError):
    pass


class AuthInvalidCredentialsError(PermissionError):
    pass


class AuthService(BaseModel):
    identity_service: InMemoryIdentityService | SqlIdentityService
    session_store: AuthSessionStore = Field(default_factory=InMemoryAuthSessionStore)
    access_token_secret: str = Field(min_length=1)
    access_token_ttl_seconds: int = Field(gt=0)
    sso_provider_registry: Any | None = None

    def login(
        self,
        tenant_id: str,
        email: str,
        password: str,
        now: datetime | None = None,
    ) -> AuthLoginResult:
        try:
            account = self.identity_service.get_user_by_email(tenant_id, email)
        except NotFoundError as error:
            raise AuthInvalidCredentialsError("invalid credentials") from error
        if account.status != "active":
            raise AuthInvalidCredentialsError("invalid credentials")
        if not self._password_login_allowed(tenant_id, account.email):
            raise AuthInvalidCredentialsError("invalid credentials")
        if not self.identity_service.verify_password(tenant_id, email, password):
            raise AuthInvalidCredentialsError("invalid credentials")
        issued_at = now or utc_now()
        expires_at = issued_at + timedelta(seconds=self.access_token_ttl_seconds)
        session = self.session_store.create_session(
            tenant_id=account.tenant_id,
            user_id=account.id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        claims = self._claims_for_account(
            account,
            session_id=session.id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return AuthLoginResult(
            access_token=self.issue_token(claims),
            session_id=session.id,
            expires_at=claims.expires_at,
            tenant_id=claims.tenant_id,
            user_id=claims.user_id,
        )

    def authenticate_authorization_header(
        self,
        authorization: str | None,
        now: datetime | None = None,
    ) -> AuthTokenClaims:
        if authorization is None or not authorization.startswith("Bearer "):
            raise AuthRequiredError("authentication required")
        return self.validate_token(authorization.removeprefix("Bearer ").strip(), now=now)

    def logout_authorization_header(
        self,
        authorization: str | None,
        now: datetime | None = None,
    ) -> AuthLogoutResult:
        claims = self.authenticate_authorization_header(authorization, now=now)
        revoked = self.session_store.revoke_session(
            claims.tenant_id,
            claims.session_id,
            now or utc_now(),
        )
        return AuthLogoutResult(revoked=revoked)

    def issue_token(self, claims: AuthTokenClaims) -> str:
        payload = self._encode_json(claims.model_dump(mode="json"))
        signature = self._sign(payload)
        return f"{payload}.{signature}"

    def validate_token(self, token: str, now: datetime | None = None) -> AuthTokenClaims:
        try:
            payload, signature = token.split(".", 1)
        except ValueError as error:
            raise AuthRequiredError("invalid access token") from error
        expected_signature = self._sign(payload)
        if not hmac.compare_digest(signature, expected_signature):
            raise AuthRequiredError("invalid access token")
        try:
            claims = AuthTokenClaims.model_validate_json(self._decode_text(payload))
        except ValueError as error:
            raise AuthRequiredError("invalid access token") from error
        if claims.expires_at <= (now or utc_now()):
            raise AuthRequiredError("access token expired")
        session = self.session_store.get_session(claims.tenant_id, claims.session_id)
        if session is None or session.user_id != claims.user_id:
            raise AuthRequiredError("invalid access token")
        if session.revoked_at is not None:
            raise AuthRequiredError("access token revoked")
        if session.expires_at <= (now or utc_now()):
            raise AuthRequiredError("access token expired")
        try:
            account = self.identity_service.get_user(claims.tenant_id, claims.user_id)
        except NotFoundError as error:
            raise AuthRequiredError("invalid access token") from error
        if account.status != "active":
            raise AuthRequiredError("access token revoked")
        return claims

    def _claims_for_account(
        self,
        account: UserAccount,
        session_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> AuthTokenClaims:
        return AuthTokenClaims(
            session_id=session_id,
            tenant_id=account.tenant_id,
            user_id=account.id,
            email=account.email,
            role_ids=self.identity_service.list_role_ids_for_user(account.tenant_id, account.id),
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def _password_login_allowed(self, tenant_id: str, email: str) -> bool:
        if self.sso_provider_registry is None:
            return True
        provider_entry = self.sso_provider_registry.find_enabled_for_email(tenant_id, email)
        if provider_entry is None:
            return True
        return provider_entry.provider.password_fallback_enabled

    def _sign(self, payload: str) -> str:
        digest = hmac.new(
            self.access_token_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return self._encode_bytes(digest)

    def _encode_json(self, value: dict[str, Any]) -> str:
        return self._encode_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    def _encode_bytes(self, value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    def _decode_text(self, value: str) -> str:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")

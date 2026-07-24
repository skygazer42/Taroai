import base64
import hashlib
import hmac
import json
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from taroai.auth.models import (
    AuthActionPurpose,
    AuthActionTokenClaims,
    AuthLoginResult,
    AuthLogoutResult,
    AuthTokenClaims,
)
from taroai.auth.sessions import AuthSessionStore, InMemoryAuthSessionStore
from taroai.domain import utc_now
from taroai.identity import InMemoryIdentityService, SqlIdentityService, UserAccount
from taroai.store import NotFoundError


class AuthRequiredError(PermissionError):
    pass


class AuthInvalidCredentialsError(PermissionError):
    pass


class AuthActionTokenError(PermissionError):
    pass


class AuthEmailDeliveryError(RuntimeError):
    pass


class AuthService(BaseModel):
    identity_service: InMemoryIdentityService | SqlIdentityService
    session_store: AuthSessionStore = Field(default_factory=InMemoryAuthSessionStore)
    access_token_secret: str = Field(min_length=1)
    access_token_ttl_seconds: int = Field(gt=0)
    remembered_access_token_ttl_seconds: int = Field(default=2_592_000, gt=0)
    sso_provider_registry: Any | None = None

    def login(
        self,
        tenant_id: str | None,
        email: str,
        password: str,
        now: datetime | None = None,
        remember_me: bool = False,
    ) -> AuthLoginResult:
        accounts = self.identity_service.find_users_by_email(email)
        matches = [
            account
            for account in accounts
            if account.status == "active"
            and self._password_login_allowed(account.tenant_id, account.email)
            and self.identity_service.verify_password(
                account.tenant_id, account.email, password
            )
        ]
        preferred = [account for account in matches if account.tenant_id == tenant_id]
        if len(preferred) == 1:
            matches = preferred
        if len(matches) != 1:
            raise AuthInvalidCredentialsError("invalid credentials")
        account = matches[0]
        issued_at = now or utc_now()
        ttl_seconds = (
            self.remembered_access_token_ttl_seconds
            if remember_me
            else self.access_token_ttl_seconds
        )
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
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
            display_name=claims.display_name,
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

    def issue_action_token(
        self,
        account: UserAccount,
        purpose: AuthActionPurpose,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> str:
        claims = AuthActionTokenClaims(
            purpose=purpose,
            tenant_id=account.tenant_id,
            user_id=account.id,
            credential_fingerprint=self._credential_fingerprint(account),
            expires_at=(now or utc_now()) + timedelta(seconds=ttl_seconds),
        )
        payload = self._encode_json(claims.model_dump(mode="json"))
        return f"{payload}.{self._sign_action(payload, account)}"

    def verify_email_action(self, token: str, now: datetime | None = None) -> UserAccount:
        account = self._validate_action_token(token, "email_verification", now)
        if account.status == "active":
            return account
        if account.status != "pending":
            raise AuthActionTokenError("invalid or expired email verification token")
        return self.identity_service.activate_user(account.tenant_id, account.id)

    def reset_password_action(
        self,
        token: str,
        password: str,
        now: datetime | None = None,
    ) -> UserAccount:
        reset_at = now or utc_now()
        account = self._validate_action_token(token, "password_reset", reset_at)
        if account.status != "active":
            raise AuthActionTokenError("invalid or expired password reset token")
        updated = self.identity_service.update_password(
            account.tenant_id,
            account.id,
            password,
        )
        self.session_store.revoke_user_sessions(
            account.tenant_id,
            account.id,
            reset_at,
        )
        return updated

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
        return claims.model_copy(
            update={
                "email": account.email,
                "display_name": account.display_name,
                "role_ids": self.identity_service.list_role_ids_for_user(
                    account.tenant_id, account.id
                ),
            }
        )

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
            display_name=account.display_name,
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

    def _validate_action_token(
        self,
        token: str,
        purpose: AuthActionPurpose,
        now: datetime | None,
    ) -> UserAccount:
        try:
            payload, signature = token.split(".", 1)
            claims = AuthActionTokenClaims.model_validate_json(
                self._decode_text(payload)
            )
            account = self.identity_service.get_user(
                claims.tenant_id,
                claims.user_id,
            )
        except (ValueError, NotFoundError) as error:
            raise AuthActionTokenError("invalid or expired auth action token") from error
        expected_signature = self._sign_action(payload, account)
        if (
            not hmac.compare_digest(signature, expected_signature)
            or claims.purpose != purpose
            or claims.credential_fingerprint != self._credential_fingerprint(account)
            or claims.expires_at <= (now or utc_now())
        ):
            raise AuthActionTokenError("invalid or expired auth action token")
        return account

    def _sign_action(self, payload: str, account: UserAccount) -> str:
        digest = hmac.new(
            f"{self.access_token_secret}\0{account.password_hash}".encode("utf-8"),
            f"auth_action:{payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return self._encode_bytes(digest)

    @staticmethod
    def _credential_fingerprint(account: UserAccount) -> str:
        return hashlib.sha256(account.password_hash.encode("utf-8")).hexdigest()

    def _encode_json(self, value: dict[str, Any]) -> str:
        return self._encode_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    def _encode_bytes(self, value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    def _decode_text(self, value: str) -> str:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")


def send_auth_email(
    smtp_url: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    parsed = urlparse(smtp_url)
    if parsed.scheme not in {"smtp", "smtps"} or not parsed.hostname:
        raise AuthEmailDeliveryError("auth email delivery is not configured")
    try:
        port = parsed.port or (465 if parsed.scheme == "smtps" else 587)
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        tls_context = ssl.create_default_context()
        client_type = smtplib.SMTP_SSL if parsed.scheme == "smtps" else smtplib.SMTP
        client_kwargs = {"context": tls_context} if parsed.scheme == "smtps" else {}
        with client_type(parsed.hostname, port, timeout=10, **client_kwargs) as client:
            if parsed.scheme == "smtp":
                client.starttls(context=tls_context)
            if parsed.username is not None:
                client.login(
                    unquote(parsed.username),
                    unquote(parsed.password or ""),
                )
            client.send_message(message)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        raise AuthEmailDeliveryError("auth email delivery failed") from error

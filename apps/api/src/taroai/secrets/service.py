from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from taroai.domain import new_id, utc_now
from taroai.secrets.models import (
    SecretLease,
    SecretLeaseResolution,
    SecretRef,
    SecretScope,
)


class SecretAccessDeniedError(PermissionError):
    pass


class SecretLeaseExpiredError(PermissionError):
    pass


class SecretNotFoundError(LookupError):
    pass


class SecretStoreError(RuntimeError):
    pass


class AwsSecretsManagerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_name: str = Field(default="us-east-1", min_length=1)
    endpoint_url: str | None = None
    secret_name_prefix: str = Field(default="taroai", min_length=1)
    kms_key_id: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "AwsSecretsManagerConfig":
        return cls(
            region_name=settings.secret_service_region,
            endpoint_url=settings.secret_service_endpoint_url or None,
            secret_name_prefix=settings.secret_service_name_prefix,
            kms_key_id=settings.secret_service_kms_key_id or None,
        )


class InMemorySecretService(BaseModel):
    secrets: dict[str, SecretRef] = Field(default_factory=dict)
    leases: dict[str, SecretLease] = Field(default_factory=dict)
    _secret_values: dict[str, str] = PrivateAttr(default_factory=dict)

    def create_secret(
        self,
        tenant_id: str,
        workspace_id: str | None,
        name: str,
        value: str,
        scope: SecretScope,
        created_at: datetime | None = None,
    ) -> SecretRef:
        if scope.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret scope tenant does not match")
        if scope.workspace_id is not None and scope.workspace_id != workspace_id:
            raise SecretAccessDeniedError("secret scope workspace does not match")
        secret = SecretRef(
            id=new_id("secret"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=name,
            scope=scope,
            created_at=created_at or utc_now(),
        )
        self.secrets[secret.id] = secret
        self._secret_values[secret.id] = value
        return secret

    def register_secret_ref(self, secret: SecretRef) -> SecretRef:
        self.secrets[secret.id] = secret
        return secret

    def create_lease(
        self,
        tenant_id: str,
        workspace_id: str | None,
        secret_id: str,
        tool_name: str,
        actions: list[str],
        ttl_seconds: int,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> SecretLease:
        secret = self._get_secret(tenant_id, secret_id)
        if not secret.scope.allows(tenant_id, workspace_id, tool_name, actions):
            raise SecretAccessDeniedError("secret scope does not allow this lease")
        issued_at = now or utc_now()
        lease = SecretLease(
            id=new_id("lease"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            secret_ref_id=secret.id,
            tool_name=tool_name,
            actions=actions,
            lease_token=new_id("lease_token"),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=ttl_seconds),
        )
        self.leases[lease.lease_token] = lease
        return lease

    def resolve_lease_value(
        self,
        tenant_id: str,
        lease_token: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        require_bound_context: bool = False,
        now: datetime | None = None,
    ) -> str:
        return self.resolve_lease(
            tenant_id=tenant_id,
            lease_token=lease_token,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            tool_name=tool_name,
            action=action,
            require_bound_context=require_bound_context,
            now=now,
        ).value

    def resolve_lease(
        self,
        tenant_id: str,
        lease_token: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        require_bound_context: bool = False,
        now: datetime | None = None,
    ) -> SecretLeaseResolution:
        lease = validate_secret_lease_resolution(
            lease=self.leases.get(lease_token),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            tool_name=tool_name,
            action=action,
            require_bound_context=require_bound_context,
            now=now,
        )
        value = self._secret_values.get(lease.secret_ref_id)
        if value is None:
            raise SecretNotFoundError("secret value is not available")
        return build_secret_lease_resolution(lease, value, action)

    def rotate_secret_value(
        self,
        tenant_id: str,
        secret_id: str,
        value: str,
    ) -> SecretRef:
        secret = self._get_secret(tenant_id, secret_id)
        self._secret_values[secret.id] = value
        return secret

    def _get_secret(self, tenant_id: str, secret_id: str) -> SecretRef:
        secret = self.secrets.get(secret_id)
        if secret is None:
            raise SecretNotFoundError(f"secret not found: {secret_id}")
        if secret.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret is not in tenant")
        return secret


class LocalEncryptedSecretService(BaseModel):
    """单机开发后端：密文和短期租约保存在 Compose 共享卷。"""

    path: Path = Path("/data/taroai/secrets.db")
    _cipher: Fernet = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._cipher = Fernet(self._load_or_create_key())
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS local_secret_values (
                        id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        ciphertext BLOB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS local_secret_leases (
                        lease_token TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    """
                )
            os.chmod(self.path, 0o600)
        except (OSError, sqlite3.Error, ValueError) as error:
            raise SecretStoreError("local secret backend initialization failed") from error

    def create_secret(
        self,
        tenant_id: str,
        workspace_id: str | None,
        name: str,
        value: str,
        scope: SecretScope,
        created_at: datetime | None = None,
    ) -> SecretRef:
        if scope.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret scope tenant does not match")
        if scope.workspace_id is not None and scope.workspace_id != workspace_id:
            raise SecretAccessDeniedError("secret scope workspace does not match")
        secret_id = new_id("secret")
        secret = SecretRef(
            id=secret_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=name,
            scope=scope,
            backend="local",
            external_name=secret_id,
            created_at=created_at or utc_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_secret_values (id, tenant_id, payload, ciphertext)
                VALUES (?, ?, ?, ?)
                """,
                (
                    secret.id,
                    tenant_id,
                    secret.model_dump_json(),
                    self._cipher.encrypt(value.encode("utf-8")),
                ),
            )
        return secret

    def register_secret_ref(self, secret: SecretRef) -> SecretRef:
        if secret.backend != "local" or secret.external_name not in {None, secret.id}:
            raise SecretStoreError("local secret reference is incomplete")
        return self._get_secret(secret.tenant_id, secret.id)

    def create_lease(
        self,
        tenant_id: str,
        workspace_id: str | None,
        secret_id: str,
        tool_name: str,
        actions: list[str],
        ttl_seconds: int,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> SecretLease:
        secret = self._get_secret(tenant_id, secret_id)
        if not secret.scope.allows(tenant_id, workspace_id, tool_name, actions):
            raise SecretAccessDeniedError("secret scope does not allow this lease")
        issued_at = now or utc_now()
        lease = SecretLease(
            id=new_id("lease"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            secret_ref_id=secret.id,
            tool_name=tool_name,
            actions=actions,
            lease_token=new_id("lease_token"),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=ttl_seconds),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_secret_leases (
                    lease_token, tenant_id, expires_at, payload
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    lease.lease_token,
                    tenant_id,
                    lease.expires_at.isoformat(),
                    lease.model_dump_json(),
                ),
            )
        return lease

    def resolve_lease_value(self, **kwargs) -> str:
        return self.resolve_lease(**kwargs).value

    def resolve_lease(
        self,
        tenant_id: str,
        lease_token: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        require_bound_context: bool = False,
        now: datetime | None = None,
    ) -> SecretLeaseResolution:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM local_secret_leases
                WHERE tenant_id = ? AND lease_token = ?
                """,
                (tenant_id, lease_token),
            ).fetchone()
        lease = validate_secret_lease_resolution(
            lease=SecretLease.model_validate_json(row["payload"]) if row else None,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            tool_name=tool_name,
            action=action,
            require_bound_context=require_bound_context,
            now=now,
        )
        value = self._read_value(tenant_id, lease.secret_ref_id)
        return build_secret_lease_resolution(lease, value, action)

    def rotate_secret_value(
        self,
        tenant_id: str,
        secret_id: str,
        value: str,
    ) -> SecretRef:
        secret = self._get_secret(tenant_id, secret_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE local_secret_values SET ciphertext = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (self._cipher.encrypt(value.encode("utf-8")), tenant_id, secret_id),
            )
        return secret

    def is_ready(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM local_secret_values LIMIT 1")
            return True
        except sqlite3.Error:
            return False

    def _get_secret(self, tenant_id: str, secret_id: str) -> SecretRef:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM local_secret_values
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, secret_id),
            ).fetchone()
        if row is None:
            raise SecretNotFoundError(f"secret not found: {secret_id}")
        return SecretRef.model_validate_json(row["payload"])

    def _read_value(self, tenant_id: str, secret_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT ciphertext FROM local_secret_values
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, secret_id),
            ).fetchone()
        if row is None:
            raise SecretNotFoundError("secret value is not available")
        try:
            return self._cipher.decrypt(row["ciphertext"]).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise SecretStoreError("local secret value cannot be decrypted") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _load_or_create_key(self) -> bytes:
        key_path = Path(f"{self.path}.key")
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(Fernet.generate_key())
        if key_path.is_symlink() or not key_path.is_file():
            raise SecretStoreError("local secret key file is invalid")
        os.chmod(key_path, 0o600)
        return key_path.read_bytes().strip()


class AwsSecretsManagerSecretService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: AwsSecretsManagerConfig = Field(default_factory=AwsSecretsManagerConfig)
    client: Any | None = Field(default=None, exclude=True, repr=False)
    secrets: dict[str, SecretRef] = Field(default_factory=dict)
    leases: dict[str, SecretLease] = Field(default_factory=dict)

    def create_secret(
        self,
        tenant_id: str,
        workspace_id: str | None,
        name: str,
        value: str,
        scope: SecretScope,
        created_at: datetime | None = None,
    ) -> SecretRef:
        if scope.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret scope tenant does not match")
        if scope.workspace_id is not None and scope.workspace_id != workspace_id:
            raise SecretAccessDeniedError("secret scope workspace does not match")
        secret = SecretRef(
            id=new_id("secret"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=name,
            scope=scope,
            backend="aws_secrets_manager",
            created_at=created_at or utc_now(),
        )
        secret = secret.model_copy(
            update={
                "external_name": self._external_secret_name(secret),
            }
        )
        payload = {
            "Name": secret.external_name,
            "SecretString": value,
            "Tags": self._tags(secret),
        }
        if self.config.kms_key_id is not None:
            payload["KmsKeyId"] = self.config.kms_key_id
        try:
            self._client().create_secret(**payload)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code != "ResourceExistsException":
                raise SecretStoreError("secret backend create failed") from error
            self._client().put_secret_value(
                SecretId=secret.external_name,
                SecretString=value,
            )
        except BotoCoreError as error:
            raise SecretStoreError("secret backend create failed") from error
        self.secrets[secret.id] = secret
        return secret

    def register_secret_ref(self, secret: SecretRef) -> SecretRef:
        if secret.backend != "aws_secrets_manager" or not secret.external_name:
            raise SecretStoreError("external secret reference is incomplete")
        self.secrets[secret.id] = secret
        return secret

    def create_lease(
        self,
        tenant_id: str,
        workspace_id: str | None,
        secret_id: str,
        tool_name: str,
        actions: list[str],
        ttl_seconds: int,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> SecretLease:
        secret = self._get_secret(tenant_id, secret_id)
        if not secret.scope.allows(tenant_id, workspace_id, tool_name, actions):
            raise SecretAccessDeniedError("secret scope does not allow this lease")
        issued_at = now or utc_now()
        lease = SecretLease(
            id=new_id("lease"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            secret_ref_id=secret.id,
            tool_name=tool_name,
            actions=actions,
            lease_token=new_id("lease_token"),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=ttl_seconds),
        )
        self.leases[lease.lease_token] = lease
        return lease

    def resolve_lease_value(
        self,
        tenant_id: str,
        lease_token: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        require_bound_context: bool = False,
        now: datetime | None = None,
    ) -> str:
        return self.resolve_lease(
            tenant_id=tenant_id,
            lease_token=lease_token,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            tool_name=tool_name,
            action=action,
            require_bound_context=require_bound_context,
            now=now,
        ).value

    def resolve_lease(
        self,
        tenant_id: str,
        lease_token: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        session_id: str | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        require_bound_context: bool = False,
        now: datetime | None = None,
    ) -> SecretLeaseResolution:
        lease = validate_secret_lease_resolution(
            lease=self.leases.get(lease_token),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
            tool_name=tool_name,
            action=action,
            require_bound_context=require_bound_context,
            now=now,
        )
        secret = self._get_secret(tenant_id, lease.secret_ref_id)
        if secret.external_name is None:
            raise SecretNotFoundError("secret external name is not available")
        try:
            response = self._client().get_secret_value(SecretId=secret.external_name)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundError("secret value is not available") from error
            raise SecretStoreError("secret backend read failed") from error
        except BotoCoreError as error:
            raise SecretStoreError("secret backend read failed") from error
        value = response.get("SecretString")
        if not isinstance(value, str):
            raise SecretNotFoundError("secret string value is not available")
        return build_secret_lease_resolution(lease, value, action)

    def rotate_secret_value(
        self,
        tenant_id: str,
        secret_id: str,
        value: str,
    ) -> SecretRef:
        secret = self._get_secret(tenant_id, secret_id)
        if secret.external_name is None:
            raise SecretNotFoundError("secret external name is not available")
        try:
            self._client().put_secret_value(
                SecretId=secret.external_name,
                SecretString=value,
            )
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundError("secret value is not available") from error
            raise SecretStoreError("secret backend write failed") from error
        except BotoCoreError as error:
            raise SecretStoreError("secret backend write failed") from error
        return secret

    def credentials_available(self) -> bool:
        try:
            client = self._client()
        except BotoCoreError:
            return False
        signer = getattr(client, "_request_signer", None)
        return signer is None or getattr(signer, "_credentials", None) is not None

    def _get_secret(self, tenant_id: str, secret_id: str) -> SecretRef:
        secret = self.secrets.get(secret_id)
        if secret is None:
            raise SecretNotFoundError(f"secret not found: {secret_id}")
        if secret.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret is not in tenant")
        return secret

    def _client(self):
        if self.client is None:
            self.client = boto3.client(
                "secretsmanager",
                region_name=self.config.region_name,
                endpoint_url=self.config.endpoint_url,
            )
        return self.client

    def _external_secret_name(self, secret: SecretRef) -> str:
        parts = [
            self.config.secret_name_prefix.strip("/"),
            self._name_part(secret.tenant_id),
            self._name_part(secret.workspace_id or "tenant"),
            self._name_part(secret.id),
        ]
        return "/".join(part for part in parts if part)

    def _name_part(self, value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_+=.@-]", "-", value.strip())
        normalized = normalized.strip("/-")
        if not normalized:
            raise SecretStoreError("secret backend name part must not be empty")
        return normalized

    def _tags(self, secret: SecretRef) -> list[dict[str, str]]:
        tags = [
            {"Key": "tenant_id", "Value": secret.tenant_id},
            {"Key": "secret_ref_id", "Value": secret.id},
        ]
        if secret.workspace_id is not None:
            tags.append({"Key": "workspace_id", "Value": secret.workspace_id})
        return tags


SecretService = (
    InMemorySecretService
    | LocalEncryptedSecretService
    | AwsSecretsManagerSecretService
)


def validate_secret_lease_resolution(
    lease: SecretLease | None,
    tenant_id: str,
    workspace_id: str | None,
    run_id: str | None,
    step_id: str | None,
    session_id: str | None,
    tool_name: str | None,
    action: str | None,
    require_bound_context: bool = False,
    now: datetime | None = None,
) -> SecretLease:
    if lease is None or lease.tenant_id != tenant_id:
        raise SecretAccessDeniedError("secret lease is not available")
    if require_bound_context and lease.workspace_id is not None and workspace_id is None:
        raise SecretAccessDeniedError("secret lease workspace is required")
    if workspace_id is not None and lease.workspace_id != workspace_id:
        raise SecretAccessDeniedError("secret lease workspace is not available")
    if require_bound_context and lease.run_id is not None and run_id is None:
        raise SecretAccessDeniedError("secret lease run is required")
    if run_id is not None and lease.run_id != run_id:
        raise SecretAccessDeniedError("secret lease run is not available")
    if require_bound_context and lease.step_id is not None and step_id is None:
        raise SecretAccessDeniedError("secret lease step is required")
    if step_id is not None and lease.step_id != step_id:
        raise SecretAccessDeniedError("secret lease step is not available")
    if require_bound_context and lease.session_id is not None and session_id is None:
        raise SecretAccessDeniedError("secret lease session is required")
    if session_id is not None and lease.session_id != session_id:
        raise SecretAccessDeniedError("secret lease session is not available")
    if tool_name is not None and lease.tool_name != tool_name:
        raise SecretAccessDeniedError("secret lease tool is not available")
    if action is not None and action not in lease.actions:
        raise SecretAccessDeniedError("secret lease action is not available")
    if lease.expires_at <= (now or utc_now()):
        raise SecretLeaseExpiredError("secret lease expired")
    return lease


def build_secret_lease_resolution(
    lease: SecretLease,
    value: str,
    action: str | None,
) -> SecretLeaseResolution:
    return SecretLeaseResolution(
        lease_id=lease.id,
        secret_ref_id=lease.secret_ref_id,
        workspace_id=lease.workspace_id,
        run_id=lease.run_id,
        step_id=lease.step_id,
        session_id=lease.session_id,
        tool_name=lease.tool_name,
        action=action or (lease.actions[0] if lease.actions else ""),
        expires_at=lease.expires_at,
        value=value,
    )


def build_secret_service_from_settings(settings: Any) -> SecretService:
    if settings.secret_service_backend == "aws_secrets_manager":
        return AwsSecretsManagerSecretService(
            config=AwsSecretsManagerConfig.from_settings(settings)
        )
    if settings.secret_service_backend == "local":
        return LocalEncryptedSecretService(path=settings.secret_service_local_path)
    return InMemorySecretService()

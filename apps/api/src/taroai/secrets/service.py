from datetime import datetime, timedelta
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
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


SecretService = InMemorySecretService | AwsSecretsManagerSecretService


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
    return InMemorySecretService()

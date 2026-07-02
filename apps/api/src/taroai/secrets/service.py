from datetime import datetime, timedelta

from pydantic import BaseModel, Field, PrivateAttr

from taroai.domain import new_id, utc_now
from taroai.secrets.models import SecretLease, SecretRef, SecretScope


class SecretAccessDeniedError(PermissionError):
    pass


class SecretLeaseExpiredError(PermissionError):
    pass


class SecretNotFoundError(LookupError):
    pass


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
        now: datetime | None = None,
    ) -> str:
        lease = self.leases.get(lease_token)
        if lease is None or lease.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret lease is not available")
        if lease.expires_at <= (now or utc_now()):
            raise SecretLeaseExpiredError("secret lease expired")
        value = self._secret_values.get(lease.secret_ref_id)
        if value is None:
            raise SecretNotFoundError("secret value is not available")
        return value

    def _get_secret(self, tenant_id: str, secret_id: str) -> SecretRef:
        secret = self.secrets.get(secret_id)
        if secret is None:
            raise SecretNotFoundError(f"secret not found: {secret_id}")
        if secret.tenant_id != tenant_id:
            raise SecretAccessDeniedError("secret is not in tenant")
        return secret

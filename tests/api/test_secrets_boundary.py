from datetime import timedelta

import pytest

from taroai.domain import utc_now
from taroai.secrets import (
    InMemorySecretService,
    SecretAccessDeniedError,
    SecretLeaseExpiredError,
    SecretScope,
)


def test_secret_service_issues_scoped_short_lived_leases_without_exposing_secret_values():
    service = InMemorySecretService()
    created_at = utc_now()
    secret = service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="super-secret-api-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
        created_at=created_at,
    )
    lease = service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="crm.lookup",
        actions=["read"],
        ttl_seconds=60,
        now=created_at,
    )

    assert secret.name == "salesforce-api-key"
    assert "super-secret-api-key" not in str(secret.model_dump(mode="json"))
    assert "super-secret-api-key" not in str(lease.model_dump(mode="json"))
    assert service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease.lease_token,
        now=created_at + timedelta(seconds=30),
    ) == "super-secret-api-key"

    audit_metadata = lease.to_audit_metadata()
    assert audit_metadata["secret_ref_id"] == secret.id
    assert audit_metadata["tool_name"] == "crm.lookup"
    assert "super-secret-api-key" not in str(audit_metadata)
    assert lease.lease_token not in str(audit_metadata)

    with pytest.raises(SecretLeaseExpiredError):
        service.resolve_lease_value(
            tenant_id="tenant_acme",
            lease_token=lease.lease_token,
            now=created_at + timedelta(seconds=61),
        )


def test_secret_service_denies_cross_scope_leases_before_exposing_values():
    service = InMemorySecretService()
    secret = service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="super-secret-api-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
    )

    with pytest.raises(SecretAccessDeniedError):
        service.create_lease(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            secret_id=secret.id,
            tool_name="crm.lookup",
            actions=["read"],
            ttl_seconds=60,
        )

    with pytest.raises(SecretAccessDeniedError):
        service.create_lease(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            secret_id=secret.id,
            tool_name="crm.update",
            actions=["write"],
            ttl_seconds=60,
        )

    assert "super-secret-api-key" not in str(service.model_dump(mode="json"))

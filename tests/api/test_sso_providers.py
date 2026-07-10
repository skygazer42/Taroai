from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from taroai.app import create_app
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.licensing import (
    Entitlement,
    LicenseKey,
    LicenseService,
    LicensedFeature,
)
from taroai.sso import (
    InMemorySsoProviderRegistry,
    SsoProviderCreate,
    SsoProviderProtocol,
    SqlSsoProviderRegistry,
)
from taroai.store import InMemoryControlPlaneStore


def create_sso_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sso-admin@example.com",
            display_name="SSO Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sso_admin",
            name="SSO Admin",
            permissions=[
                Permission(action="sso.read", resource="tenant:tenant_acme"),
                Permission(action="sso.manage", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sso_admin")
    return identity, account


def oidc_provider_payload() -> dict:
    return {
        "id": "okta_workforce",
        "display_name": "Okta Workforce",
        "protocol": "oidc",
        "domains": ["Acme.COM"],
        "password_fallback_enabled": False,
        "jit_provisioning_enabled": True,
        "default_role_ids": ["role_employee"],
        "oidc": {
            "issuer_url": "https://idp.acme.com/oauth2/default",
            "client_id": "taroai-client",
            "client_secret_ref_id": "secret_okta_oidc_client",
            "scopes": ["openid", "email", "profile"],
        },
    }


def activated_sso_license() -> LicenseService:
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_sso",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.SSO),
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365),
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    return license_service


def test_sso_provider_model_normalizes_domains_and_rejects_raw_secrets():
    provider = SsoProviderCreate.model_validate(oidc_provider_payload())

    assert provider.protocol == SsoProviderProtocol.OIDC
    assert provider.domains == ["acme.com"]
    assert provider.oidc.client_secret_ref_id == "secret_okta_oidc_client"

    with pytest.raises(ValidationError):
        SsoProviderCreate.model_validate(
            {
                **oidc_provider_payload(),
                "oidc": {
                    **oidc_provider_payload()["oidc"],
                    "client_secret": "raw-secret-value",
                },
            }
        )


def test_in_memory_sso_registry_finds_enabled_provider_by_email_domain():
    registry = InMemorySsoProviderRegistry()

    entry = registry.create_or_update(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        request=SsoProviderCreate.model_validate(oidc_provider_payload()),
    )
    registry.enable("tenant_acme", entry.provider.id)

    assert registry.find_enabled_for_email("tenant_acme", "buyer@acme.com").provider.id == "okta_workforce"
    assert registry.find_enabled_for_email("tenant_acme", "buyer@example.com") is None
    assert registry.find_enabled_for_email("tenant_other", "buyer@acme.com") is None


def test_sso_provider_api_registers_enables_and_audits_with_license():
    identity, account = create_sso_admin_identity()
    store = InMemoryControlPlaneStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            license_service=activated_sso_license(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post("/api/sso/providers", headers=headers, json=oidc_provider_payload())
    enabled = client.post("/api/sso/providers/okta_workforce/enable", headers=headers)
    listed = client.get("/api/sso/providers", headers=headers)
    audits = client.get("/api/audit-events?event_type=sso.provider.enabled", headers=headers)

    assert created.status_code == 201
    assert created.json()["provider"]["domains"] == ["acme.com"]
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"
    assert listed.status_code == 200
    assert [item["provider"]["id"] for item in listed.json()] == ["okta_workforce"]
    assert audits.status_code == 200
    assert audits.json()[0]["metadata"]["provider_id"] == "okta_workforce"
    assert audits.json()[0]["metadata"]["protocol"] == "oidc"
    assert "client_secret" not in str(audits.json())
    assert "raw-secret-value" not in str(audits.json())


def test_sso_provider_api_requires_sso_entitlement_when_enforcement_enabled():
    identity, account = create_sso_admin_identity()
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_without_sso",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365)
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    client = TestClient(
        create_app(
            identity_service=identity,
            license_service=license_service,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    response = client.post("/api/sso/providers", headers=headers, json=oidc_provider_payload())

    assert response.status_code == 403
    assert response.json()["code"] == "license_entitlement_denied"
    assert "sso" in response.json()["message"]


def test_sql_sso_provider_registry_persists_provider_lifecycle(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'sso.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSsoProviderRegistry(config=DatabaseConfig(url=database_url))

    created = registry.create_or_update(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        request=SsoProviderCreate.model_validate(oidc_provider_payload()),
    )
    registry.enable("tenant_acme", created.provider.id)
    restarted = SqlSsoProviderRegistry(config=DatabaseConfig(url=database_url))

    entry = restarted.get_for_tenant("tenant_acme", "okta_workforce")
    by_email = restarted.find_enabled_for_email("tenant_acme", "owner@acme.com")

    assert entry.provider.display_name == "Okta Workforce"
    assert entry.provider.domains == ["acme.com"]
    assert entry.status.value == "enabled"
    assert by_email.provider.id == "okta_workforce"

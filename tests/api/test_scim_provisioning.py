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
from taroai.licensing import Entitlement, LicenseKey, LicenseService, LicensedFeature
from taroai.scim import (
    InMemoryScimProvisioningStore,
    ScimGroupRoleMapping,
    ScimImportRequest,
    ScimProviderCreate,
    ScimProviderStatus,
    ScimProvisioningService,
    SqlScimProvisioningStore,
)
from taroai.store import InMemoryControlPlaneStore


def create_scim_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="scim-admin@example.com",
            display_name="SCIM Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_scim_admin",
            name="SCIM Admin",
            permissions=[
                Permission(action="scim.read", resource="tenant:tenant_acme"),
                Permission(action="scim.manage", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_employee",
            name="Employee",
            permissions=[Permission(action="runs.create", resource="tenant:tenant_acme")],
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sales",
            name="Sales",
            permissions=[Permission(action="knowledge.read", resource="tenant:tenant_acme")],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_scim_admin")
    return identity, account


def scim_provider_payload() -> dict:
    return {
        "id": "okta_scim",
        "display_name": "Okta SCIM",
        "bearer_token_secret_ref_id": "secret_okta_scim_token",
        "default_role_ids": ["role_employee"],
        "jit_create_users": True,
    }


def scim_import_payload(active: bool = True) -> dict:
    return {
        "users": [
            {
                "id": "scim_user_1",
                "userName": "Buyer@Acme.COM",
                "displayName": "Buyer One",
                "active": active,
                "groups": [{"value": "group_sales", "display": "Sales"}],
            }
        ],
        "groups": [
            {
                "id": "group_sales",
                "displayName": "Sales",
                "members": [{"value": "scim_user_1", "display": "Buyer One"}],
            }
        ],
    }


def activated_scim_license() -> LicenseService:
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_scim",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.SCIM),
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365),
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    return license_service


def test_scim_provider_model_rejects_raw_tokens_and_normalizes_roles():
    provider = ScimProviderCreate.model_validate(
        {
            **scim_provider_payload(),
            "default_role_ids": [" role_employee ", "role_employee"],
        }
    )

    assert provider.default_role_ids == ["role_employee"]
    assert provider.bearer_token_secret_ref_id == "secret_okta_scim_token"

    with pytest.raises(ValidationError):
        ScimProviderCreate.model_validate(
            {
                **scim_provider_payload(),
                "bearer_token": "raw-token-value",
            }
        )


def test_scim_service_imports_users_groups_and_disables_departed_accounts():
    identity, _ = create_scim_admin_identity()
    store = InMemoryScimProvisioningStore()
    service = ScimProvisioningService(identity_service=identity, store=store)
    provider = store.create_or_update_provider(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        request=ScimProviderCreate.model_validate(scim_provider_payload()),
    )
    store.enable_provider("tenant_acme", provider.provider.id)
    store.upsert_group_role_mapping(
        tenant_id="tenant_acme",
        provider_id="okta_scim",
        created_by_user_id="user_admin",
        mapping=ScimGroupRoleMapping(
            group_external_id="group_sales",
            role_ids=["role_sales"],
        ),
    )

    result = service.apply_import(
        tenant_id="tenant_acme",
        provider_id="okta_scim",
        imported_by_user_id="user_admin",
        request=ScimImportRequest.model_validate(scim_import_payload()),
    )
    disabled = service.apply_import(
        tenant_id="tenant_acme",
        provider_id="okta_scim",
        imported_by_user_id="user_admin",
        request=ScimImportRequest.model_validate(scim_import_payload(active=False)),
    )

    account = identity.get_user_by_email("tenant_acme", "buyer@acme.com")

    assert result.users_seen == 1
    assert result.users_created == 1
    assert result.roles_assigned == 2
    assert identity.list_role_ids_for_user("tenant_acme", account.id) == [
        "role_employee",
        "role_sales",
    ]
    assert disabled.users_disabled == 1
    assert identity.get_user("tenant_acme", account.id).status == "disabled"
    assert store.get_user_link("tenant_acme", "okta_scim", "scim_user_1").user_id == account.id


def test_scim_api_configures_mapping_imports_and_audits_with_license():
    identity, account = create_scim_admin_identity()
    client = TestClient(
        create_app(
            store=InMemoryControlPlaneStore(),
            identity_service=identity,
            license_service=activated_scim_license(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post("/api/scim/providers", headers=headers, json=scim_provider_payload())
    enabled = client.post("/api/scim/providers/okta_scim/enable", headers=headers)
    mapped = client.post(
        "/api/scim/providers/okta_scim/group-role-mappings",
        headers=headers,
        json={"group_external_id": "group_sales", "role_ids": ["role_sales"]},
    )
    imported = client.post(
        "/api/scim/providers/okta_scim/import",
        headers=headers,
        json=scim_import_payload(),
    )
    listed = client.get("/api/scim/providers", headers=headers)
    audits = client.get("/api/audit-events?event_type=scim.import.completed", headers=headers)

    assert created.status_code == 201
    assert created.json()["provider"]["id"] == "okta_scim"
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"
    assert mapped.status_code == 201
    assert imported.status_code == 201
    assert imported.json()["users_created"] == 1
    assert imported.json()["roles_assigned"] == 2
    assert listed.status_code == 200
    assert [item["provider"]["id"] for item in listed.json()] == ["okta_scim"]
    assert audits.status_code == 200
    assert audits.json()[0]["metadata"]["provider_id"] == "okta_scim"
    assert audits.json()[0]["metadata"]["users_created"] == 1
    assert "raw-token-value" not in str(audits.json())
    assert "bearer_token" not in str(audits.json())


def test_scim_api_requires_scim_entitlement_when_enforcement_enabled():
    identity, account = create_scim_admin_identity()
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_without_scim",
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
    client = TestClient(create_app(identity_service=identity, license_service=license_service))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    response = client.post("/api/scim/providers", headers=headers, json=scim_provider_payload())

    assert response.status_code == 403
    assert response.json()["code"] == "license_entitlement_denied"
    assert "scim" in response.json()["message"]


def test_sql_scim_store_persists_provider_mapping_link_and_import_record(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'scim.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlScimProvisioningStore(config=DatabaseConfig(url=database_url))

    provider = store.create_or_update_provider(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        request=ScimProviderCreate.model_validate(scim_provider_payload()),
    )
    store.enable_provider("tenant_acme", provider.provider.id)
    store.upsert_group_role_mapping(
        tenant_id="tenant_acme",
        provider_id="okta_scim",
        created_by_user_id="user_admin",
        mapping=ScimGroupRoleMapping(group_external_id="group_sales", role_ids=["role_sales"]),
    )
    store.upsert_user_link(
        tenant_id="tenant_acme",
        provider_id="okta_scim",
        external_id="scim_user_1",
        user_id="user_1",
        email="buyer@acme.com",
        active=True,
    )
    store.record_import_result(
        tenant_id="tenant_acme",
        provider_id="okta_scim",
        imported_by_user_id="user_admin",
        result=ScimProvisioningService.empty_result("okta_scim").model_copy(
            update={"users_seen": 1, "users_created": 1}
        ),
    )
    restarted = SqlScimProvisioningStore(config=DatabaseConfig(url=database_url))

    loaded_provider = restarted.get_provider("tenant_acme", "okta_scim")
    mappings = restarted.list_group_role_mappings("tenant_acme", "okta_scim")
    link = restarted.get_user_link("tenant_acme", "okta_scim", "scim_user_1")
    imports = restarted.list_import_records("tenant_acme", "okta_scim")

    assert loaded_provider.status == ScimProviderStatus.ENABLED
    assert mappings[0].mapping.role_ids == ["role_sales"]
    assert link.user_id == "user_1"
    assert imports[0].users_created == 1

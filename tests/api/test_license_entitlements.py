import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from taroai.audit import AuditService
from taroai.app import create_app
from taroai.config import Settings
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
    LicenseEntitlementDeniedError,
    LicenseSignatureVerificationError,
    LicenseSignatureVerifier,
    LicenseService,
    LicenseStatus,
    LicensedFeature,
    SignedLicenseEnvelope,
)
from taroai.store import InMemoryControlPlaneStore


def license_window() -> tuple[datetime, datetime, datetime]:
    issued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    active_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    expires_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    return issued_at, active_at, expires_at


def enterprise_license(**overrides) -> LicenseKey:
    issued_at, _, expires_at = license_window()
    values = {
        "id": "license_acme_enterprise",
        "tenant_id": "tenant_acme",
        "customer_name": "Acme Inc",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "deployment_modes": ["private", "byoc", "air_gapped"],
        "offline_validation_allowed": True,
        "entitlements": [
            Entitlement(feature=LicensedFeature.SSO),
            Entitlement(feature=LicensedFeature.SCIM),
            Entitlement(feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT, limit=5),
            Entitlement(feature=LicensedFeature.SANDBOX_CONCURRENCY, limit=3),
            Entitlement(feature=LicensedFeature.SOLUTION_PACKS),
            Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365),
        ],
    }
    values.update(overrides)
    return LicenseKey(**values)


def sign_license_file(
    tmp_path: Path,
    license_key: LicenseKey,
    key_id: str = "creao-license-2026-01",
) -> tuple[Path, str]:
    envelope, public_key = sign_license_envelope(license_key, key_id=key_id)
    license_file = tmp_path / "signed-license.json"
    license_file.write_text(json.dumps(envelope))
    return license_file, public_key


def sign_license_envelope(
    license_key: LicenseKey,
    key_id: str = "creao-license-2026-01",
) -> tuple[dict, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = license_key.model_dump(mode="json")
    signature_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    envelope = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "payload": payload,
        "signature": base64.b64encode(private_key.sign(signature_payload)).decode("ascii"),
    }
    return envelope, base64.b64encode(public_key).decode("ascii")


def create_license_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="license-admin@example.com",
            display_name="License Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_license_admin",
            name="License Admin",
            permissions=[
                Permission(action="licenses.manage", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_license_admin")
    return identity, account


def create_license_memory_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="license-memory-admin@example.com",
            display_name="License Memory Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_license_memory_admin",
            name="License Memory Admin",
            permissions=[
                Permission(action="licenses.manage", resource="tenant:tenant_acme"),
                Permission(action="memory.write", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_license_memory_admin")
    return identity, account


def test_valid_license_allows_feature_within_entitlement_limit():
    _, active_at, _ = license_window()
    service = LicenseService()

    result = service.validate_license(
        enterprise_license(),
        deployment_mode="private",
        now=active_at,
    )
    decision = service.check_entitlement(
        result,
        LicensedFeature.PRIVATE_CONNECTOR_COUNT,
        requested_amount=4,
        now=active_at,
    )

    assert result.status == LicenseStatus.ACTIVE
    assert decision.allowed is True
    assert decision.limit == 5
    assert decision.requested_amount == 4


def test_expired_license_denies_enterprise_feature_access_and_records_status_change():
    issued_at, active_at, expires_at = license_window()
    store = InMemoryControlPlaneStore()
    service = LicenseService(audit_service=AuditService(store=store))
    license_key = enterprise_license(issued_at=issued_at, expires_at=expires_at)

    service.validate_license(license_key, deployment_mode="private", now=active_at)
    expired = service.validate_license(
        license_key,
        deployment_mode="private",
        now=expires_at + timedelta(seconds=1),
    )
    decision = service.check_entitlement(
        expired,
        LicensedFeature.SSO,
        now=expires_at + timedelta(seconds=1),
    )

    events = service.audit_service.list_for_tenant("tenant_acme")

    assert expired.status == LicenseStatus.EXPIRED
    assert decision.allowed is False
    assert decision.reason == "license status is expired"
    assert [event.event_type for event in events] == [
        "license.status_changed",
        "license.status_changed",
    ]
    assert events[0].metadata["status"] == "active"
    assert events[1].metadata["previous_status"] == "active"
    assert events[1].metadata["status"] == "expired"


def test_missing_entitlement_denies_feature_with_explicit_reason():
    _, active_at, _ = license_window()
    service = LicenseService()
    limited_license = enterprise_license(
        entitlements=[Entitlement(feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT, limit=1)]
    )

    result = service.validate_license(
        limited_license,
        deployment_mode="private",
        now=active_at,
    )
    decision = service.check_entitlement(
        result,
        LicensedFeature.SOLUTION_PACKS,
        now=active_at,
    )

    assert result.status == LicenseStatus.ACTIVE
    assert decision.allowed is False
    assert decision.reason == "missing entitlement for solution_packs"


def test_entitlement_limit_denies_overage():
    _, active_at, _ = license_window()
    service = LicenseService()

    result = service.validate_license(
        enterprise_license(),
        deployment_mode="private",
        now=active_at,
    )
    decision = service.check_entitlement(
        result,
        LicensedFeature.SANDBOX_CONCURRENCY,
        requested_amount=4,
        now=active_at,
    )

    assert decision.allowed is False
    assert decision.limit == 3
    assert decision.reason == "sandbox_concurrency entitlement limit exceeded"


def test_offline_license_file_validation_uses_local_file_contract(tmp_path: Path):
    _, active_at, _ = license_window()
    license_key = enterprise_license()
    license_file = tmp_path / "license.json"
    license_file.write_text(json.dumps(license_key.model_dump(mode="json")))
    service = LicenseService()

    result = service.validate_offline_file(
        license_file,
        deployment_mode="private",
        now=active_at,
    )

    assert result.status == LicenseStatus.ACTIVE
    assert result.source == "offline_file"
    assert result.license.id == "license_acme_enterprise"


def test_signed_offline_license_file_validation_uses_trusted_public_key(tmp_path: Path):
    _, active_at, _ = license_window()
    license_file, public_key = sign_license_file(tmp_path, enterprise_license())
    service = LicenseService(
        signature_verifier=LicenseSignatureVerifier(
            trusted_public_keys={"creao-license-2026-01": public_key}
        )
    )

    result = service.validate_signed_offline_file(
        license_file,
        deployment_mode="private",
        now=active_at,
    )

    assert result.status == LicenseStatus.ACTIVE
    assert result.source == "signed_offline_file"
    assert result.license.id == "license_acme_enterprise"


def test_signed_offline_license_envelope_validation_uses_trusted_public_key():
    _, active_at, _ = license_window()
    envelope, public_key = sign_license_envelope(enterprise_license())
    service = LicenseService(
        signature_verifier=LicenseSignatureVerifier(
            trusted_public_keys={"creao-license-2026-01": public_key}
        )
    )

    result = service.validate_signed_offline_envelope(
        SignedLicenseEnvelope.model_validate(envelope),
        deployment_mode="private",
        now=active_at,
    )

    assert result.status == LicenseStatus.ACTIVE
    assert result.source == "signed_offline_file"
    assert result.license.id == "license_acme_enterprise"


def test_signed_offline_license_rejects_tampered_payload(tmp_path: Path):
    license_file, public_key = sign_license_file(tmp_path, enterprise_license())
    envelope = json.loads(license_file.read_text())
    envelope["payload"]["customer_name"] = "Changed Inc"
    license_file.write_text(json.dumps(envelope))
    service = LicenseService(
        signature_verifier=LicenseSignatureVerifier(
            trusted_public_keys={"creao-license-2026-01": public_key}
        )
    )

    with pytest.raises(
        LicenseSignatureVerificationError,
        match="license signature verification failed",
    ):
        service.validate_signed_offline_file(license_file, deployment_mode="private")


def test_signed_offline_license_rejects_untrusted_key_id(tmp_path: Path):
    license_file, _ = sign_license_file(
        tmp_path,
        enterprise_license(),
        key_id="untrusted-license-key",
    )
    service = LicenseService(
        signature_verifier=LicenseSignatureVerifier(
            trusted_public_keys={"creao-license-2026-01": base64.b64encode(b"0" * 32).decode("ascii")}
        )
    )

    with pytest.raises(
        LicenseSignatureVerificationError,
        match="license signing key is not trusted",
    ):
        service.validate_signed_offline_file(license_file, deployment_mode="private")


def test_signed_offline_license_status_change_audit_omits_signature_material(tmp_path: Path):
    _, active_at, _ = license_window()
    store = InMemoryControlPlaneStore()
    license_file, public_key = sign_license_file(tmp_path, enterprise_license())
    service = LicenseService(
        audit_service=AuditService(store=store),
        signature_verifier=LicenseSignatureVerifier(
            trusted_public_keys={"creao-license-2026-01": public_key}
        ),
    )

    service.validate_signed_offline_file(
        license_file,
        deployment_mode="private",
        now=active_at,
    )

    events = service.audit_service.list_for_tenant("tenant_acme")
    assert len(events) == 1
    assert events[0].metadata["source"] == "signed_offline_file"
    assert "signature" not in events[0].metadata
    assert "public_key" not in events[0].metadata


def test_runtime_license_enforcement_requires_active_tenant_license_when_enabled():
    service = LicenseService(runtime_enforcement_enabled=True)

    with pytest.raises(
        LicenseEntitlementDeniedError,
        match="active license is required for private_connector_count",
    ):
        service.require_entitlement(
            tenant_id="tenant_acme",
            feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT,
            requested_amount=1,
        )


def test_runtime_license_enforcement_uses_activated_validation():
    _, active_at, _ = license_window()
    service = LicenseService(runtime_enforcement_enabled=True)
    validation = service.validate_license(
        enterprise_license(
            entitlements=[Entitlement(feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT, limit=2)]
        ),
        deployment_mode="private",
        now=active_at,
    )

    service.activate_validation(validation)
    allowed = service.require_entitlement(
        tenant_id="tenant_acme",
        feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT,
        requested_amount=2,
        now=active_at,
    )

    assert allowed.allowed is True
    assert allowed.limit == 2


def test_license_service_persists_activated_validation_through_store():
    _, active_at, _ = license_window()
    store = InMemoryControlPlaneStore()
    first_service = LicenseService(
        runtime_enforcement_enabled=True,
        validation_store=store,
    )
    validation = first_service.validate_license(
        enterprise_license(
            entitlements=[Entitlement(feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT, limit=2)]
        ),
        deployment_mode="private",
        now=active_at,
    )

    first_service.activate_validation(validation)
    restarted_service = LicenseService(
        runtime_enforcement_enabled=True,
        validation_store=store,
    )

    allowed = restarted_service.require_entitlement(
        tenant_id="tenant_acme",
        feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT,
        requested_amount=2,
        now=active_at,
    )

    assert restarted_service.get_active_validation("tenant_acme") == validation
    assert allowed.allowed is True
    assert allowed.limit == 2


def test_license_import_api_activates_signed_license_without_leaking_signature_material():
    identity, account = create_license_admin_identity()
    store = InMemoryControlPlaneStore()
    envelope, public_key = sign_license_envelope(enterprise_license())
    signature = envelope["signature"]
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            settings=Settings(
                license_trusted_public_keys={"creao-license-2026-01": public_key},
                _env_file=None,
            ),
        )
    )

    response = client.post(
        "/api/licenses/import",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"deployment_mode": "private", "envelope": envelope},
    )

    body = response.json()
    active_validation = store.get_active_license_validation("tenant_acme")
    audit_events = store.list_audit_events("tenant_acme")
    imported_events = [
        event for event in audit_events if event.event_type == "license.imported"
    ]

    assert response.status_code == 201
    assert body == {
        "license_id": "license_acme_enterprise",
        "tenant_id": "tenant_acme",
        "customer_name": "Acme Inc",
        "status": "active",
        "deployment_mode": "private",
        "source": "signed_offline_file",
            "entitlements_count": 6,
        "activated": True,
    }
    assert active_validation is not None
    assert active_validation.license.id == "license_acme_enterprise"
    assert [event.event_type for event in audit_events] == [
        "license.status_changed",
        "license.imported",
    ]
    assert imported_events[0].metadata["license_id"] == "license_acme_enterprise"
    assert imported_events[0].metadata["status"] == "active"
    assert imported_events[0].metadata["actor"]["user_id"] == account.id
    assert "signature" not in body
    assert "payload" not in body
    assert "public_key" not in str(audit_events)
    assert signature not in str(audit_events)


def test_license_import_api_rejects_cross_tenant_license_before_activation_or_audit():
    identity, account = create_license_admin_identity()
    store = InMemoryControlPlaneStore()
    envelope, public_key = sign_license_envelope(
        enterprise_license(id="license_other", tenant_id="tenant_other")
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            settings=Settings(
                license_trusted_public_keys={"creao-license-2026-01": public_key},
                _env_file=None,
            ),
        )
    )

    response = client.post(
        "/api/licenses/import",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"deployment_mode": "private", "envelope": envelope},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert store.get_active_license_validation("tenant_acme") is None
    assert store.get_active_license_validation("tenant_other") is None
    assert store.list_audit_events("tenant_acme") == []
    assert store.list_audit_events("tenant_other") == []


def test_license_import_api_rejects_disallowed_deployment_without_activation():
    identity, account = create_license_admin_identity()
    store = InMemoryControlPlaneStore()
    envelope, public_key = sign_license_envelope(
        enterprise_license(deployment_modes=["private"])
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            settings=Settings(
                license_trusted_public_keys={"creao-license-2026-01": public_key},
                _env_file=None,
            ),
        )
    )

    response = client.post(
        "/api/licenses/import",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"deployment_mode": "cloud", "envelope": envelope},
    )

    audit_events = store.list_audit_events("tenant_acme")

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert "license does not allow cloud deployment" in response.json()["message"]
    assert store.get_active_license_validation("tenant_acme") is None
    assert [event.event_type for event in audit_events] == ["license.status_changed"]
    assert audit_events[0].metadata["status"] == "invalid"
    assert audit_events[0].metadata["deployment_mode"] == "cloud"
    assert "license.imported" not in [event.event_type for event in audit_events]


def test_license_import_api_enforces_audit_retention_entitlement_for_business_audit():
    identity, account = create_license_memory_admin_identity()
    store = InMemoryControlPlaneStore()
    envelope, public_key = sign_license_envelope(
        enterprise_license(
            entitlements=[
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=30)
            ]
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            settings=Settings(
                audit_retention_days=365,
                license_runtime_enforcement_enabled=True,
                license_trusted_public_keys={"creao-license-2026-01": public_key},
                _env_file=None,
            ),
        )
    )
    import_response = client.post(
        "/api/licenses/import",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"deployment_mode": "private", "envelope": envelope},
    )

    response = client.post(
        "/api/memory/candidates",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "workspace_id": "workspace_sales",
            "scope_type": "team",
            "scope_id": "team_sales",
            "source_run_id": "run_123",
            "content": "Use approved renewal checklist for enterprise accounts.",
            "metadata": {"source": "run_summary"},
            "sensitivity_level": 1,
            "confidence": 0.9,
        },
    )

    assert import_response.status_code == 201
    assert response.status_code == 403
    assert response.json()["code"] == "license_entitlement_denied"
    assert "audit_retention_days entitlement limit exceeded" in response.json()["message"]
    assert "memory.candidate_created" not in [
        event.event_type for event in store.list_audit_events("tenant_acme")
    ]


def test_runtime_license_enforcement_rejects_entitlement_overage():
    _, active_at, _ = license_window()
    service = LicenseService(runtime_enforcement_enabled=True)
    validation = service.validate_license(
        enterprise_license(
            entitlements=[Entitlement(feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT, limit=1)]
        ),
        deployment_mode="private",
        now=active_at,
    )
    service.activate_validation(validation)

    with pytest.raises(
        LicenseEntitlementDeniedError,
        match="private_connector_count entitlement limit exceeded",
    ):
        service.require_entitlement(
            tenant_id="tenant_acme",
            feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT,
            requested_amount=2,
            now=active_at,
        )

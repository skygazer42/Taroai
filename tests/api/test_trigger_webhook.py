import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.triggers import (
    InMemoryTriggerStore,
    TriggerService,
    TriggerWebhookSignatureError,
    TriggerWebhookVerifier,
)


def create_trigger_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="automation-admin@example.com",
            display_name="Automation Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_trigger_admin",
            name="Trigger Admin",
            permissions=[
                Permission(action="triggers.read", resource="tenant:tenant_acme"),
                Permission(action="triggers.manage", resource="tenant:tenant_acme"),
                Permission(action="triggers.invoke", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
                Permission(action="billing.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_trigger_admin")
    return identity, account


def webhook_trigger_payload() -> dict:
    return {
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "type": "webhook",
        "name": "SLA webhook",
        "input_template": {"message": "Review inbound SLA event."},
        "policy_profile": "business-hours",
        "budget_profile": "automation-low",
    }


def signature_for(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=str(timestamp).encode("ascii") + b"." + body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_trigger_webhook_verifier_accepts_signed_raw_body_with_timestamp_tolerance():
    body = b'{"ticket_id":"ticket_123","priority":"high"}'
    timestamp = 1782983100
    verifier = TriggerWebhookVerifier(
        signing_secrets=["webhook_signing_secret"],
        tolerance_seconds=300,
    )

    result = verifier.verify(
        body=body,
        timestamp_header=str(timestamp),
        signature_header=signature_for("webhook_signing_secret", timestamp, body),
        now=datetime.fromtimestamp(timestamp + 120, tz=timezone.utc),
    )

    assert result.verified is True
    assert result.algorithm == "hmac-sha256"
    assert result.body_sha256 == hashlib.sha256(body).hexdigest()
    assert result.timestamp == datetime.fromtimestamp(timestamp, tz=timezone.utc)


def test_trigger_webhook_verifier_rejects_unsigned_and_stale_deliveries():
    body = b'{"ticket_id":"ticket_123"}'
    timestamp = 1782983100
    verifier = TriggerWebhookVerifier(
        signing_secrets=["webhook_signing_secret"],
        tolerance_seconds=60,
    )

    with pytest.raises(TriggerWebhookSignatureError):
        verifier.verify(
            body=body,
            timestamp_header=str(timestamp),
            signature_header=None,
            now=datetime.fromtimestamp(timestamp, tz=timezone.utc),
        )

    with pytest.raises(TriggerWebhookSignatureError):
        verifier.verify(
            body=body,
            timestamp_header=str(timestamp),
            signature_header=signature_for("webhook_signing_secret", timestamp, body),
            now=datetime.fromtimestamp(timestamp + 120, tz=timezone.utc),
        )


def test_signed_webhook_trigger_endpoint_creates_accountable_run_without_user_header():
    identity, account = create_trigger_admin_identity()
    client = TestClient(
        create_app(
            settings=Settings(
                trigger_webhook_signing_secrets=["webhook_signing_secret"],
                trigger_webhook_signature_tolerance_seconds=300,
                _env_file=None,
            ),
            identity_service=identity,
            trigger_service=TriggerService(store=InMemoryTriggerStore()),
        )
    )
    admin_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    created = client.post("/api/triggers", headers=admin_headers, json=webhook_trigger_payload())
    body = json.dumps(
        {"ticket_id": "ticket_123", "priority": "high"},
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp = int(datetime.now(timezone.utc).timestamp())

    invoked = client.post(
        f"/api/triggers/{created.json()['id']}/webhook",
        headers={
            "X-Tenant-ID": "tenant_acme",
            "X-Taroai-Webhook-Timestamp": str(timestamp),
            "X-Taroai-Webhook-Signature": signature_for(
                "webhook_signing_secret",
                timestamp,
                body,
            ),
        },
        content=body,
    )
    run = client.get(f"/api/runs/{invoked.json()['run_id']}", headers=admin_headers)
    audits = client.get(
        "/api/audit-events?event_type=trigger.invoked",
        headers=admin_headers,
    )

    assert invoked.status_code == 202
    assert invoked.json()["trigger_id"] == created.json()["id"]
    assert run.status_code == 200
    assert run.json()["user_id"] == account.id
    assert run.json()["mode"] == "autonomous"
    assert run.json()["message"] == "Review inbound SLA event."
    assert audits.status_code == 200
    audit_metadata = audits.json()[0]["metadata"]
    business_metadata = {
        key: audit_metadata[key]
        for key in [
            "trigger_id",
            "trigger_type",
            "run_id",
            "invocation_payload_keys",
            "webhook_signature_verified",
            "webhook_signature_algorithm",
            "webhook_body_sha256",
        ]
    }
    assert business_metadata == {
        "trigger_id": created.json()["id"],
        "trigger_type": "webhook",
        "run_id": invoked.json()["run_id"],
        "invocation_payload_keys": ["priority", "ticket_id"],
        "webhook_signature_verified": True,
        "webhook_signature_algorithm": "hmac-sha256",
        "webhook_body_sha256": hashlib.sha256(body).hexdigest(),
    }
    assert audit_metadata["audit_retention_days"] == 365
    assert audit_metadata["actor"]["tenant_id"] == "tenant_acme"
    assert "ticket_123" not in str(audits.json())


def test_signed_webhook_trigger_endpoint_rejects_missing_signature():
    identity, account = create_trigger_admin_identity()
    client = TestClient(
        create_app(
            settings=Settings(
                trigger_webhook_signing_secrets=["webhook_signing_secret"],
                _env_file=None,
            ),
            identity_service=identity,
            trigger_service=TriggerService(store=InMemoryTriggerStore()),
        )
    )
    created = client.post(
        "/api/triggers",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json=webhook_trigger_payload(),
    )

    response = client.post(
        f"/api/triggers/{created.json()['id']}/webhook",
        headers={"X-Tenant-ID": "tenant_acme"},
        json={"ticket_id": "ticket_123"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "webhook_signature_invalid"
    assert response.json()["message"] == "webhook signature invalid"


def test_signed_webhook_trigger_endpoint_replays_same_delivery_without_duplicate_run():
    identity, account = create_trigger_admin_identity()
    client = TestClient(
        create_app(
            settings=Settings(
                trigger_webhook_signing_secrets=["webhook_signing_secret"],
                _env_file=None,
            ),
            identity_service=identity,
            trigger_service=TriggerService(store=InMemoryTriggerStore()),
        )
    )
    admin_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    created = client.post("/api/triggers", headers=admin_headers, json=webhook_trigger_payload())
    body = json.dumps(
        {"ticket_id": "ticket_123", "priority": "high"},
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp = int(datetime.now(timezone.utc).timestamp())
    webhook_headers = {
        "X-Tenant-ID": "tenant_acme",
        "X-Taroai-Webhook-Timestamp": str(timestamp),
        "X-Taroai-Webhook-Signature": signature_for(
            "webhook_signing_secret",
            timestamp,
            body,
        ),
        "X-Taroai-Webhook-Delivery-ID": "delivery_123",
    }

    first = client.post(
        f"/api/triggers/{created.json()['id']}/webhook",
        headers=webhook_headers,
        content=body,
    )
    replay = client.post(
        f"/api/triggers/{created.json()['id']}/webhook",
        headers=webhook_headers,
        content=body,
    )
    runs = client.get("/api/runs", headers=admin_headers)
    audits = client.get(
        "/api/audit-events?event_type=trigger.invoked",
        headers=admin_headers,
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json() == first.json()
    assert [run["id"] for run in runs.json()["items"]] == [first.json()["run_id"]]
    assert len(audits.json()) == 1


def test_signed_webhook_trigger_endpoint_rejects_delivery_id_reused_with_different_body():
    identity, account = create_trigger_admin_identity()
    client = TestClient(
        create_app(
            settings=Settings(
                trigger_webhook_signing_secrets=["webhook_signing_secret"],
                _env_file=None,
            ),
            identity_service=identity,
            trigger_service=TriggerService(store=InMemoryTriggerStore()),
        )
    )
    admin_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    created = client.post("/api/triggers", headers=admin_headers, json=webhook_trigger_payload())
    first_body = b'{"ticket_id":"ticket_123"}'
    changed_body = b'{"ticket_id":"ticket_456"}'
    timestamp = int(datetime.now(timezone.utc).timestamp())
    first_headers = {
        "X-Tenant-ID": "tenant_acme",
        "X-Taroai-Webhook-Timestamp": str(timestamp),
        "X-Taroai-Webhook-Signature": signature_for(
            "webhook_signing_secret",
            timestamp,
            first_body,
        ),
        "X-Taroai-Webhook-Delivery-ID": "delivery_123",
    }
    changed_headers = {
        **first_headers,
        "X-Taroai-Webhook-Signature": signature_for(
            "webhook_signing_secret",
            timestamp,
            changed_body,
        ),
    }

    first = client.post(
        f"/api/triggers/{created.json()['id']}/webhook",
        headers=first_headers,
        content=first_body,
    )
    conflict = client.post(
        f"/api/triggers/{created.json()['id']}/webhook",
        headers=changed_headers,
        content=changed_body,
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"

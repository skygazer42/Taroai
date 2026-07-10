from datetime import timedelta

import pytest

from taroai.domain import RunCreate, utc_now
from taroai.store import InMemoryControlPlaneStore
from taroai.support import (
    InMemorySupportAccessService,
    SupportAccessDeniedError,
    SupportAccessScope,
    SupportSessionCreate,
    SupportSessionStatus,
)


def seed_support_run():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_customer",
        payload=RunCreate(
            workspace_id="workspace_ops",
            agent_id="agent_support",
            message="Customer prompt with password=customer-secret and sk-testsupport123456789",
            attachments=["s3://tenant_acme/raw/customer-export.csv"],
            mode="autonomous",
        ),
    )
    store.append_run_event(
        run,
        "tool_call.completed",
        {
            "tool_name": "crm.lookup",
            "tool_input": {
                "prompt": "Customer prompt with password=customer-secret",
                "api_key": "sk-testsupport123456789",
            },
            "result": {"ok": True, "customer_note": "customer-secret"},
        },
    )
    artifact = store.create_artifact(
        tenant_id="tenant_acme",
        run_id=run.id,
        name="report.md",
        artifact_type="markdown",
        uri="s3://tenant_acme/runs/report.md?X-Amz-Signature=customer-secret",
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="model_tokens_input",
        quantity=42,
        unit="tokens",
        metadata={"prompt": "customer-secret"},
        provider="openai-compatible",
        model="enterprise-default",
        cost_estimate=0.12,
    )
    store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        event_type="model.requested",
        metadata={"raw_prompt": "customer-secret", "request_id": "req_123"},
    )
    return store, run, artifact


def test_approved_support_session_builds_redacted_read_only_run_debug_bundle():
    store, run, artifact = seed_support_run()
    service = InMemorySupportAccessService(store=store)
    requested = service.request_session(
        "tenant_acme",
        SupportSessionCreate(
            requested_by_user_id="support_user",
            scope=SupportAccessScope.RUN_DEBUG,
            reason="Investigate failed customer run.",
            expires_at=utc_now() + timedelta(minutes=30),
        ),
    )
    session = service.approve_session(
        "tenant_acme",
        requested.id,
        approved_by_user_id="owner_user",
    )

    bundle = service.build_run_debug_bundle("tenant_acme", session.id, run.id)

    assert session.status == SupportSessionStatus.APPROVED
    assert bundle.session_id == session.id
    assert bundle.run.id == run.id
    assert bundle.run.message == "[REDACTED]"
    assert bundle.run.message_length > 0
    assert bundle.events[-1].type == "audit.recorded"
    assert bundle.events[-1].payload_keys == ["audit_event_id"]
    assert bundle.artifacts[0].id == artifact.id
    assert bundle.artifacts[0].uri == "[REDACTED]"
    assert bundle.billing_summary.meter_count == 2
    assert bundle.billing_summary.quantity_by_meter_type["model_tokens_input"] == 42
    assert bundle.trace_summary.trace_id == run.id
    assert bundle.trace_summary.span_count >= 1
    assert bundle.audit_summary.event_count >= 1
    assert "raw_prompt" in bundle.audit_summary.metadata_keys

    safe_dump = bundle.model_dump_json()
    assert "customer-secret" not in safe_dump
    assert "sk-testsupport" not in safe_dump
    assert "Customer prompt" not in safe_dump
    audit_events = store.list_audit_events("tenant_acme")
    assert audit_events[-1].event_type == "support.run_debug.accessed"
    assert audit_events[-1].metadata == {
        "support_session_id": session.id,
        "run_id": run.id,
        "scope": "run_debug",
        "accessed_by_user_id": "support_user",
        "artifact_count": 1,
        "event_count": len(bundle.events),
    }


def test_expired_support_session_cannot_access_customer_debug_bundle():
    store, run, _artifact = seed_support_run()
    service = InMemorySupportAccessService(store=store)
    session = service.request_session(
        "tenant_acme",
        SupportSessionCreate(
            requested_by_user_id="support_user",
            scope=SupportAccessScope.RUN_DEBUG,
            reason="Expired access.",
            expires_at=utc_now() - timedelta(minutes=1),
        ),
    )
    approved = service.approve_session(
        "tenant_acme",
        session.id,
        approved_by_user_id="owner_user",
    )

    with pytest.raises(SupportAccessDeniedError, match="support session is expired"):
        service.build_run_debug_bundle("tenant_acme", approved.id, run.id)


def test_sensitive_tenant_debugging_requires_approval():
    store, run, _artifact = seed_support_run()
    service = InMemorySupportAccessService(store=store)
    session = service.request_session(
        "tenant_acme",
        SupportSessionCreate(
            requested_by_user_id="support_user",
            scope=SupportAccessScope.TENANT_DEBUG,
            reason="Need sensitive tenant debugging.",
            expires_at=utc_now() + timedelta(minutes=30),
        ),
    )

    with pytest.raises(SupportAccessDeniedError, match="support session is not approved"):
        service.build_run_debug_bundle("tenant_acme", session.id, run.id)


def test_break_glass_support_session_is_time_bound_and_audited():
    store, run, _artifact = seed_support_run()
    service = InMemorySupportAccessService(store=store)

    session = service.break_glass_session(
        tenant_id="tenant_acme",
        requested_by_user_id="support_incident_commander",
        reason="sev1 customer outage",
        expires_at=utc_now() + timedelta(minutes=10),
    )
    bundle = service.build_run_debug_bundle("tenant_acme", session.id, run.id)

    assert session.status == SupportSessionStatus.APPROVED
    assert session.break_glass is True
    assert session.approved_by_user_id == "support_incident_commander"
    assert bundle.session_id == session.id
    break_glass_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "support.session.break_glass"
    ]
    assert len(break_glass_events) == 1
    assert break_glass_events[0].metadata == {
        "support_session_id": session.id,
        "scope": "tenant_debug",
        "requested_by_user_id": "support_incident_commander",
        "reason_code": "sev1_customer_outage",
        "expires_at": session.expires_at.isoformat(),
    }

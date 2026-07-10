from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taroai.agent import AgentRuntime
from taroai.app import create_app
from taroai.audit import (
    AuditActor,
    AuditCoverageRequirement,
    AuditEventCreate,
    AuditService,
    DEFAULT_AUDIT_COVERAGE_REQUIREMENTS,
)
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import AuditEvent, RunCreate, new_id, utc_now
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    SqlIdentityService,
    UserAccountCreate,
)
from taroai.licensing import (
    Entitlement,
    LicenseEntitlementDeniedError,
    LicenseKey,
    LicenseService,
    LicensedFeature,
)
from taroai.model_gateway import PlannedToolCall
from taroai.onboarding import TenantBootstrapRequest, TenantBootstrapService, TenantReadinessService
from taroai.store import InMemoryControlPlaneStore
from tests.api.adapters import DeterministicModelGateway, DeterministicToolGateway


def create_memory_writer_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="memory-writer@example.com",
            display_name="Memory Writer",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_memory_writer",
            name="Memory Writer",
            permissions=[
                Permission(action="memory.write", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_memory_writer")
    return identity, account


def test_audit_service_redacts_sensitive_metadata_and_returns_defensive_copies():
    store = InMemoryControlPlaneStore()
    service = AuditService(store=store)

    event = service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=None,
            event_type="storage.signed_url.created",
            metadata={
                "operation": "read",
                "access_token": "tenant-token",
                "nested": {"password": "plain-password"},
                "items": [{"api_key": "provider-key"}],
            },
        )
    )
    listed = service.list_for_tenant("tenant_acme")
    event.metadata["operation"] = "changed"
    listed[0].metadata["operation"] = "changed-again"

    fresh = service.list_for_tenant("tenant_acme")

    assert fresh[0].metadata["operation"] == "read"
    assert fresh[0].metadata["access_token"] == "[REDACTED]"
    assert fresh[0].metadata["nested"]["password"] == "[REDACTED]"
    assert fresh[0].metadata["items"][0]["api_key"] == "[REDACTED]"
    assert "tenant-token" not in str(fresh)
    assert "plain-password" not in str(fresh)
    assert "provider-key" not in str(fresh)


def test_audit_service_records_pydantic_actor_attribution():
    store = InMemoryControlPlaneStore()
    service = AuditService(store=store)

    service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            event_type="memory.candidate_created",
            metadata={"memory_id": "memory_1"},
            actor=AuditActor(
                tenant_id="tenant_acme",
                user_id="user_1",
                actor_type="user",
                ip_address="203.0.113.42",
                user_agent="Taroai Admin Console",
            ),
        )
    )

    recorded = service.list_for_tenant("tenant_acme")[0]

    assert recorded.metadata["actor"] == {
        "tenant_id": "tenant_acme",
        "user_id": "user_1",
        "actor_type": "user",
        "ip_address": "203.0.113.42",
        "user_agent": "Taroai Admin Console",
    }


def test_audit_service_adds_policy_retention_metadata():
    store = InMemoryControlPlaneStore()
    service = AuditService(store=store, retention_days=30)

    recorded = service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            event_type="memory.candidate_created",
            metadata={"memory_id": "memory_1"},
        )
    )

    expires_at = datetime.fromisoformat(recorded.metadata["audit_retention_expires_at"])

    assert recorded.metadata["audit_retention_days"] == 30
    assert expires_at.tzinfo is not None
    assert expires_at - recorded.created_at > timedelta(days=29)
    assert expires_at - recorded.created_at <= timedelta(days=31)


def test_audit_service_enforces_license_retention_limit():
    store = InMemoryControlPlaneStore()
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_limited_audit",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            offline_validation_allowed=True,
            entitlements=[
                Entitlement(
                    feature=LicensedFeature.AUDIT_RETENTION_DAYS,
                    limit=90,
                )
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    service = AuditService(
        store=store,
        retention_days=365,
        license_service=license_service,
    )

    with pytest.raises(
        LicenseEntitlementDeniedError,
        match="audit_retention_days entitlement limit exceeded",
    ):
        service.record(
            AuditEventCreate(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                event_type="memory.candidate_created",
                metadata={"memory_id": "memory_1"},
            )
        )

    assert store.list_audit_events("tenant_acme") == []


def test_audit_service_allows_license_events_before_active_license():
    store = InMemoryControlPlaneStore()
    service = AuditService(
        store=store,
        retention_days=365,
        license_service=LicenseService(runtime_enforcement_enabled=True),
    )

    recorded = service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            event_type="license.status_changed",
            metadata={
                "license_id": "license_acme_enterprise",
                "status": "active",
                "deployment_mode": "private",
            },
        )
    )

    assert recorded.event_type == "license.status_changed"
    assert service.list_for_tenant("tenant_acme")[0].event_type == "license.status_changed"


def test_audit_service_reports_required_event_coverage_by_metadata_keys():
    store = InMemoryControlPlaneStore()
    service = AuditService(store=store)
    service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            event_type="identity.user.created",
            metadata={"user_id": "user_1", "status": "active"},
        )
    )
    service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            event_type="tool.executed",
            metadata={"tool_name": "crm.lookup"},
        )
    )
    service.record(
        AuditEventCreate(
            tenant_id="tenant_acme",
            event_type="storage.signed_url.created",
            metadata={"object_id": "object_1"},
        )
    )

    report = service.check_coverage(
        "tenant_acme",
        [
            AuditCoverageRequirement(
                area="identity",
                event_type="identity.user.created",
                required_metadata_keys={"user_id", "status"},
            ),
            AuditCoverageRequirement(
                area="tool",
                event_type="tool.executed",
                required_metadata_keys={"tool_name"},
            ),
            AuditCoverageRequirement(
                area="storage",
                event_type="storage.signed_url.created",
                required_metadata_keys={"object_id", "operation"},
            ),
            AuditCoverageRequirement(
                area="rbac",
                event_type="identity.role.assigned",
                required_metadata_keys={"assigned_user_id", "role_id"},
            ),
        ],
    )

    assert report.tenant_id == "tenant_acme"
    assert report.is_complete is False
    assert report.total_requirements == 4
    assert report.covered_event_types == ["identity.user.created", "tool.executed"]
    assert [finding.event_type for finding in report.missing_requirements] == [
        "storage.signed_url.created",
        "identity.role.assigned",
    ]
    assert report.missing_requirements[0].missing_metadata_keys == ["operation"]
    assert report.missing_requirements[1].missing_metadata_keys == [
        "assigned_user_id",
        "role_id",
    ]


def test_default_audit_coverage_matrix_names_enterprise_sensitive_actions():
    requirements_by_event = {
        requirement.event_type: requirement
        for requirement in DEFAULT_AUDIT_COVERAGE_REQUIREMENTS
    }

    expected_event_types = {
        "identity.user.created",
        "identity.user.disabled",
        "run.cancelled",
        "run.retry_requested",
        "identity.role.created",
        "identity.role.assigned",
        "knowledge.query.executed",
        "embedding.gateway.called",
        "memory.candidate_created",
        "tool.executed",
        "tool.approval_required",
        "approval.resolved",
        "approval.rejected",
        "storage.signed_url.created",
        "storage.uploaded",
        "sandbox.command.executed",
        "browser.action.performed",
        "billing.metered",
        "skill.published",
        "license.status_changed",
        "license.imported",
    }

    assert expected_event_types.issubset(requirements_by_event)
    assert requirements_by_event["identity.user.created"].area == "identity"
    assert "user_id" in requirements_by_event["identity.user.created"].required_metadata_keys
    assert requirements_by_event["identity.role.assigned"].area == "rbac"
    assert "assigned_user_id" in requirements_by_event["identity.role.assigned"].required_metadata_keys
    assert requirements_by_event["tool.executed"].area == "tool"
    assert requirements_by_event["embedding.gateway.called"].area == "embedding"
    assert "purpose" in requirements_by_event["embedding.gateway.called"].required_metadata_keys
    assert "input_count" in requirements_by_event["embedding.gateway.called"].required_metadata_keys
    assert requirements_by_event["license.status_changed"].area == "license"
    assert "license_id" in requirements_by_event["license.status_changed"].required_metadata_keys
    assert "status" in requirements_by_event["license.status_changed"].required_metadata_keys
    assert requirements_by_event["license.imported"].area == "license"
    assert "license_id" in requirements_by_event["license.imported"].required_metadata_keys
    assert "status" in requirements_by_event["license.imported"].required_metadata_keys


class RecordingAuditService:
    def __init__(self):
        self.events: list[AuditEventCreate] = []

    def record(self, event: AuditEventCreate) -> AuditEvent:
        self.events.append(event)
        return AuditEvent(
            id=new_id("audit"),
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            user_id=event.user_id,
            run_id=event.run_id,
            event_type=event.event_type,
            metadata=event.metadata,
            created_at=utc_now(),
        )


def test_app_records_business_audit_events_through_audit_service():
    identity, account = create_memory_writer_identity()
    audit_service = RecordingAuditService()
    client = TestClient(create_app(identity_service=identity, audit_service=audit_service))

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

    assert response.status_code == 201
    assert [event.event_type for event in audit_service.events] == ["memory.candidate_created"]
    assert [event.tenant_id for event in audit_service.events] == ["tenant_acme"]
    assert [event.workspace_id for event in audit_service.events] == ["workspace_sales"]
    assert [event.user_id for event in audit_service.events] == [account.id]


def test_agent_runtime_records_model_and_tool_audit_through_audit_service():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales",
            message="Create a prospect brief.",
            mode="workflow",
        ),
    )
    audit_service = RecordingAuditService()
    runtime = AgentRuntime(
        store=store,
        audit_service=audit_service,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect"},
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    runtime.execute_run("tenant_acme", run.id)

    assert [event.event_type for event in audit_service.events] == [
        "model.plan.created",
        "tool.executed",
    ]
    assert [event.run_id for event in audit_service.events] == [run.id, run.id]
    assert [event.actor.user_id for event in audit_service.events] == ["user_1", "user_1"]
    assert [event.actor.actor_type for event in audit_service.events] == ["user", "user"]
    assert [event.actor.tenant_id for event in audit_service.events] == [
        "tenant_acme",
        "tenant_acme",
    ]


def test_tenant_bootstrap_records_audit_through_audit_service():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    store = InMemoryControlPlaneStore()
    settings = Settings(tenant_bootstrap_token="bootstrap_secret", _env_file=None)
    readiness_service = TenantReadinessService(
        identity_service=identity,
        store=store,
        settings=settings,
        job_queue=None,
    )
    audit_service = RecordingAuditService()
    service = TenantBootstrapService(
        identity_service=identity,
        store=store,
        settings=settings,
        readiness_service=readiness_service,
        audit_service=audit_service,
    )

    result = service.bootstrap(
        TenantBootstrapRequest(
            tenant_id="tenant_acme",
            owner_email="owner@example.com",
            owner_display_name="Owner",
            owner_password="correct horse battery staple",
        ),
        bootstrap_token="bootstrap_secret",
    )

    assert result.owner_user_id.startswith("user_")
    assert [event.event_type for event in audit_service.events] == [
        "tenant.bootstrap.completed"
    ]
    assert audit_service.events[0].metadata["owner_user_id"] == result.owner_user_id


def test_in_memory_identity_service_records_user_and_role_audit_events():
    audit_service = RecordingAuditService()
    identity = InMemoryIdentityService(
        password_hasher=PasswordHasher(salt="test_salt"),
        audit_service=audit_service,
    )

    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="rbac-admin@example.com",
            display_name="RBAC Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_rbac_admin",
            name="RBAC Admin",
            permissions=[
                Permission(action="skills.publish", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_rbac_admin")
    identity.disable_user("tenant_acme", account.id)

    assert [event.event_type for event in audit_service.events] == [
        "identity.user.created",
        "identity.role.created",
        "identity.role.assigned",
        "identity.user.disabled",
    ]
    assert audit_service.events[0].metadata["user_id"] == account.id
    assert audit_service.events[0].metadata["email"] == "rbac-admin@example.com"
    assert audit_service.events[1].metadata["role_id"] == "role_rbac_admin"
    assert audit_service.events[1].metadata["permissions_count"] == 2
    assert audit_service.events[2].metadata["assigned_user_id"] == account.id
    assert audit_service.events[3].metadata["status"] == "disabled"
    assert "correct horse battery staple" not in str(audit_service.events)
    assert "password_hash" not in str(audit_service.events)


def test_sql_identity_service_records_user_and_role_audit_events(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    audit_service = RecordingAuditService()
    identity = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
        audit_service=audit_service,
    )

    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="sql-rbac-admin@example.com",
            display_name="SQL RBAC Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_sql_rbac_admin",
            name="SQL RBAC Admin",
            permissions=[
                Permission(action="storage.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_sql_rbac_admin")
    identity.disable_user("tenant_acme", account.id)

    assert [event.event_type for event in audit_service.events] == [
        "identity.user.created",
        "identity.role.created",
        "identity.role.assigned",
        "identity.user.disabled",
    ]
    assert audit_service.events[0].metadata["user_id"] == account.id
    assert audit_service.events[1].metadata["role_id"] == "role_sql_rbac_admin"
    assert audit_service.events[2].metadata["assigned_user_id"] == account.id
    assert audit_service.events[3].metadata["status"] == "disabled"
    assert "correct horse battery staple" not in str(audit_service.events)

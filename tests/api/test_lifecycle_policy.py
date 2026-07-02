from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.lifecycle import (
    DataCategory,
    DeletionBehavior,
    InMemoryLifecyclePolicyStore,
    LegalHoldCreate,
    LegalHoldScopeType,
    LifecyclePolicyCreate,
    SqlLifecyclePolicyStore,
)


def test_lifecycle_policy_models_define_enterprise_retention_rules():
    policy = LifecyclePolicyCreate(
        tenant_id="tenant_acme",
        category=DataCategory.STORAGE_OBJECT,
        retention_days=365,
        deletion_behavior=DeletionBehavior.TOMBSTONE,
        exportable=True,
        residency_region="us-east-1",
        backup_class="standard",
        legal_hold_supported=True,
    )

    assert policy.category == DataCategory.STORAGE_OBJECT
    assert policy.deletion_behavior == DeletionBehavior.TOMBSTONE
    assert policy.legal_hold_supported is True

    with pytest.raises(ValidationError):
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            category=DataCategory.AUDIT,
            retention_days=0,
            deletion_behavior=DeletionBehavior.RETAIN,
            exportable=False,
            residency_region="us-east-1",
            backup_class="archive",
            legal_hold_supported=True,
        )


def test_lifecycle_policy_store_tracks_active_legal_holds_by_scope():
    store = InMemoryLifecyclePolicyStore()
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    hold = store.create_legal_hold(
        LegalHoldCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            scope_type=LegalHoldScopeType.STORAGE_OBJECT,
            scope_id="storage_123",
            reason="customer litigation hold",
            created_by_user_id="compliance_admin",
            expires_at=now + timedelta(days=30),
        )
    )

    assert store.is_under_legal_hold(
        tenant_id="tenant_acme",
        category=DataCategory.STORAGE_OBJECT,
        scope_type=LegalHoldScopeType.STORAGE_OBJECT,
        scope_id="storage_123",
        now=now,
    )
    assert store.list_active_legal_holds(
        tenant_id="tenant_acme",
        category=DataCategory.STORAGE_OBJECT,
        scope_type=LegalHoldScopeType.STORAGE_OBJECT,
        scope_id="storage_123",
        now=now,
    ) == [hold]
    assert not store.is_under_legal_hold(
        tenant_id="tenant_acme",
        category=DataCategory.STORAGE_OBJECT,
        scope_type=LegalHoldScopeType.STORAGE_OBJECT,
        scope_id="storage_123",
        now=now + timedelta(days=31),
    )


def test_lifecycle_policy_store_resolves_workspace_override_before_tenant_default():
    store = InMemoryLifecyclePolicyStore()
    tenant_default = store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=365,
            deletion_behavior=DeletionBehavior.TOMBSTONE,
            exportable=True,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )
    workspace_override = store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=30,
            deletion_behavior=DeletionBehavior.HARD_DELETE,
            exportable=False,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )

    assert store.get_policy(
        "tenant_acme",
        DataCategory.STORAGE_OBJECT,
        workspace_id="workspace_sales",
    ) == workspace_override
    assert store.resolve_policy(
        "tenant_acme",
        DataCategory.STORAGE_OBJECT,
        workspace_id="workspace_sales",
    ) == workspace_override
    assert store.resolve_policy(
        "tenant_acme",
        DataCategory.STORAGE_OBJECT,
        workspace_id="workspace_support",
    ) == tenant_default


def test_sql_lifecycle_policy_store_persists_policies_and_legal_holds(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlLifecyclePolicyStore(config=DatabaseConfig(url=database_url))
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    policy = store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=180,
            deletion_behavior=DeletionBehavior.TOMBSTONE,
            exportable=True,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )
    hold = store.create_legal_hold(
        LegalHoldCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            scope_type=LegalHoldScopeType.WORKSPACE,
            scope_id="workspace_sales",
            reason="workspace investigation",
            created_by_user_id="compliance_admin",
            expires_at=now + timedelta(days=30),
        )
    )
    restarted = SqlLifecyclePolicyStore(config=DatabaseConfig(url=database_url))

    persisted_policy = restarted.get_policy("tenant_acme", DataCategory.STORAGE_OBJECT)
    active_holds = restarted.list_active_legal_holds(
        tenant_id="tenant_acme",
        category=DataCategory.STORAGE_OBJECT,
        scope_type=LegalHoldScopeType.WORKSPACE,
        scope_id="workspace_sales",
        now=now,
    )

    assert persisted_policy == policy
    assert active_holds == [hold]
    assert restarted.is_under_legal_hold(
        tenant_id="tenant_acme",
        category=DataCategory.STORAGE_OBJECT,
        scope_type=LegalHoldScopeType.WORKSPACE,
        scope_id="workspace_sales",
        now=now,
    )


def test_sql_lifecycle_policy_store_persists_workspace_overrides(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlLifecyclePolicyStore(config=DatabaseConfig(url=database_url))
    tenant_default = store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=365,
            deletion_behavior=DeletionBehavior.TOMBSTONE,
            exportable=True,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )
    workspace_override = store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=30,
            deletion_behavior=DeletionBehavior.HARD_DELETE,
            exportable=False,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )
    restarted = SqlLifecyclePolicyStore(config=DatabaseConfig(url=database_url))

    assert restarted.get_policy(
        "tenant_acme",
        DataCategory.STORAGE_OBJECT,
        workspace_id="workspace_sales",
    ) == workspace_override
    assert restarted.resolve_policy(
        "tenant_acme",
        DataCategory.STORAGE_OBJECT,
        workspace_id="workspace_sales",
    ) == workspace_override
    assert restarted.resolve_policy(
        "tenant_acme",
        DataCategory.STORAGE_OBJECT,
        workspace_id="workspace_support",
    ) == tenant_default

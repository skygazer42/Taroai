from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from taroai.domain import utc_now
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.storage import SqlStorageCatalog, StorageObjectCreate, StoragePurpose
from taroai.store import NotFoundError


def test_sql_storage_catalog_persists_objects_across_instances(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    restarted = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    listed = restarted.list_for_run("tenant_acme", "run_123")
    fetched = restarted.get("tenant_acme", stored.id)

    assert listed == [stored]
    assert fetched == stored
    assert fetched.uri == (
        "s3://taroai-artifacts/"
        "tenant_acme/workspace_sales/runs/run_123/artifacts/agent-result.md"
    )
    assert fetched.content_type == "text/markdown"
    assert fetched.size_bytes == 128


def test_sql_storage_catalog_persists_internal_objects_without_run_scope(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id=None,
            purpose=StoragePurpose.KNOWLEDGE_DOCUMENT,
            filename="sales.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    restarted = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    fetched = restarted.get("tenant_acme", stored.id)

    assert fetched == stored
    assert fetched.run_id is None
    assert restarted.list_for_run("tenant_acme", "run_123") == []
    assert fetched.uri == (
        "s3://taroai-artifacts/"
        "tenant_acme/workspace_sales/knowledge-documents/sales.md"
    )


def test_sql_storage_catalog_persists_object_acl_and_sensitivity(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="sales-plan.md",
            content_type="text/markdown",
            size_bytes=128,
            acl_subjects=["team:sales"],
            sensitivity_level=2,
        )
    )
    restarted = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    fetched = restarted.get("tenant_acme", stored.id)

    assert fetched.acl_subjects == ["team:sales"]
    assert fetched.sensitivity_level == 2


def test_sql_storage_catalog_updates_uploaded_object_size(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )
    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.SANDBOX_SNAPSHOT,
            filename="snapshot.json",
            content_type="application/json",
            size_bytes=0,
        )
    )

    updated = catalog.mark_uploaded(
        tenant_id="tenant_acme",
        storage_object_id=stored.id,
        size_bytes=256,
    )
    restarted = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    assert updated.size_bytes == 256
    assert restarted.get("tenant_acme", stored.id).size_bytes == 256


def test_sql_storage_catalog_get_query_sets_tenant_context(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )
    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.SANDBOX_SNAPSHOT,
            filename="snapshot.json",
            content_type="application/json",
            size_bytes=0,
        )
    )

    fetched = catalog.get("tenant_acme", stored.id)

    assert fetched == stored


def test_sql_storage_catalog_get_uses_tenant_scoped_lookup(monkeypatch):
    executed_sql: list[str] = []
    now = datetime(2026, 7, 3, 13, 50, tzinfo=timezone.utc)

    class Result:
        def fetchone(self):
            return {
                "id": "storage_1",
                "tenant_id": "tenant_acme",
                "workspace_id": "workspace_sales",
                "run_id": "run_123",
                "purpose": StoragePurpose.SANDBOX_SNAPSHOT.value,
                "filename": "snapshot.json",
                "content_type": "application/json",
                "size_bytes": 256,
                "acl_subjects": [],
                "sensitivity_level": 0,
                "bucket": "taroai-artifacts",
                "object_key": "tenant_acme/workspace_sales/runs/run_123/sandbox-snapshots/snapshot.json",
                "retention_expires_at": None,
                "deleted_at": None,
                "created_at": now,
            }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, error_type, error, traceback):
            return None

        def execute(self, sql, params):
            executed_sql.append(" ".join(sql.split()))
            return Result()

    monkeypatch.setattr(
        "taroai.storage.repository.connect_database",
        lambda _config: Connection(),
    )

    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url="postgresql://example"),
        bucket="taroai-artifacts",
    )

    catalog.get("tenant_acme", "storage_1")

    assert executed_sql == ["SELECT * FROM storage_objects WHERE tenant_id = ? AND id = ?"]


def test_sql_storage_catalog_hydrates_postgresql_native_values(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    created_at = utc_now()
    retention_expires_at = utc_now()
    with SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )._connect() as connection:
        connection.execute(
            """
            INSERT INTO storage_objects (
                id, tenant_id, workspace_id, run_id, purpose, filename,
                content_type, size_bytes, acl_subjects, sensitivity_level,
                bucket, object_key, retention_expires_at, deleted_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "storage_native",
                "tenant_acme",
                "workspace_sales",
                "run_123",
                StoragePurpose.ARTIFACT.value,
                "native.md",
                "text/markdown",
                128,
                '["team:sales"]',
                2,
                "taroai-artifacts",
                "tenant_acme/workspace_sales/runs/run_123/artifacts/native.md",
                retention_expires_at.isoformat(),
                None,
                created_at.isoformat(),
            ),
        )
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )
    row = {
        "id": "storage_native",
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
        "run_id": "run_123",
        "purpose": StoragePurpose.ARTIFACT.value,
        "filename": "native.md",
        "content_type": "text/markdown",
        "size_bytes": 128,
        "acl_subjects": ["team:sales"],
        "sensitivity_level": 2,
        "bucket": "taroai-artifacts",
        "object_key": "tenant_acme/workspace_sales/runs/run_123/artifacts/native.md",
        "retention_expires_at": retention_expires_at,
        "deleted_at": None,
        "created_at": created_at,
    }
    hydrated = catalog._from_row(row)

    assert hydrated.acl_subjects == ["team:sales"]
    assert hydrated.retention_expires_at == retention_expires_at
    assert hydrated.created_at == created_at


def test_sql_storage_catalog_enforces_tenant_boundary(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="input.csv",
            content_type="text/csv",
            size_bytes=2048,
        )
    )

    assert catalog.list_for_run("tenant_other", "run_123") == []
    with pytest.raises(NotFoundError):
        catalog.get("tenant_other", stored.id)


def test_sql_storage_catalog_marks_deleted_objects_and_hides_them_from_active_reads(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )
    retention_expires_at = utc_now()

    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="input.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=retention_expires_at,
        )
    )
    deleted = catalog.mark_deleted(
        tenant_id="tenant_acme",
        storage_object_id=stored.id,
        deleted_at=utc_now(),
    )
    restarted = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    assert deleted.deleted_at is not None
    assert deleted.retention_expires_at == retention_expires_at
    assert restarted.list_for_run("tenant_acme", "run_123") == []
    with pytest.raises(NotFoundError):
        restarted.get("tenant_acme", stored.id)


def test_sql_storage_catalog_lists_expired_retention_objects_by_tenant(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    expired = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="expired.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(seconds=1),
        )
    )
    future = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="future.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now + timedelta(days=1),
        )
    )
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_support",
            run_id="run_999",
            purpose=StoragePurpose.UPLOAD,
            filename="other.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=1),
        )
    )
    no_retention = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="no-retention.csv",
            content_type="text/csv",
            size_bytes=2048,
        )
    )
    deleted = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="deleted.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=2),
        )
    )
    catalog.mark_deleted(
        tenant_id="tenant_acme",
        storage_object_id=deleted.id,
        deleted_at=now,
    )

    listed = catalog.list_expired_for_retention("tenant_acme", now)

    assert [storage_object.id for storage_object in listed] == [expired.id]
    assert future.id not in [storage_object.id for storage_object in listed]
    assert no_retention.id not in [storage_object.id for storage_object in listed]


def test_sql_storage_catalog_lists_active_objects_by_tenant_workspace_and_run_scope(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    catalog = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )
    sales_run_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    sales_internal_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id=None,
            purpose=StoragePurpose.KNOWLEDGE_DOCUMENT,
            filename="source.md",
            content_type="text/markdown",
            size_bytes=256,
        )
    )
    support_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            run_id="run_456",
            purpose=StoragePurpose.UPLOAD,
            filename="support.csv",
            content_type="text/csv",
            size_bytes=512,
        )
    )
    deleted_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_deleted",
            purpose=StoragePurpose.UPLOAD,
            filename="deleted.csv",
            content_type="text/csv",
            size_bytes=1024,
        )
    )
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            run_id="run_999",
            purpose=StoragePurpose.UPLOAD,
            filename="other.csv",
            content_type="text/csv",
            size_bytes=2048,
        )
    )
    catalog.mark_deleted("tenant_acme", deleted_object.id, utc_now())
    restarted = SqlStorageCatalog(
        config=DatabaseConfig(url=database_url),
        bucket="taroai-artifacts",
    )

    assert restarted.list_active("tenant_acme") == [
        sales_run_object,
        sales_internal_object,
        support_object,
    ]
    assert restarted.list_active("tenant_acme", workspace_id="workspace_sales") == [
        sales_run_object,
        sales_internal_object,
    ]
    assert restarted.list_active("tenant_acme", run_id="run_123") == [sales_run_object]
    assert restarted.list_active("tenant_other") != restarted.list_active("tenant_acme")

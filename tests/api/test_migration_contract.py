from pathlib import Path
import sqlite3

from taroai.db import DatabaseConfig, MigrationRunner


def test_initial_migration_declares_core_phase_one_tables():
    migration = Path("apps/api/migrations/001_initial.sql")

    sql = migration.read_text()

    for table_name in [
        "tenants",
        "workspaces",
        "users",
        "roles",
        "role_assignments",
        "runs",
        "run_events",
        "artifacts",
        "storage_objects",
        "knowledge_bases",
        "knowledge_documents",
        "knowledge_chunks",
        "approval_requests",
        "memory_records",
        "short_term_memory_reviews",
        "skill_registry_entries",
        "skill_registry_versions",
        "skill_installations",
        "audit_events",
        "billing_meter_events",
        "runtime_states",
        "lifecycle_policies",
        "legal_holds",
        "model_policy_scopes",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql


def test_runtime_tables_include_tenant_and_run_boundaries():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()

    for table_name in [
        "runs",
        "run_events",
        "artifacts",
        "storage_objects",
        "knowledge_bases",
        "knowledge_documents",
        "knowledge_chunks",
        "approval_requests",
        "memory_records",
        "short_term_memory_reviews",
        "skill_registry_entries",
        "skill_registry_versions",
        "skill_installations",
        "audit_events",
        "billing_meter_events",
        "lifecycle_policies",
        "legal_holds",
        "model_policy_scopes",
    ]:
        table_start = sql.index(f"CREATE TABLE IF NOT EXISTS {table_name}")
        table_end = sql.index(");", table_start)
        table_sql = sql[table_start:table_end]
        assert "tenant_id TEXT NOT NULL" in table_sql


def test_users_store_password_hash_not_plain_password():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS users")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "password_hash TEXT NOT NULL" in table_sql
    assert "password TEXT" not in table_sql

    for table_name in ["run_events", "artifacts", "audit_events", "billing_meter_events"]:
        table_start = sql.index(f"CREATE TABLE IF NOT EXISTS {table_name}")
        table_end = sql.index(");", table_start)
        table_sql = sql[table_start:table_end]
        assert "run_id TEXT" in table_sql


def test_storage_objects_include_lifecycle_columns():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS storage_objects")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "run_id TEXT REFERENCES runs(id)" in table_sql
    assert "run_id TEXT NOT NULL REFERENCES runs(id)" not in table_sql
    assert "acl_subjects JSONB NOT NULL DEFAULT '[]'::jsonb" in table_sql
    assert "sensitivity_level INTEGER NOT NULL DEFAULT 0" in table_sql
    assert "retention_expires_at TIMESTAMPTZ" in table_sql
    assert "deleted_at TIMESTAMPTZ" in table_sql


def test_short_term_memory_review_migration_applies_after_existing_initial_schema(tmp_path):
    sqlite_path = tmp_path / "upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            ("001_initial.sql",),
        )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'short_term_memory_reviews'
            """
        ).fetchone()

    assert row is not None


def test_knowledge_documents_include_managed_source_object_reference():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS knowledge_documents")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "storage_object_id TEXT REFERENCES storage_objects(id)" in table_sql


def test_model_policy_scope_migration_applies_after_existing_initial_schema(tmp_path):
    sqlite_path = tmp_path / "model_policy_upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            ("001_initial.sql",),
        )
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            ("002_short_term_memory_reviews.sql",),
        )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'model_policy_scopes'
            """
        ).fetchone()

    assert row is not None

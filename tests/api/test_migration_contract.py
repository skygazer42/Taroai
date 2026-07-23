from pathlib import Path
import sqlite3

import pytest

from taroai.db import DatabaseConfig, MigrationRunner


def test_build_migration_plan_script_wraps_python_cli():
    script = Path("scripts/build-migration-plan.sh")

    text = script.read_text()

    assert "python -m taroai.db.migration_cli" in text
    assert "--database-url" in text
    assert "--migrations-path" in text
    assert "TAROAI_DATABASE_URL" in text


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
        "solution_pack_entries",
        "solution_pack_versions",
        "solution_pack_installations",
        "customer_feedback_records",
        "customer_feedback_evaluation_candidates",
        "customer_solution_pack_feedback_candidates",
        "customer_feedback_evaluation_cases",
        "customer_solution_pack_publication_drafts",
        "sso_provider_configs",
        "scim_provider_configs",
        "scim_group_role_mappings",
        "scim_user_links",
        "scim_import_records",
        "connector_definitions",
        "trigger_definitions",
        "audit_events",
        "billing_meter_events",
        "billing_pricing_rules",
        "billing_invoices",
        "share_grants",
        "runtime_states",
        "lifecycle_policies",
        "legal_holds",
        "restore_drill_schedules",
        "restore_drill_runs",
        "model_policy_scopes",
        "model_policy_change_requests",
        "model_policy_versions",
        "model_provider_records",
        "model_provider_versions",
        "model_provider_change_requests",
        "model_provider_rate_limit_samples",
        "idempotency_records",
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
        "solution_pack_entries",
        "solution_pack_versions",
        "solution_pack_installations",
        "customer_feedback_records",
        "customer_feedback_evaluation_candidates",
        "customer_solution_pack_feedback_candidates",
        "customer_feedback_evaluation_cases",
        "customer_solution_pack_publication_drafts",
        "sso_provider_configs",
        "scim_provider_configs",
        "scim_group_role_mappings",
        "scim_user_links",
        "scim_import_records",
        "connector_definitions",
        "trigger_definitions",
        "audit_events",
        "billing_meter_events",
        "billing_pricing_rules",
        "billing_invoices",
        "share_grants",
        "lifecycle_policies",
        "legal_holds",
        "restore_drill_schedules",
        "restore_drill_runs",
        "model_policy_scopes",
        "model_policy_change_requests",
        "model_policy_versions",
        "model_provider_records",
        "model_provider_versions",
        "model_provider_change_requests",
        "model_provider_rate_limit_samples",
        "idempotency_records",
    ]:
        table_start = sql.index(f"CREATE TABLE IF NOT EXISTS {table_name}")
        table_end = sql.index(");", table_start)
        table_sql = sql[table_start:table_end]
        assert "tenant_id TEXT NOT NULL" in table_sql


def test_run_events_include_sequence_for_sse_replay():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS run_events")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "sequence INTEGER NOT NULL" in table_sql


def test_idempotency_records_include_route_hash_and_unique_key_scope():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS idempotency_records")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "key TEXT NOT NULL" in table_sql
    assert "method TEXT NOT NULL" in table_sql
    assert "path TEXT NOT NULL" in table_sql
    assert "request_hash TEXT NOT NULL" in table_sql
    assert "response_body JSONB NOT NULL DEFAULT '{}'::jsonb" in table_sql
    assert "UNIQUE (tenant_id, key, method, path)" in table_sql


def test_restore_drill_runs_have_schedule_time_uniqueness():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()

    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_restore_drill_runs_schedule_time"
        in sql
    )
    assert (
        "ON restore_drill_runs (tenant_id, schedule_id, scheduled_for)"
        in sql
    )


def test_trigger_definitions_include_schedule_and_connector_event_configs():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS trigger_definitions")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "schedule JSONB" in table_sql
    assert "connector_event JSONB" in table_sql
    assert "agent_handoff JSONB" in table_sql


def test_model_policy_scopes_include_sensitivity_limits():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS model_policy_scopes")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "model_sensitivity_limits JSONB NOT NULL DEFAULT '{}'::jsonb" in table_sql


def test_connector_definitions_store_references_not_credential_values():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS connector_definitions")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "credential_ref JSONB" in table_sql
    assert "capabilities JSONB" in table_sql
    assert "metadata JSONB" in table_sql
    assert "sync_state JSONB" in table_sql
    assert "raw_token" not in table_sql
    assert "api_key_value" not in table_sql


def test_users_store_password_hash_not_plain_password():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS users")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "password_hash TEXT NOT NULL" in table_sql
    assert "password TEXT" not in table_sql
    assert "status TEXT NOT NULL DEFAULT 'active'" in table_sql
    assert "CHECK (status IN ('active', 'disabled', 'pending', 'deleted'))" in table_sql
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_lower_email "
        "ON users (tenant_id, lower(trim(email)))"
    ) in sql

    for table_name in [
        "run_events",
        "artifacts",
        "audit_events",
        "billing_meter_events",
    ]:
        table_start = sql.index(f"CREATE TABLE IF NOT EXISTS {table_name}")
        table_end = sql.index(");", table_start)
        table_sql = sql[table_start:table_end]
        assert "run_id TEXT" in table_sql


def test_users_status_database_constraint_rejects_unknown_values(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    connection = sqlite3.connect(tmp_path / "taroai.sqlite3")
    connection.execute(
        "INSERT INTO tenants (id, name, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        ("tenant_acme", "Acme",),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO users (
                id, tenant_id, email, display_name, password_hash, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                "user_bad",
                "tenant_acme",
                "bad@example.com",
                "Bad Status",
                "hash",
                "suspended",
            ),
        )


def test_users_email_database_constraint_is_case_insensitive(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    connection = sqlite3.connect(tmp_path / "taroai.sqlite3")
    connection.execute(
        "INSERT INTO tenants (id, name, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        ("tenant_acme", "Acme",),
    )
    connection.execute(
        """
        INSERT INTO users (
            id, tenant_id, email, display_name, password_hash, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            "user_upper",
            "tenant_acme",
            "  Luke@Example.com  ",
            "Luke",
            "hash",
            "active",
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO users (
                id, tenant_id, email, display_name, password_hash, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                "user_lower",
                "tenant_acme",
                "luke@example.com",
                "Luke Duplicate",
                "hash",
                "active",
            ),
        )


def test_billing_meter_events_support_operation_level_records():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS billing_meter_events")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "run_id TEXT REFERENCES runs(id)" in table_sql
    assert "run_id TEXT NOT NULL REFERENCES runs(id)" not in table_sql


def test_billing_pricing_rules_include_tenant_workspace_and_price_columns():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS billing_pricing_rules")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "tenant_id TEXT NOT NULL REFERENCES tenants(id)" in table_sql
    assert "workspace_id TEXT NOT NULL DEFAULT ''" in table_sql
    assert "skill_id TEXT NOT NULL DEFAULT ''" in table_sql
    assert "meter_type TEXT NOT NULL" in table_sql
    assert "price_per_unit REAL NOT NULL" in table_sql
    assert "pricing_unit_quantity REAL NOT NULL DEFAULT 1" in table_sql
    assert "updated_by_user_id TEXT" in table_sql
    assert (
        "PRIMARY KEY (tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency)"
        in table_sql
    )


def test_billing_invoices_include_snapshot_and_creator_columns():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS billing_invoices")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "invoice_id TEXT PRIMARY KEY" in table_sql
    assert "tenant_id TEXT NOT NULL REFERENCES tenants(id)" in table_sql
    assert "invoice JSONB NOT NULL" in table_sql
    assert "created_by_user_id TEXT NOT NULL" in table_sql
    assert "total_cost_estimate REAL" in table_sql


def test_share_grants_include_resource_subject_status_and_expiration_columns():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS share_grants")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "id TEXT PRIMARY KEY" in table_sql
    assert "tenant_id TEXT NOT NULL REFERENCES tenants(id)" in table_sql
    assert "resource_type TEXT NOT NULL" in table_sql
    assert "resource_id TEXT NOT NULL" in table_sql
    assert "subject_type TEXT NOT NULL" in table_sql
    assert "subject_id TEXT NOT NULL" in table_sql
    assert "permission TEXT NOT NULL" in table_sql
    assert "status TEXT NOT NULL" in table_sql
    assert "expires_at TIMESTAMPTZ" in table_sql
    assert "revoked_by_user_id TEXT" in table_sql
    assert "revoked_at TIMESTAMPTZ" in table_sql
    assert "idx_share_grants_resource" in sql
    assert "idx_share_grants_subject" in sql


def test_billing_invoices_migration_applies_after_existing_schema(tmp_path):
    sqlite_path = tmp_path / "billing-invoices-upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in [
            "001_initial.sql",
            "002_short_term_memory_reviews.sql",
            "003_model_policy_scopes.sql",
            "004_run_event_sequence.sql",
            "005_idempotency_records.sql",
            "006_postgresql_tenant_rls.sql",
            "007_trigger_connector_event_config.sql",
            "008_trigger_agent_handoff_config.sql",
            "009_connector_definitions.sql",
            "010_connector_sync_state.sql",
            "011_license_validations.sql",
            "012_solution_packs.sql",
            "013_sso_provider_configs.sql",
            "014_scim_provisioning.sql",
            "015_model_policy_sensitivity_limits.sql",
            "016_knowledge_chunk_embeddings.sql",
            "017_operation_level_billing_meters.sql",
            "018_billing_pricing_rules.sql",
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'billing_invoices'
            """
        ).fetchone()

    assert row is not None


def test_billing_invoices_migration_adds_postgresql_rls_for_upgrade_path():
    sql = Path("apps/api/migrations/019_billing_invoices.sql").read_text()

    assert "-- taroai:postgresql-only-start" in sql
    assert "ALTER TABLE billing_invoices ENABLE ROW LEVEL SECURITY;" in sql
    assert "ALTER TABLE billing_invoices FORCE ROW LEVEL SECURITY;" in sql
    assert "CREATE POLICY billing_invoices_tenant_isolation" in sql
    assert "current_setting('taroai.tenant_id', true)" in sql


def test_billing_pricing_rules_migration_applies_after_existing_initial_schema(
    tmp_path,
):
    sqlite_path = tmp_path / "billing-pricing-upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in [
            "001_initial.sql",
            "002_short_term_memory_reviews.sql",
            "003_model_policy_scopes.sql",
            "004_run_event_sequence.sql",
            "005_idempotency_records.sql",
            "006_postgresql_tenant_rls.sql",
            "007_trigger_connector_event_config.sql",
            "008_trigger_agent_handoff_config.sql",
            "009_connector_definitions.sql",
            "010_connector_sync_state.sql",
            "011_license_validations.sql",
            "012_solution_packs.sql",
            "013_sso_provider_configs.sql",
            "014_scim_provisioning.sql",
            "015_model_policy_sensitivity_limits.sql",
            "016_knowledge_chunk_embeddings.sql",
            "017_operation_level_billing_meters.sql",
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'billing_pricing_rules'
            """
        ).fetchone()

    assert row is not None


def test_billing_pricing_rules_migration_adds_postgresql_rls_for_upgrade_path():
    sql = Path("apps/api/migrations/018_billing_pricing_rules.sql").read_text()

    assert "-- taroai:postgresql-only-start" in sql
    assert "ALTER TABLE billing_pricing_rules ENABLE ROW LEVEL SECURITY;" in sql
    assert "ALTER TABLE billing_pricing_rules FORCE ROW LEVEL SECURITY;" in sql
    assert (
        "DROP POLICY IF EXISTS billing_pricing_rules_tenant_isolation "
        "ON billing_pricing_rules;"
    ) in sql
    assert "CREATE POLICY billing_pricing_rules_tenant_isolation" in sql
    assert "current_setting('taroai.tenant_id', true)" in sql


def test_billing_pricing_rule_skill_scope_migration_updates_existing_sqlite_schema(
    tmp_path,
):
    sqlite_path = tmp_path / "billing-pricing-skill-upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in [
            "001_initial.sql",
            "002_short_term_memory_reviews.sql",
            "003_model_policy_scopes.sql",
            "004_run_event_sequence.sql",
            "005_idempotency_records.sql",
            "006_postgresql_tenant_rls.sql",
            "007_trigger_connector_event_config.sql",
            "008_trigger_agent_handoff_config.sql",
            "009_connector_definitions.sql",
            "010_connector_sync_state.sql",
            "011_license_validations.sql",
            "012_solution_packs.sql",
            "013_sso_provider_configs.sql",
            "014_scim_provisioning.sql",
            "015_model_policy_sensitivity_limits.sql",
            "016_knowledge_chunk_embeddings.sql",
            "017_operation_level_billing_meters.sql",
            "018_billing_pricing_rules.sql",
            "019_billing_invoices.sql",
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
        connection.execute(
            """
            CREATE TABLE billing_pricing_rules (
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '',
                meter_type TEXT NOT NULL,
                unit TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'USD',
                price_per_unit REAL NOT NULL,
                pricing_unit_quantity REAL NOT NULL DEFAULT 1,
                updated_by_user_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, workspace_id, meter_type, unit, provider, model, currency)
            )
            """
        )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(billing_pricing_rules)"
            ).fetchall()
        }
        primary_key_columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(billing_pricing_rules)"
            ).fetchall()
            if row[5] > 0
        ]

    assert "skill_id" in columns
    assert primary_key_columns == [
        "tenant_id",
        "workspace_id",
        "skill_id",
        "meter_type",
        "unit",
        "provider",
        "model",
        "currency",
    ]


def test_billing_pricing_rule_skill_scope_migration_updates_postgresql_primary_key():
    sql = Path(
        "apps/api/migrations/020_billing_pricing_rule_skill_scope.sql"
    ).read_text()

    assert (
        "ALTER TABLE billing_pricing_rules ADD COLUMN IF NOT EXISTS skill_id TEXT NOT NULL DEFAULT '';"
        in sql
    )
    assert (
        "ALTER TABLE billing_pricing_rules DROP CONSTRAINT IF EXISTS billing_pricing_rules_pkey;"
        in sql
    )
    assert (
        "PRIMARY KEY (tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency)"
        in sql
    )


def test_share_grants_migration_applies_after_existing_schema(tmp_path):
    sqlite_path = tmp_path / "share-grants-upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in [
            "001_initial.sql",
            "002_short_term_memory_reviews.sql",
            "003_model_policy_scopes.sql",
            "004_run_event_sequence.sql",
            "005_idempotency_records.sql",
            "006_postgresql_tenant_rls.sql",
            "007_trigger_connector_event_config.sql",
            "008_trigger_agent_handoff_config.sql",
            "009_connector_definitions.sql",
            "010_connector_sync_state.sql",
            "011_license_validations.sql",
            "012_solution_packs.sql",
            "013_sso_provider_configs.sql",
            "014_scim_provisioning.sql",
            "015_model_policy_sensitivity_limits.sql",
            "016_knowledge_chunk_embeddings.sql",
            "017_operation_level_billing_meters.sql",
            "018_billing_pricing_rules.sql",
            "019_billing_invoices.sql",
            "020_billing_pricing_rule_skill_scope.sql",
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'share_grants'
            """
        ).fetchone()

    assert row is not None


def test_share_grants_migration_adds_postgresql_rls_for_upgrade_path():
    sql = Path("apps/api/migrations/021_share_grants.sql").read_text()

    assert "-- taroai:postgresql-only-start" in sql
    assert "ALTER TABLE share_grants ENABLE ROW LEVEL SECURITY;" in sql
    assert "ALTER TABLE share_grants FORCE ROW LEVEL SECURITY;" in sql
    assert "DROP POLICY IF EXISTS share_grants_tenant_isolation ON share_grants;" in sql
    assert "CREATE POLICY share_grants_tenant_isolation" in sql
    assert "current_setting('taroai.tenant_id', true)" in sql


def test_customer_feedback_tables_store_review_workflow_records():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()

    for table_name in [
        "customer_feedback_records",
        "customer_feedback_evaluation_candidates",
        "customer_solution_pack_feedback_candidates",
        "customer_feedback_evaluation_cases",
        "customer_solution_pack_publication_drafts",
    ]:
        table_start = sql.index(f"CREATE TABLE IF NOT EXISTS {table_name}")
        table_end = sql.index(");", table_start)
        table_sql = sql[table_start:table_end]
        assert "tenant_id TEXT NOT NULL REFERENCES tenants(id)" in table_sql

    feedback_table_start = sql.index(
        "CREATE TABLE IF NOT EXISTS customer_feedback_records"
    )
    feedback_table_end = sql.index(");", feedback_table_start)
    feedback_table = sql[feedback_table_start:feedback_table_end]
    assert "comment TEXT" in feedback_table
    assert "metadata JSONB NOT NULL DEFAULT '{}'::jsonb" in feedback_table

    case_table_start = sql.index(
        "CREATE TABLE IF NOT EXISTS customer_feedback_evaluation_cases"
    )
    case_table_end = sql.index(");", case_table_start)
    case_table = sql[case_table_start:case_table_end]
    assert "source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb" in case_table
    assert "review_note" not in case_table
    assert "comment" not in case_table
    assert "metadata" not in case_table


def test_customer_feedback_migration_applies_after_existing_schema(tmp_path):
    sqlite_path = tmp_path / "customer-feedback-upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in [
            "001_initial.sql",
            "002_short_term_memory_reviews.sql",
            "003_model_policy_scopes.sql",
            "004_run_event_sequence.sql",
            "005_idempotency_records.sql",
            "006_postgresql_tenant_rls.sql",
            "007_trigger_connector_event_config.sql",
            "008_trigger_agent_handoff_config.sql",
            "009_connector_definitions.sql",
            "010_connector_sync_state.sql",
            "011_license_validations.sql",
            "012_solution_packs.sql",
            "013_sso_provider_configs.sql",
            "014_scim_provisioning.sql",
            "015_model_policy_sensitivity_limits.sql",
            "016_knowledge_chunk_embeddings.sql",
            "017_operation_level_billing_meters.sql",
            "018_billing_pricing_rules.sql",
            "019_billing_invoices.sql",
            "020_billing_pricing_rule_skill_scope.sql",
            "021_share_grants.sql",
            "022_runtime_browser_session_state.sql",
            "023_restore_drill_schedule_store.sql",
            "024_model_provider_records.sql",
            "025_model_provider_versions.sql",
            "026_model_provider_change_requests.sql",
            "027_model_policy_change_requests.sql",
            "028_model_provider_rate_limit_samples.sql",
            "029_model_policy_versions.sql",
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }

    assert "customer_feedback_records" in tables
    assert "customer_feedback_evaluation_cases" in tables
    assert "customer_solution_pack_publication_drafts" in tables
    with sqlite3.connect(sqlite_path) as connection:
        draft_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(customer_solution_pack_publication_drafts)"
            ).fetchall()
        }
    assert "proposed_pack_version" in draft_columns
    assert "proposed_skill_manifest" in draft_columns
    assert "proposed_skill_manifests" in draft_columns


def test_solution_pack_publication_draft_application_migration_is_additive():
    initial_sql = Path("apps/api/migrations/001_initial.sql").read_text()
    migration_sql = Path(
        "apps/api/migrations/031_solution_pack_publication_draft_application.sql"
    ).read_text()

    table_start = initial_sql.index(
        "CREATE TABLE IF NOT EXISTS customer_solution_pack_publication_drafts"
    )
    table_end = initial_sql.index(");", table_start)
    table_sql = initial_sql[table_start:table_end]
    assert "proposed_pack_version TEXT" in table_sql
    assert "proposed_skill_manifest JSONB" in table_sql
    assert (
        "ALTER TABLE customer_solution_pack_publication_drafts "
        "ADD COLUMN proposed_pack_version TEXT"
    ) in migration_sql
    assert (
        "ALTER TABLE customer_solution_pack_publication_drafts "
        "ADD COLUMN proposed_skill_manifest JSONB"
    ) in migration_sql


def test_solution_pack_publication_draft_multi_manifest_migration_is_additive():
    initial_sql = Path("apps/api/migrations/001_initial.sql").read_text()
    migration_sql = Path(
        "apps/api/migrations/032_solution_pack_publication_draft_multi_manifest.sql"
    ).read_text()

    table_start = initial_sql.index(
        "CREATE TABLE IF NOT EXISTS customer_solution_pack_publication_drafts"
    )
    table_end = initial_sql.index(");", table_start)
    table_sql = initial_sql[table_start:table_end]
    assert "proposed_skill_manifests JSONB NOT NULL DEFAULT '[]'::jsonb" in table_sql
    assert (
        "ALTER TABLE customer_solution_pack_publication_drafts "
        "ADD COLUMN proposed_skill_manifests JSONB NOT NULL DEFAULT '[]'::jsonb"
    ) in migration_sql


def test_customer_feedback_migration_adds_postgresql_rls_for_upgrade_path():
    sql = Path("apps/api/migrations/030_customer_feedback_records.sql").read_text()

    assert "-- taroai:postgresql-only-start" in sql
    assert "-- taroai:postgresql-only-end" in sql
    for table_name in [
        "customer_feedback_records",
        "customer_feedback_evaluation_candidates",
        "customer_solution_pack_feedback_candidates",
        "customer_feedback_evaluation_cases",
        "customer_solution_pack_publication_drafts",
    ]:
        assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;" in sql
        assert f"CREATE POLICY {table_name}_tenant_isolation" in sql
        assert f"ON {table_name}" in sql
    assert "current_setting('taroai.tenant_id', true)" in sql


def test_operation_level_billing_migration_updates_existing_sqlite_schema(tmp_path):
    sqlite_path = tmp_path / "operation-billing-upgrade.sqlite3"
    applied_versions = [
        "001_initial.sql",
        "002_short_term_memory_reviews.sql",
        "003_model_policy_scopes.sql",
        "004_run_event_sequence.sql",
        "005_idempotency_records.sql",
        "006_postgresql_tenant_rls.sql",
        "007_trigger_connector_event_config.sql",
        "008_trigger_agent_handoff_config.sql",
        "009_connector_definitions.sql",
        "010_connector_sync_state.sql",
        "011_license_validations.sql",
        "012_solution_packs.sql",
        "013_sso_provider_configs.sql",
        "014_scim_provisioning.sql",
        "015_model_policy_sensitivity_limits.sql",
        "016_knowledge_chunk_embeddings.sql",
    ]
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in applied_versions:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
        connection.execute(
            """
            CREATE TABLE billing_meter_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                agent_id TEXT,
                skill_id TEXT,
                meter_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                cost_estimate REAL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        columns = connection.execute(
            "PRAGMA table_info(billing_meter_events)"
        ).fetchall()
        run_id_column = [column for column in columns if column[1] == "run_id"][0]
        connection.execute(
            """
            INSERT INTO billing_meter_events (
                id, tenant_id, workspace_id, user_id, run_id, meter_type,
                quantity, unit, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meter_operation",
                "tenant_acme",
                "workspace_sales",
                "user_1",
                None,
                "embedding_call_count",
                1,
                "call",
                "{}",
                "2026-07-03T00:00:00Z",
            ),
        )

    assert run_id_column[3] == 0


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


def test_short_term_memory_review_migration_applies_after_existing_initial_schema(
    tmp_path,
):
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
        connection.execute(
            """
            CREATE TABLE run_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
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


def test_sqlite_migration_skips_postgresql_only_blocks(tmp_path):
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    sqlite_path = tmp_path / "sqlite-only-filter.sqlite3"
    migration = migrations_path / "001_filtered.sql"
    migration.write_text(
        """
        CREATE TABLE tenant_records (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL
        );

        -- taroai:postgresql-only-start
        ALTER TABLE tenant_records ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_records_tenant_isolation
            ON tenant_records
            USING (tenant_id = current_setting('taroai.tenant_id', true));
        -- taroai:postgresql-only-end
        """,
    )

    result = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=migrations_path,
    ).apply()

    assert result.applied_versions == ["001_filtered.sql"]
    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'tenant_records'
            """
        ).fetchone()

    assert row is not None


def test_postgresql_migration_keeps_postgresql_only_blocks():
    runner = MigrationRunner(
        config=DatabaseConfig(url="postgresql://taroai:taroai@db.internal:5432/taroai"),
        migrations_path=Path("apps/api/migrations"),
    )

    sql = runner._migration_sql(
        """
        CREATE TABLE tenant_records (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL
        );

        -- taroai:postgresql-only-start
        ALTER TABLE tenant_records ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_records_tenant_isolation
            ON tenant_records
            USING (tenant_id = current_setting('taroai.tenant_id', true));
        -- taroai:postgresql-only-end
        """,
    )

    assert "ALTER TABLE tenant_records ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY tenant_records_tenant_isolation" in sql
    assert "taroai:postgresql-only" not in sql


def test_postgresql_rls_migration_protects_tenant_scoped_tables():
    sql = Path("apps/api/migrations/006_postgresql_tenant_rls.sql").read_text()
    tenant_scoped_tables = [
        "workspaces",
        "users",
        "auth_sessions",
        "roles",
        "role_assignments",
        "runs",
        "run_events",
        "idempotency_records",
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
        "solution_pack_entries",
        "solution_pack_versions",
        "solution_pack_installations",
        "customer_feedback_records",
        "customer_feedback_evaluation_candidates",
        "customer_solution_pack_feedback_candidates",
        "customer_feedback_evaluation_cases",
        "customer_solution_pack_publication_drafts",
        "sso_provider_configs",
        "scim_provider_configs",
        "scim_group_role_mappings",
        "scim_user_links",
        "scim_import_records",
        "connector_definitions",
        "trigger_definitions",
        "audit_events",
        "billing_meter_events",
        "billing_pricing_rules",
        "billing_invoices",
        "runtime_states",
        "lifecycle_policies",
        "legal_holds",
        "restore_drill_schedules",
        "restore_drill_runs",
        "model_policy_scopes",
        "model_policy_change_requests",
        "tenant_offboarding_plans",
    ]

    assert "-- taroai:postgresql-only-start" in sql
    assert "-- taroai:postgresql-only-end" in sql
    for table_name in tenant_scoped_tables:
        assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;" in sql
        assert (
            f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name};"
            in sql
        )
        assert f"CREATE POLICY {table_name}_tenant_isolation" in sql
        assert f"ON {table_name}" in sql

    assert "current_setting('taroai.tenant_id', true)" in sql
    assert "WITH CHECK" in sql


def test_knowledge_documents_include_managed_source_object_reference():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS knowledge_documents")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "storage_object_id TEXT REFERENCES storage_objects(id)" in table_sql


def test_knowledge_chunks_include_embedding_metadata_columns():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS knowledge_chunks")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "embedding_vector JSONB NOT NULL DEFAULT '[]'::jsonb" in table_sql
    assert "embedding_model TEXT" in table_sql
    assert "embedding_provider TEXT" in table_sql
    assert "embedded_at TIMESTAMPTZ" in table_sql


def test_runtime_states_include_execution_session_columns():
    sql = Path("apps/api/migrations/001_initial.sql").read_text()
    table_start = sql.index("CREATE TABLE IF NOT EXISTS runtime_states")
    table_end = sql.index(");", table_start)
    table_sql = sql[table_start:table_end]

    assert "sandbox_session_id TEXT" in table_sql
    assert "browser_session_id TEXT" in table_sql
    assert (
        Path("apps/api/migrations/022_runtime_browser_session_state.sql")
        .read_text()
        .count("ADD COLUMN")
        == 2
    )


def test_runtime_session_state_migration_applies_after_existing_schema(tmp_path):
    sqlite_path = tmp_path / "runtime-session-upgrade.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in sorted(
            path.name
            for path in Path("apps/api/migrations").glob("*.sql")
            if path.name < "022_runtime_browser_session_state.sql"
        ):
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
        connection.execute(
            """
            CREATE TABLE runtime_states (
                run_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT '[]',
                current_step_id TEXT,
                completed_step_ids TEXT NOT NULL DEFAULT '[]',
                approved_step_ids TEXT NOT NULL DEFAULT '[]',
                approved_guardrail_keys TEXT NOT NULL DEFAULT '[]',
                pending_guardrail_approval_key TEXT,
                pending_guardrail_approval_stage TEXT,
                tool_results TEXT NOT NULL DEFAULT '[]',
                retrieved_context TEXT NOT NULL DEFAULT '{}',
                approval_id TEXT,
                failure_reason TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        columns = connection.execute("PRAGMA table_info(runtime_states)").fetchall()

    column_names = [column[1] for column in columns]
    assert "sandbox_session_id" in column_names
    assert "browser_session_id" in column_names


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
        connection.execute(
            """
            CREATE TABLE run_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
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


def test_run_event_sequence_migration_applies_after_existing_schema(tmp_path):
    sqlite_path = tmp_path / "run-event-sequence-upgrade.sqlite3"
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
            """
            CREATE TABLE run_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        for version in [
            "001_initial.sql",
            "002_short_term_memory_reviews.sql",
            "003_model_policy_scopes.sql",
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        columns = connection.execute("PRAGMA table_info(run_events)").fetchall()

    assert "sequence" in [column[1] for column in columns]


def test_unique_run_event_sequence_migration_repairs_existing_duplicates(tmp_path):
    sqlite_path = tmp_path / "unique-run-event-sequence-upgrade.sqlite3"
    migrations_path = Path("apps/api/migrations")
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
            """
            CREATE TABLE run_events (
                id TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            [
                (path.name,)
                for path in sorted(migrations_path.glob("0*.sql"))
                if path.name < "040_unique_run_event_sequence.sql"
            ],
        )
        connection.executemany(
            """
            INSERT INTO run_events (
                id, sequence, tenant_id, workspace_id, run_id, type, created_at
            ) VALUES (?, ?, 'tenant_1', 'workspace_1', 'run_1', ?, ?)
            """,
            [
                ("event_1", 1, "run.created", "2026-07-13T00:00:00Z"),
                ("event_2", 2, "audit.recorded", "2026-07-13T00:00:01Z"),
                ("event_3", 2, "audit.recorded", "2026-07-13T00:00:02Z"),
            ],
        )

    result = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=migrations_path,
    ).apply()

    assert result.applied_versions == [
        "040_unique_run_event_sequence.sql",
        "041_chat_message_execution_content.sql",
        "042_workflow_agent_approvals.sql",
        "043_owner_connector_invoke_permission.sql",
        "044_notifications.sql",
        "045_tenant_invitations.sql",
    ]
    with sqlite3.connect(sqlite_path) as connection:
        sequences = connection.execute(
            "SELECT sequence FROM run_events ORDER BY sequence"
        ).fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO run_events (
                    id, sequence, tenant_id, workspace_id, run_id, type, created_at
                ) VALUES (
                    'event_4', 3, 'tenant_1', 'workspace_1', 'run_1',
                    'audit.recorded', '2026-07-13T00:00:03Z'
                )
                """
            )

    assert sequences == [(1,), (2,), (3,)]


def test_owner_connector_invoke_permission_migration_upgrades_existing_role(
    tmp_path: Path,
):
    sqlite_path = tmp_path / "owner-permission.sqlite3"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE roles (id TEXT, tenant_id TEXT, permissions TEXT)"
        )
        connection.execute(
            "INSERT INTO roles VALUES ('tenant_owner', 'tenant_acme', '[]')"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            [
                (path.name,)
                for path in sorted(Path("apps/api/migrations").glob("*.sql"))
                if path.name < "043_owner_connector_invoke_permission.sql"
            ],
        )

    result = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        permission = connection.execute(
            """
            SELECT json_extract(value, '$.resource')
            FROM roles, json_each(roles.permissions)
            WHERE json_extract(value, '$.action') = 'connectors.invoke'
            """
        ).fetchone()
    assert result.applied_versions == [
        "043_owner_connector_invoke_permission.sql",
        "044_notifications.sql",
        "045_tenant_invitations.sql",
    ]
    assert permission == ("tenant:tenant_acme",)


def test_migration_runner_ignores_duplicate_column_errors_from_postgresql():
    class DuplicateColumnError(Exception):
        sqlstate = "42701"

    class DuplicateColumnConnection:
        def __init__(self):
            self.executed: list[str] = []

        def execute(self, statement):
            self.executed.append(statement)
            if statement.startswith("ALTER TABLE"):
                raise DuplicateColumnError(
                    'column "sequence" of relation "run_events" already exists'
                )

    runner = MigrationRunner(
        config=DatabaseConfig(url="postgresql://taroai:taroai@db.internal:5432/taroai"),
        migrations_path=Path("apps/api/migrations"),
    )
    connection = DuplicateColumnConnection()

    runner._execute_script(
        connection,
        "ALTER TABLE run_events ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0;",
    )

    assert connection.executed == [
        "SAVEPOINT taroai_migration_statement",
        "ALTER TABLE run_events ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0",
        "ROLLBACK TO SAVEPOINT taroai_migration_statement",
        "RELEASE SAVEPOINT taroai_migration_statement",
    ]


def test_chat_threads_agent_loop_v2_migration_is_additive_and_sqlite_compatible(
    tmp_path: Path,
):
    migration = Path("apps/api/migrations/033_chat_threads_agent_loop_v2.sql")
    sql = migration.read_text()

    tenant_tables = [
        "chat_threads",
        "chat_messages",
        "agent_cycles",
        "agent_actions",
        "agent_checkpoints",
    ]
    for table_name in tenant_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql
        assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;" in sql
        assert f"CREATE POLICY {table_name}_tenant_isolation" in sql

    for column in [
        "thread_id",
        "trigger_message_id",
        "provider_id",
        "model_id",
        "reasoning_effort",
        "resource_refs",
    ]:
        assert f"ALTER TABLE runs ADD COLUMN {column}" in sql

    assert "ALTER TABLE run_events ADD COLUMN thread_id" in sql
    assert "ALTER TABLE run_events ADD COLUMN thread_sequence" in sql
    assert "idx_chat_messages_thread_sequence" in sql
    assert "idx_agent_checkpoints_run_sequence" in sql
    assert "-- taroai:postgresql-only-start" in sql
    assert "-- taroai:postgresql-only-end" in sql

    sqlite_path = tmp_path / "chat-agent-loop-v2.sqlite3"
    MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{sqlite_path}"),
        migrations_path=Path("apps/api/migrations"),
    ).apply()

    with sqlite3.connect(sqlite_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        run_event_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(run_events)").fetchall()
        }
        agent_action_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(agent_actions)").fetchall()
        }

    assert set(tenant_tables) <= existing_tables
    assert {
        "thread_id",
        "trigger_message_id",
        "provider_id",
        "model_id",
        "reasoning_effort",
        "resource_refs",
    } <= run_columns
    assert {"thread_id", "thread_sequence"} <= run_event_columns
    assert "lease_owner_id TEXT" in sql
    assert "lease_expires_at TIMESTAMPTZ" in sql
    assert (
        "lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0)"
        in sql
    )
    assert {
        "lease_owner_id",
        "lease_expires_at",
        "lease_generation",
    } <= agent_action_columns
    assert "idx_agent_actions_lease_recovery" in sql
    assert "(tenant_id, status, lease_expires_at, id)" in sql

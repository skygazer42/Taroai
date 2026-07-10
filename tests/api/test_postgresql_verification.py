from pathlib import Path

import pytest

from taroai.db.postgresql_verification import (
    TENANT_SCOPED_RLS_TABLES,
    PostgresqlRlsVerificationConfig,
    parse_args,
)


def test_postgresql_rls_verification_config_requires_postgresql_url():
    with pytest.raises(ValueError, match="PostgreSQL verification requires a PostgreSQL URL"):
        PostgresqlRlsVerificationConfig(
            database_url="sqlite:////tmp/taroai.sqlite3",
            migrations_path=Path("apps/api/migrations"),
        )


def test_postgresql_rls_verification_tracks_core_tenant_scoped_tables():
    assert "runs" in TENANT_SCOPED_RLS_TABLES
    assert "workspaces" in TENANT_SCOPED_RLS_TABLES
    assert "memory_records" in TENANT_SCOPED_RLS_TABLES
    assert "solution_pack_entries" in TENANT_SCOPED_RLS_TABLES
    assert "solution_pack_installations" in TENANT_SCOPED_RLS_TABLES
    assert "sso_provider_configs" in TENANT_SCOPED_RLS_TABLES
    assert "scim_provider_configs" in TENANT_SCOPED_RLS_TABLES
    assert "scim_group_role_mappings" in TENANT_SCOPED_RLS_TABLES
    assert "scim_user_links" in TENANT_SCOPED_RLS_TABLES
    assert "scim_import_records" in TENANT_SCOPED_RLS_TABLES
    assert "audit_events" in TENANT_SCOPED_RLS_TABLES
    assert "billing_meter_events" in TENANT_SCOPED_RLS_TABLES
    assert "model_provider_records" in TENANT_SCOPED_RLS_TABLES
    assert "model_provider_versions" in TENANT_SCOPED_RLS_TABLES
    assert "model_provider_change_requests" in TENANT_SCOPED_RLS_TABLES
    assert "model_provider_rate_limit_samples" in TENANT_SCOPED_RLS_TABLES
    assert "model_policy_change_requests" in TENANT_SCOPED_RLS_TABLES
    assert "chat_threads" in TENANT_SCOPED_RLS_TABLES
    assert "chat_messages" in TENANT_SCOPED_RLS_TABLES
    assert "agent_cycles" in TENANT_SCOPED_RLS_TABLES
    assert "agent_actions" in TENANT_SCOPED_RLS_TABLES
    assert "agent_checkpoints" in TENANT_SCOPED_RLS_TABLES
    assert "tenants" not in TENANT_SCOPED_RLS_TABLES


def test_postgresql_rls_verification_cli_parses_database_url_and_migrations_path(tmp_path):
    config = parse_args(
        [
            "--database-url",
            "postgresql://taroai:taroai@localhost:5432/taroai",
            "--migrations-path",
            str(tmp_path),
            "--tenant-suffix",
            "ci",
        ]
    )

    assert config.database_url == "postgresql://taroai:taroai@localhost:5432/taroai"
    assert config.migrations_path == tmp_path
    assert config.tenant_suffix == "ci"

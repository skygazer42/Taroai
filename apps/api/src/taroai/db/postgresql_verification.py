import argparse
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from taroai.db.connection import connect_database
from taroai.db.migrations import MigrationRunner
from taroai.db.models import DatabaseConfig


TENANT_SCOPED_RLS_TABLES: tuple[str, ...] = (
    "workspaces",
    "users",
    "auth_sessions",
    "roles",
    "role_assignments",
    "runs",
    "run_events",
    "chat_threads",
    "chat_messages",
    "agent_cycles",
    "agent_actions",
    "agent_checkpoints",
    "agent_definitions",
    "agent_versions",
    "agent_workspace_assignments",
    "agent_reference_files",
    "agent_runtime_profiles",
    "thread_share_links",
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
    "skill_packages",
    "skill_package_files",
    "skill_evaluation_runs",
    "solution_pack_entries",
    "solution_pack_versions",
    "solution_pack_installations",
    "sso_provider_configs",
    "scim_provider_configs",
    "scim_group_role_mappings",
    "scim_user_links",
    "scim_import_records",
    "audit_events",
    "billing_meter_events",
    "runtime_states",
    "lifecycle_policies",
    "legal_holds",
    "model_policy_scopes",
    "model_policy_change_requests",
    "model_provider_records",
    "model_provider_versions",
    "model_provider_change_requests",
    "model_provider_rate_limit_samples",
    "agent_api_keys",
    "connector_definitions",
    "tenant_offboarding_plans",
)


class PostgresqlRlsVerificationConfig(BaseModel):
    database_url: str = Field(min_length=1)
    migrations_path: Path
    tenant_suffix: str = Field(default_factory=lambda: uuid4().hex[:12], min_length=1)

    @model_validator(mode="after")
    def validate_postgresql_url(self) -> "PostgresqlRlsVerificationConfig":
        database_config = DatabaseConfig(url=self.database_url)
        if database_config.dialect != "postgresql":
            raise ValueError("PostgreSQL verification requires a PostgreSQL URL")
        return self

    def database_config(self) -> DatabaseConfig:
        return DatabaseConfig(url=self.database_url)


class PostgresqlRlsVerificationResult(BaseModel):
    applied_versions: list[str] = Field(default_factory=list)
    rls_tables_verified: list[str] = Field(default_factory=list)
    tenant_a_visible_workspaces: list[str] = Field(default_factory=list)
    tenant_b_visible_workspaces: list[str] = Field(default_factory=list)
    no_context_visible_workspaces: list[str] = Field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> PostgresqlRlsVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify PostgreSQL migrations and tenant RLS behavior."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--migrations-path",
        default="/app/migrations",
        type=Path,
    )
    parser.add_argument("--tenant-suffix", default=None)
    parsed = parser.parse_args(argv)
    config_data = {
        "database_url": parsed.database_url,
        "migrations_path": parsed.migrations_path,
    }
    if parsed.tenant_suffix is not None:
        config_data["tenant_suffix"] = parsed.tenant_suffix
    return PostgresqlRlsVerificationConfig(**config_data)


def verify_postgresql_rls(
    config: PostgresqlRlsVerificationConfig,
) -> PostgresqlRlsVerificationResult:
    migration_result = MigrationRunner(
        config=config.database_config(),
        migrations_path=config.migrations_path,
    ).apply()
    rls_tables = verify_rls_policies(config)
    tenant_a = f"tenant_rls_a_{config.tenant_suffix}"
    tenant_b = f"tenant_rls_b_{config.tenant_suffix}"
    workspace_a = f"workspace_rls_a_{config.tenant_suffix}"
    workspace_b = f"workspace_rls_b_{config.tenant_suffix}"
    cleanup_verification_records(config, tenant_a, tenant_b)
    try:
        create_verification_records(config, tenant_a, tenant_b, workspace_a, workspace_b)
        tenant_a_visible = visible_workspace_ids(config, tenant_a)
        tenant_b_visible = visible_workspace_ids(config, tenant_b)
        no_context_visible = visible_workspace_ids_without_context(config)
        if tenant_a_visible != [workspace_a]:
            raise RuntimeError("tenant A did not see exactly its own workspace")
        if tenant_b_visible != [workspace_b]:
            raise RuntimeError("tenant B did not see exactly its own workspace")
        if no_context_visible:
            raise RuntimeError("RLS allowed workspace rows without tenant context")
        return PostgresqlRlsVerificationResult(
            applied_versions=migration_result.applied_versions,
            rls_tables_verified=rls_tables,
            tenant_a_visible_workspaces=tenant_a_visible,
            tenant_b_visible_workspaces=tenant_b_visible,
            no_context_visible_workspaces=no_context_visible,
        )
    finally:
        cleanup_verification_records(config, tenant_a, tenant_b)


def verify_rls_policies(config: PostgresqlRlsVerificationConfig) -> list[str]:
    with connect_database(config.database_config()) as connection:
        rows = connection.execute(
            """
            SELECT c.relname AS table_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = ANY(%s)
              AND c.relrowsecurity IS TRUE
              AND c.relforcerowsecurity IS TRUE
            ORDER BY c.relname
            """,
            (list(TENANT_SCOPED_RLS_TABLES),),
        ).fetchall()
    verified_tables = [row["table_name"] for row in rows]
    missing_tables = sorted(set(TENANT_SCOPED_RLS_TABLES) - set(verified_tables))
    if missing_tables:
        raise RuntimeError(f"PostgreSQL RLS is not enabled for: {missing_tables}")
    return verified_tables


def create_verification_records(
    config: PostgresqlRlsVerificationConfig,
    tenant_a: str,
    tenant_b: str,
    workspace_a: str,
    workspace_b: str,
) -> None:
    with connect_database(config.database_config()) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name) VALUES (?, ?)",
            (tenant_a, "RLS verification A"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name) VALUES (?, ?)",
            (tenant_b, "RLS verification B"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name) VALUES (?, ?, ?)",
            (workspace_a, tenant_a, "RLS verification A"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name) VALUES (?, ?, ?)",
            (workspace_b, tenant_b, "RLS verification B"),
        )


def visible_workspace_ids(
    config: PostgresqlRlsVerificationConfig,
    tenant_id: str,
) -> list[str]:
    with connect_database(config.database_config()) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM workspaces
            WHERE tenant_id = ?
            ORDER BY id
            """,
            (tenant_id,),
        ).fetchall()
    return [row["id"] for row in rows]


def visible_workspace_ids_without_context(
    config: PostgresqlRlsVerificationConfig,
) -> list[str]:
    with connect_database(config.database_config()) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM workspaces
            WHERE id LIKE ?
            ORDER BY id
            """,
            ("workspace_rls_%",),
        ).fetchall()
    return [row["id"] for row in rows]


def cleanup_verification_records(
    config: PostgresqlRlsVerificationConfig,
    tenant_a: str,
    tenant_b: str,
) -> None:
    with connect_database(config.database_config()) as connection:
        for tenant_id in [tenant_a, tenant_b]:
            connection.execute(
                "DELETE FROM workspaces WHERE tenant_id = ?",
                (tenant_id,),
            )
        connection.execute(
            "DELETE FROM tenants WHERE id IN (?, ?)",
            (tenant_a, tenant_b),
        )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_postgresql_rls(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

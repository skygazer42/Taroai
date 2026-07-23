from pathlib import Path
from datetime import datetime, timezone
import os
import sqlite3
import subprocess
import sys

import pytest

from taroai.agent import AgentRuntimeState, PlanStep
from taroai.db.migration_cli import parse_args as parse_migration_args
from taroai.db.migration_cli import run_migration_command
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.domain import (
    ApprovalStatus,
    IdempotencyRecord,
    Run,
    RunCreate,
    RunMode,
    RunStatus,
    utc_now,
)
from taroai.licensing import (
    Entitlement,
    LicenseKey,
    LicenseStatus,
    LicenseValidationResult,
    LicensedFeature,
)
from taroai.store import NotFoundError


def test_migration_runner_applies_pending_schema_and_records_versions(tmp_path: Path):
    database_path = tmp_path / "taroai.sqlite3"
    expected_versions = [
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
        "030_customer_feedback_records.sql",
        "031_solution_pack_publication_draft_application.sql",
        "032_solution_pack_publication_draft_multi_manifest.sql",
        "033_chat_threads_agent_loop_v2.sql",
        "034_skill_runtime_v2.sql",
        "035_agents_shares_and_rich_artifacts.sql",
        "036_browser_profiles.sql",
        "037_agent_engines.sql",
        "038_coding_workspaces.sql",
        "039_evaluation_runtime.sql",
        "040_unique_run_event_sequence.sql",
            "041_chat_message_execution_content.sql",
            "042_workflow_agent_approvals.sql",
            "043_owner_connector_invoke_permission.sql",
            "044_notifications.sql",
            "045_tenant_invitations.sql",
    ]
    runner = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=Path("apps/api/migrations"),
    )

    result = runner.apply()

    assert result.applied_versions == expected_versions
    with runner.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        runtime_state_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(runtime_states)"
            ).fetchall()
        }

    assert "runs" in tables
    assert "runtime_states" in tables
    assert "short_term_memory_reviews" in tables
    assert "model_policy_scopes" in tables
    assert "model_policy_change_requests" in tables
    assert "model_policy_versions" in tables
    assert "model_provider_versions" in tables
    assert "model_provider_change_requests" in tables
    assert "model_provider_rate_limit_samples" in tables
    assert "license_validations" in tables
    assert "sso_provider_configs" in tables
    assert "scim_provider_configs" in tables
    assert "scim_group_role_mappings" in tables
    assert "scim_user_links" in tables
    assert "scim_import_records" in tables
    assert "billing_pricing_rules" in tables
    assert "billing_invoices" in tables
    assert "share_grants" in tables
    assert {
        "chat_threads",
        "chat_messages",
        "agent_cycles",
        "agent_actions",
        "agent_checkpoints",
    } <= tables
    assert "state_payload" in runtime_state_columns
    assert versions == expected_versions


def test_chat_loop_migration_adds_state_payload_to_existing_runtime_states(
    tmp_path: Path,
):
    database_path = tmp_path / "runtime-state-upgrade.sqlite3"
    migrations_path = Path("apps/api/migrations")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for migration in sorted(
            path.name
            for path in migrations_path.glob("*.sql")
            if path.name < "033_chat_threads_agent_loop_v2.sql"
        ):
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (migration,),
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
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runtime_states (
                run_id, tenant_id, workspace_id, user_id, goal, status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "run_legacy",
                "tenant_acme",
                "workspace_sales",
                "user_1",
                "Continue the legacy run.",
                "running",
            ),
        )

    result = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=migrations_path,
    ).apply()

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(runtime_states)"
            ).fetchall()
        }
        state_payload = connection.execute(
            "SELECT state_payload FROM runtime_states WHERE run_id = ?",
            ("run_legacy",),
        ).fetchone()[0]

    assert result.applied_versions == [
        "033_chat_threads_agent_loop_v2.sql",
        "034_skill_runtime_v2.sql",
        "035_agents_shares_and_rich_artifacts.sql",
        "036_browser_profiles.sql",
        "037_agent_engines.sql",
        "038_coding_workspaces.sql",
        "039_evaluation_runtime.sql",
        "040_unique_run_event_sequence.sql",
            "041_chat_message_execution_content.sql",
            "042_workflow_agent_approvals.sql",
            "043_owner_connector_invoke_permission.sql",
            "044_notifications.sql",
            "045_tenant_invitations.sql",
    ]
    assert "state_payload" in columns
    assert state_payload == "{}"


def test_migration_runner_plans_pending_and_unknown_versions(tmp_path: Path):
    database_path = tmp_path / "taroai.sqlite3"
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS alpha(id TEXT PRIMARY KEY);\n"
    )
    (migrations_path / "002_next.sql").write_text(
        "CREATE TABLE IF NOT EXISTS beta(id TEXT PRIMARY KEY);\n"
    )
    runner = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=migrations_path,
    )
    with runner.connect() as connection:
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
            ("999_outside_package.sql",),
        )

    plan = runner.plan()

    assert plan.available_versions == ["001_initial.sql", "002_next.sql"]
    assert plan.applied_versions == ["001_initial.sql"]
    assert plan.pending_versions == ["002_next.sql"]
    assert plan.unknown_applied_versions == ["999_outside_package.sql"]
    assert plan.up_to_date is False
    with runner.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "beta" not in tables


def test_migration_runner_plan_does_not_create_migration_table(tmp_path: Path):
    database_path = tmp_path / "taroai.sqlite3"
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS alpha(id TEXT PRIMARY KEY);\n"
    )
    runner = MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=migrations_path,
    )

    plan = runner.plan()

    assert plan.available_versions == ["001_initial.sql"]
    assert plan.applied_versions == []
    assert plan.pending_versions == ["001_initial.sql"]
    assert plan.unknown_applied_versions == []
    assert plan.up_to_date is False
    with runner.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "schema_migrations" not in tables
    assert "alpha" not in tables


def test_migration_tooling_defaults_to_plan_mode(tmp_path: Path):
    config = parse_migration_args(
        [
            "--database-url",
            f"sqlite:///{tmp_path / 'taroai.sqlite3'}",
            "--migrations-path",
            str(tmp_path),
        ]
    )

    assert config.database_url == f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    assert config.migrations_path == tmp_path
    assert config.mode == "plan"


def test_migration_tooling_runs_plan_without_applying(tmp_path: Path):
    database_path = tmp_path / "taroai.sqlite3"
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS alpha(id TEXT PRIMARY KEY);\n"
    )
    config = parse_migration_args(
        [
            "--database-url",
            f"sqlite:///{database_path}",
            "--migrations-path",
            str(migrations_path),
        ]
    )

    result = run_migration_command(config)

    assert result.pending_versions == ["001_initial.sql"]
    with MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=migrations_path,
    ).connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "alpha" not in tables


def test_migration_tooling_applies_only_when_requested(tmp_path: Path):
    database_path = tmp_path / "taroai.sqlite3"
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS alpha(id TEXT PRIMARY KEY);\n"
    )
    config = parse_migration_args(
        [
            "--database-url",
            f"sqlite:///{database_path}",
            "--migrations-path",
            str(migrations_path),
            "--apply",
        ]
    )

    result = run_migration_command(config)

    assert result.applied_versions == ["001_initial.sql"]
    with MigrationRunner(
        config=DatabaseConfig(url=f"sqlite:///{database_path}"),
        migrations_path=migrations_path,
    ).connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "alpha" in tables


def test_migration_cli_module_outputs_clean_json_without_runtime_warning(
    tmp_path: Path,
):
    database_path = tmp_path / "taroai.sqlite3"
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS alpha(id TEXT PRIMARY KEY);\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = "apps/api/src"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "taroai.db.migration_cli",
            "--database-url",
            f"sqlite:///{database_path}",
            "--migrations-path",
            str(migrations_path),
        ],
        cwd=Path("."),
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "RuntimeWarning" not in completed.stderr
    assert '"pending_versions"' in completed.stdout


def test_sql_repository_persists_run_events_and_runtime_state_across_instances(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))

    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Research this prospect.",
            attachments=["file_123"],
            mode="autonomous",
        ),
    )
    runtime_state = AgentRuntimeState(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id=run.id,
        goal=run.message,
        status=RunStatus.RUNNING,
        plan=[
            PlanStep(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
        current_step_id="step_research",
        approved_guardrail_keys=["model_request:rule_1"],
        pending_guardrail_approval_key="model_request:rule_2",
        pending_guardrail_approval_stage="model_request",
        sandbox_session_id="sandbox_session_1",
        browser_session_id="browser_session_1",
        iteration=3,
        max_iterations=9,
        active_plan_revision=2,
        repair_attempts=1,
        replan_count=1,
        steering_messages=["Keep the report concise."],
        checkpoint_sequence=4,
    )
    repository.save_runtime_state(runtime_state)
    repository.save_runtime_state(
        runtime_state.model_copy(
            update={
                "iteration": 4,
                "steering_messages": ["Add an executive summary."],
                "checkpoint_sequence": 5,
            }
        )
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    events = restarted.list_run_events("tenant_acme", run.id)
    snapshot = restarted.get_runtime_state("tenant_acme", run.id)

    assert persisted_run.message == "Research this prospect."
    assert persisted_run.attachments == ["file_123"]
    assert [event.type for event in events] == [
        "run.created",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert [
        event.sequence
        for event in restarted.list_run_events("tenant_acme", run.id, after_sequence=2)
    ] == [3, 4]
    assert snapshot.status == RunStatus.RUNNING
    assert snapshot.plan[0]["tool_name"] == "research.lookup"
    assert snapshot.approved_guardrail_keys == ["model_request:rule_1"]
    assert snapshot.pending_guardrail_approval_key == "model_request:rule_2"
    assert snapshot.pending_guardrail_approval_stage == "model_request"
    assert snapshot.sandbox_session_id == "sandbox_session_1"
    assert snapshot.browser_session_id == "browser_session_1"
    assert snapshot.state_payload["iteration"] == 4
    assert snapshot.state_payload["steering_messages"] == [
        "Add an executive summary."
    ]
    restored_payload = snapshot.to_runtime_state_payload()
    assert restored_payload["max_iterations"] == 9
    assert restored_payload["active_plan_revision"] == 2
    assert restored_payload["repair_attempts"] == 1
    assert restored_payload["replan_count"] == 1
    assert restored_payload["checkpoint_sequence"] == 5

    with pytest.raises(NotFoundError):
        restarted.get_run("tenant_other", run.id)


def test_sql_repository_falls_back_to_legacy_projection_for_empty_state_payload(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'legacy-runtime-state.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Continue a legacy run.",
        ),
    )
    repository.save_runtime_state(
        AgentRuntimeState(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id=run.id,
            goal=run.message,
            status=RunStatus.RUNNING,
            current_step_id="step_legacy",
            completed_step_ids=["step_previous"],
        )
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE runtime_states SET state_payload = ? WHERE run_id = ?",
            ("{}", run.id),
        )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    snapshot = restarted.get_runtime_state("tenant_acme", run.id)
    restored_payload = snapshot.to_runtime_state_payload()

    assert snapshot.state_payload == {}
    assert restored_payload["goal"] == "Continue a legacy run."
    assert restored_payload["current_step_id"] == "step_legacy"
    assert restored_payload["completed_step_ids"] == ["step_previous"]
    assert "state_payload" not in restored_payload


def test_sql_repository_hydrates_postgresql_native_json_and_datetime_values():
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url="postgresql://example"))
    now = datetime(2026, 7, 3, 13, 40, tzinfo=timezone.utc)

    event = repository._audit_event_from_row(
        {
            "id": "audit_1",
            "tenant_id": "tenant_acme",
            "workspace_id": None,
            "user_id": "user_owner",
            "run_id": None,
            "event_type": "tenant.bootstrap.completed",
            "metadata": {"owner_user_id": "user_owner"},
            "created_at": now,
        }
    )

    assert event.metadata == {"owner_user_id": "user_owner"}
    assert event.created_at == now


def test_sql_repository_get_run_uses_tenant_scoped_lookup(monkeypatch):
    executed_sql: list[str] = []
    now = datetime(2026, 7, 3, 13, 50, tzinfo=timezone.utc)

    class Result:
        def fetchone(self):
            return {
                "id": "run_1",
                "tenant_id": "tenant_acme",
                "workspace_id": "workspace_acme",
                "user_id": "user_owner",
                "agent_id": None,
                "message": "Generate report.",
                "attachments": [],
                "mode": "workflow",
                "status": "created",
                "created_at": now,
                "updated_at": now,
            }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, error_type, error, traceback):
            return None

        def execute(self, sql, params):
            executed_sql.append(" ".join(sql.split()))
            return Result()

    monkeypatch.setattr("taroai.db.repository.connect_database", lambda _config: Connection())

    repository = SqlControlPlaneRepository(config=DatabaseConfig(url="postgresql://example"))

    repository.get_run("tenant_acme", "run_1")

    assert executed_sql == ["SELECT * FROM runs WHERE tenant_id = ? AND id = ?"]


def test_sql_repository_get_runtime_state_uses_tenant_scoped_lookup(monkeypatch):
    executed: list[tuple[str, tuple]] = []

    class Result:
        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, error_type, error, traceback):
            return None

        def execute(self, sql, params):
            executed.append((" ".join(sql.split()), tuple(params)))
            return Result()

    monkeypatch.setattr("taroai.db.repository.connect_database", lambda _config: Connection())
    monkeypatch.setattr(SqlControlPlaneRepository, "get_run", lambda *_args: None)
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url="postgresql://example"))

    with pytest.raises(NotFoundError, match="Runtime state not found"):
        repository.get_runtime_state("tenant_acme", "run_1")

    assert executed == [
        (
            "SELECT * FROM runtime_states WHERE tenant_id = ? AND run_id = ?",
            ("tenant_acme", "run_1"),
        )
    ]


def test_sql_repository_append_run_event_sequence_lookup_is_tenant_scoped():
    executed: list[tuple[str, tuple]] = []
    now = datetime(2026, 7, 4, 0, 20, tzinfo=timezone.utc)

    class SelectResult:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class InsertResult:
        def fetchone(self):
            return None

    class Connection:
        def execute(self, sql, params):
            normalized = " ".join(sql.split())
            executed.append((normalized, tuple(params)))
            if "FOR UPDATE" in normalized:
                return SelectResult({"id": "run_1"})
            if "MAX(sequence)" in normalized:
                return SelectResult({"next_sequence": 7})
            return InsertResult()

    repository = SqlControlPlaneRepository(config=DatabaseConfig(url="postgresql://example"))
    run = Run(
        id="run_1",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        agent_id=None,
        message="Generate report.",
        attachments=[],
        mode=RunMode.AUTONOMOUS,
        status=RunStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )

    event = repository._append_run_event(Connection(), run, "run.succeeded", {})

    assert event.sequence == 7
    assert executed[0] == (
        "SELECT id FROM runs WHERE tenant_id = ? AND id = ? FOR UPDATE",
        ("tenant_acme", "run_1"),
    )
    assert executed[1] == (
        "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
        "FROM run_events WHERE tenant_id = ? AND run_id = ?",
        ("tenant_acme", "run_1"),
    )


def test_sql_repository_persists_idempotency_records_across_instances(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    record = IdempotencyRecord(
        tenant_id="tenant_acme",
        key="run-create-001",
        method="POST",
        path="/api/runs",
        request_hash="hash_1",
        status_code=201,
        response_body={
            "run_id": "run_1",
            "status": "created",
            "events_url": "/api/runs/run_1/events",
        },
        created_at=utc_now(),
    )

    repository.save_idempotency_record(record)
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))

    persisted = restarted.get_idempotency_record(
        tenant_id="tenant_acme",
        key="run-create-001",
        method="POST",
        path="/api/runs",
    )

    assert persisted == record
    assert (
        restarted.get_idempotency_record(
            tenant_id="tenant_other",
            key="run-create-001",
            method="POST",
            path="/api/runs",
        )
        is None
    )


def test_sql_repository_persists_active_license_validation_across_instances(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    issued_at = utc_now()
    expires_at = issued_at.replace(year=issued_at.year + 1)
    validation = LicenseValidationResult(
        license=LicenseKey(
            id="license_acme_private",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=issued_at,
            expires_at=expires_at,
            deployment_modes=["private"],
            entitlements=[
                Entitlement(
                    feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT,
                    limit=3,
                )
            ],
            offline_validation_allowed=True,
        ),
        status=LicenseStatus.ACTIVE,
        deployment_mode="private",
        source="signed_offline_file",
    )

    repository.save_license_validation(validation)
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))

    persisted = restarted.get_active_license_validation("tenant_acme")

    assert persisted == validation
    assert restarted.get_active_license_validation("tenant_other") is None


def test_sql_repository_persists_status_artifacts_approvals_and_reads(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )

    repository.update_run_status("tenant_acme", run.id, RunStatus.QUEUED)
    artifact = repository.create_artifact(
        tenant_id="tenant_acme",
        run_id=run.id,
        name="agent-result.md",
        artifact_type="markdown",
        uri="s3://taroai-artifacts/tenant_acme/workspace_sales/runs/run_123/artifacts/agent-result.md",
    )
    approval = repository.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    artifacts = restarted.list_artifacts("tenant_acme", run.id)
    approvals = restarted.list_approval_requests("tenant_acme", run.id)
    meters = restarted.list_billing_meters("tenant_acme")
    audits = restarted.list_audit_events("tenant_acme")

    assert persisted_run.status == RunStatus.QUEUED
    assert artifacts == [artifact]
    assert approvals == [approval]
    assert [meter.meter_type for meter in meters] == ["run_count"]
    assert [event.event_type for event in audits] == [
        "billing.metered",
        "run.created",
    ]

    resolved = restarted.resolve_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )
    assert resolved.status == ApprovalStatus.APPROVED
    assert (
        restarted.list_approval_requests("tenant_acme", run.id)[0].status
        == ApprovalStatus.APPROVED
    )
    approval_audits = [
        event
        for event in restarted.list_audit_events("tenant_acme")
        if event.event_type == "approval.resolved"
    ]
    assert len(approval_audits) == 1
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "approved",
    }


def test_sql_repository_audit_tie_break_is_deterministic(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        "tenant_acme",
        "user_owner",
        RunCreate(workspace_id="workspace_sales", message="Create a report."),
    )
    fixed_time = datetime(2026, 7, 11, 4, 0, tzinfo=timezone.utc).isoformat()
    with repository._connect() as connection:
        connection.execute(
            "DELETE FROM audit_events WHERE tenant_id = ?",
            ("tenant_acme",),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, tenant_id, workspace_id, user_id, run_id,
                event_type, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "audit_a_run_created",
                "tenant_acme",
                "workspace_sales",
                "user_owner",
                run.id,
                "run.created",
                "{}",
                fixed_time,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, tenant_id, workspace_id, user_id, run_id,
                event_type, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "audit_z_billing_metered",
                "tenant_acme",
                "workspace_sales",
                "user_owner",
                run.id,
                "billing.metered",
                "{}",
                fixed_time,
            ),
        )

    assert [
        event.event_type for event in repository.list_audit_events("tenant_acme")
    ] == ["billing.metered", "run.created"]


def test_sql_repository_persists_approval_rejection(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )
    approval = repository.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    rejected = repository.reject_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        rejected_by_user_id="manager_1",
    )
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted = restarted.list_approval_requests("tenant_acme", run.id)[0]

    assert rejected.status == ApprovalStatus.REJECTED
    assert persisted.status == ApprovalStatus.REJECTED
    assert persisted.resolved_by_user_id == "manager_1"
    approval_audits = [
        event
        for event in restarted.list_audit_events("tenant_acme")
        if event.event_type == "approval.rejected"
    ]
    assert len(approval_audits) == 1
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "rejected",
    }


def test_sql_repository_persists_run_cancellation_and_pending_approval_cancellation(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )
    approval = repository.create_approval_request(
        tenant_id="tenant_acme",
        run_id=run.id,
        step_id="step_send",
        reason="Step requires approval: Send customer email",
    )

    cancelled_run = repository.cancel_run(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
        reason_code="user_requested",
    )
    cancelled_approvals = repository.cancel_pending_approval_requests(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
    )
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    persisted_approval = restarted.list_approval_requests("tenant_acme", run.id)[0]

    assert cancelled_run.status == RunStatus.CANCELLED
    assert persisted_run.status == RunStatus.CANCELLED
    assert cancelled_approvals[0].id == approval.id
    assert persisted_approval.status == ApprovalStatus.CANCELLED
    assert persisted_approval.resolved_by_user_id == "manager_1"
    audits = restarted.list_audit_events("tenant_acme")
    run_audits = [event for event in audits if event.event_type == "run.cancelled"]
    approval_audits = [
        event for event in audits if event.event_type == "approval.cancelled"
    ]
    assert run_audits[0].metadata == {
        "cancelled_by_user_id": "manager_1",
        "reason_code": "user_requested",
        "status": "cancelled",
    }
    assert approval_audits[0].metadata == {
        "approval_id": approval.id,
        "resolved_by_user_id": "manager_1",
        "status": "cancelled",
    }


def test_sql_repository_persists_run_retry_request(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )
    repository.update_run_status("tenant_acme", run.id, RunStatus.FAILED)

    retrying_run = repository.request_run_retry(
        tenant_id="tenant_acme",
        run_id=run.id,
        requested_by_user_id="manager_1",
        reason_code="operator_retry",
    )
    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    persisted_run = restarted.get_run("tenant_acme", run.id)
    retry_audits = [
        event
        for event in restarted.list_audit_events("tenant_acme")
        if event.event_type == "run.retry_requested"
    ]

    assert retrying_run.status == RunStatus.RETRYING
    assert persisted_run.status == RunStatus.RETRYING
    assert retry_audits[0].metadata == {
        "requested_by_user_id": "manager_1",
        "reason_code": "operator_retry",
        "previous_status": "failed",
        "status": "retrying",
    }


def test_sql_repository_records_tool_call_audit_and_billing(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )

    meter = repository.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="tool_call_count",
        quantity=1,
        unit="call",
        metadata={"step_id": "step_research", "tool_name": "research.lookup"},
    )
    audit = repository.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        event_type="tool.executed",
        metadata={"step_id": "step_research", "tool_name": "research.lookup"},
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    meters = restarted.list_billing_meters("tenant_acme")
    audits = restarted.list_audit_events("tenant_acme")
    event_types = [
        event.type for event in restarted.list_run_events("tenant_acme", run.id)
    ]

    assert meter in meters
    assert audit in audits
    assert event_types.count("billing.metered") == 2
    assert event_types.count("audit.recorded") == 4
    assert [
        event.event_type for event in audits if event.event_type == "billing.metered"
    ] == ["billing.metered", "billing.metered"]


def test_sql_repository_persists_embedding_billing_meter_types(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create an executive escalation plan.",
            mode="autonomous",
        ),
    )

    repository.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="embedding_call_count",
        quantity=1,
        unit="call",
        provider="openai_compatible",
        model="text-embedding-3-small",
        metadata={"purpose": "knowledge_query", "input_count": 1},
    )
    repository.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="embedding_tokens",
        quantity=5,
        unit="token",
        provider="openai_compatible",
        model="text-embedding-3-small",
        metadata={"purpose": "knowledge_query", "input_count": 1},
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    embedding_meters = [
        meter
        for meter in restarted.list_billing_meters("tenant_acme")
        if meter.meter_type.startswith("embedding")
    ]

    assert [
        (meter.meter_type, meter.quantity, meter.unit) for meter in embedding_meters
    ] == [
        ("embedding_call_count", 1, "call"),
        ("embedding_tokens", 5, "token"),
    ]
    assert {meter.provider for meter in embedding_meters} == {"openai_compatible"}
    assert {meter.model for meter in embedding_meters} == {"text-embedding-3-small"}


def test_sql_repository_persists_operation_level_billing_meters(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Create an executive escalation plan.",
            mode="autonomous",
        ),
    )
    original_run_events = repository.list_run_events("tenant_acme", run.id)

    repository.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=None,
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        meter_type="embedding_call_count",
        quantity=1,
        unit="call",
        provider="openai_compatible",
        model="text-embedding-3-small",
        metadata={"purpose": "knowledge_index", "input_count": 2},
    )

    restarted = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    operation_meters = [
        meter
        for meter in restarted.list_billing_meters("tenant_acme")
        if meter.run_id is None and meter.meter_type == "embedding_call_count"
    ]

    assert len(operation_meters) == 1

    billing_audits = [
        event
        for event in restarted.list_audit_events("tenant_acme")
        if event.metadata.get("meter_id") == operation_meters[0].id
    ]

    assert operation_meters[0].workspace_id == run.workspace_id
    assert operation_meters[0].user_id == run.user_id
    assert restarted.list_run_events("tenant_acme", run.id) == original_run_events
    assert billing_audits[0].run_id is None
    assert billing_audits[0].event_type == "billing.metered"

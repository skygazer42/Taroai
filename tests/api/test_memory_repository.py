from pathlib import Path

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import utc_now
from taroai.memory import (
    MemoryScopeType,
    MemoryStatus,
    MemoryWriteRequest,
    ShortTermMemoryReview,
    ShortTermMemoryReviewStatus,
)


def write_request() -> MemoryWriteRequest:
    return MemoryWriteRequest(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        scope_type=MemoryScopeType.TEAM,
        scope_id="team_sales",
        source_run_id="run_123",
        content="Use the account renewal checklist for late-stage deals.",
        created_by="user_1",
        metadata={"source": "approval_review"},
        sensitivity_level=2,
        confidence=0.85,
    )


def test_sql_long_term_memory_persists_records_by_tenant_and_scope(tmp_path: Path):
    from taroai.memory.repository import SqlLongTermMemoryService

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))

    memory = service.write(write_request())

    restarted = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))
    records = restarted.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales")

    assert len(records) == 1
    assert records[0].id == memory.id
    assert (
        records[0].content == "Use the account renewal checklist for late-stage deals."
    )
    assert records[0].metadata == {"source": "approval_review"}
    assert records[0].sensitivity_level == 2
    assert records[0].confidence == 0.85
    assert (
        restarted.list_by_scope("tenant_other", MemoryScopeType.TEAM, "team_sales")
        == []
    )


def test_sql_memory_repository_accepts_native_postgres_values():
    from taroai.memory.repository import SqlLongTermMemoryService

    service = SqlLongTermMemoryService(config=DatabaseConfig(url="sqlite:///:memory:"))
    now = utc_now()

    assert service._loads({"source": "postgres-jsonb"}) == {"source": "postgres-jsonb"}
    assert service._parse_dt(now) is now


def test_sql_long_term_memory_forget_redacts_one_record(tmp_path: Path):
    from taroai.memory.repository import SqlLongTermMemoryService

    database_url = f"sqlite:///{tmp_path / 'taroai-forget-memory.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))
    memory = service.write(write_request())

    forgotten = service.forget("tenant_acme", memory.id)

    assert forgotten.status == MemoryStatus.EXPIRED
    assert forgotten.content == ""
    assert forgotten.metadata == {}
    assert service.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales") == []


def test_sql_long_term_memory_delete_for_tenant_expires_and_redacts_records(
    tmp_path: Path,
):
    from taroai.memory.repository import SqlLongTermMemoryService

    database_url = f"sqlite:///{tmp_path / 'taroai-delete-memory.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))
    memory = service.write(write_request())
    other = service.write(
        write_request().model_copy(
            update={
                "tenant_id": "tenant_other",
                "workspace_id": "workspace_other",
                "scope_id": "team_other",
                "source_run_id": "run_other",
                "content": "Other tenant memory.",
            }
        )
    )

    deleted_ids = service.delete_for_tenant("tenant_acme")
    restarted = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))
    expired = restarted.get("tenant_acme", memory.id)

    assert deleted_ids == [memory.id]
    assert expired.status == MemoryStatus.EXPIRED
    assert expired.content == ""
    assert expired.metadata == {}
    assert restarted.get("tenant_other", other.id).content == "Other tenant memory."
    assert (
        restarted.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales") == []
    )


def test_sql_short_term_memory_review_store_persists_review_state(tmp_path: Path):
    from taroai.memory.repository import SqlShortTermMemoryReviewStore

    database_url = f"sqlite:///{tmp_path / 'taroai-short-review.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlShortTermMemoryReviewStore(config=DatabaseConfig(url=database_url))
    now = utc_now()
    review = ShortTermMemoryReview(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_123",
        key="planner.scratchpad",
        value={"note": "requires review"},
        ttl_seconds=300,
        created_by="user_1",
        created_at=now,
        expires_at=now,
        guardrail_metadata={
            "guardrail_action": "require_approval",
            "guardrail_rule_ids": ["rule_1"],
        },
    )

    stored = store.save_review(review)
    approved = store.save_review(
        stored.model_copy(
            update={
                "status": ShortTermMemoryReviewStatus.APPROVED,
                "approved_by_user_id": "manager_1",
                "approved_at": now,
                "activated_entry_expires_at": now,
            }
        )
    )

    restarted = SqlShortTermMemoryReviewStore(config=DatabaseConfig(url=database_url))

    assert (
        restarted.get_review("tenant_acme", review.id).status
        == ShortTermMemoryReviewStatus.APPROVED
    )
    assert (
        restarted.get_review("tenant_acme", review.id).approved_by_user_id
        == "manager_1"
    )
    assert restarted.get_review("tenant_acme", review.id).guardrail_metadata == {
        "guardrail_action": "require_approval",
        "guardrail_rule_ids": ["rule_1"],
    }
    assert restarted.list_reviews(
        "tenant_acme",
        run_id="run_123",
        status=ShortTermMemoryReviewStatus.APPROVED,
    ) == [approved]
    assert restarted.list_reviews("tenant_other") == []

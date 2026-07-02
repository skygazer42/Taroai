from pathlib import Path

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.memory import (
    InMemoryLongTermMemoryService,
    MemoryScopeType,
    MemoryWriteRequest,
)


def memory_request() -> MemoryWriteRequest:
    return MemoryWriteRequest(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        scope_type=MemoryScopeType.TEAM,
        scope_id="team_sales",
        source_run_id="run_123",
        content="Use the approved renewal checklist for enterprise accounts.",
        created_by="agent_sales",
        metadata={"source": "run_summary"},
        sensitivity_level=1,
        confidence=0.9,
    )


def test_long_term_memory_candidates_require_review_before_active_reads():
    service = InMemoryLongTermMemoryService()

    candidate = service.propose_candidate(memory_request())
    active_before_review = service.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales")
    approved = service.approve(candidate.tenant_id, candidate.id, reviewed_by_user_id="manager_1")
    active_after_review = service.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales")
    rejected_candidate = service.propose_candidate(
        memory_request().model_copy(update={"content": "Rejected guidance."})
    )
    rejected = service.reject(
        rejected_candidate.tenant_id,
        rejected_candidate.id,
        reviewed_by_user_id="manager_1",
    )

    assert candidate.status == "candidate"
    assert active_before_review == []
    assert approved.status == "active"
    assert active_after_review == [approved]
    assert rejected.status == "rejected"
    assert service.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales") == [approved]


def test_sql_long_term_memory_persists_candidate_review_state(tmp_path: Path):
    from taroai.memory.repository import SqlLongTermMemoryService

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))

    candidate = service.propose_candidate(memory_request())
    approved = service.approve(candidate.tenant_id, candidate.id, reviewed_by_user_id="manager_1")

    restarted = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))
    active_records = restarted.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales")

    assert candidate.status == "candidate"
    assert approved.status == "active"
    assert [record.id for record in active_records] == [candidate.id]
    assert restarted.get("tenant_acme", candidate.id).status == "active"

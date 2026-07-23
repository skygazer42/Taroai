from pathlib import Path

import pytest

from taroai.agent import AgentRuntime
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import RunCreate
from taroai.memory import (
    InMemoryLongTermMemoryService,
    MemoryScopeType,
    MemoryWriteRequest,
)
from taroai.memory.tools import register_memory_tool_handler
from taroai.tool_gateway import (
    ToolApprovalRequiredError,
    ToolGateway,
    ToolGatewayRequest,
)
from taroai.store import InMemoryControlPlaneStore


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
    active_before_review = service.list_by_scope(
        "tenant_acme", MemoryScopeType.TEAM, "team_sales"
    )
    approved = service.approve(
        candidate.tenant_id, candidate.id, reviewed_by_user_id="manager_1"
    )
    active_after_review = service.list_by_scope(
        "tenant_acme", MemoryScopeType.TEAM, "team_sales"
    )
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
    assert service.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales") == [
        approved
    ]


def test_agent_memory_tool_requires_approval_and_saves_only_for_the_current_user():
    service = InMemoryLongTermMemoryService()
    gateway = ToolGateway()
    register_memory_tool_handler(gateway, service)
    request = ToolGatewayRequest(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id="run_123",
        step_id="step_memory",
        tool_name="memory.save",
        tool_input={
            "content": "I prefer concise Chinese answers.",
            "memory_key": "profile.response_style",
        },
        granted_scopes=["memory.write"],
    )

    with pytest.raises(ToolApprovalRequiredError):
        gateway.execute_request(request)

    result = gateway.execute_request(request.model_copy(update={"approved": True}))
    records = service.list_by_scope(
        "tenant_acme",
        MemoryScopeType.USER,
        "user_1",
    )

    assert result.output["memory_id"] == records[0].id
    assert records[0].content == "I prefer concise Chinese answers."
    assert records[0].source_run_id == "run_123"
    assert records[0].status == "active"
    assert records[0].metadata["memory_key"] == "profile.response_style"


def test_agent_memory_tool_indexes_the_request_for_cross_thread_recall():
    store = InMemoryControlPlaneStore()
    source_run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="请记住：我的跨线程验收代号是青竹-724。",
            mode="chat",
        ),
    )
    service = InMemoryLongTermMemoryService()
    gateway = ToolGateway()
    register_memory_tool_handler(gateway, service, store)
    gateway.execute_request(
        ToolGatewayRequest(
            tenant_id=source_run.tenant_id,
            workspace_id=source_run.workspace_id,
            user_id=source_run.user_id,
            run_id=source_run.id,
            step_id="step_memory",
            tool_name="memory.save",
            tool_input={
                "content": "青竹-724",
                "memory_key": "cross_thread_acceptance_code",
            },
            granted_scopes=["memory.write"],
            approved=True,
        )
    )
    recall_run = store.create_run(
        source_run.tenant_id,
        source_run.user_id,
        RunCreate(
            workspace_id=source_run.workspace_id,
            message="我的跨线程验收代号是什么？",
            mode="chat",
        ),
    )

    recalled = AgentRuntime(
        store=store,
        long_term_memory_service=service,
    )._load_memory_context(recall_run)

    assert [record.content for record in recalled] == ["青竹-724"]


def test_agent_memory_is_recalled_only_by_the_same_agent():
    store = InMemoryControlPlaneStore()
    service = InMemoryLongTermMemoryService()
    gateway = ToolGateway()
    register_memory_tool_handler(gateway, service, store)

    def create_run(agent_id: str | None = None):
        return store.create_run(
            "tenant_acme",
            "user_1",
            RunCreate(
                workspace_id="workspace_sales",
                agent_id=agent_id,
                message="这个 Agent 的发布格式是什么？",
                mode="autonomous" if agent_id else "chat",
            ),
        )

    source = create_run("agent_a")
    request = ToolGatewayRequest(
        tenant_id=source.tenant_id,
        workspace_id=source.workspace_id,
        user_id=source.user_id,
        run_id=source.id,
        step_id="step_memory",
        tool_name="memory.save",
        tool_input={
            "content": "这个 Agent 的发布格式使用简短中文。",
            "memory_key": "agent.release_format",
        },
        granted_scopes=["memory.write"],
        approved=True,
    )
    with pytest.raises(ValueError, match="does not belong"):
        gateway.execute_request(request.model_copy(update={"user_id": "user_2"}))
    result = gateway.execute_request(request)
    runtime = AgentRuntime(store=store, long_term_memory_service=service)

    assert result.output["scope_type"] == MemoryScopeType.AGENT.value
    assert result.output["scope_id"] == "agent_a"
    assert service.list_by_scope("tenant_acme", MemoryScopeType.USER, "user_1") == []
    assert [
        record.content for record in runtime._load_memory_context(create_run("agent_a"))
    ] == ["这个 Agent 的发布格式使用简短中文。"]
    assert runtime._load_memory_context(create_run("agent_b")) == []
    assert runtime._load_memory_context(create_run()) == []


def test_agent_memory_tool_replaces_only_the_current_users_matching_fact():
    service = InMemoryLongTermMemoryService()
    old = service.write(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.USER,
            scope_id="user_1",
            source_run_id="run_old",
            content="My demo code is OLD.",
            created_by="user_1",
        )
    )
    gateway = ToolGateway()
    register_memory_tool_handler(gateway, service)

    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_new",
            step_id="step_memory",
            tool_name="memory.save",
            tool_input={
                "content": "My demo code is NEW.",
                "memory_key": "profile.demo_code",
                "supersedes_memory_ids": [old.id],
            },
            granted_scopes=["memory.write"],
            approved=True,
        )
    )

    active = service.list_by_scope("tenant_acme", MemoryScopeType.USER, "user_1")
    assert [record.content for record in active] == ["My demo code is NEW."]
    assert result.output["superseded_memory_ids"] == [old.id]


def test_agent_memory_tool_rejects_generic_keys_before_writing():
    service = InMemoryLongTermMemoryService()
    gateway = ToolGateway()
    register_memory_tool_handler(gateway, service)

    with pytest.raises(ValueError, match="must identify the specific fact"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_new",
                step_id="step_memory",
                tool_name="memory.save",
                tool_input={"content": "My demo code is NEW.", "memory_key": "legacy"},
                granted_scopes=["memory.write"],
                approved=True,
            )
        )

    assert service.list_by_scope("tenant_acme", MemoryScopeType.USER, "user_1") == []


def test_sql_long_term_memory_persists_candidate_review_state(tmp_path: Path):
    from taroai.memory.repository import SqlLongTermMemoryService

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    service = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))

    candidate = service.propose_candidate(memory_request())
    approved = service.approve(
        candidate.tenant_id, candidate.id, reviewed_by_user_id="manager_1"
    )

    restarted = SqlLongTermMemoryService(config=DatabaseConfig(url=database_url))
    active_records = restarted.list_by_scope(
        "tenant_acme", MemoryScopeType.TEAM, "team_sales"
    )

    assert candidate.status == "candidate"
    assert approved.status == "active"
    assert [record.id for record in active_records] == [candidate.id]
    assert restarted.get("tenant_acme", candidate.id).status == "active"

from types import SimpleNamespace

import pytest

from taroai.agents import (
    AgentDefinitionCreate,
    AgentRegistryService,
    AgentRunRequest,
    AgentVersionSpec,
    InMemoryAgentRegistry,
    register_agent_tool_handlers,
)
from taroai.agent import AgentRuntime, AgentRuntimeState
from taroai.agent.loop import AgentExecutionServices
from taroai.agent.models import AgentAction, AgentCycle, AgentDecision, AgentObservation
from taroai.connectors import ConnectorStatus, ConnectorType
from taroai.domain import (
    ChatMessageCreate,
    ChatThreadCreate,
    ResourceReference,
    RunCreate,
    RunMode,
    RunStatus,
)
from taroai.skills.discovery import SkillDiscoverySummary
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import ToolGateway, ToolGatewayRequest


def _request(run, tool_name: str, tool_input: dict):
    return ToolGatewayRequest(
        tenant_id=run.tenant_id,
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        step_id="step_agent_app",
        tool_name=tool_name,
        tool_input=tool_input,
    )


def test_agent_tools_create_and_version_a_draft_from_chat():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Create an agent",
            resource_refs=[
                ResourceReference(type="skill", id="skill.bound"),
                ResourceReference(type="connector", id="connector_bound"),
            ],
        ),
    )
    store.save_runtime_state(
        AgentRuntimeState(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            goal=run.message,
            status=RunStatus.RUNNING,
            runtime_metadata={
                "used_skills": [
                    {
                        "skill_id": "skill.bound",
                        "version": "1.2.3",
                        "package_digest": "package-digest",
                        "source_digest": "source-digest",
                    }
                ]
            },
        )
    )
    registry = InMemoryAgentRegistry()
    gateway = ToolGateway()
    register_agent_tool_handlers(
        gateway, AgentRegistryService(registry=registry, store=store)
    )

    created = gateway.execute_request(
        _request(
            run,
            "agent.create_draft",
            {
                "name": "Account brief",
                "description": "Prepare an account brief",
                "instructions": "Research the account and return a sourced brief.",
            },
        )
    )
    agent_id = created.output["agent_id"]

    updated = gateway.execute_request(
        _request(
            run,
            "agent.update_draft",
            {
                "agent_id": agent_id,
                "instructions": "Research the account and return a concise sourced brief.",
                "change_note": "Make output concise",
            },
        )
    )

    assert updated.output["version"] == 2
    assert registry.get_version(
        "tenant_acme", agent_id, 1
    ).spec.instructions.startswith("Research the account")
    assert registry.get_version("tenant_acme", agent_id, 2).spec.change_note == (
        "Make output concise"
    )
    created_spec = registry.get_version("tenant_acme", agent_id, 1).spec
    assert created_spec.skill_bindings == [
        {
            "id": "skill.bound",
            "version": "1.2.3",
            "package_digest": "package-digest",
            "source_digest": "source-digest",
        }
    ]
    assert created_spec.connector_bindings == [{"id": "connector_bound"}]
    assert [
        event.type
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type.startswith("app_")
    ] == ["app_created", "app_updated"]


def test_reusable_agent_only_discovers_bound_skills_and_connectors():
    skill = SkillDiscoverySummary(
        skill_id="skill.bound",
        version="1.0.0",
        name="Bound skill",
        description="Only available when pinned.",
        package_digest="package-digest",
        source_digest="source-digest",
        input_schema={"type": "object", "properties": {}},
        risk_level="low",
    )

    class SkillService:
        def discover(self, **_):
            return [skill]

    capability = SimpleNamespace(
        name="lookup",
        description="Bound connector",
        enabled=True,
        input_schema={"type": "object", "properties": {}},
        required_scopes=[],
        risk_level="low",
        approval_required=False,
    )
    connector = SimpleNamespace(
        id="connector_bound",
        status=ConnectorStatus.ENABLED,
        type=ConnectorType.SAAS,
        display_name="Bound connector",
        metadata={},
        capabilities=[capability],
    )

    class ConnectorRegistry:
        def list_connectors(self, *_):
            return [connector]

    store = InMemoryControlPlaneStore()
    runtime = AgentRuntime(
        store=store,
        skill_service=SkillService(),
        connector_registry=ConnectorRegistry(),
    )
    services = AgentExecutionServices(runtime)
    agent_ref = ResourceReference(type="agent", id="agent_1", version="1")
    unbound = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Run the reusable agent",
            resource_refs=[agent_ref],
        ),
    )
    bound = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Run the reusable agent",
            resource_refs=[
                agent_ref,
                ResourceReference(type="skill", id="skill.bound", version="1.0.0"),
                ResourceReference(type="connector", id="connector_bound"),
            ],
        ),
    )

    assert services._discover_skill_summaries(unbound) == {}
    assert services._discover_connector_tools(unbound) == []
    assert set(services._discover_skill_summaries(bound)) == {"skill.bound"}
    assert [
        item["connector_id"] for item in services._discover_connector_tools(bound)
    ] == ["connector_bound"]


def test_agent_update_cannot_cross_workspace():
    store = InMemoryControlPlaneStore()
    first_run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_one", message="Create an agent"),
    )
    other_run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_two", message="Update an agent"),
    )
    registry = InMemoryAgentRegistry()
    gateway = ToolGateway()
    register_agent_tool_handlers(
        gateway, AgentRegistryService(registry=registry, store=store)
    )
    agent_id = gateway.execute_request(
        _request(
            first_run,
            "agent.create_draft",
            {"name": "Scoped", "instructions": "Stay in this workspace."},
        )
    ).output["agent_id"]

    try:
        gateway.execute_request(
            _request(
                other_run,
                "agent.update_draft",
                {"agent_id": agent_id, "name": "Wrong workspace"},
            )
        )
    except ValueError as error:
        assert str(error) == "Agent is not available in this workspace"
    else:
        raise AssertionError("cross-workspace Agent update should fail")


def test_agent_app_kind_selects_default_run_mode():
    store = InMemoryControlPlaneStore()
    registry = InMemoryAgentRegistry()
    service = AgentRegistryService(registry=registry, store=store)
    modes = {}

    for app_kind in ("agent", "workflow"):
        definition, _ = service.create(
            "tenant_acme",
            "user_1",
            AgentDefinitionCreate(
                workspace_id="workspace_sales",
                name=f"{app_kind} app",
                app_kind=app_kind,
                version=AgentVersionSpec(instructions="Complete the structured input."),
            ),
        )
        registry.publish("tenant_acme", definition.id, 1)
        invocation = service.run(
            "tenant_acme", "user_1", definition.id, AgentRunRequest()
        )
        modes[app_kind] = store.get_run("tenant_acme", invocation.run_id).mode

    assert modes == {"agent": RunMode.AUTONOMOUS, "workflow": RunMode.WORKFLOW}


def test_conversation_extraction_uses_the_runtime_model_instead_of_hiding_a_pin():
    store = InMemoryControlPlaneStore()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales", title="Reusable chat"),
    )
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            message="Create a concise answer.",
            provider_id="retired-provider",
            model_id="retired-model",
        ),
    )
    store.update_run_status("tenant_acme", run.id, RunStatus.SUCCEEDED)

    draft = AgentRegistryService(registry=InMemoryAgentRegistry(), store=store).extract(
        "tenant_acme", thread.id
    )

    assert draft.version.model_policy == {}


def test_conversation_can_compile_one_successful_sandbox_action_into_a_playbook():
    store = InMemoryControlPlaneStore()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales", title="Repeatable math"),
    )
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            message="Calculate the result.",
        ),
    )
    cycle = store.create_agent_cycle(
        AgentCycle(
            id="cycle_source",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            thread_id=thread.id,
            iteration=1,
        )
    )
    action = store.create_agent_action(
        AgentAction(
            id="action_source",
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            thread_id=thread.id,
            cycle_id=cycle.id,
            action_key="source-action",
            decision=AgentDecision(
                kind="action",
                tool_name="sandbox.command",
                tool_input={
                    "command": 'python3 -c "print(42)"',
                    "session_id": "sandbox_ephemeral",
                    "result_mode": "raw_stdout",
                },
            ),
        )
    )
    claimed = store.claim_agent_action(
        run.tenant_id,
        action.id,
        lease_owner_id="worker_test",
        lease_seconds=60,
    )
    assert claimed is not None
    state = AgentRuntimeState(
        tenant_id=run.tenant_id,
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        goal=run.message,
        status=RunStatus.RUNNING,
    )
    store.commit_agent_action_observation(
        run.tenant_id,
        action.id,
        AgentObservation(
            action_id=action.id,
            success=True,
            output={"stdout": "42", "exit_code": 0},
        ),
        lease_owner_id="worker_test",
        lease_generation=claimed.lease_generation,
        usage={},
        state_payload=state.model_dump(mode="json"),
        checksum="test-checksum",
    )
    store.update_run_status(run.tenant_id, run.id, RunStatus.SUCCEEDED)

    draft = AgentRegistryService(
        registry=InMemoryAgentRegistry(),
        store=store,
    ).extract(
        run.tenant_id,
        thread.id,
        compile_playbook=True,
    )

    playbook = draft.version.runtime_snapshot["compiled_playbook"]
    assert draft.version.input_schema == {"type": "object", "properties": {}}
    assert playbook["schema"] == "taroai.playbook.v1"
    assert playbook["result"] == {"mode": "raw_stdout"}
    assert playbook["decision"]["tool_input"] == {
        "command": 'python3 -c "print(42)"',
        "result_mode": "raw_stdout",
    }
    assert playbook["decision"]["action_key"] == "playbook:action_source"


def test_conversation_extraction_keeps_explicit_files_not_transient_attachments():
    store = InMemoryControlPlaneStore()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales", title="Reusable chat"),
    )
    store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_1",
        ChatMessageCreate(
            content="Use the attached example with the pinned reference.",
            attachments=["storage_transient"],
            resource_refs=[ResourceReference(type="file", id="storage_pinned")],
        ),
    )
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            message="Complete the task.",
            attachments=["storage_transient"],
        ),
    )
    store.update_run_status("tenant_acme", run.id, RunStatus.SUCCEEDED)

    draft = AgentRegistryService(registry=InMemoryAgentRegistry(), store=store).extract(
        "tenant_acme", thread.id
    )

    assert draft.version.reference_files == [
        {"storage_object_id": "storage_pinned"}
    ]


def test_agent_extraction_does_not_hide_runtime_snapshot_failures():
    def fail(*_):
        raise RuntimeError("database down")

    with pytest.raises(RuntimeError, match="database down"):
        AgentRegistryService(
            registry=InMemoryAgentRegistry(),
            store=SimpleNamespace(get_runtime_state=fail),
        )._runtime_snapshot("tenant_acme", "run_1")


def test_agent_run_can_override_its_pinned_model():
    store = InMemoryControlPlaneStore()
    registry = InMemoryAgentRegistry()
    service = AgentRegistryService(registry=registry, store=store)
    definition, _ = service.create(
        "tenant_acme",
        "user_1",
        AgentDefinitionCreate(
            workspace_id="workspace_sales",
            name="Pinned model app",
            version=AgentVersionSpec(
                instructions="Complete the structured input.",
                model_policy={
                    "provider_id": "deepseek",
                    "model_id": "deepseek-v4-flash",
                    "reasoning_effort": "none",
                },
            ),
        ),
    )
    registry.publish("tenant_acme", definition.id, 1)

    invocation = service.run(
        "tenant_acme",
        "user_1",
        definition.id,
        AgentRunRequest(
            input={"request": "Use GLM"},
            provider_id="zhipu",
            model_id="glm-5.2",
            reasoning_effort="high",
        ),
    )

    run = store.get_run("tenant_acme", invocation.run_id)
    assert (run.provider_id, run.model_id, run.reasoning_effort) == (
        "zhipu",
        "glm-5.2",
        "high",
    )
    message = store.list_chat_messages("tenant_acme", invocation.thread_id)[0]
    assert message.content == "Use GLM"
    assert message.execution_content == '{"request": "Use GLM"}'

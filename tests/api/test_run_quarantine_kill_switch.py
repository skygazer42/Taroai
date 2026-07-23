from taroai.agent import AgentRuntime
from taroai.domain import (
    ChatMessageCreate,
    ChatMessageDeliveryStatus,
    ChatMessageDispatchStatus,
    ChatThreadCreate,
    RunCreate,
    RunStatus,
)
from taroai.incidents.quarantine import (
    InMemoryOperationalControlService,
    KillSwitchScope,
    OperationalPolicyService,
    QuarantineTargetType,
)
from taroai.model_gateway import PlannedToolCall
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import ToolPolicy, ToolRiskLevel
from tests.api.adapters import DeterministicModelGateway, DeterministicToolGateway


def create_run(agent_id: str | None = "agent_sales"):
    store = InMemoryControlPlaneStore()
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    trigger = store.append_chat_message(
        "tenant_acme",
        thread.id,
        "user_1",
        ChatMessageCreate(
            content="Investigate the customer incident.",
            dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
        ),
    )
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id=agent_id,
            message=trigger.content,
            mode="autonomous",
            thread_id=thread.id,
            trigger_message_id=trigger.id,
        ),
    )
    return store, run


def test_operational_control_service_audits_quarantine_and_kill_switch_changes():
    store = InMemoryControlPlaneStore()
    controls = InMemoryOperationalControlService(audit_store=store)

    quarantine = controls.quarantine(
        tenant_id="tenant_acme",
        target_type=QuarantineTargetType.RUN,
        target_id="run_123",
        reason_code="unsafe_output",
        created_by_user_id="user_sre",
    )
    kill_switch = controls.enable_kill_switch(
        tenant_id="tenant_acme",
        scope=KillSwitchScope.SANDBOX_CREATION,
        reason_code="sandbox_provider_incident",
        enabled_by_user_id="user_sre",
    )

    audits = store.list_audit_events("tenant_acme")
    assert quarantine.audit_event_id == audits[0].id
    assert kill_switch.audit_event_id == audits[1].id
    assert audits[0].event_type == "quarantine.enabled"
    assert audits[0].metadata == {
        "quarantine_id": quarantine.id,
        "target_type": "run",
        "target_id": "run_123",
        "reason_code": "unsafe_output",
        "created_by_user_id": "user_sre",
    }
    assert audits[1].event_type == "kill_switch.enabled"
    assert audits[1].metadata == {
        "kill_switch_id": kill_switch.id,
        "scope": "sandbox_creation",
        "reason_code": "sandbox_provider_incident",
        "enabled_by_user_id": "user_sre",
    }


def test_agent_runtime_refuses_quarantined_run_before_planning():
    store, run = create_run()
    model_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_analyze",
                title="Analyze data",
                tool_name="data.analyze",
            )
        ]
    )
    controls = InMemoryOperationalControlService()
    controls.quarantine(
        tenant_id="tenant_acme",
        target_type=QuarantineTargetType.RUN,
        target_id=run.id,
        reason_code="incident_review",
        created_by_user_id="user_sre",
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        tool_gateway=DeterministicToolGateway(),
        policy_service=OperationalPolicyService(control_service=controls),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    trigger = store.get_chat_message("tenant_acme", run.trigger_message_id)
    assert trigger.dispatch_status == ChatMessageDispatchStatus.FAILED
    assert trigger.delivery_status == ChatMessageDeliveryStatus.FAILED
    events = store.list_run_events("tenant_acme", run.id)
    assert [event.type for event in events[-3:]] == [
        "policy.blocked",
        "run.failed",
        "agent.loop.completed",
    ]
    assert events[-3].payload == {
        "decision": "denied",
        "reason": "run is quarantined: incident_review",
        "target_type": "run",
        "target_id": run.id,
    }


def test_agent_runtime_denies_quarantined_skill_before_tool_execution():
    store, run = create_run()
    tool_gateway = DeterministicToolGateway()
    controls = InMemoryOperationalControlService()
    controls.quarantine(
        tenant_id="tenant_acme",
        target_type=QuarantineTargetType.SKILL,
        target_id="support.ticket_triage",
        reason_code="bad_connector_mapping",
        created_by_user_id="user_sre",
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_triage",
                    title="Triage ticket",
                    tool_name="support.route",
                    skill_id="support.ticket_triage",
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=OperationalPolicyService(control_service=controls),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert tool_gateway.call_counts == {}
    events = store.list_run_events("tenant_acme", run.id)
    blocked = next(event for event in events if event.type == "policy.blocked")
    assert blocked.payload["target_type"] == "skill"
    assert blocked.payload["target_id"] == "support.ticket_triage"


def test_agent_runtime_denies_sandbox_creation_when_tenant_kill_switch_is_enabled():
    store, run = create_run()
    tool_gateway = DeterministicToolGateway()
    controls = InMemoryOperationalControlService()
    controls.enable_kill_switch(
        tenant_id="tenant_acme",
        scope=KillSwitchScope.SANDBOX_CREATION,
        reason_code="sandbox_isolation_incident",
        enabled_by_user_id="user_sre",
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_report",
                    title="Generate report",
                    tool_name="sandbox.command",
                    tool_input={"command": "python -V"},
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=OperationalPolicyService(control_service=controls),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert tool_gateway.call_counts == {}
    events = store.list_run_events("tenant_acme", run.id)
    blocked = next(event for event in events if event.type == "policy.blocked")
    assert blocked.payload == {
        "decision": "denied",
        "reason": "sandbox_creation kill switch is enabled: sandbox_isolation_incident",
        "target_type": "kill_switch",
        "target_id": "sandbox_creation",
    }


def test_agent_runtime_denies_high_risk_tool_when_kill_switch_is_enabled():
    store, run = create_run()
    tool_gateway = DeterministicToolGateway()
    tool_gateway.policies["crm.account.delete"] = ToolPolicy(
        tool_name="crm.account.delete",
        risk_level=ToolRiskLevel.HIGH,
    )
    controls = InMemoryOperationalControlService()
    controls.enable_kill_switch(
        tenant_id="tenant_acme",
        scope=KillSwitchScope.HIGH_RISK_TOOLS,
        reason_code="tenant_incident",
        enabled_by_user_id="user_sre",
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_delete",
                    title="Delete stale account",
                    tool_name="crm.account.delete",
                )
            ]
        ),
        tool_gateway=tool_gateway,
        policy_service=OperationalPolicyService(control_service=controls),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert tool_gateway.call_counts == {}
    events = store.list_run_events("tenant_acme", run.id)
    blocked = next(event for event in events if event.type == "policy.blocked")
    assert blocked.payload == {
        "decision": "denied",
        "reason": "high_risk_tools kill switch is enabled: tenant_incident",
        "target_type": "kill_switch",
        "target_id": "high_risk_tools",
    }

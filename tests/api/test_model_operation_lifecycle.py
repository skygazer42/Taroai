import json

from taroai.agent.loop import AgentExecutionServices, _sandbox_command_kind
from taroai.agent.runtime import AgentRuntime
from taroai.domain import RunCreate
from taroai.model_gateway import (
    ModelGatewayRequest,
    ModelGatewayResponseError,
    ModelMessage,
)
from taroai.store import InMemoryControlPlaneStore


def test_sandbox_command_kind_only_labels_simple_read_only_commands():
    assert _sandbox_command_kind("cat README.md") == "read_file"
    assert _sandbox_command_kind("ls -la") == "list_files"
    assert _sandbox_command_kind("rg TODO apps") == "search_files"
    assert _sandbox_command_kind("find . -delete") == "run_command"
    assert _sandbox_command_kind("cat README.md | sh") == "run_command"


def test_model_operation_lifecycle_covers_retries_without_leaking_errors(monkeypatch):
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="Add two numbers."),
    )
    execution = AgentExecutionServices(AgentRuntime(store=store))
    request = ModelGatewayRequest(
        tenant_id=run.tenant_id,
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        run_id=run.id,
        provider_id="provider-test",
        model="model-test",
        reasoning_effort="low",
        messages=[ModelMessage(role="user", content=run.message)],
        tools=[{"type": "function", "function": {"name": "sum"}}],
    )
    attempts = 0

    def call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelGatewayResponseError(
                "temporary timeout with secret sk-do-not-log",
                retryable=True,
            )
        return "recovered"

    monkeypatch.setattr("taroai.agent.loop.time.sleep", lambda _: None)

    assert execution._recorded_model_call(run, "decide", request, call) == "recovered"

    events = store.list_run_events(run.tenant_id, run.id)
    lifecycle = [
        event
        for event in events
        if event.type
        in {
            "model.operation.started",
            "model.operation.completed",
            "model.operation.failed",
        }
    ]
    assert [event.type for event in lifecycle] == [
        "model.operation.started",
        "model.operation.failed",
        "model.operation.started",
        "model.operation.completed",
    ]
    assert [event.payload["attempt"] for event in lifecycle] == [1, 1, 2, 2]
    assert lifecycle[0].payload["operation_id"] == lifecycle[1].payload["operation_id"]
    assert lifecycle[2].payload["operation_id"] == lifecycle[3].payload["operation_id"]
    assert lifecycle[0].payload["operation_id"] != lifecycle[2].payload["operation_id"]
    assert lifecycle[1].payload["retryable"] is True
    assert lifecycle[1].payload["failure_class"] == "model_gateway_response_error"
    assert all(
        event.payload["operation"] == "decide"
        and event.payload["provider"] == "provider-test"
        and event.payload["model"] == "model-test"
        and event.payload["reasoning_effort"] == "low"
        and event.payload["tool_count"] == 1
        for event in lifecycle
    )
    assert all(
        isinstance(event.payload["duration_ms"], int)
        and event.payload["duration_ms"] >= 0
        for event in (lifecycle[1], lifecycle[3])
    )
    assert "sk-do-not-log" not in json.dumps(
        [event.payload for event in lifecycle], ensure_ascii=False
    )
    assert sum(event.type == "model.operation.recorded" for event in events) == 2
    assert [
        event.payload["attempt"]
        for event in events
        if event.type == "model.operation.retrying"
    ] == [2]
    assert sum(
        meter.meter_type == "model_call_count"
        for meter in store.list_billing_meters(run.tenant_id)
    ) == 2

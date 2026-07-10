from datetime import timedelta

import pytest

from taroai.domain import RunCreate, utc_now
from taroai.model_gateway import (
    ModelBudgetExceededError,
    ModelBudgetGuard,
    ModelBudgetPolicy,
)
from taroai.store import InMemoryControlPlaneStore


def create_run(
    store: InMemoryControlPlaneStore,
    workspace_id: str = "workspace_sales",
    user_id: str = "user_1",
    agent_id: str | None = "agent_sales",
):
    return store.create_run(
        tenant_id="tenant_acme",
        user_id=user_id,
        payload=RunCreate(
            workspace_id=workspace_id,
            agent_id=agent_id,
            message="Create a governed model budget brief.",
            mode="autonomous",
        ),
    )


def test_model_budget_guard_blocks_workspace_call_budget_before_provider_call():
    store = InMemoryControlPlaneStore()
    first_run = create_run(store)
    second_run = create_run(store)
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=first_run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
    )
    guard = ModelBudgetGuard(
        policy=ModelBudgetPolicy(max_model_calls_per_workspace=1),
    )

    with pytest.raises(ModelBudgetExceededError) as error:
        guard.assert_plan_allowed(store, "tenant_acme", second_run.id)

    assert error.value.metadata["scope_type"] == "workspace"
    assert error.value.metadata["scope_id"] == "workspace_sales"
    assert error.value.metadata["limit_type"] == "model_call_count"
    assert error.value.metadata["current_quantity"] == 1
    assert error.value.metadata["limit"] == 1


def test_model_budget_guard_blocks_user_token_budget_across_workspaces():
    store = InMemoryControlPlaneStore()
    first_run = create_run(store, workspace_id="workspace_sales", user_id="user_1")
    second_run = create_run(store, workspace_id="workspace_support", user_id="user_1")
    create_run(store, workspace_id="workspace_support", user_id="user_2")
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=first_run.id,
        meter_type="model_tokens_input",
        quantity=6,
        unit="token",
        model="gpt-enterprise-planner",
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=first_run.id,
        meter_type="model_tokens_output",
        quantity=4,
        unit="token",
        model="gpt-enterprise-planner",
    )
    guard = ModelBudgetGuard(
        policy=ModelBudgetPolicy(max_model_tokens_per_user=10),
    )

    with pytest.raises(ModelBudgetExceededError) as error:
        guard.assert_plan_allowed(store, "tenant_acme", second_run.id)

    assert error.value.metadata["scope_type"] == "user"
    assert error.value.metadata["scope_id"] == "user_1"
    assert error.value.metadata["limit_type"] == "model_tokens_total"
    assert error.value.metadata["current_quantity"] == 10
    assert error.value.metadata["limit"] == 10


def test_model_budget_guard_ignores_meters_outside_configured_window():
    store = InMemoryControlPlaneStore()
    first_run = create_run(store, workspace_id="workspace_sales", user_id="user_1")
    second_run = create_run(store, workspace_id="workspace_sales", user_id="user_1")
    old_meter = store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=first_run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
    )
    old_meter.created_at = utc_now() - timedelta(hours=2)
    guard = ModelBudgetGuard(
        policy=ModelBudgetPolicy(
            max_model_calls_per_workspace=1,
            budget_window_seconds=3600,
        ),
    )

    guard.assert_plan_allowed(store, "tenant_acme", second_run.id)


def test_model_budget_guard_blocks_meters_inside_configured_window():
    store = InMemoryControlPlaneStore()
    first_run = create_run(store, workspace_id="workspace_sales", user_id="user_1")
    second_run = create_run(store, workspace_id="workspace_sales", user_id="user_1")
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=first_run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
    )
    guard = ModelBudgetGuard(
        policy=ModelBudgetPolicy(
            max_model_calls_per_workspace=1,
            budget_window_seconds=3600,
        ),
    )

    with pytest.raises(ModelBudgetExceededError) as error:
        guard.assert_plan_allowed(store, "tenant_acme", second_run.id)

    assert error.value.metadata["scope_type"] == "workspace"
    assert error.value.metadata["current_quantity"] == 1
    assert error.value.metadata["window_seconds"] == 3600

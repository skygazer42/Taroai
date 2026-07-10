from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import utc_now


class ModelBudgetExceededError(RuntimeError):
    def __init__(self, message: str, metadata: dict[str, Any]):
        super().__init__(message)
        self.metadata = metadata


class ModelBudgetPolicy(BaseModel):
    budget_window_seconds: int = Field(default=0, ge=0)
    max_model_calls_per_run: int = Field(default=0, ge=0)
    max_model_tokens_per_run: int = Field(default=0, ge=0)
    max_model_calls_per_tenant: int = Field(default=0, ge=0)
    max_model_tokens_per_tenant: int = Field(default=0, ge=0)
    max_model_calls_per_workspace: int = Field(default=0, ge=0)
    max_model_tokens_per_workspace: int = Field(default=0, ge=0)
    max_model_calls_per_user: int = Field(default=0, ge=0)
    max_model_tokens_per_user: int = Field(default=0, ge=0)
    max_model_calls_per_agent: int = Field(default=0, ge=0)
    max_model_tokens_per_agent: int = Field(default=0, ge=0)


class ModelBudgetGuard(BaseModel):
    policy: ModelBudgetPolicy = Field(default_factory=ModelBudgetPolicy)

    def assert_plan_allowed(self, store, tenant_id: str, run_id: str) -> None:
        run = store.get_run(tenant_id, run_id)
        tenant_meters = self._filter_window_meters(store.list_billing_meters(tenant_id))
        scope_checks = [
            (
                "run",
                run.id,
                self.policy.max_model_calls_per_run,
                self.policy.max_model_tokens_per_run,
                [meter for meter in tenant_meters if meter.run_id == run.id],
            ),
            (
                "tenant",
                run.tenant_id,
                self.policy.max_model_calls_per_tenant,
                self.policy.max_model_tokens_per_tenant,
                tenant_meters,
            ),
            (
                "workspace",
                run.workspace_id,
                self.policy.max_model_calls_per_workspace,
                self.policy.max_model_tokens_per_workspace,
                [
                    meter
                    for meter in tenant_meters
                    if meter.workspace_id == run.workspace_id
                ],
            ),
            (
                "user",
                run.user_id,
                self.policy.max_model_calls_per_user,
                self.policy.max_model_tokens_per_user,
                [meter for meter in tenant_meters if meter.user_id == run.user_id],
            ),
        ]
        if run.agent_id is not None:
            scope_checks.append(
                (
                    "agent",
                    run.agent_id,
                    self.policy.max_model_calls_per_agent,
                    self.policy.max_model_tokens_per_agent,
                    [
                        meter
                        for meter in tenant_meters
                        if meter.agent_id == run.agent_id
                    ],
                )
            )
        for scope_type, scope_id, call_limit, token_limit, meters in scope_checks:
            self._assert_scope_allowed(
                run_id=run.id,
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                agent_id=run.agent_id,
                scope_type=scope_type,
                scope_id=scope_id,
                call_limit=call_limit,
                token_limit=token_limit,
                meters=meters,
            )

    def _assert_scope_allowed(
        self,
        run_id: str,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        agent_id: str | None,
        scope_type: str,
        scope_id: str,
        call_limit: int,
        token_limit: int,
        meters: list,
    ) -> None:
        current_calls = self._meter_quantity(meters, {"model_call_count"})
        if call_limit > 0 and current_calls >= call_limit:
            metadata = self._metadata(
                run_id=run_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=agent_id,
                scope_type=scope_type,
                scope_id=scope_id,
                limit_type="model_call_count",
                current_quantity=current_calls,
                limit=call_limit,
            )
            raise ModelBudgetExceededError("model call budget exceeded", metadata)

        current_tokens = self._meter_quantity(
            meters,
            {"model_tokens_input", "model_tokens_output"},
        )
        if token_limit > 0 and current_tokens >= token_limit:
            metadata = self._metadata(
                run_id=run_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=agent_id,
                scope_type=scope_type,
                scope_id=scope_id,
                limit_type="model_tokens_total",
                current_quantity=current_tokens,
                limit=token_limit,
            )
            raise ModelBudgetExceededError("model token budget exceeded", metadata)

    def _meter_quantity(self, meters: list, meter_types: set[str]) -> float:
        return sum(
            meter.quantity
            for meter in meters
            if meter.meter_type in meter_types
        )

    def _filter_window_meters(self, meters: list) -> list:
        if self.policy.budget_window_seconds <= 0:
            return meters
        cutoff = utc_now() - timedelta(seconds=self.policy.budget_window_seconds)
        return [meter for meter in meters if meter.created_at >= cutoff]

    def _metadata(
        self,
        run_id: str,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        agent_id: str | None,
        scope_type: str,
        scope_id: str,
        limit_type: str,
        current_quantity: float,
        limit: int,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "limit_type": limit_type,
            "current_quantity": current_quantity,
            "limit": limit,
            "window_seconds": self.policy.budget_window_seconds,
        }

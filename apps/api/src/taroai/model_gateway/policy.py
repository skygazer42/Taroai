from pydantic import BaseModel, Field, field_validator, model_validator

from taroai.model_gateway.models import ModelGatewayRequest, ModelPolicyDeniedError


class ModelPolicyScope(BaseModel):
    tenant_id: str | None = None
    workspace_id: str | None = None
    default_model: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    model_sensitivity_limits: dict[str, int] = Field(default_factory=dict)

    @field_validator("model_sensitivity_limits")
    @classmethod
    def validate_model_sensitivity_limits(cls, value: dict[str, int]) -> dict[str, int]:
        if any(limit < 0 for limit in value.values()):
            raise ValueError("model sensitivity limits must be greater than or equal to 0")
        return value

    @model_validator(mode="after")
    def require_tenant_for_workspace(self):
        if self.workspace_id is not None and self.tenant_id is None:
            raise ValueError("tenant_id is required when workspace_id is set")
        return self

    def matches(self, request: ModelGatewayRequest) -> bool:
        if self.tenant_id is not None and self.tenant_id != request.tenant_id:
            return False
        if self.workspace_id is not None and self.workspace_id != request.workspace_id:
            return False
        return True

    def specificity(self) -> int:
        score = 0
        if self.tenant_id is not None:
            score += 1
        if self.workspace_id is not None:
            score += 2
        return score

    def identity(self) -> dict[str, str | None]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
        }


class ModelPolicy(BaseModel):
    default_model: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    model_sensitivity_limits: dict[str, int] = Field(default_factory=dict)
    scoped_policies: list[ModelPolicyScope] = Field(default_factory=list)

    @field_validator("model_sensitivity_limits")
    @classmethod
    def validate_model_sensitivity_limits(cls, value: dict[str, int]) -> dict[str, int]:
        if any(limit < 0 for limit in value.values()):
            raise ValueError("model sensitivity limits must be greater than or equal to 0")
        return value

    def resolve_model(self, request: ModelGatewayRequest) -> str | None:
        policy_scope = self._selected_scope(request)
        if request.model is not None:
            return request.model
        if policy_scope is not None and policy_scope.default_model is not None:
            return policy_scope.default_model
        return self.default_model

    def assert_request_allowed(self, request: ModelGatewayRequest) -> str | None:
        requested_model = self.resolve_model(request)
        if requested_model is None:
            return None
        policy_scope = self._selected_scope(request)
        denied_models = self._effective_denied_models(policy_scope)
        allowed_models = self._effective_allowed_models(policy_scope)
        if requested_model in denied_models:
            raise self._denied_error(
                policy_scope=policy_scope,
                requested_model=requested_model,
                reason="model is explicitly denied by policy",
                allowed_models=allowed_models,
                denied_models=denied_models,
            )
        if allowed_models and requested_model not in allowed_models:
            raise self._denied_error(
                policy_scope=policy_scope,
                requested_model=requested_model,
                reason="model is not in the allowed model list",
                allowed_models=allowed_models,
                denied_models=denied_models,
            )
        self._assert_sensitivity_allowed(
            policy_scope=policy_scope,
            requested_model=requested_model,
            sensitivity_level=request.sensitivity_level,
            allowed_models=allowed_models,
            denied_models=denied_models,
        )
        return requested_model

    def _assert_sensitivity_allowed(
        self,
        policy_scope: ModelPolicyScope | None,
        requested_model: str,
        sensitivity_level: int,
        allowed_models: list[str],
        denied_models: list[str],
    ) -> None:
        if sensitivity_level <= 0:
            return
        model_sensitivity_limits = self._effective_model_sensitivity_limits(policy_scope)
        model_sensitivity_limit = model_sensitivity_limits.get(requested_model)
        if model_sensitivity_limit is None:
            raise self._denied_error(
                policy_scope=policy_scope,
                requested_model=requested_model,
                reason="model sensitivity limit is not configured",
                allowed_models=allowed_models,
                denied_models=denied_models,
                extra_metadata={
                    "sensitivity_level": sensitivity_level,
                    "model_sensitivity_limit": None,
                    "model_sensitivity_limits": model_sensitivity_limits,
                },
            )
        if sensitivity_level > model_sensitivity_limit:
            raise self._denied_error(
                policy_scope=policy_scope,
                requested_model=requested_model,
                reason="request sensitivity exceeds model limit",
                allowed_models=allowed_models,
                denied_models=denied_models,
                extra_metadata={
                    "sensitivity_level": sensitivity_level,
                    "model_sensitivity_limit": model_sensitivity_limit,
                    "model_sensitivity_limits": model_sensitivity_limits,
                },
            )

    def _selected_scope(self, request: ModelGatewayRequest) -> ModelPolicyScope | None:
        matching_scopes = [
            scope for scope in self.scoped_policies if scope.matches(request)
        ]
        if not matching_scopes:
            return None
        return max(matching_scopes, key=lambda scope: scope.specificity())

    def _effective_allowed_models(self, policy_scope: ModelPolicyScope | None) -> list[str]:
        if policy_scope is not None and policy_scope.allowed_models:
            return policy_scope.allowed_models
        return self.allowed_models

    def _effective_denied_models(self, policy_scope: ModelPolicyScope | None) -> list[str]:
        denied_models = list(self.denied_models)
        if policy_scope is not None:
            denied_models.extend(policy_scope.denied_models)
        return list(dict.fromkeys(denied_models))

    def _effective_model_sensitivity_limits(
        self,
        policy_scope: ModelPolicyScope | None,
    ) -> dict[str, int]:
        limits = dict(self.model_sensitivity_limits)
        if policy_scope is not None:
            limits.update(policy_scope.model_sensitivity_limits)
        return limits

    def _denied_error(
        self,
        policy_scope: ModelPolicyScope | None,
        requested_model: str,
        reason: str,
        allowed_models: list[str],
        denied_models: list[str],
        extra_metadata: dict | None = None,
    ) -> ModelPolicyDeniedError:
        metadata = {
            "requested_model": requested_model,
            "allowed_models": allowed_models,
            "denied_models": denied_models,
            "policy_scope": (
                policy_scope.identity()
                if policy_scope is not None
                else {"tenant_id": None, "workspace_id": None}
            ),
            "reason": reason,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        return ModelPolicyDeniedError(
            reason,
            metadata=metadata,
        )

import pytest

from taroai.model_gateway import (
    ModelGatewayRequest,
    ModelMessage,
    ModelPolicy,
    ModelPolicyDeniedError,
    ModelPolicyScope,
)


def create_model_request(
    workspace_id: str = "workspace_sales",
    model: str | None = None,
    sensitivity_level: int = 0,
) -> ModelGatewayRequest:
    return ModelGatewayRequest(
        tenant_id="tenant_acme",
        workspace_id=workspace_id,
        user_id="user_1",
        run_id="run_1",
        model=model,
        sensitivity_level=sensitivity_level,
        messages=[ModelMessage(role="user", content="Create a prospect brief.")],
    )


def test_model_policy_uses_most_specific_workspace_defaults_and_allowlist():
    policy = ModelPolicy(
        default_model="global-default",
        allowed_models=["global-default", "tenant-default", "workspace-default"],
        scoped_policies=[
            ModelPolicyScope(
                tenant_id="tenant_acme",
                default_model="tenant-default",
                allowed_models=["tenant-default", "workspace-default"],
            ),
            ModelPolicyScope(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                default_model="workspace-default",
                allowed_models=["workspace-default"],
            ),
        ],
    )

    assert policy.assert_request_allowed(create_model_request()) == "workspace-default"
    with pytest.raises(ModelPolicyDeniedError) as error:
        policy.assert_request_allowed(create_model_request(model="tenant-default"))

    assert error.value.metadata["requested_model"] == "tenant-default"
    assert error.value.metadata["allowed_models"] == ["workspace-default"]
    assert error.value.metadata["policy_scope"] == {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
    }


def test_model_policy_applies_global_and_scoped_denied_models():
    policy = ModelPolicy(
        allowed_models=["global-default", "workspace-default"],
        denied_models=["globally-denied"],
        scoped_policies=[
            ModelPolicyScope(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                allowed_models=["workspace-default"],
                denied_models=["workspace-denied"],
            )
        ],
    )

    with pytest.raises(ModelPolicyDeniedError) as global_error:
        policy.assert_request_allowed(create_model_request(model="globally-denied"))
    with pytest.raises(ModelPolicyDeniedError) as scoped_error:
        policy.assert_request_allowed(create_model_request(model="workspace-denied"))

    assert global_error.value.metadata["denied_models"] == [
        "globally-denied",
        "workspace-denied",
    ]
    assert scoped_error.value.metadata["denied_models"] == [
        "globally-denied",
        "workspace-denied",
    ]


def test_workspace_model_policy_scope_requires_tenant_scope():
    with pytest.raises(ValueError, match="tenant_id"):
        ModelPolicyScope(
            workspace_id="workspace_sales",
            default_model="workspace-default",
        )


def test_model_policy_requires_explicit_model_limit_for_sensitive_requests():
    policy = ModelPolicy(
        default_model="enterprise-default",
        allowed_models=["enterprise-default"],
    )

    with pytest.raises(ModelPolicyDeniedError) as error:
        policy.assert_request_allowed(create_model_request(sensitivity_level=2))

    assert error.value.metadata["requested_model"] == "enterprise-default"
    assert error.value.metadata["sensitivity_level"] == 2
    assert error.value.metadata["model_sensitivity_limit"] is None
    assert error.value.metadata["reason"] == "model sensitivity limit is not configured"


def test_model_policy_denies_requests_above_model_sensitivity_limit():
    policy = ModelPolicy(
        default_model="enterprise-default",
        allowed_models=["enterprise-default"],
        model_sensitivity_limits={"enterprise-default": 2},
    )

    assert policy.assert_request_allowed(create_model_request(sensitivity_level=2)) == "enterprise-default"
    with pytest.raises(ModelPolicyDeniedError) as error:
        policy.assert_request_allowed(create_model_request(sensitivity_level=3))

    assert error.value.metadata["requested_model"] == "enterprise-default"
    assert error.value.metadata["sensitivity_level"] == 3
    assert error.value.metadata["model_sensitivity_limit"] == 2
    assert error.value.metadata["reason"] == "request sensitivity exceeds model limit"


def test_workspace_model_policy_sensitivity_limits_override_tenant_defaults():
    policy = ModelPolicy(
        default_model="global-default",
        allowed_models=["workspace-default"],
        model_sensitivity_limits={"workspace-default": 4},
        scoped_policies=[
            ModelPolicyScope(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                default_model="workspace-default",
                allowed_models=["workspace-default"],
                model_sensitivity_limits={"workspace-default": 1},
            )
        ],
    )

    with pytest.raises(ModelPolicyDeniedError) as error:
        policy.assert_request_allowed(create_model_request(sensitivity_level=2))

    assert error.value.metadata["policy_scope"] == {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
    }
    assert error.value.metadata["model_sensitivity_limit"] == 1

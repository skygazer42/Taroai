from pathlib import Path

import pytest
from pydantic import ValidationError

from taroai.config import Settings, load_settings


def customer_operated_settings(**overrides) -> dict:
    values = {
        "deployment_external_url": "https://agent.enterprise.example.com",
        "deployment_callback_url": "https://agent.enterprise.example.com/api/connectors/oauth/callback",
        "deployment_storage_region": "us-west-2",
        "deployment_sandbox_region": "us-west-2",
        "deployment_secret_manager_type": "aws_secrets_manager",
        "object_storage_region": "us-west-2",
        "sandbox_provider": "k8s",
        "sandbox_controller_base_url": "https://sandbox-controller.enterprise.example.com",
        "sandbox_controller_api_key": "enterprise_sandbox_controller_key_2026",
        "sandbox_provider_region": "us-west-2",
        "secret_service_backend": "aws_secrets_manager",
        "control_plane_store_backend": "sql",
        "identity_service_backend": "sql",
        "connector_registry_backend": "sql",
        "customer_feedback_service_backend": "sql",
        "skill_registry_backend": "sql",
        "agent_registry_backend": "sql",
        "browser_profile_store_backend": "sql",
        "agent_engine_store_backend": "sql",
        "coding_workspace_store_backend": "sql",
        "evaluation_repository_backend": "sql",
        "thread_share_store_backend": "sql",
        "solution_pack_registry_backend": "sql",
        "sso_provider_registry_backend": "sql",
        "scim_provisioning_store_backend": "sql",
        "knowledge_service_backend": "sql",
        "long_term_memory_backend": "sql",
        "trigger_store_backend": "sql",
        "storage_catalog_backend": "sql",
        "lifecycle_policy_backend": "sql",
        "restore_drill_schedule_backend": "sql",
        "model_gateway_policy_store_backend": "sql",
        "model_gateway_provider_store_backend": "sql",
        "model_gateway_provider_rate_limit_backend": "sql",
        "short_term_memory_backend": "redis",
        "job_queue_backend": "redis",
        "dev_request_headers_enabled": False,
        "password_hash_salt": "enterprise_password_hash_salt_2026",
        "access_token_secret": "enterprise_access_token_secret_2026",
        "_env_file": None,
    }
    values.update(overrides)
    return values


def production_cloud_settings(**overrides) -> dict:
    values = {
        "environment": "production",
        "deployment_mode": "cloud",
        "deployment_secret_manager_type": "aws_secrets_manager",
        "secret_service_backend": "aws_secrets_manager",
        "sandbox_provider": "k8s",
        "sandbox_controller_base_url": "https://sandbox-controller.example.com",
        "sandbox_controller_api_key": "production_sandbox_controller_key_2026",
        "control_plane_store_backend": "sql",
        "identity_service_backend": "sql",
        "connector_registry_backend": "sql",
        "customer_feedback_service_backend": "sql",
        "skill_registry_backend": "sql",
        "agent_registry_backend": "sql",
        "browser_profile_store_backend": "sql",
        "agent_engine_store_backend": "sql",
        "coding_workspace_store_backend": "sql",
        "evaluation_repository_backend": "sql",
        "thread_share_store_backend": "sql",
        "solution_pack_registry_backend": "sql",
        "sso_provider_registry_backend": "sql",
        "scim_provisioning_store_backend": "sql",
        "knowledge_service_backend": "sql",
        "long_term_memory_backend": "sql",
        "trigger_store_backend": "sql",
        "storage_catalog_backend": "sql",
        "lifecycle_policy_backend": "sql",
        "restore_drill_schedule_backend": "sql",
        "model_gateway_policy_store_backend": "sql",
        "model_gateway_provider_store_backend": "sql",
        "model_gateway_provider_rate_limit_backend": "sql",
        "short_term_memory_backend": "redis",
        "job_queue_backend": "redis",
        "dev_request_headers_enabled": False,
        "password_hash_salt": "production_password_hash_salt_2026",
        "access_token_secret": "production_access_token_secret_2026",
        "_env_file": None,
    }
    values.update(overrides)
    return values


def test_cloud_deployment_profile_has_safe_defaults():
    settings = Settings(_env_file=None)

    assert settings.deployment_mode == "cloud"
    assert settings.deployment_external_url == ""
    assert settings.deployment_callback_url == ""
    assert settings.deployment_storage_region == "us-east-1"
    assert settings.deployment_sandbox_region == "us-east-1"
    assert settings.deployment_secret_manager_type == "local"


def test_private_deployment_requires_operator_urls():
    with pytest.raises(ValidationError) as error:
        Settings(deployment_mode="private", **customer_operated_settings(deployment_external_url=""))

    assert "deployment_external_url is required for private deployments" in str(error.value)


def test_private_deployment_requires_durable_backends():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            deployment_external_url="https://agent.private.example.com",
            deployment_callback_url="https://agent.private.example.com/api/connectors/oauth/callback",
            deployment_secret_manager_type="aws_secrets_manager",
            _env_file=None,
        )

    assert "private deployments require durable settings" in str(error.value)
    assert "control_plane_store_backend=sql" in str(error.value)
    assert "short_term_memory_backend=redis" in str(error.value)


def test_byoc_deployment_profile_accepts_complete_persistent_configuration():
    settings = Settings(deployment_mode="byoc", **customer_operated_settings())

    assert settings.deployment_mode == "byoc"
    assert settings.deployment_external_url == "https://agent.enterprise.example.com"
    assert settings.deployment_callback_url.endswith("/api/connectors/oauth/callback")
    assert settings.deployment_storage_region == "us-west-2"
    assert settings.deployment_sandbox_region == "us-west-2"
    assert settings.deployment_secret_manager_type == "aws_secrets_manager"


def test_customer_operated_deployment_rejects_local_process_sandbox_provider():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(sandbox_provider="local_process"),
        )

    assert (
        "byoc deployments cannot use local_process sandbox provider"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_non_enterprise_sandbox_provider():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(sandbox_provider="docker"),
        )

    assert "byoc deployments require an enterprise sandbox provider" in str(error.value)


def test_production_environment_rejects_local_process_sandbox_provider():
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            deployment_mode="cloud",
            sandbox_provider="local_process",
            _env_file=None,
        )

    assert (
        "production environment cannot use local_process sandbox provider"
        in str(error.value)
    )


def test_production_environment_requires_enterprise_sandbox_provider():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(sandbox_provider="disabled"),
        )

    assert "production environment requires an enterprise sandbox provider" in str(
        error.value
    )


def test_production_environment_requires_sandbox_controller_endpoint():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(sandbox_controller_base_url=""),
        )

    assert "production environment requires a sandbox controller endpoint" in str(
        error.value
    )


def test_production_environment_requires_sandbox_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(sandbox_controller_api_key=""),
        )

    assert "production environment requires a sandbox controller API key" in str(
        error.value
    )


def test_production_environment_rejects_local_sandbox_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(
                sandbox_controller_api_key="local_sandbox_controller_key_2026_dev_only",
            ),
        )

    assert (
        "production environment cannot use default sandbox_controller_api_key"
        in str(error.value)
    )


def test_production_environment_requires_browser_controller_api_key_when_browser_enabled():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(
                browser_provider="playwright",
                browser_controller_base_url="http://browser-controller.taroai.svc.cluster.local",
                browser_controller_api_key="",
            ),
        )

    assert "production environment requires a browser controller API key" in str(
        error.value
    )


def test_production_environment_rejects_local_browser_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(
                browser_provider="playwright",
                browser_controller_base_url="http://browser-controller.taroai.svc.cluster.local",
                browser_controller_api_key="local_browser_controller_key_2026_dev_only",
            ),
        )

    assert (
        "production environment cannot use default browser_controller_api_key"
        in str(error.value)
    )


def test_production_environment_requires_browser_controller_endpoint_when_browser_enabled():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(
                browser_provider="playwright",
                browser_controller_base_url="",
                browser_controller_api_key="production_browser_controller_key_2026",
            ),
        )

    assert "production environment requires a browser controller endpoint" in str(
        error.value
    )


def test_production_environment_rejects_dev_request_headers():
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            deployment_mode="cloud",
            dev_request_headers_enabled=True,
            _env_file=None,
        )

    assert "production environment cannot enable dev request headers" in str(error.value)


def test_production_environment_rejects_default_access_token_secret():
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            deployment_mode="cloud",
            dev_request_headers_enabled=False,
            password_hash_salt="production_password_hash_salt_2026",
            access_token_secret="change_me_in_production",
            _env_file=None,
        )

    assert "production environment cannot use default access_token_secret" in str(error.value)


def test_production_environment_rejects_default_password_hash_salt():
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            deployment_mode="cloud",
            dev_request_headers_enabled=False,
            password_hash_salt="change_me_in_production",
            access_token_secret="production_access_token_secret_2026",
            _env_file=None,
        )

    assert "production environment cannot use default password_hash_salt" in str(error.value)


def test_production_environment_rejects_short_access_token_secret():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(access_token_secret="short_token_secret"),
        )

    assert "production environment requires access_token_secret to be at least 32 characters" in str(error.value)


def test_production_environment_rejects_short_password_hash_salt():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(password_hash_salt="short_password_salt"),
        )

    assert "production environment requires password_hash_salt to be at least 32 characters" in str(error.value)


def test_production_environment_rejects_low_password_hash_iterations():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(password_hash_iterations=1000),
        )

    assert "production environment requires password_hash_iterations to be at least 600000" in str(error.value)


def test_customer_operated_deployment_rejects_low_password_hash_iterations():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(password_hash_iterations=1000),
        )

    assert "byoc deployments require password_hash_iterations to be at least 600000" in str(error.value)


def test_customer_operated_deployment_rejects_short_external_share_link_hash_secret():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(
                external_share_link_token_hash_secret="short_share_secret",
            ),
        )

    assert (
        "byoc deployments require external_share_link_token_hash_secret to be at least 32 characters"
        in str(error.value)
    )


def test_production_environment_requires_durable_backends():
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            deployment_mode="cloud",
            dev_request_headers_enabled=False,
            password_hash_salt="production_password_hash_salt_2026",
            access_token_secret="production_access_token_secret_2026",
            _env_file=None,
        )

    assert "production environment requires durable settings" in str(error.value)
    assert "control_plane_store_backend=sql" in str(error.value)
    assert "short_term_memory_backend=redis" in str(error.value)


def test_production_environment_rejects_local_secret_manager_type():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(deployment_secret_manager_type="local"),
        )

    assert (
        "production environment requires a non-local secret manager type"
        in str(error.value)
    )


def test_production_environment_rejects_memory_secret_service_backend():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(secret_service_backend="memory"),
        )

    assert (
        "production environment requires a non-memory secret service backend"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_dev_request_headers():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(dev_request_headers_enabled=True),
        )

    assert "byoc deployments cannot enable dev request headers" in str(error.value)


def test_customer_operated_deployment_requires_sandbox_controller_endpoint():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(sandbox_controller_base_url=""),
        )

    assert "byoc deployments require a sandbox controller endpoint" in str(error.value)


def test_customer_operated_deployment_requires_sandbox_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(sandbox_controller_api_key=""),
        )

    assert "byoc deployments require a sandbox controller API key" in str(error.value)


def test_customer_operated_deployment_requires_browser_controller_api_key_when_browser_enabled():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(
                browser_provider="playwright",
                browser_controller_base_url="https://browser.enterprise.example.com",
                browser_controller_api_key="",
            ),
        )

    assert "byoc deployments require a browser controller API key" in str(error.value)


def test_customer_operated_deployment_requires_browser_controller_endpoint_when_browser_enabled():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(
                browser_provider="playwright",
                browser_controller_base_url="",
                browser_controller_api_key="byoc_browser_controller_key_2026",
            ),
        )

    assert "byoc deployments require a browser controller endpoint" in str(error.value)


def test_customer_operated_deployment_rejects_default_browser_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(
                browser_provider="playwright",
                browser_controller_base_url="https://browser.private.example.com",
                browser_controller_api_key="replace-with-browser-controller-key",
            ),
        )

    assert (
        "private deployments cannot use default browser_controller_api_key"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_default_sandbox_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(
                sandbox_controller_api_key="replace-with-sandbox-controller-key",
            ),
        )

    assert (
        "private deployments cannot use default sandbox_controller_api_key"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_default_auth_secrets():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="byoc",
            **customer_operated_settings(
                access_token_secret="local_cloud_poc_access_token_key"
            ),
        )

    assert "byoc deployments cannot use default access_token_secret" in str(error.value)


def test_production_environment_rejects_local_bootstrap_token():
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            deployment_mode="cloud",
            dev_request_headers_enabled=False,
            password_hash_salt="production_password_hash_salt_2026",
            access_token_secret="production_access_token_secret_2026",
            tenant_bootstrap_token="local_bootstrap_token",
            _env_file=None,
        )

    assert "production environment cannot use default tenant_bootstrap_token" in str(error.value)


def test_production_environment_rejects_short_bootstrap_token():
    with pytest.raises(ValidationError) as error:
        Settings(
            **production_cloud_settings(tenant_bootstrap_token="short_bootstrap"),
        )

    assert "production environment requires tenant_bootstrap_token to be at least 32 characters" in str(error.value)


def test_customer_operated_deployment_rejects_default_sandbox_resolver_token():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(
                sandbox_secret_resolver_token="replace-with-sandbox-resolver-token"
            ),
        )

    assert (
        "private deployments cannot use default sandbox_secret_resolver_token"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_short_sandbox_resolver_token():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(
                sandbox_secret_resolver_token="short_resolver_token"
            ),
        )

    assert (
        "private deployments require sandbox_secret_resolver_token to be at least 32 characters"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_short_sandbox_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(
                sandbox_controller_api_key="short_sandbox_key",
            ),
        )

    assert (
        "private deployments require sandbox_controller_api_key to be at least 32 characters"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_short_browser_controller_api_key():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(
                browser_provider="playwright",
                browser_controller_base_url="https://browser.private.example.com",
                browser_controller_api_key="short_browser_key",
            ),
        )

    assert (
        "private deployments require browser_controller_api_key to be at least 32 characters"
        in str(error.value)
    )


def test_customer_operated_deployment_rejects_memory_secret_service_backend():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="private",
            **customer_operated_settings(secret_service_backend="memory"),
        )

    assert (
        "private deployments require a non-memory secret service backend"
        in str(error.value)
    )


def test_air_gapped_deployment_rejects_public_model_gateway():
    with pytest.raises(ValidationError) as error:
        Settings(
            deployment_mode="air_gapped",
            **customer_operated_settings(
                deployment_secret_manager_type="kubernetes_secret",
                model_gateway_base_url="https://api.openai.com/v1",
                sandbox_provider="k8s",
            ),
        )

    assert "air-gapped deployments require an internal model gateway endpoint" in str(error.value)


def test_air_gapped_deployment_accepts_internal_model_and_sandbox_endpoints():
    settings = Settings(
        deployment_mode="air_gapped",
        **customer_operated_settings(
            deployment_secret_manager_type="kubernetes_secret",
            model_gateway_base_url="http://model-gateway.taroai.svc.cluster.local/v1",
            sandbox_provider="k8s",
            browser_provider="playwright",
            browser_controller_base_url="http://browser-controller.taroai.svc.cluster.local",
            browser_controller_api_key="air_gapped_browser_controller_key_2026",
        ),
    )

    assert settings.deployment_mode == "air_gapped"
    assert settings.model_gateway_base_url.startswith("http://model-gateway")
    assert settings.sandbox_provider == "k8s"
    assert settings.browser_provider == "playwright"


def test_deployment_profile_env_examples_are_parseable():
    profiles = {
        "cloud": Path("infra/config/cloud.env.example"),
        "byoc": Path("infra/config/byoc.env.example"),
        "private": Path("infra/config/private.env.example"),
    }

    for profile, path in profiles.items():
        assert path.exists()
        settings = load_settings(env_file=path)
        text = path.read_text()

        assert settings.deployment_mode == profile
        assert "TAROAI_DEPLOYMENT_MODE=" in text
        assert "TAROAI_DEPLOYMENT_EXTERNAL_URL=" in text
        assert "TAROAI_DEPLOYMENT_CALLBACK_URL=" in text
        assert "TAROAI_DEPLOYMENT_STORAGE_REGION=" in text
        assert "TAROAI_DEPLOYMENT_SANDBOX_REGION=" in text
        assert "TAROAI_DEPLOYMENT_SECRET_MANAGER_TYPE=" in text
        assert "TAROAI_SANDBOX_CONTROLLER_PROVIDER=kubernetes" in text
        assert "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME=" in text
        assert "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED=true" in text
        assert "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES=" in text
        if profile == "cloud":
            assert settings.environment == "production"
            assert settings.control_plane_store_backend == "sql"
            assert settings.short_term_memory_backend == "redis"
            assert settings.sandbox_provider == "k8s"
            assert settings.sandbox_controller_base_url
            assert settings.deployment_secret_manager_type == "aws_secrets_manager"
            assert settings.secret_service_backend == "aws_secrets_manager"


def test_deepseek_model_gateway_env_profile_is_parseable_without_secret_values():
    path = Path("infra/config/deepseek.env.example")

    assert path.exists()
    text = path.read_text()
    settings = load_settings(env_file=path)

    assert settings.model_gateway_base_url == "https://api.deepseek.com"
    assert settings.model_gateway_model == "deepseek-v4-flash"
    assert settings.model_gateway_api_key == ""
    assert "TAROAI_MODEL_GATEWAY_API_KEY=" in text
    assert "TAROAI_MODEL_GATEWAY_VERIFICATION_PROFILE=deepseek" in text
    assert "TAROAI_MODEL_GATEWAY_API_KEY_ENV_VAR=DEEPSEEK_API_KEY" in text
    assert "sk-" not in text

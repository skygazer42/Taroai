from pathlib import Path

from taroai.app import create_app
from taroai.config import Settings, load_settings


def test_settings_have_safe_local_defaults():
    settings = Settings(_env_file=None)

    assert settings.api_title == "Taroai Control Plane API"
    assert settings.environment == "local"
    assert settings.database_url == "postgresql://taroai:taroai@localhost:5432/taroai"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.object_storage_region == "us-east-1"
    assert settings.data_residency_primary_region == "us-east-1"
    assert settings.data_residency_allowed_regions == ["us-east-1"]
    assert settings.data_residency_cross_region_replication_mode == "disabled"
    assert settings.vector_index_region == "us-east-1"
    assert settings.sandbox_provider_region == "us-east-1"
    assert settings.object_storage_access_key_id == ""
    assert settings.object_storage_secret_access_key == ""
    assert settings.object_storage_signed_url_ttl_seconds == 3600
    assert settings.object_storage_content_scan_blocked_terms == []
    assert settings.lifecycle_policy_backend == "memory"
    assert settings.sandbox_provider == "disabled"
    assert settings.sandbox_runtime_image == "python:3.12-slim"
    assert settings.sandbox_timeout_seconds == 300
    assert settings.sandbox_network_mode == "disabled"
    assert settings.browser_provider == "disabled"
    assert settings.model_gateway_base_url == "https://api.openai.com/v1"
    assert settings.model_gateway_api_key == ""
    assert settings.model_gateway_model is None
    assert settings.model_gateway_timeout_seconds == 30
    assert settings.model_gateway_allowed_models == []
    assert settings.model_gateway_denied_models == []
    assert settings.model_gateway_policy_scopes == []
    assert settings.model_gateway_policy_store_backend == "memory"
    assert settings.model_gateway_run_call_limit == 0
    assert settings.model_gateway_run_token_limit == 0
    assert settings.model_gateway_tenant_call_limit == 0
    assert settings.model_gateway_tenant_token_limit == 0
    assert settings.model_gateway_workspace_call_limit == 0
    assert settings.model_gateway_workspace_token_limit == 0
    assert settings.model_gateway_user_call_limit == 0
    assert settings.model_gateway_user_token_limit == 0
    assert settings.model_gateway_agent_call_limit == 0
    assert settings.model_gateway_agent_token_limit == 0
    assert settings.guardrail_secret_detector_enabled is False
    assert settings.guardrail_secret_detector_action == "redact"
    assert settings.guardrail_secret_detector_stages == [
        "input",
        "model_request",
        "model_response",
        "tool_request",
        "tool_response",
        "artifact",
        "memory_write",
    ]
    assert settings.guardrail_prompt_threat_detector_enabled is False
    assert settings.guardrail_prompt_threat_detector_action == "block"
    assert settings.guardrail_prompt_threat_detector_stages == [
        "input",
        "model_request",
        "tool_request",
        "memory_write",
    ]
    assert settings.guardrail_http_detector_enabled is False
    assert settings.guardrail_http_detector_url == ""
    assert settings.guardrail_http_detector_api_key == ""
    assert settings.guardrail_http_detector_timeout_seconds == 5
    assert settings.guardrail_http_detector_failure_action == "allow"
    assert settings.guardrail_http_detector_stages == [
        "input",
        "model_request",
        "model_response",
        "tool_request",
        "tool_response",
        "artifact",
        "memory_write",
    ]
    assert settings.tenant_bootstrap_token == ""
    assert settings.auth_session_backend == "auto"
    assert settings.audit_retention_days == 365
    assert settings.trace_exporter_backend == "disabled"
    assert settings.trace_exporter_endpoint_url == ""
    assert settings.trace_exporter_api_key == ""
    assert settings.trace_exporter_timeout_seconds == 5
    assert settings.trace_exporter_service_name == "taroai-api"
    assert settings.cors_origins == ["http://localhost:3000"]


def test_settings_load_from_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TAROAI_API_TITLE=Custom Taroai API",
                "TAROAI_ENVIRONMENT=staging",
                "TAROAI_DATABASE_URL=postgresql://user:pass@db.internal:5432/taroai",
                "TAROAI_REDIS_URL=redis://redis.internal:6379/1",
                "TAROAI_OBJECT_STORAGE_REGION=us-west-2",
                "TAROAI_DATA_RESIDENCY_PRIMARY_REGION=us-east-1",
                'TAROAI_DATA_RESIDENCY_ALLOWED_REGIONS=["us-east-1","us-west-2"]',
                "TAROAI_DATA_RESIDENCY_CROSS_REGION_REPLICATION_MODE=approved_regions",
                "TAROAI_VECTOR_INDEX_REGION=us-west-2",
                "TAROAI_SANDBOX_PROVIDER_REGION=us-east-1",
                "TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID=minio_access",
                "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY=minio_secret",
                "TAROAI_OBJECT_STORAGE_SIGNED_URL_TTL_SECONDS=900",
                'TAROAI_OBJECT_STORAGE_CONTENT_SCAN_BLOCKED_TERMS=["secret","token"]',
                "TAROAI_LIFECYCLE_POLICY_BACKEND=sql",
                "TAROAI_SANDBOX_PROVIDER=e2b",
                "TAROAI_SANDBOX_RUNTIME_IMAGE=python:3.12",
                "TAROAI_SANDBOX_TIMEOUT_SECONDS=120",
                "TAROAI_SANDBOX_NETWORK_MODE=allowlist",
                "TAROAI_BROWSER_PROVIDER=playwright",
                "TAROAI_MODEL_GATEWAY_BASE_URL=https://model.example.com/v1",
                "TAROAI_MODEL_GATEWAY_API_KEY=test_key",
                "TAROAI_MODEL_GATEWAY_MODEL=enterprise-default",
                "TAROAI_MODEL_GATEWAY_TIMEOUT_SECONDS=45",
                'TAROAI_MODEL_GATEWAY_ALLOWED_MODELS=["enterprise-default","gpt-4.1"]',
                'TAROAI_MODEL_GATEWAY_DENIED_MODELS=["consumer-free"]',
                'TAROAI_MODEL_GATEWAY_POLICY_SCOPES=[{"tenant_id":"tenant_acme","workspace_id":"workspace_sales","default_model":"sales-approved","allowed_models":["sales-approved"],"denied_models":["consumer-free"]}]',
                "TAROAI_MODEL_GATEWAY_POLICY_STORE_BACKEND=sql",
                "TAROAI_MODEL_GATEWAY_RUN_CALL_LIMIT=3",
                "TAROAI_MODEL_GATEWAY_RUN_TOKEN_LIMIT=10000",
                "TAROAI_MODEL_GATEWAY_TENANT_CALL_LIMIT=300",
                "TAROAI_MODEL_GATEWAY_TENANT_TOKEN_LIMIT=1000000",
                "TAROAI_MODEL_GATEWAY_WORKSPACE_CALL_LIMIT=120",
                "TAROAI_MODEL_GATEWAY_WORKSPACE_TOKEN_LIMIT=400000",
                "TAROAI_MODEL_GATEWAY_USER_CALL_LIMIT=30",
                "TAROAI_MODEL_GATEWAY_USER_TOKEN_LIMIT=100000",
                "TAROAI_MODEL_GATEWAY_AGENT_CALL_LIMIT=60",
                "TAROAI_MODEL_GATEWAY_AGENT_TOKEN_LIMIT=200000",
                "TAROAI_GUARDRAIL_SECRET_DETECTOR_ENABLED=true",
                "TAROAI_GUARDRAIL_SECRET_DETECTOR_ACTION=block",
                'TAROAI_GUARDRAIL_SECRET_DETECTOR_STAGES=["model_request","memory_write"]',
                "TAROAI_GUARDRAIL_PROMPT_THREAT_DETECTOR_ENABLED=true",
                "TAROAI_GUARDRAIL_PROMPT_THREAT_DETECTOR_ACTION=require_approval",
                'TAROAI_GUARDRAIL_PROMPT_THREAT_DETECTOR_STAGES=["model_request","tool_request"]',
                "TAROAI_GUARDRAIL_HTTP_DETECTOR_ENABLED=true",
                "TAROAI_GUARDRAIL_HTTP_DETECTOR_URL=https://detector.example.com/v1/evaluate",
                "TAROAI_GUARDRAIL_HTTP_DETECTOR_API_KEY=detector_secret",
                "TAROAI_GUARDRAIL_HTTP_DETECTOR_TIMEOUT_SECONDS=9",
                "TAROAI_GUARDRAIL_HTTP_DETECTOR_FAILURE_ACTION=block",
                'TAROAI_GUARDRAIL_HTTP_DETECTOR_STAGES=["model_request","tool_request"]',
                "TAROAI_TENANT_BOOTSTRAP_TOKEN=bootstrap_secret",
                "TAROAI_AUTH_SESSION_BACKEND=sql",
                "TAROAI_AUDIT_RETENTION_DAYS=90",
                "TAROAI_TRACE_EXPORTER_BACKEND=otlp_http",
                "TAROAI_TRACE_EXPORTER_ENDPOINT_URL=https://otel.example.com/v1/traces",
                "TAROAI_TRACE_EXPORTER_API_KEY=otel_secret",
                "TAROAI_TRACE_EXPORTER_TIMEOUT_SECONDS=7",
                "TAROAI_TRACE_EXPORTER_SERVICE_NAME=taroai-enterprise-api",
                'TAROAI_CORS_ORIGINS=["https://console.example.com","https://admin.example.com"]',
            ]
        )
    )

    settings = load_settings(env_file=env_file)

    assert settings.api_title == "Custom Taroai API"
    assert settings.environment == "staging"
    assert settings.database_url == "postgresql://user:pass@db.internal:5432/taroai"
    assert settings.redis_url == "redis://redis.internal:6379/1"
    assert settings.object_storage_region == "us-west-2"
    assert settings.data_residency_primary_region == "us-east-1"
    assert settings.data_residency_allowed_regions == ["us-east-1", "us-west-2"]
    assert settings.data_residency_cross_region_replication_mode == "approved_regions"
    assert settings.vector_index_region == "us-west-2"
    assert settings.sandbox_provider_region == "us-east-1"
    assert settings.object_storage_access_key_id == "minio_access"
    assert settings.object_storage_secret_access_key == "minio_secret"
    assert settings.object_storage_signed_url_ttl_seconds == 900
    assert settings.object_storage_content_scan_blocked_terms == ["secret", "token"]
    assert settings.lifecycle_policy_backend == "sql"
    assert settings.sandbox_provider == "e2b"
    assert settings.sandbox_runtime_image == "python:3.12"
    assert settings.sandbox_timeout_seconds == 120
    assert settings.sandbox_network_mode == "allowlist"
    assert settings.browser_provider == "playwright"
    assert settings.model_gateway_base_url == "https://model.example.com/v1"
    assert settings.model_gateway_api_key == "test_key"
    assert settings.model_gateway_model == "enterprise-default"
    assert settings.model_gateway_timeout_seconds == 45
    assert settings.model_gateway_allowed_models == ["enterprise-default", "gpt-4.1"]
    assert settings.model_gateway_denied_models == ["consumer-free"]
    assert len(settings.model_gateway_policy_scopes) == 1
    assert settings.model_gateway_policy_scopes[0].tenant_id == "tenant_acme"
    assert settings.model_gateway_policy_scopes[0].workspace_id == "workspace_sales"
    assert settings.model_gateway_policy_scopes[0].default_model == "sales-approved"
    assert settings.model_gateway_policy_scopes[0].allowed_models == ["sales-approved"]
    assert settings.model_gateway_policy_scopes[0].denied_models == ["consumer-free"]
    assert settings.model_gateway_policy_store_backend == "sql"
    assert settings.model_gateway_run_call_limit == 3
    assert settings.model_gateway_run_token_limit == 10000
    assert settings.model_gateway_tenant_call_limit == 300
    assert settings.model_gateway_tenant_token_limit == 1000000
    assert settings.model_gateway_workspace_call_limit == 120
    assert settings.model_gateway_workspace_token_limit == 400000
    assert settings.model_gateway_user_call_limit == 30
    assert settings.model_gateway_user_token_limit == 100000
    assert settings.model_gateway_agent_call_limit == 60
    assert settings.model_gateway_agent_token_limit == 200000
    assert settings.guardrail_secret_detector_enabled is True
    assert settings.guardrail_secret_detector_action == "block"
    assert settings.guardrail_secret_detector_stages == ["model_request", "memory_write"]
    assert settings.guardrail_prompt_threat_detector_enabled is True
    assert settings.guardrail_prompt_threat_detector_action == "require_approval"
    assert settings.guardrail_prompt_threat_detector_stages == ["model_request", "tool_request"]
    assert settings.guardrail_http_detector_enabled is True
    assert settings.guardrail_http_detector_url == "https://detector.example.com/v1/evaluate"
    assert settings.guardrail_http_detector_api_key == "detector_secret"
    assert settings.guardrail_http_detector_timeout_seconds == 9
    assert settings.guardrail_http_detector_failure_action == "block"
    assert settings.guardrail_http_detector_stages == ["model_request", "tool_request"]
    assert settings.tenant_bootstrap_token == "bootstrap_secret"
    assert settings.auth_session_backend == "sql"
    assert settings.audit_retention_days == 90
    assert settings.trace_exporter_backend == "otlp_http"
    assert settings.trace_exporter_endpoint_url == "https://otel.example.com/v1/traces"
    assert settings.trace_exporter_api_key == "otel_secret"
    assert settings.trace_exporter_timeout_seconds == 7
    assert settings.trace_exporter_service_name == "taroai-enterprise-api"
    assert settings.cors_origins == ["https://console.example.com", "https://admin.example.com"]


def test_app_uses_injected_settings_for_title():
    settings = Settings(api_title="Injected API", _env_file=None)

    app = create_app(settings=settings)

    assert app.title == "Injected API"


def test_app_wires_http_guardrail_detector_from_settings():
    settings = Settings(
        guardrail_http_detector_enabled=True,
        guardrail_http_detector_url="https://detector.example.com/v1/evaluate",
        guardrail_http_detector_api_key="detector_secret",
        guardrail_http_detector_stages=["model_request"],
        _env_file=None,
    )

    app = create_app(settings=settings)

    detector_ids = [detector.id for detector in app.state.guardrail_service.detectors]
    assert "http_guardrail_detector" in detector_ids
    assert app.state.settings == settings


def test_app_wires_prompt_threat_guardrail_detector_from_settings():
    settings = Settings(
        guardrail_prompt_threat_detector_enabled=True,
        guardrail_prompt_threat_detector_action="require_approval",
        guardrail_prompt_threat_detector_stages=["model_request"],
        _env_file=None,
    )

    app = create_app(settings=settings)

    detector_ids = [detector.id for detector in app.state.guardrail_service.detectors]
    assert "builtin_prompt_threat" in detector_ids
    assert app.state.settings == settings


def test_app_wires_trace_exporter_from_settings():
    settings = Settings(
        trace_exporter_backend="otlp_http",
        trace_exporter_endpoint_url="https://otel.example.com/v1/traces",
        trace_exporter_api_key="otel_secret",
        trace_exporter_timeout_seconds=7,
        trace_exporter_service_name="taroai-enterprise-api",
        _env_file=None,
    )

    app = create_app(settings=settings)

    exporter = app.state.run_trace_service.exporter
    assert exporter.endpoint_url == "https://otel.example.com/v1/traces"
    assert exporter.api_key == "otel_secret"
    assert exporter.timeout_seconds == 7
    assert exporter.service_name == "taroai-enterprise-api"
    assert app.state.settings == settings


def test_app_wires_model_policy_scopes_from_settings():
    settings = Settings(
        model_gateway_model="global-default",
        model_gateway_allowed_models=["global-default", "sales-approved"],
        model_gateway_policy_scopes=[
            {
                "tenant_id": "tenant_acme",
                "workspace_id": "workspace_sales",
                "default_model": "sales-approved",
                "allowed_models": ["sales-approved"],
            }
        ],
        _env_file=None,
    )

    app = create_app(settings=settings)

    policy = app.state.runtime.model_policy
    assert policy.default_model == "global-default"
    assert policy.allowed_models == ["global-default", "sales-approved"]
    assert len(policy.scoped_policies) == 1
    assert policy.scoped_policies[0].workspace_id == "workspace_sales"
    assert policy.scoped_policies[0].default_model == "sales-approved"

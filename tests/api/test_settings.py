from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from taroai.app import build_sandbox_readiness, create_app
from taroai.config import Settings, load_settings
from taroai.customer_success import SqlCustomerFeedbackService
from taroai.sandbox.adapter import SandboxAdapter
from taroai.sandbox.browser import BrowserController, BrowserControllerCapabilities
from taroai.sandbox.models import SandboxControllerCapabilities
from taroai.secrets import AwsSecretsManagerSecretService


class CapabilityReportingSandboxAdapter(SandboxAdapter):
    provider: str = "k8s"

    def get_capabilities(self) -> SandboxControllerCapabilities:
        return SandboxControllerCapabilities(
            provider=self.provider,
            network_isolation=True,
            filesystem_isolation=True,
            resource_limits=True,
            destroy_supported=True,
            session_ttl_enforced=True,
            max_session_ttl_seconds=1800,
            max_sessions=50,
            max_sessions_per_tenant=20,
            max_sessions_per_run=3,
            runtime_isolation=True,
            image_policy_enforced=True,
            allowed_image_count=1,
        )


class FailingCapabilitySandboxAdapter(SandboxAdapter):
    provider: str = "k8s"

    def get_capabilities(self) -> SandboxControllerCapabilities:
        raise RuntimeError("controller unavailable")


class CapabilityReportingBrowserController(BrowserController):
    provider: str = "playwright"

    def capabilities(self) -> BrowserControllerCapabilities:
        return BrowserControllerCapabilities(
            provider=self.provider,
            auth_required=True,
            session_ttl_enforced=True,
            max_session_ttl_seconds=900,
            max_sessions=25,
            max_sessions_per_tenant=10,
            max_sessions_per_run=2,
            navigation_allowlist_enforced=True,
            navigation_allowed_host_count=3,
        )


class FailingCapabilityBrowserController(BrowserController):
    provider: str = "playwright"

    def capabilities(self) -> BrowserControllerCapabilities:
        raise RuntimeError("controller unavailable")


def test_settings_have_safe_local_defaults():
    settings = Settings(_env_file=None)

    assert settings.api_title == "Taroai Control Plane API"
    assert settings.environment == "local"
    assert settings.database_url == "postgresql://taroai:taroai@localhost:5432/taroai"
    assert settings.database_pool_min_size == 1
    assert settings.database_pool_max_size == 10
    assert settings.database_pool_timeout_seconds == 30
    assert settings.database_config().pool_max_size == 10
    assert settings.connector_registry_backend == "memory"
    assert settings.customer_feedback_service_backend == "memory"
    assert settings.sso_provider_registry_backend == "memory"
    assert settings.scim_provisioning_store_backend == "memory"
    assert settings.knowledge_chunk_max_characters == 1200
    assert settings.knowledge_chunk_overlap_characters == 120
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
    assert settings.secret_service_backend == "memory"
    assert settings.secret_service_local_path == Path("/data/taroai/secrets.db")
    assert settings.secret_service_region == "us-east-1"
    assert settings.secret_service_endpoint_url == ""
    assert settings.secret_service_name_prefix == "taroai"
    assert settings.secret_service_kms_key_id == ""
    assert settings.lifecycle_policy_backend == "memory"
    assert settings.restore_drill_schedule_backend == "memory"
    assert settings.sandbox_provider == "disabled"
    assert settings.sandbox_root_dir == "/tmp/taroai/sandboxes"
    assert settings.sandbox_runtime_image == "python:3.12-slim"
    assert settings.sandbox_timeout_seconds == 300
    assert settings.sandbox_network_mode == "disabled"
    assert settings.sandbox_max_sessions == 50
    assert settings.sandbox_max_sessions_per_tenant == 20
    assert settings.sandbox_max_sessions_per_run == 3
    assert settings.sandbox_secret_resolver_token == ""
    assert settings.sandbox_controller_base_url == ""
    assert settings.sandbox_controller_api_key == ""
    assert settings.sandbox_controller_timeout_seconds == 30
    assert settings.sandbox_docker_memory_limit == "1g"
    assert settings.sandbox_docker_cpus == 1.0
    assert settings.sandbox_docker_pids_limit == 256
    assert settings.sandbox_docker_user == "65532:65532"
    assert settings.sandbox_docker_read_only_rootfs is True
    assert settings.sandbox_docker_drop_all_capabilities is True
    assert settings.sandbox_docker_security_opts == ["no-new-privileges:true"]
    assert settings.sandbox_docker_tmpfs_mounts == [
        "/tmp:rw,noexec,nosuid,size=256m"
    ]
    assert settings.browser_provider == "disabled"
    assert settings.browser_controller_base_url == ""
    assert settings.browser_controller_api_key == ""
    assert settings.browser_controller_timeout_seconds == 30
    assert settings.model_gateway_base_url == "https://api.openai.com/v1"
    assert settings.model_gateway_api_key == ""
    assert settings.model_gateway_api_key_secret_ref_id == ""
    assert settings.model_gateway_secret_lease_ttl_seconds == 60
    assert settings.model_gateway_model is None
    assert settings.model_gateway_timeout_seconds == 30
    assert settings.model_gateway_chat_request_options == {}
    assert settings.model_gateway_reasoning_efforts == []
    assert settings.model_gateway_default_reasoning_effort is None
    assert settings.model_gateway_providers == []
    assert settings.embedding_gateway_enabled is False
    assert settings.embedding_gateway_base_url == "https://api.openai.com/v1"
    assert settings.embedding_gateway_api_key == ""
    assert settings.embedding_gateway_api_key_secret_ref_id == ""
    assert settings.embedding_gateway_secret_lease_ttl_seconds == 60
    assert settings.embedding_gateway_model is None
    assert settings.embedding_gateway_dimensions is None
    assert settings.embedding_gateway_timeout_seconds == 30
    assert settings.billing_pricing_rules == []
    assert settings.billing_pricing_rule_store_backend == "memory"
    assert settings.billing_invoice_store_backend == "memory"
    assert settings.share_grant_store_backend == "memory"
    assert settings.external_share_links_enabled is False
    assert settings.external_share_link_token_hash_secret == ""
    assert settings.model_gateway_allowed_models == []
    assert settings.model_gateway_denied_models == []
    assert settings.model_gateway_sensitivity_limits == {}
    assert settings.model_gateway_policy_scopes == []
    assert settings.model_gateway_policy_store_backend == "memory"
    assert settings.model_gateway_provider_store_backend == "memory"
    assert settings.model_gateway_provider_rate_limit_backend == "memory"
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
    assert settings.model_gateway_budget_window_seconds == 0
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
    assert settings.trigger_webhook_signing_secrets == []
    assert settings.trigger_webhook_signature_tolerance_seconds == 300
    assert settings.trigger_webhook_allow_unsigned is False
    assert settings.trigger_operations_stuck_after_seconds == 900
    assert settings.license_trusted_public_keys == {}
    assert settings.license_signature_verifier().trusted_public_keys == {}
    assert settings.license_runtime_enforcement_enabled is False
    assert settings.cors_origins == ["http://localhost:3000"]


def test_settings_load_from_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TAROAI_API_TITLE=Custom Taroai API",
                "TAROAI_ENVIRONMENT=staging",
                "TAROAI_DATABASE_URL=postgresql://user:pass@db.internal:5432/taroai",
                "TAROAI_DATABASE_POOL_MIN_SIZE=2",
                "TAROAI_DATABASE_POOL_MAX_SIZE=20",
                "TAROAI_DATABASE_POOL_TIMEOUT_SECONDS=11",
                "TAROAI_CONNECTOR_REGISTRY_BACKEND=sql",
                "TAROAI_CUSTOMER_FEEDBACK_SERVICE_BACKEND=sql",
                "TAROAI_SSO_PROVIDER_REGISTRY_BACKEND=sql",
                "TAROAI_SCIM_PROVISIONING_STORE_BACKEND=sql",
                "TAROAI_KNOWLEDGE_CHUNK_MAX_CHARACTERS=900",
                "TAROAI_KNOWLEDGE_CHUNK_OVERLAP_CHARACTERS=90",
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
                "TAROAI_SECRET_SERVICE_BACKEND=aws_secrets_manager",
                "TAROAI_SECRET_SERVICE_REGION=us-west-2",
                "TAROAI_SECRET_SERVICE_ENDPOINT_URL=https://secrets.example.com",
                "TAROAI_SECRET_SERVICE_NAME_PREFIX=taroai/staging",
                "TAROAI_SECRET_SERVICE_KMS_KEY_ID=alias/taroai-secrets",
                "TAROAI_LIFECYCLE_POLICY_BACKEND=sql",
                "TAROAI_RESTORE_DRILL_SCHEDULE_BACKEND=sql",
                "TAROAI_SANDBOX_PROVIDER=e2b",
                "TAROAI_SANDBOX_ROOT_DIR=/srv/taroai/sandboxes",
                "TAROAI_SANDBOX_RUNTIME_IMAGE=python:3.12",
                "TAROAI_SANDBOX_TIMEOUT_SECONDS=120",
                "TAROAI_SANDBOX_NETWORK_MODE=allowlist",
                "TAROAI_SANDBOX_MAX_SESSIONS=17",
                "TAROAI_SANDBOX_MAX_SESSIONS_PER_TENANT=9",
                "TAROAI_SANDBOX_MAX_SESSIONS_PER_RUN=4",
                "TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN=resolver_secret",
                "TAROAI_SANDBOX_CONTROLLER_BASE_URL=https://sandbox.example.com",
                "TAROAI_SANDBOX_CONTROLLER_API_KEY=sandbox_controller_secret",
                "TAROAI_SANDBOX_CONTROLLER_TIMEOUT_SECONDS=13",
                "TAROAI_SANDBOX_DOCKER_MEMORY_LIMIT=768m",
                "TAROAI_SANDBOX_DOCKER_CPUS=0.5",
                "TAROAI_SANDBOX_DOCKER_PIDS_LIMIT=128",
                "TAROAI_SANDBOX_DOCKER_USER=10001:10001",
                "TAROAI_SANDBOX_DOCKER_READ_ONLY_ROOTFS=false",
                "TAROAI_SANDBOX_DOCKER_DROP_ALL_CAPABILITIES=false",
                'TAROAI_SANDBOX_DOCKER_SECURITY_OPTS=["no-new-privileges:true","seccomp=default"]',
                'TAROAI_SANDBOX_DOCKER_TMPFS_MOUNTS=["/tmp:rw,size=128m","/run:rw,size=32m"]',
                "TAROAI_BROWSER_PROVIDER=playwright",
                "TAROAI_BROWSER_CONTROLLER_BASE_URL=https://browser.example.com",
                "TAROAI_BROWSER_CONTROLLER_API_KEY=browser_secret",
                "TAROAI_BROWSER_CONTROLLER_TIMEOUT_SECONDS=12",
                'TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS=["browser.example.com"]',
                "TAROAI_MODEL_GATEWAY_BASE_URL=https://model.example.com/v1",
                "TAROAI_MODEL_GATEWAY_API_KEY=test_key",
                "TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID=secret_model_key",
                "TAROAI_MODEL_GATEWAY_SECRET_LEASE_TTL_SECONDS=45",
                "TAROAI_MODEL_GATEWAY_MODEL=enterprise-default",
                "TAROAI_MODEL_GATEWAY_TIMEOUT_SECONDS=45",
                'TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS={"response_format":{"type":"json_object"},"thinking":{"type":"disabled"}}',
                'TAROAI_MODEL_GATEWAY_PROVIDERS=[{"id":"sales-openai","base_url":"https://sales-model.example.com/v1","api_key_secret_ref_id":"secret_sales_model_key","secret_lease_ttl_seconds":30,"default_model":"gpt-4.1","model_ids":["gpt-4.1"],"tenant_id":"tenant_acme","workspace_id":"workspace_sales","priority":5,"chat_request_options":{"response_format":{"type":"json_object"},"thinking":{"type":"disabled"}},"rate_limit":{"max_requests_per_minute":60,"max_tokens_per_minute":120000},"fallback_policy":{"on_response_error":false,"on_rate_limit":true}}]',
                "TAROAI_EMBEDDING_GATEWAY_ENABLED=true",
                "TAROAI_EMBEDDING_GATEWAY_BASE_URL=https://embedding.example.com/v1",
                "TAROAI_EMBEDDING_GATEWAY_API_KEY=embedding_key",
                "TAROAI_EMBEDDING_GATEWAY_API_KEY_SECRET_REF_ID=secret_embedding_key",
                "TAROAI_EMBEDDING_GATEWAY_SECRET_LEASE_TTL_SECONDS=50",
                "TAROAI_EMBEDDING_GATEWAY_MODEL=text-embedding-3-small",
                "TAROAI_EMBEDDING_GATEWAY_DIMENSIONS=512",
                "TAROAI_EMBEDDING_GATEWAY_TIMEOUT_SECONDS=17",
                'TAROAI_BILLING_PRICING_RULES=[{"tenant_id":"tenant_acme","workspace_id":"workspace_sales","meter_type":"embedding_tokens","unit":"token","provider":"openai_compatible","model":"text-embedding-3-small","price_per_unit":0.00002,"pricing_unit_quantity":1000},{"tenant_id":"tenant_acme","workspace_id":"workspace_sales","skill_id":"sales.crm_lookup","meter_type":"skill_call_count","unit":"call","price_per_unit":0.08}]',
                "TAROAI_BILLING_PRICING_RULE_STORE_BACKEND=sql",
                "TAROAI_BILLING_INVOICE_STORE_BACKEND=sql",
                "TAROAI_SHARE_GRANT_STORE_BACKEND=sql",
                "TAROAI_EXTERNAL_SHARE_LINKS_ENABLED=true",
                "TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET=external_link_hash_secret",
                'TAROAI_MODEL_GATEWAY_ALLOWED_MODELS=["enterprise-default","gpt-4.1"]',
                'TAROAI_MODEL_GATEWAY_DENIED_MODELS=["consumer-free"]',
                'TAROAI_MODEL_GATEWAY_SENSITIVITY_LIMITS={"enterprise-default":2,"gpt-4.1":4}',
                'TAROAI_MODEL_GATEWAY_POLICY_SCOPES=[{"tenant_id":"tenant_acme","workspace_id":"workspace_sales","default_model":"sales-approved","allowed_models":["sales-approved"],"denied_models":["consumer-free"],"model_sensitivity_limits":{"sales-approved":3}}]',
                "TAROAI_MODEL_GATEWAY_POLICY_STORE_BACKEND=sql",
                "TAROAI_MODEL_GATEWAY_PROVIDER_STORE_BACKEND=sql",
                "TAROAI_MODEL_GATEWAY_PROVIDER_RATE_LIMIT_BACKEND=sql",
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
                "TAROAI_MODEL_GATEWAY_BUDGET_WINDOW_SECONDS=86400",
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
                'TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS=["webhook_secret","previous_secret"]',
                "TAROAI_TRIGGER_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS=120",
                "TAROAI_TRIGGER_WEBHOOK_ALLOW_UNSIGNED=true",
                "TAROAI_TRIGGER_OPERATIONS_STUCK_AFTER_SECONDS=180",
                'TAROAI_LICENSE_TRUSTED_PUBLIC_KEYS={"creao-license-2026-01":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
                "TAROAI_LICENSE_RUNTIME_ENFORCEMENT_ENABLED=true",
                'TAROAI_CORS_ORIGINS=["https://console.example.com","https://admin.example.com"]',
            ]
        )
    )

    settings = load_settings(env_file=env_file)

    assert settings.api_title == "Custom Taroai API"
    assert settings.environment == "staging"
    assert settings.database_url == "postgresql://user:pass@db.internal:5432/taroai"
    assert settings.database_pool_min_size == 2
    assert settings.database_pool_max_size == 20
    assert settings.database_pool_timeout_seconds == 11
    assert settings.database_config().pool_min_size == 2
    assert settings.database_config().pool_max_size == 20
    assert settings.database_config().pool_timeout_seconds == 11
    assert settings.connector_registry_backend == "sql"
    assert settings.customer_feedback_service_backend == "sql"
    assert settings.sso_provider_registry_backend == "sql"
    assert settings.scim_provisioning_store_backend == "sql"
    assert settings.knowledge_chunk_max_characters == 900
    assert settings.knowledge_chunk_overlap_characters == 90
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
    assert settings.secret_service_backend == "aws_secrets_manager"
    assert settings.secret_service_region == "us-west-2"
    assert settings.secret_service_endpoint_url == "https://secrets.example.com"
    assert settings.secret_service_name_prefix == "taroai/staging"
    assert settings.secret_service_kms_key_id == "alias/taroai-secrets"
    assert settings.lifecycle_policy_backend == "sql"
    assert settings.restore_drill_schedule_backend == "sql"
    assert settings.sandbox_provider == "e2b"
    assert settings.sandbox_root_dir == "/srv/taroai/sandboxes"
    assert settings.sandbox_runtime_image == "python:3.12"
    assert settings.sandbox_timeout_seconds == 120
    assert settings.sandbox_network_mode == "allowlist"
    assert settings.sandbox_max_sessions == 17
    assert settings.sandbox_max_sessions_per_tenant == 9
    assert settings.sandbox_max_sessions_per_run == 4
    assert settings.sandbox_secret_resolver_token == "resolver_secret"
    assert settings.sandbox_controller_base_url == "https://sandbox.example.com"
    assert settings.sandbox_controller_api_key == "sandbox_controller_secret"
    assert settings.sandbox_controller_timeout_seconds == 13
    assert settings.sandbox_docker_memory_limit == "768m"
    assert settings.sandbox_docker_cpus == 0.5
    assert settings.sandbox_docker_pids_limit == 128
    assert settings.sandbox_docker_user == "10001:10001"
    assert settings.sandbox_docker_read_only_rootfs is False
    assert settings.sandbox_docker_drop_all_capabilities is False
    assert settings.sandbox_docker_security_opts == [
        "no-new-privileges:true",
        "seccomp=default",
    ]
    assert settings.sandbox_docker_tmpfs_mounts == [
        "/tmp:rw,size=128m",
        "/run:rw,size=32m",
    ]
    assert settings.browser_provider == "playwright"
    assert settings.browser_controller_base_url == "https://browser.example.com"
    assert settings.browser_controller_api_key == "browser_secret"
    assert settings.browser_controller_timeout_seconds == 12
    assert settings.model_gateway_base_url == "https://model.example.com/v1"
    assert settings.model_gateway_api_key == "test_key"
    assert settings.model_gateway_api_key_secret_ref_id == "secret_model_key"
    assert settings.model_gateway_secret_lease_ttl_seconds == 45
    assert settings.model_gateway_model == "enterprise-default"
    assert settings.model_gateway_timeout_seconds == 45
    assert settings.model_gateway_chat_request_options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    assert len(settings.model_gateway_providers) == 1
    assert settings.model_gateway_providers[0].id == "sales-openai"
    assert (
        settings.model_gateway_providers[0].base_url
        == "https://sales-model.example.com/v1"
    )
    assert (
        settings.model_gateway_providers[0].api_key_secret_ref_id
        == "secret_sales_model_key"
    )
    assert settings.model_gateway_providers[0].secret_lease_ttl_seconds == 30
    assert settings.model_gateway_providers[0].default_model == "gpt-4.1"
    assert settings.model_gateway_providers[0].model_ids == ["gpt-4.1"]
    assert settings.model_gateway_providers[0].tenant_id == "tenant_acme"
    assert settings.model_gateway_providers[0].workspace_id == "workspace_sales"
    assert settings.model_gateway_providers[0].priority == 5
    assert settings.model_gateway_providers[0].chat_request_options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    assert settings.model_gateway_providers[0].rate_limit.max_requests_per_minute == 60
    assert (
        settings.model_gateway_providers[0].rate_limit.max_tokens_per_minute == 120000
    )
    assert settings.model_gateway_providers[0].fallback_policy.on_response_error is False
    assert settings.model_gateway_providers[0].fallback_policy.on_rate_limit is True
    assert settings.embedding_gateway_enabled is True
    assert settings.embedding_gateway_base_url == "https://embedding.example.com/v1"
    assert settings.embedding_gateway_api_key == "embedding_key"
    assert settings.embedding_gateway_api_key_secret_ref_id == "secret_embedding_key"
    assert settings.embedding_gateway_secret_lease_ttl_seconds == 50
    assert settings.embedding_gateway_model == "text-embedding-3-small"
    assert settings.embedding_gateway_dimensions == 512
    assert settings.embedding_gateway_timeout_seconds == 17
    assert len(settings.billing_pricing_rules) == 2
    assert settings.billing_pricing_rules[0].tenant_id == "tenant_acme"
    assert settings.billing_pricing_rules[0].workspace_id == "workspace_sales"
    assert settings.billing_pricing_rules[0].meter_type == "embedding_tokens"
    assert settings.billing_pricing_rules[0].unit == "token"
    assert settings.billing_pricing_rules[0].provider == "openai_compatible"
    assert settings.billing_pricing_rules[0].model == "text-embedding-3-small"
    assert settings.billing_pricing_rules[0].price_per_unit == 0.00002
    assert settings.billing_pricing_rules[0].pricing_unit_quantity == 1000
    assert settings.billing_pricing_rules[1].skill_id == "sales.crm_lookup"
    assert settings.billing_pricing_rules[1].meter_type == "skill_call_count"
    assert settings.billing_pricing_rules[1].unit == "call"
    assert settings.billing_pricing_rule_store_backend == "sql"
    assert settings.billing_invoice_store_backend == "sql"
    assert settings.share_grant_store_backend == "sql"
    assert settings.external_share_links_enabled is True
    assert (
        settings.external_share_link_token_hash_secret
        == "external_link_hash_secret"
    )
    assert settings.model_gateway_allowed_models == ["enterprise-default", "gpt-4.1"]
    assert settings.model_gateway_denied_models == ["consumer-free"]
    assert settings.model_gateway_sensitivity_limits == {
        "enterprise-default": 2,
        "gpt-4.1": 4,
    }
    assert len(settings.model_gateway_policy_scopes) == 1
    assert settings.model_gateway_policy_scopes[0].tenant_id == "tenant_acme"
    assert settings.model_gateway_policy_scopes[0].workspace_id == "workspace_sales"
    assert settings.model_gateway_policy_scopes[0].default_model == "sales-approved"
    assert settings.model_gateway_policy_scopes[0].allowed_models == ["sales-approved"]
    assert settings.model_gateway_policy_scopes[0].denied_models == ["consumer-free"]
    assert settings.model_gateway_policy_scopes[0].model_sensitivity_limits == {
        "sales-approved": 3
    }
    assert settings.model_gateway_policy_store_backend == "sql"
    assert settings.model_gateway_provider_store_backend == "sql"
    assert settings.model_gateway_provider_rate_limit_backend == "sql"
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
    assert settings.model_gateway_budget_window_seconds == 86400
    assert settings.guardrail_secret_detector_enabled is True
    assert settings.guardrail_secret_detector_action == "block"
    assert settings.guardrail_secret_detector_stages == [
        "model_request",
        "memory_write",
    ]
    assert settings.guardrail_prompt_threat_detector_enabled is True
    assert settings.guardrail_prompt_threat_detector_action == "require_approval"
    assert settings.guardrail_prompt_threat_detector_stages == [
        "model_request",
        "tool_request",
    ]
    assert settings.guardrail_http_detector_enabled is True
    assert (
        settings.guardrail_http_detector_url
        == "https://detector.example.com/v1/evaluate"
    )
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
    assert settings.trigger_webhook_signing_secrets == [
        "webhook_secret",
        "previous_secret",
    ]
    assert settings.trigger_webhook_signature_tolerance_seconds == 120
    assert settings.trigger_webhook_allow_unsigned is True
    assert settings.trigger_operations_stuck_after_seconds == 180
    assert settings.license_trusted_public_keys == {
        "creao-license-2026-01": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    }
    assert settings.license_signature_verifier().trusted_public_keys == {
        "creao-license-2026-01": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    }
    assert settings.license_runtime_enforcement_enabled is True
    assert settings.cors_origins == [
        "https://console.example.com",
        "https://admin.example.com",
    ]


def test_production_rejects_default_external_share_link_hash_secret():
    with pytest.raises(ValueError, match="external_share_link_token_hash_secret"):
        Settings(
            environment="production",
            access_token_secret="production_access_token_secret_2026",
            password_hash_salt="production_password_hash_salt_2026",
            external_share_link_token_hash_secret="change_me_in_production",
            control_plane_store_backend="sql",
            identity_service_backend="sql",
            connector_registry_backend="sql",
            customer_feedback_service_backend="sql",
            skill_registry_backend="sql",
            solution_pack_registry_backend="sql",
            sso_provider_registry_backend="sql",
            scim_provisioning_store_backend="sql",
            knowledge_service_backend="sql",
            long_term_memory_backend="sql",
            trigger_store_backend="sql",
            storage_catalog_backend="sql",
            lifecycle_policy_backend="sql",
            restore_drill_schedule_backend="sql",
            model_gateway_policy_store_backend="sql",
            model_gateway_provider_store_backend="sql",
            model_gateway_provider_rate_limit_backend="sql",
            short_term_memory_backend="redis",
            job_queue_backend="redis",
            deployment_secret_manager_type="aws_secrets_manager",
            secret_service_backend="aws_secrets_manager",
            sandbox_provider="k8s",
            sandbox_controller_base_url="https://sandbox.internal",
            dev_request_headers_enabled=False,
            _env_file=None,
        )


def test_app_uses_injected_settings_for_title():
    settings = Settings(api_title="Injected API", _env_file=None)

    app = create_app(settings=settings)

    assert app.title == "Injected API"


def test_app_wires_sql_customer_feedback_service_from_settings(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'customer-feedback.sqlite3'}",
        customer_feedback_service_backend="sql",
        _env_file=None,
    )

    app = create_app(settings=settings)

    assert isinstance(
        app.state.customer_feedback_service,
        SqlCustomerFeedbackService,
    )


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
        model_gateway_sensitivity_limits={"global-default": 1},
        model_gateway_policy_scopes=[
            {
                "tenant_id": "tenant_acme",
                "workspace_id": "workspace_sales",
                "default_model": "sales-approved",
                "allowed_models": ["sales-approved"],
                "model_sensitivity_limits": {"sales-approved": 3},
            }
        ],
        _env_file=None,
    )

    app = create_app(settings=settings)

    policy = app.state.runtime.model_policy
    assert policy.default_model == "global-default"
    assert policy.allowed_models == ["global-default", "sales-approved"]
    assert policy.model_sensitivity_limits == {"global-default": 1}
    assert len(policy.scoped_policies) == 1
    assert policy.scoped_policies[0].workspace_id == "workspace_sales"
    assert policy.scoped_policies[0].default_model == "sales-approved"
    assert policy.scoped_policies[0].model_sensitivity_limits == {"sales-approved": 3}


def test_app_wires_model_gateway_secret_ref_from_settings():
    settings = Settings(
        model_gateway_api_key_secret_ref_id="secret_model_key",
        model_gateway_secret_lease_ttl_seconds=45,
        model_gateway_model="gpt-enterprise",
        model_gateway_chat_request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
        _env_file=None,
    )

    app = create_app(settings=settings)

    gateway = app.state.runtime.model_gateway
    assert gateway.api_key_secret_ref_id == "secret_model_key"
    assert gateway.secret_lease_ttl_seconds == 45
    assert gateway.chat_request_options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    assert gateway.secret_service is app.state.secret_service


def test_app_wires_secret_service_backend_from_settings():
    settings = Settings(
        secret_service_backend="aws_secrets_manager",
        secret_service_region="us-west-2",
        secret_service_endpoint_url="https://secrets.example.com",
        secret_service_name_prefix="taroai/staging",
        secret_service_kms_key_id="alias/taroai-secrets",
        _env_file=None,
    )

    app = create_app(settings=settings)

    assert app.state.secret_service.config.region_name == "us-west-2"
    assert app.state.secret_service.config.endpoint_url == "https://secrets.example.com"
    assert app.state.secret_service.config.secret_name_prefix == "taroai/staging"
    assert app.state.secret_service.config.kms_key_id == "alias/taroai-secrets"
    assert app.state.connector_dispatcher.secret_service is app.state.secret_service
    assert app.state.connector_oauth_service.secret_service is app.state.secret_service


def test_app_exposes_container_health_and_readiness_endpoints():
    app = create_app(settings=Settings(_env_file=None))
    client = TestClient(app)

    health = client.get("/healthz")
    readiness = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "taroai-api",
        "environment": "local",
    }
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["checks"]["settings"] == "ok"
    assert readiness.json()["checks"]["control_plane_store_backend"] == "memory"
    assert readiness.json()["checks"]["model_gateway"] == {
        "configured": False,
        "gateway_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": None,
        "provider_count": 0,
        "configured_provider_count": 0,
        "provider_ids": [],
        "configured_provider_ids": [],
        "missing": ["model", "credential"],
        "model_source": "none",
        "credential_source": "none",
    }
    assert readiness.json()["checks"]["sandbox"] == {
        "configured": False,
        "provider": "disabled",
        "controller_required": False,
        "controller_configured": False,
        "controller_endpoint_configured": False,
        "controller_auth_configured": False,
        "missing": ["provider"],
    }
    assert readiness.json()["checks"]["browser"] == {
        "configured": False,
        "provider": "disabled",
        "controller_required": False,
        "controller_configured": False,
        "controller_endpoint_configured": False,
        "controller_auth_configured": False,
        "missing": ["provider"],
    }


def test_readyz_reports_missing_aws_secret_manager_credentials():
    secret_service = AwsSecretsManagerSecretService(
        client=SimpleNamespace(
            _request_signer=SimpleNamespace(_credentials=None)
        )
    )
    client = TestClient(
        create_app(
            settings=Settings(
                secret_service_backend="aws_secrets_manager",
                _env_file=None,
            ),
            secret_service=secret_service,
        )
    )

    response = client.get("/readyz")
    readiness = response.json()

    assert response.status_code == 503
    assert readiness["ready"] is False
    assert readiness["checks"]["secret_service"] == {
        "configured": False,
        "backend": "aws_secrets_manager",
        "credentials_configured": False,
        "endpoint_configured": False,
        "missing": ["aws_credentials"],
    }


def test_readyz_accepts_local_encrypted_secret_backend(tmp_path: Path):
    response = TestClient(
        create_app(
            settings=Settings(
                secret_service_backend="local",
                secret_service_local_path=tmp_path / "secrets.db",
                _env_file=None,
            )
        )
    ).get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"]["secret_service"] == {
        "configured": True,
        "backend": "local",
        "credentials_configured": None,
        "endpoint_configured": False,
        "missing": [],
    }


def test_readyz_reports_model_gateway_configured_for_execution():
    app = create_app(
        settings=Settings(
            model_gateway_model="gpt-4.1",
            model_gateway_api_key="configured_key",
            _env_file=None,
        )
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["checks"]["model_gateway"] == {
        "configured": True,
        "gateway_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "provider_count": 0,
        "configured_provider_count": 0,
        "provider_ids": [],
        "configured_provider_ids": [],
        "missing": [],
        "model_source": "settings",
        "credential_source": "api_key",
    }


def test_readyz_reports_enterprise_sandbox_controller_configuration():
    readiness = build_sandbox_readiness(
        Settings(
            sandbox_provider="k8s",
            sandbox_controller_base_url="https://sandbox-controller.example.com",
            sandbox_controller_api_key="sandbox_controller_secret_2026",
            _env_file=None,
        ),
        sandbox_adapter=None,
    )

    assert readiness.model_dump(mode="json", exclude_none=True) == {
        "configured": True,
        "provider": "k8s",
        "controller_required": True,
        "controller_configured": True,
        "controller_endpoint_configured": True,
        "controller_auth_configured": True,
        "missing": [],
    }


def test_readyz_reports_enterprise_sandbox_controller_capabilities():
    app = create_app(
        settings=Settings(
            sandbox_provider="k8s",
            sandbox_controller_base_url="https://sandbox-controller.example.com",
            sandbox_controller_api_key="sandbox_controller_secret_2026",
            _env_file=None,
        ),
        sandbox_adapter=CapabilityReportingSandboxAdapter(),
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    sandbox = readiness.json()["checks"]["sandbox"]
    assert sandbox["configured"] is True
    assert sandbox["capabilities_checked"] is True
    assert sandbox["network_isolation_declared"] is True
    assert sandbox["filesystem_isolation_declared"] is True
    assert sandbox["resource_limits_declared"] is True
    assert sandbox["destroy_supported_declared"] is True
    assert sandbox["session_ttl_enforced_declared"] is True
    assert sandbox["max_session_ttl_seconds"] == 1800
    assert sandbox["max_sessions"] == 50
    assert sandbox["max_sessions_per_tenant"] == 20
    assert sandbox["max_sessions_per_run"] == 3
    assert sandbox["runtime_isolation_declared"] is True
    assert sandbox["image_policy_enforced_declared"] is True
    assert sandbox["allowed_image_count"] == 1


def test_readyz_rejects_enterprise_sandbox_controller_when_capabilities_fail():
    app = create_app(
        settings=Settings(
            sandbox_provider="k8s",
            sandbox_controller_base_url="https://sandbox-controller.example.com",
            sandbox_controller_api_key="sandbox_controller_secret_2026",
            _env_file=None,
        ),
        sandbox_adapter=FailingCapabilitySandboxAdapter(),
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    sandbox = readiness.json()["checks"]["sandbox"]
    assert sandbox["configured"] is False
    assert sandbox["controller_configured"] is True
    assert sandbox["capabilities_checked"] is False
    assert sandbox["missing"] == ["sandbox_controller_capabilities"]


def test_readyz_reports_local_process_poc_sandbox_capabilities():
    app = create_app(
        settings=Settings(
            sandbox_provider="local_process",
            sandbox_max_sessions=7,
            sandbox_max_sessions_per_tenant=3,
            sandbox_max_sessions_per_run=2,
            _env_file=None,
        )
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    sandbox = readiness.json()["checks"]["sandbox"]
    assert sandbox["configured"] is True
    assert sandbox["capabilities_checked"] is True
    assert sandbox["network_isolation_declared"] is False
    assert sandbox["filesystem_isolation_declared"] is False
    assert sandbox["resource_limits_declared"] is False
    assert sandbox["destroy_supported_declared"] is True
    assert sandbox["max_sessions"] == 7
    assert sandbox["max_sessions_per_tenant"] == 3
    assert sandbox["max_sessions_per_run"] == 2


def test_readyz_reports_missing_enterprise_sandbox_controller():
    app = create_app(settings=Settings(sandbox_provider="k8s", _env_file=None))
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    assert readiness.json()["checks"]["sandbox"] == {
        "configured": False,
        "provider": "k8s",
        "controller_required": True,
        "controller_configured": False,
        "controller_endpoint_configured": False,
        "controller_auth_configured": False,
        "missing": ["sandbox_controller_base_url", "sandbox_controller_api_key"],
    }


def test_readyz_reports_missing_enterprise_sandbox_controller_api_key():
    app = create_app(
        settings=Settings(
            sandbox_provider="k8s",
            sandbox_controller_base_url="https://sandbox-controller.example.com",
            _env_file=None,
        )
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    assert readiness.json()["checks"]["sandbox"] == {
        "configured": False,
        "provider": "k8s",
        "controller_required": True,
        "controller_configured": False,
        "controller_endpoint_configured": True,
        "controller_auth_configured": False,
        "missing": ["sandbox_controller_api_key"],
    }


def test_readyz_reports_browser_controller_configuration():
    app = create_app(
        settings=Settings(
            browser_provider="playwright",
            browser_controller_base_url="https://browser-controller.example.com",
            browser_controller_api_key="browser_controller_secret_2026",
            browser_controller_navigation_allowed_hosts=["browser.example.com"],
            _env_file=None,
        ),
        browser_controller=CapabilityReportingBrowserController(),
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    browser = readiness.json()["checks"]["browser"]
    assert browser["configured"] is True
    assert browser["capabilities_checked"] is True
    assert browser["auth_required_declared"] is True
    assert browser["session_ttl_enforced_declared"] is True
    assert browser["max_session_ttl_seconds"] == 900
    assert browser["max_sessions"] == 25
    assert browser["max_sessions_per_tenant"] == 10
    assert browser["max_sessions_per_run"] == 2
    assert browser["navigation_allowlist_enforced_declared"] is True
    assert browser["navigation_allowed_host_count"] == 3
    assert browser["missing"] == []


def test_readyz_rejects_browser_controller_when_capabilities_fail():
    app = create_app(
        settings=Settings(
            browser_provider="playwright",
            browser_controller_base_url="https://browser-controller.example.com",
            browser_controller_api_key="browser_controller_secret_2026",
            browser_controller_navigation_allowed_hosts=["browser.example.com"],
            _env_file=None,
        ),
        browser_controller=FailingCapabilityBrowserController(),
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 503
    browser = readiness.json()["checks"]["browser"]
    assert browser["configured"] is False
    assert browser["controller_configured"] is True
    assert browser["capabilities_checked"] is False
    assert browser["missing"] == ["browser_controller_capabilities"]


def test_readyz_reports_missing_browser_controller_api_key():
    app = create_app(
        settings=Settings(
            browser_provider="playwright",
            browser_controller_base_url="https://browser-controller.example.com",
            browser_controller_navigation_allowed_hosts=["browser.example.com"],
            _env_file=None,
        )
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 503
    assert readiness.json()["checks"]["browser"] == {
        "configured": False,
        "provider": "playwright",
        "controller_required": True,
        "controller_configured": False,
        "controller_endpoint_configured": True,
        "controller_auth_configured": False,
        "missing": ["browser_controller_api_key"],
    }


def test_readyz_reports_provider_registry_model_gateway_configuration():
    app = create_app(
        settings=Settings(
            model_gateway_providers=[
                {
                    "id": "sales-openai",
                    "base_url": "https://model.example.com/v1",
                    "api_key_secret_ref_id": "secret_sales_model_key",
                    "default_model": "gpt-4.1",
                }
            ],
            _env_file=None,
        )
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    assert readiness.json()["checks"]["model_gateway"] == {
        "configured": True,
        "gateway_type": "provider_registry",
        "base_url": None,
        "model": None,
        "provider_count": 1,
        "configured_provider_count": 1,
        "provider_ids": ["sales-openai"],
        "configured_provider_ids": ["sales-openai"],
        "missing": [],
        "model_source": "provider",
        "credential_source": "provider_registry",
    }


def test_readyz_reports_incomplete_provider_registry_configuration():
    app = create_app(
        settings=Settings(
            model_gateway_providers=[
                {
                    "id": "model-only",
                    "base_url": "https://model.example.com/v1",
                    "default_model": "gpt-4.1",
                },
                {
                    "id": "credential-only",
                    "base_url": "https://credential.example.com/v1",
                    "api_key_secret_ref_id": "secret_sales_model_key",
                },
            ],
            _env_file=None,
        )
    )
    client = TestClient(app)

    readiness = client.get("/readyz")

    assert readiness.status_code == 200
    assert readiness.json()["checks"]["model_gateway"] == {
        "configured": False,
        "gateway_type": "provider_registry",
        "base_url": None,
        "model": None,
        "provider_count": 2,
        "configured_provider_count": 0,
        "provider_ids": ["model-only", "credential-only"],
        "configured_provider_ids": [],
        "missing": ["configured_provider"],
        "model_source": "provider",
        "credential_source": "provider_registry",
    }


def test_local_cloud_poc_deployment_contract_files_are_env_driven():
    compose = Path("infra/docker-compose.yml")
    postgres_init = Path("infra/postgres/init.sql")
    dockerfile = Path("apps/api/Dockerfile")
    browser_dockerfile = Path("apps/api/Dockerfile.browser")
    sandbox_dockerfile = Path("apps/api/Dockerfile.sandbox")
    entrypoint = Path("apps/api/entrypoint.sh")
    operations_doc = Path("docs/operations/mvp-local-cloud-poc.md")
    env_example = Path(".env.example")
    gitignore = Path(".gitignore")
    requirements = Path("apps/api/requirements.txt")
    browser_requirements = Path("apps/api/requirements-browser.txt")
    local_cloud_poc_verifier = Path(
        "apps/api/src/taroai/deployment/local_cloud_poc_verification.py"
    )
    model_gateway_verifier = Path("apps/api/src/taroai/model_gateway/verification.py")
    docker_sandbox_verifier = Path(
        "apps/api/src/taroai/sandbox/docker_verification.py"
    )
    kubernetes_sandbox_verifier = Path(
        "apps/api/src/taroai/sandbox/kubernetes_verification.py"
    )
    kubernetes_sandbox_verifier_script = Path("scripts/verify-kubernetes-sandbox.sh")

    assert compose.exists()
    assert postgres_init.exists()
    assert dockerfile.exists()
    assert browser_dockerfile.exists()
    assert sandbox_dockerfile.exists()
    assert entrypoint.exists()
    assert operations_doc.exists()
    assert env_example.exists()
    assert browser_requirements.exists()
    assert local_cloud_poc_verifier.exists()
    assert model_gateway_verifier.exists()
    assert docker_sandbox_verifier.exists()
    assert kubernetes_sandbox_verifier.exists()
    assert kubernetes_sandbox_verifier_script.exists()

    compose_text = compose.read_text()
    postgres_init_text = postgres_init.read_text()
    dockerfile_text = dockerfile.read_text()
    browser_dockerfile_text = browser_dockerfile.read_text()
    sandbox_dockerfile_text = sandbox_dockerfile.read_text()
    entrypoint_text = entrypoint.read_text()
    operations_text = operations_doc.read_text()
    env_text = env_example.read_text()
    gitignore_text = gitignore.read_text()
    requirements_text = requirements.read_text()
    browser_requirements_text = browser_requirements.read_text()
    verifier_text = local_cloud_poc_verifier.read_text()
    model_gateway_verifier_text = model_gateway_verifier.read_text()
    docker_verifier_text = docker_sandbox_verifier.read_text()
    kubernetes_verifier_text = kubernetes_sandbox_verifier.read_text()

    for service_name in [
        "web:",
        "api:",
        "sandbox-controller:",
        "browser-controller:",
        "postgres:",
        "redis:",
        "minio:",
        "minio-init:",
    ]:
        assert service_name in compose_text
    for health_target in [
        "/healthz",
        "pg_isready",
        "redis-cli ping",
        "minio/health/ready",
    ]:
        assert health_target in compose_text
    for port_mapping in [
        "${TAROAI_WEB_PORT:-3000}:80",
        "${TAROAI_API_PORT:-8000}:8000",
        "${TAROAI_SANDBOX_CONTROLLER_PORT:-8002}:8002",
        "${TAROAI_BROWSER_CONTROLLER_PORT:-8001}:8001",
        "${POSTGRES_PORT:-5432}:5432",
        "${REDIS_PORT:-6379}:6379",
        "${MINIO_API_PORT:-9000}:9000",
        "${MINIO_CONSOLE_PORT:-9001}:9001",
    ]:
        assert port_mapping in compose_text

    assert "uvicorn" in dockerfile_text
    assert "taroai.app:app" in dockerfile_text
    assert "USER taroai" in dockerfile_text
    assert "uvicorn" in browser_dockerfile_text
    assert "taroai.sandbox.playwright_service:app" in browser_dockerfile_text
    assert "uvicorn" in sandbox_dockerfile_text
    assert "taroai.sandbox.controller_service:app" in sandbox_dockerfile_text
    assert "requirements.txt /app/requirements.txt" in sandbox_dockerfile_text
    assert (
        "FROM mcr.microsoft.com/playwright/python:v1.61.0-noble"
        in browser_dockerfile_text
    )
    assert compose_text.count("network: host") >= 3
    for proxy_build_arg in [
        "HTTP_PROXY: ${HTTP_PROXY:-}",
        "HTTPS_PROXY: ${HTTPS_PROXY:-}",
        "ALL_PROXY: ${ALL_PROXY:-}",
        "NO_PROXY: ${NO_PROXY:-}",
    ]:
        assert compose_text.count(proxy_build_arg) >= 3
    assert "requirements-browser.txt" in browser_dockerfile_text
    assert "requirements.txt /app/requirements.txt" in dockerfile_text
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in browser_dockerfile_text
    assert "python -m playwright install" not in browser_dockerfile_text
    assert "chown -R taroai:taroai /app /ms-playwright" not in browser_dockerfile_text
    assert "chown -R taroai:taroai /app" in browser_dockerfile_text
    assert (
        "TAROAI_DATABASE_URL: "
        "${TAROAI_DATABASE_URL:-postgresql://taroai_app:taroai_app@postgres:5432/taroai}"
    ) in compose_text
    api_service_text = compose_text.split("\n  api:\n", 1)[1].split(
        "\n  sandbox-controller:\n", 1
    )[0]
    sandbox_controller_service_text = compose_text.split(
        "\n  sandbox-controller:\n", 1
    )[1].split(
        "\n  browser-controller:\n", 1
    )[0]
    browser_controller_service_text = compose_text.split(
        "\n  browser-controller:\n", 1
    )[1].split("\n  postgres:\n", 1)[0]
    for object_storage_env in [
        "TAROAI_OBJECT_STORAGE_BUCKET: ${TAROAI_OBJECT_STORAGE_BUCKET:-taroai-artifacts}",
        "TAROAI_OBJECT_STORAGE_REGION: ${TAROAI_OBJECT_STORAGE_REGION:-us-east-1}",
    ]:
        assert object_storage_env in api_service_text
    for api_secret_env in [
        "TAROAI_ACCESS_TOKEN_SECRET: ${TAROAI_ACCESS_TOKEN_SECRET:-local_cloud_poc_access_token_key}",
        "TAROAI_PASSWORD_HASH_SALT: ${TAROAI_PASSWORD_HASH_SALT:-local_cloud_poc_password_salt}",
        "TAROAI_TENANT_BOOTSTRAP_TOKEN: ${TAROAI_TENANT_BOOTSTRAP_TOKEN:-local_bootstrap_token}",
        "TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET: ${TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET:-}",
        "TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN: ${TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN:-}",
        "TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS: '${TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS:-[\"replace-with-webhook-signing-secret\"]}'",
    ]:
        assert api_secret_env in compose_text
    assert "TAROAI_BROWSER_PROVIDER: ${TAROAI_BROWSER_PROVIDER:-disabled}" in compose_text
    assert (
        "TAROAI_SANDBOX_CONTROLLER_BASE_URL: "
        "${TAROAI_SANDBOX_CONTROLLER_BASE_URL:-http://sandbox-controller:8002}"
    ) in compose_text
    assert (
        compose_text.count(
            "TAROAI_SANDBOX_CONTROLLER_API_KEY: "
            "${TAROAI_SANDBOX_CONTROLLER_API_KEY:-local_sandbox_controller_key_2026_dev_only}"
        )
        == 2
    )
    for sandbox_controller_env in [
        "TAROAI_SANDBOX_CONTROLLER_PROVIDER: ${TAROAI_SANDBOX_CONTROLLER_PROVIDER:-docker}",
        "TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS: ${TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS:-1800}",
        "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS: ${TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS:-50}",
        "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_TENANT: ${TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_TENANT:-20}",
        "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_RUN: ${TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_RUN:-3}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_MEMORY_LIMIT: ${TAROAI_SANDBOX_CONTROLLER_DOCKER_MEMORY_LIMIT:-1g}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_CPUS: ${TAROAI_SANDBOX_CONTROLLER_DOCKER_CPUS:-1.0}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_PIDS_LIMIT: ${TAROAI_SANDBOX_CONTROLLER_DOCKER_PIDS_LIMIT:-256}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_USER: ${TAROAI_SANDBOX_CONTROLLER_DOCKER_USER:-65532:65532}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_READ_ONLY_ROOTFS: ${TAROAI_SANDBOX_CONTROLLER_DOCKER_READ_ONLY_ROOTFS:-true}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_DROP_ALL_CAPABILITIES: ${TAROAI_SANDBOX_CONTROLLER_DOCKER_DROP_ALL_CAPABILITIES:-true}",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_SECURITY_OPTS: '${TAROAI_SANDBOX_CONTROLLER_DOCKER_SECURITY_OPTS:-[\"no-new-privileges:true\"]}'",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_TMPFS_MOUNTS: '${TAROAI_SANDBOX_CONTROLLER_DOCKER_TMPFS_MOUNTS:-[\"/tmp:rw,noexec,nosuid,size=256m\"]}'",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED: ${TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED:-true}",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES: '${TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES:-[\"ghcr.io/customer/sandbox-runtime@sha256:*\"]}'",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED: ${TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED:-false}",
    ]:
        assert sandbox_controller_env in compose_text
        assert sandbox_controller_env in sandbox_controller_service_text
    assert "env_file:" not in sandbox_controller_service_text
    assert (
        "TAROAI_BROWSER_CONTROLLER_BASE_URL: "
        "${TAROAI_BROWSER_CONTROLLER_BASE_URL:-http://browser-controller:8001}"
    ) in compose_text
    assert (
        compose_text.count(
            "TAROAI_BROWSER_CONTROLLER_API_KEY: "
            "${TAROAI_BROWSER_CONTROLLER_API_KEY:-local_browser_controller_key_2026_dev_only}"
        )
        == 2
    )
    for browser_controller_env in [
        "TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS: ${TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS:-1800}",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS: ${TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS:-50}",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT: ${TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT:-20}",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_RUN: ${TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_RUN:-3}",
        "TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS: '${TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS:-[]}'",
    ]:
        assert browser_controller_env in compose_text
        assert browser_controller_env in browser_controller_service_text
    assert "env_file:" not in browser_controller_service_text
    for api_only_env in [
        "TAROAI_ACCESS_TOKEN_SECRET",
        "TAROAI_PASSWORD_HASH_SALT",
        "TAROAI_TENANT_BOOTSTRAP_TOKEN",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]:
        assert api_only_env not in sandbox_controller_service_text
        assert api_only_env not in browser_controller_service_text
    for model_gateway_key in [
        "TAROAI_MODEL_GATEWAY_BASE_URL",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID",
        "TAROAI_MODEL_GATEWAY_SECRET_LEASE_TTL_SECONDS",
        "TAROAI_MODEL_GATEWAY_MODEL",
        "TAROAI_MODEL_GATEWAY_TIMEOUT_SECONDS",
        "TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS",
        "TAROAI_MODEL_GATEWAY_REASONING_EFFORTS",
        "TAROAI_MODEL_GATEWAY_DEFAULT_REASONING_EFFORT",
        "TAROAI_MODEL_GATEWAY_PROVIDERS",
        "TAROAI_EMBEDDING_GATEWAY_ENABLED",
        "TAROAI_EMBEDDING_GATEWAY_BASE_URL",
        "TAROAI_EMBEDDING_GATEWAY_API_KEY",
        "TAROAI_EMBEDDING_GATEWAY_API_KEY_SECRET_REF_ID",
        "TAROAI_EMBEDDING_GATEWAY_MODEL",
        "TAROAI_EMBEDDING_GATEWAY_DIMENSIONS",
        "TAROAI_EMBEDDING_GATEWAY_TIMEOUT_SECONDS",
    ]:
        assert f"      {model_gateway_key}:" not in api_service_text
        assert f"{model_gateway_key}=" in env_text
    assert "POSTGRES_USER: ${POSTGRES_USER:-taroai_admin}" in compose_text
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-taroai_admin}" in compose_text
    assert (
        "./postgres/init.sql:/docker-entrypoint-initdb.d/001_taroai_app.sql:ro"
        in compose_text
    )
    assert "CREATE ROLE taroai_app LOGIN PASSWORD 'taroai_app'" in postgres_init_text
    assert "GRANT USAGE, CREATE ON SCHEMA public TO taroai_app" in postgres_init_text
    assert "mc mb" in compose_text
    assert "TAROAI_RUN_MIGRATIONS" in entrypoint_text
    assert "MigrationRunner" in entrypoint_text
    assert "exec python - \"$@\" <<'PY'" in entrypoint_text
    assert "install -d -o taroai -g taroai /data/taroai" in dockerfile_text
    assert "chown_tree" not in entrypoint_text
    assert "os.setgid" not in entrypoint_text
    assert "os.setuid" not in entrypoint_text
    assert "os.execvp" in entrypoint_text
    assert "langgraph==" in requirements_text
    assert "playwright" not in requirements_text
    assert "playwright" in browser_requirements_text
    assert "fastapi" in browser_requirements_text
    assert "cryptography" not in browser_requirements_text
    assert "uvicorn" in browser_requirements_text
    assert "langgraph" not in browser_requirements_text
    assert "psycopg" not in browser_requirements_text
    assert "langchain==" not in requirements_text
    assert "llama-index==" not in requirements_text

    required_env_keys = [
        "TAROAI_DEPLOYMENT_MODE=cloud",
        "TAROAI_DEPLOYMENT_EXTERNAL_URL=http://localhost:8000",
        "TAROAI_DEPLOYMENT_CALLBACK_URL=http://localhost:8000/api/connectors/oauth/callback",
        "TAROAI_DEPLOYMENT_STORAGE_REGION=us-east-1",
        "TAROAI_DEPLOYMENT_SANDBOX_REGION=us-east-1",
        "TAROAI_DEPLOYMENT_SECRET_MANAGER_TYPE=aws_secrets_manager",
        "POSTGRES_DB=taroai",
        "POSTGRES_USER=taroai_admin",
        "POSTGRES_PASSWORD=taroai_admin",
        "TAROAI_DATABASE_URL=postgresql://taroai_app:taroai_app@postgres:5432/taroai",
        "TAROAI_CONTROL_PLANE_STORE_BACKEND=sql",
        "TAROAI_IDENTITY_SERVICE_BACKEND=sql",
        "TAROAI_CONNECTOR_REGISTRY_BACKEND=sql",
        "TAROAI_CUSTOMER_FEEDBACK_SERVICE_BACKEND=sql",
        "TAROAI_SKILL_REGISTRY_BACKEND=sql",
        "TAROAI_SOLUTION_PACK_REGISTRY_BACKEND=sql",
        "TAROAI_SSO_PROVIDER_REGISTRY_BACKEND=sql",
        "TAROAI_SCIM_PROVISIONING_STORE_BACKEND=sql",
        "TAROAI_KNOWLEDGE_SERVICE_BACKEND=sql",
        "TAROAI_KNOWLEDGE_CHUNK_MAX_CHARACTERS=1200",
        "TAROAI_KNOWLEDGE_CHUNK_OVERLAP_CHARACTERS=120",
        "TAROAI_EMBEDDING_GATEWAY_ENABLED=false",
        "TAROAI_EMBEDDING_GATEWAY_BASE_URL=https://api.openai.com/v1",
        "TAROAI_EMBEDDING_GATEWAY_API_KEY_SECRET_REF_ID=",
        "TAROAI_EMBEDDING_GATEWAY_SECRET_LEASE_TTL_SECONDS=60",
        "TAROAI_EMBEDDING_GATEWAY_MODEL=",
        "TAROAI_EMBEDDING_GATEWAY_DIMENSIONS=",
        "TAROAI_EMBEDDING_GATEWAY_TIMEOUT_SECONDS=30",
        "TAROAI_LONG_TERM_MEMORY_BACKEND=sql",
        "TAROAI_SHORT_TERM_MEMORY_BACKEND=redis",
        "TAROAI_STORAGE_CATALOG_BACKEND=sql",
        "TAROAI_MODEL_GATEWAY_POLICY_STORE_BACKEND=sql",
        "TAROAI_JOB_QUEUE_BACKEND=redis",
        "TAROAI_OBJECT_STORAGE_ENDPOINT=http://minio:9000",
        "TAROAI_SANDBOX_PROVIDER=local_process",
        "TAROAI_SANDBOX_ROOT_DIR=/data/taroai/sandboxes",
        "TAROAI_SANDBOX_MAX_SESSIONS=50",
        "TAROAI_SANDBOX_MAX_SESSIONS_PER_TENANT=20",
        "TAROAI_SANDBOX_MAX_SESSIONS_PER_RUN=3",
        "TAROAI_SANDBOX_DOCKER_MEMORY_LIMIT=1g",
        "TAROAI_SANDBOX_DOCKER_CPUS=1.0",
        "TAROAI_SANDBOX_DOCKER_PIDS_LIMIT=256",
        "TAROAI_SANDBOX_DOCKER_USER=65532:65532",
        "TAROAI_SANDBOX_DOCKER_READ_ONLY_ROOTFS=true",
        "TAROAI_SANDBOX_DOCKER_DROP_ALL_CAPABILITIES=true",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED=true",
        "TAROAI_SANDBOX_CONTROLLER_API_KEY=local_sandbox_controller_key_2026_dev_only",
        'TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES=["ghcr.io/customer/sandbox-runtime@sha256:*"]',
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED=false",
        "TAROAI_BROWSER_PROVIDER=disabled",
        "TAROAI_BROWSER_CONTROLLER_BASE_URL=http://browser-controller:8001",
        "TAROAI_BROWSER_CONTROLLER_API_KEY=local_browser_controller_key_2026_dev_only",
        "TAROAI_BROWSER_CONTROLLER_PORT=8001",
        "TAROAI_BROWSER_CONTROLLER_TIMEOUT_SECONDS=30",
        "TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS=1800",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS=50",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT=20",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_RUN=3",
        "TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS=[]",
        "TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET=",
        'TAROAI_CORS_ORIGINS=["http://localhost:3000","http://localhost:3300","http://web"]',
        "TAROAI_RUN_MIGRATIONS=true",
    ]
    for key in required_env_keys:
        assert key in env_text

    assert "_BACKEND=memory" not in env_text
    assert ".env" in gitignore_text
    assert "!.env.example" in gitignore_text
    assert "a.out" in gitignore_text
    assert (
        "docker compose --env-file .env -f infra/docker-compose.yml up --build"
        in operations_text
    )
    assert "/readyz" in operations_text
    assert "http://localhost:8001/healthz" in operations_text
    assert "TAROAI_TENANT_BOOTSTRAP_TOKEN" in operations_text
    assert "scripts/verify-local-cloud-poc.sh" in operations_text
    assert "python -m taroai.model_gateway.verification" in operations_text
    assert "python -m taroai.sandbox.docker_verification" in operations_text
    assert "python -m taroai.sandbox.kubernetes_verification" in operations_text
    assert "scripts/verify-kubernetes-sandbox.sh" in operations_text
    assert "LocalCloudPocVerificationConfig" in verifier_text
    assert "LocalCloudPocVerificationResult" in verifier_text
    assert "OpenAICompatibleModelGatewayVerificationConfig" in model_gateway_verifier_text
    assert "OpenAICompatibleModelGatewayVerificationResult" in model_gateway_verifier_text
    assert "DockerSandboxVerificationConfig" in docker_verifier_text
    assert "DockerSandboxVerificationResult" in docker_verifier_text
    assert "KubernetesSandboxVerificationConfig" in kubernetes_verifier_text
    assert "KubernetesSandboxVerificationResult" in kubernetes_verifier_text

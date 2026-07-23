from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taroai.billing import BillingPricingRule
from taroai.db import DatabaseConfig
from taroai.licensing import LicenseSignatureVerifier
from taroai.model_gateway import ModelPolicyScope, ModelProviderConfig, ReasoningEffort
from taroai.model_gateway.providers import validate_chat_request_options


CUSTOMER_OPERATED_DEPLOYMENT_MODES = {"byoc", "vpc", "private", "air_gapped"}
ENTERPRISE_SANDBOX_PROVIDERS = {"k8s", "e2b"}
AUTH_SECRET_MIN_LENGTH = 32
PASSWORD_HASH_MIN_ITERATIONS = 600000
OPERATOR_TOKEN_MIN_LENGTH = 32
DEFAULT_AUTH_SECRET_VALUES = {
    "",
    "change_me_in_production",
    "local_cloud_poc_access_token_key",
    "local_cloud_poc_password_salt",
}
DEFAULT_OPERATOR_TOKEN_VALUES = {
    "change_me_in_production",
    "local_bootstrap_token",
    "local_browser_controller_key_2026_dev_only",
    "local_sandbox_controller_key_2026_dev_only",
    "replace-with-bootstrap-token",
    "replace-with-browser-controller-key",
    "replace-with-sandbox-controller-key",
    "replace-with-sandbox-resolver-token",
}
DURABLE_DEPLOYMENT_BACKENDS = {
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
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TAROAI_",
        extra="ignore",
    )

    api_title: str = "Taroai Control Plane API"
    environment: str = "local"
    deployment_mode: Literal["cloud", "byoc", "vpc", "private", "air_gapped"] = "cloud"
    deployment_external_url: str = ""
    deployment_callback_url: str = ""
    deployment_storage_region: str = "us-east-1"
    deployment_sandbox_region: str = "us-east-1"
    deployment_secret_manager_type: Literal[
        "local",
        "aws_secrets_manager",
        "kubernetes_secret",
        "vault",
        "gcp_secret_manager",
        "azure_key_vault",
    ] = "local"
    database_url: str = "postgresql://taroai:taroai@localhost:5432/taroai"
    database_pool_min_size: int = Field(default=1, ge=0)
    database_pool_max_size: int = Field(default=10, ge=1)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    control_plane_store_backend: Literal["memory", "sql"] = "memory"
    identity_service_backend: Literal["memory", "sql"] = "memory"
    connector_registry_backend: Literal["memory", "sql"] = "memory"
    customer_feedback_service_backend: Literal["memory", "sql"] = "memory"
    skill_registry_backend: Literal["memory", "sql"] = "memory"
    solution_pack_registry_backend: Literal["memory", "sql"] = "memory"
    sso_provider_registry_backend: Literal["memory", "sql"] = "memory"
    scim_provisioning_store_backend: Literal["memory", "sql"] = "memory"
    knowledge_service_backend: Literal["memory", "sql"] = "memory"
    knowledge_chunk_max_characters: int = Field(default=1200, ge=1)
    knowledge_chunk_overlap_characters: int = Field(default=120, ge=0)
    long_term_memory_backend: Literal["memory", "sql"] = "memory"
    trigger_store_backend: Literal["memory", "sql"] = "memory"
    short_term_memory_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    short_term_memory_ttl_seconds: int = 3600
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_bucket: str = "taroai-artifacts"
    object_storage_region: str = "us-east-1"
    data_residency_primary_region: str = "us-east-1"
    data_residency_allowed_regions: list[str] = Field(
        default_factory=lambda: ["us-east-1"]
    )
    data_residency_cross_region_replication_mode: Literal[
        "disabled",
        "approved_regions",
        "any_region",
    ] = "disabled"
    vector_index_region: str = "us-east-1"
    object_storage_access_key_id: str = ""
    object_storage_secret_access_key: str = ""
    object_storage_signed_url_ttl_seconds: int = 3600
    object_storage_content_scan_blocked_terms: list[str] = Field(default_factory=list)
    upload_max_bytes: int = Field(default=25_000_000, ge=1)
    upload_allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/plain", "text/markdown", "text/csv", "application/json",
            "application/pdf", "image/png", "image/jpeg", "image/webp",
            "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/ogg",
            "application/zip",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ]
    )
    secret_service_backend: Literal["memory", "local", "aws_secrets_manager"] = "memory"
    secret_service_local_path: Path = Path("/data/taroai/secrets.db")
    secret_service_region: str = "us-east-1"
    secret_service_endpoint_url: str = ""
    secret_service_name_prefix: str = "taroai"
    secret_service_kms_key_id: str = ""
    storage_catalog_backend: Literal["memory", "sql"] = "memory"
    lifecycle_policy_backend: Literal["memory", "sql"] = "memory"
    restore_drill_schedule_backend: Literal["memory", "sql"] = "memory"
    sandbox_provider: Literal[
        "disabled",
        "local_process",
        "docker",
        "e2b",
        "k8s",
    ] = "disabled"
    sandbox_provider_region: str = "us-east-1"
    sandbox_root_dir: str = "/tmp/taroai/sandboxes"
    sandbox_runtime_image: str = "python:3.12-slim"
    sandbox_timeout_seconds: int = 300
    sandbox_network_mode: Literal["disabled", "allowlist", "open"] = "disabled"
    sandbox_max_sessions: int = Field(default=50, ge=1)
    sandbox_max_sessions_per_tenant: int = Field(default=20, ge=1)
    sandbox_max_sessions_per_run: int = Field(default=3, ge=1)
    sandbox_secret_resolver_token: str = Field(default="", repr=False)
    sandbox_controller_base_url: str = ""
    sandbox_controller_api_key: str = Field(default="", repr=False)
    sandbox_controller_timeout_seconds: int = Field(default=30, ge=1)
    e2b_api_key: str = Field(default="", repr=False)
    e2b_template: str = ""
    e2b_request_timeout_seconds: int = Field(default=30, ge=1)
    e2b_max_session_ttl_seconds: int = Field(default=3600, ge=1)
    sandbox_docker_memory_limit: str = "1g"
    sandbox_docker_cpus: float = Field(default=1.0, gt=0)
    sandbox_docker_pids_limit: int = Field(default=256, ge=1)
    sandbox_docker_user: str = Field(default="65532:65532", min_length=1)
    sandbox_docker_read_only_rootfs: bool = True
    sandbox_docker_drop_all_capabilities: bool = True
    sandbox_docker_security_opts: list[str] = Field(
        default_factory=lambda: ["no-new-privileges:true"]
    )
    sandbox_docker_tmpfs_mounts: list[str] = Field(
        default_factory=lambda: ["/tmp:rw,noexec,nosuid,size=256m"]
    )
    browser_provider: Literal["disabled", "playwright", "browserbase"] = "disabled"
    browser_controller_base_url: str = ""
    browser_controller_api_key: str = ""
    browser_controller_timeout_seconds: int = Field(default=30, ge=1)
    browser_controller_navigation_allowed_hosts: list[str] = Field(
        default_factory=list
    )
    tavily_api_key: str = Field(default="", repr=False)
    tavily_timeout_seconds: int = Field(default=15, ge=1)
    model_gateway_base_url: str = "https://api.openai.com/v1"
    model_gateway_api_key: str = Field(default="", repr=False)
    model_gateway_api_key_secret_ref_id: str = ""
    model_gateway_secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    model_gateway_model: str | None = None
    model_gateway_timeout_seconds: int = 30
    model_gateway_chat_request_options: dict[str, object] = Field(default_factory=dict)
    model_gateway_reasoning_efforts: list[ReasoningEffort] = Field(
        default_factory=list
    )
    model_gateway_default_reasoning_effort: ReasoningEffort | None = None
    model_gateway_providers: list[ModelProviderConfig] = Field(default_factory=list)
    embedding_gateway_enabled: bool = False
    embedding_gateway_base_url: str = "https://api.openai.com/v1"
    embedding_gateway_api_key: str = Field(default="", repr=False)
    embedding_gateway_api_key_secret_ref_id: str = ""
    embedding_gateway_secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    embedding_gateway_model: str | None = None
    embedding_gateway_dimensions: int | None = Field(default=None, ge=1)
    embedding_gateway_timeout_seconds: int = Field(default=30, ge=1)
    billing_pricing_rules: list[BillingPricingRule] = Field(default_factory=list)
    billing_pricing_rule_store_backend: Literal["memory", "sql"] = "memory"
    billing_invoice_store_backend: Literal["memory", "sql"] = "memory"
    share_grant_store_backend: Literal["memory", "sql"] = "memory"
    thread_share_store_backend: Literal["memory", "sql"] = "memory"
    thread_share_token_hash_secret: str = Field(
        default="local_thread_share_token_hash_secret_change_me",
        min_length=32,
        repr=False,
    )
    agent_registry_backend: Literal["memory", "sql"] = "memory"
    browser_profile_store_backend: Literal["memory", "sql"] = "memory"
    agent_engine_store_backend: Literal["memory", "sql"] = "memory"
    coding_workspace_store_backend: Literal["memory", "sql"] = "memory"
    evaluation_repository_backend: Literal["memory", "sql"] = "memory"
    external_share_links_enabled: bool = False
    external_share_link_token_hash_secret: str = Field(default="", repr=False)
    model_gateway_allowed_models: list[str] = Field(default_factory=list)
    model_gateway_denied_models: list[str] = Field(default_factory=list)
    model_gateway_sensitivity_limits: dict[str, int] = Field(default_factory=dict)
    model_gateway_policy_scopes: list[ModelPolicyScope] = Field(default_factory=list)
    model_gateway_policy_store_backend: Literal["memory", "sql"] = "memory"
    model_gateway_provider_store_backend: Literal["memory", "sql"] = "memory"
    model_gateway_provider_rate_limit_backend: Literal["memory", "sql", "redis"] = "memory"
    model_gateway_run_call_limit: int = Field(default=0, ge=0)
    model_gateway_run_token_limit: int = Field(default=0, ge=0)
    model_gateway_tenant_call_limit: int = Field(default=0, ge=0)
    model_gateway_tenant_token_limit: int = Field(default=0, ge=0)
    model_gateway_workspace_call_limit: int = Field(default=0, ge=0)
    model_gateway_workspace_token_limit: int = Field(default=0, ge=0)
    model_gateway_user_call_limit: int = Field(default=0, ge=0)
    model_gateway_user_token_limit: int = Field(default=0, ge=0)
    model_gateway_agent_call_limit: int = Field(default=0, ge=0)
    model_gateway_agent_token_limit: int = Field(default=0, ge=0)
    model_gateway_budget_window_seconds: int = Field(default=0, ge=0)
    guardrail_secret_detector_enabled: bool = False
    guardrail_secret_detector_action: Literal[
        "warn", "redact", "require_approval", "block"
    ] = "redact"
    guardrail_secret_detector_stages: list[str] = Field(
        default_factory=lambda: [
            "input",
            "model_request",
            "model_response",
            "tool_request",
            "tool_response",
            "artifact",
            "memory_write",
        ]
    )
    guardrail_prompt_threat_detector_enabled: bool = False
    guardrail_prompt_threat_detector_action: Literal[
        "warn",
        "redact",
        "require_approval",
        "block",
    ] = "block"
    guardrail_prompt_threat_detector_stages: list[str] = Field(
        default_factory=lambda: [
            "input",
            "model_request",
            "tool_request",
            "memory_write",
        ]
    )
    guardrail_http_detector_enabled: bool = False
    guardrail_http_detector_url: str = ""
    guardrail_http_detector_api_key: str = ""
    guardrail_http_detector_timeout_seconds: int = Field(default=5, ge=1)
    guardrail_http_detector_failure_action: Literal[
        "allow",
        "warn",
        "redact",
        "require_approval",
        "block",
    ] = "allow"
    guardrail_http_detector_stages: list[str] = Field(
        default_factory=lambda: [
            "input",
            "model_request",
            "model_response",
            "tool_request",
            "tool_response",
            "artifact",
            "memory_write",
        ]
    )
    password_hash_iterations: int = 600000
    password_hash_salt: str = "change_me_in_production"
    access_token_secret: str = "change_me_in_production"
    access_token_ttl_seconds: int = 3600
    remembered_access_token_ttl_seconds: int = 2_592_000
    auth_session_backend: Literal["auto", "memory", "sql"] = "auto"
    auth_public_registration_enabled: bool = False
    auth_password_reset_enabled: bool = False
    auth_smtp_url: str = Field(default="", repr=False)
    auth_email_from: str = ""
    auth_email_verification_ttl_seconds: int = Field(default=86_400, ge=300)
    auth_password_reset_ttl_seconds: int = Field(default=3_600, ge=300)
    audit_retention_days: int = Field(default=365, ge=1)
    trace_exporter_backend: Literal["disabled", "otlp_http"] = "disabled"
    trace_exporter_endpoint_url: str = ""
    trace_exporter_api_key: str = ""
    trace_exporter_timeout_seconds: int = Field(default=5, ge=1)
    trace_exporter_service_name: str = "taroai-api"
    dev_request_headers_enabled: bool = True
    tenant_bootstrap_token: str = ""
    tenant_quota_profile: Literal["trial", "poc", "business", "enterprise"] = "poc"
    job_queue_backend: Literal["disabled", "redis"] = "disabled"
    run_execution_dispatch_mode: Literal["inline", "queue"] = "inline"
    agent_loop_max_iterations: int = Field(default=12, ge=1)
    agent_loop_max_repairs: int = Field(default=4, ge=0)
    agent_loop_timeout_seconds: int = Field(default=1800, ge=1)
    agent_loop_cost_limit: float = Field(default=0, ge=0)
    agent_loop_action_lease_seconds: int = Field(default=600, ge=1)
    agent_loop_full_auto_requires_isolation: bool = True
    run_execution_queue_name: str = "runs.execute"
    billing_queue_name: str = "billing.aggregate"
    cleanup_queue_name: str = "system.cleanup"
    trigger_queue_name: str = "triggers.due"
    trigger_webhook_signing_secrets: list[str] = Field(default_factory=list, repr=False)
    trigger_webhook_signature_tolerance_seconds: int = Field(default=300, ge=1)
    trigger_webhook_allow_unsigned: bool = False
    trigger_operations_stuck_after_seconds: int = Field(default=900, ge=1)
    license_trusted_public_keys: dict[str, str] = Field(
        default_factory=dict, repr=False
    )
    license_runtime_enforcement_enabled: bool = False
    worker_job_lease_seconds: int = 300
    worker_job_retry_delay_seconds: int = 30
    worker_job_max_attempts: int = 3
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    event_stream_media_type: str = "text/event-stream"
    event_stream_heartbeat_seconds: float = Field(default=10.0, gt=0)
    event_stream_follow_seconds: int = Field(default=30, ge=1)
    speech_provider: Literal["disabled"] = "disabled"
    speech_max_audio_bytes: int = Field(default=10_000_000, ge=1)

    @field_validator("data_residency_allowed_regions")
    @classmethod
    def validate_data_residency_allowed_regions(cls, value: list[str]) -> list[str]:
        normalized = [region.strip() for region in value]
        if not normalized or any(not region for region in normalized):
            raise ValueError("data residency allowed regions must not be empty")
        return list(dict.fromkeys(normalized))

    @field_validator("embedding_gateway_dimensions", mode="before")
    @classmethod
    def validate_optional_embedding_dimensions(cls, value):
        if value == "":
            return None
        return value

    @field_validator("model_gateway_default_reasoning_effort", mode="before")
    @classmethod
    def validate_optional_reasoning_effort(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_data_residency_primary_region(self) -> "Settings":
        if (
            self.data_residency_primary_region
            not in self.data_residency_allowed_regions
        ):
            raise ValueError("data residency primary region must be in allowed regions")
        if self.database_pool_max_size < self.database_pool_min_size:
            raise ValueError(
                "database pool max size must be greater than or equal to min size"
            )
        if (
            self.knowledge_chunk_overlap_characters
            >= self.knowledge_chunk_max_characters
        ):
            raise ValueError(
                "knowledge chunk overlap characters must be less than max characters"
            )
        validate_chat_request_options(dict(self.model_gateway_chat_request_options))
        if (
            self.model_gateway_default_reasoning_effort is not None
            and self.model_gateway_default_reasoning_effort
            not in self.model_gateway_reasoning_efforts
        ):
            raise ValueError(
                "model gateway default reasoning effort must be listed in supported efforts"
            )
        return self

    @model_validator(mode="after")
    def validate_deployment_profile(self) -> "Settings":
        self._validate_browser_navigation_allowlist()
        self._validate_runtime_environment()
        if self.deployment_mode in CUSTOMER_OPERATED_DEPLOYMENT_MODES:
            self._validate_customer_operated_deployment()
        if self.deployment_mode == "air_gapped":
            self._validate_air_gapped_deployment()
        return self

    def _validate_runtime_environment(self) -> None:
        environment = self.environment.strip().lower()
        if (
            environment in {"prod", "production"}
            and self.sandbox_provider == "local_process"
        ):
            raise ValueError(
                f"{environment} environment cannot use local_process sandbox provider"
            )
        if (
            environment in {"prod", "production"}
            and self.dev_request_headers_enabled
        ):
            raise ValueError(
                f"{environment} environment cannot enable dev request headers"
            )
        if environment in {"prod", "production"}:
            self._validate_auth_secret_values(f"{environment} environment")
            self._validate_auth_email_delivery(f"{environment} environment")
            self._validate_operator_token_values(f"{environment} environment")
            self._validate_durable_backends(f"{environment} environment", "requires")
            self._validate_secret_management(f"{environment} environment", "requires")
            self._validate_enterprise_sandbox_provider(
                f"{environment} environment", "requires"
            )
            self._validate_sandbox_controller_endpoint(
                f"{environment} environment", "requires"
            )
            self._validate_sandbox_controller_auth(
                f"{environment} environment", "requires"
            )
            self._validate_browser_controller_endpoint(
                f"{environment} environment", "requires"
            )
            self._validate_browser_controller_auth(
                f"{environment} environment", "requires"
            )

    def _validate_customer_operated_deployment(self) -> None:
        missing_urls = [
            field_name
            for field_name in ["deployment_external_url", "deployment_callback_url"]
            if not getattr(self, field_name).strip()
        ]
        if missing_urls:
            raise ValueError(
                f"{', '.join(missing_urls)} is required for {self.deployment_mode} deployments"
            )

        self._validate_durable_backends(f"{self.deployment_mode} deployments", "require")

        self._validate_secret_management(f"{self.deployment_mode} deployments", "require")

        if self.sandbox_provider == "local_process":
            raise ValueError(
                f"{self.deployment_mode} deployments cannot use local_process sandbox provider"
            )
        self._validate_enterprise_sandbox_provider(
            f"{self.deployment_mode} deployments", "require"
        )
        self._validate_sandbox_controller_endpoint(
            f"{self.deployment_mode} deployments", "require"
        )
        self._validate_sandbox_controller_auth(
            f"{self.deployment_mode} deployments", "require"
        )
        self._validate_browser_controller_endpoint(
            f"{self.deployment_mode} deployments", "require"
        )
        self._validate_browser_controller_auth(
            f"{self.deployment_mode} deployments", "require"
        )
        if self.dev_request_headers_enabled:
            raise ValueError(
                f"{self.deployment_mode} deployments cannot enable dev request headers"
            )
        self._validate_auth_secret_values(f"{self.deployment_mode} deployments")
        self._validate_auth_email_delivery(f"{self.deployment_mode} deployments")
        self._validate_operator_token_values(f"{self.deployment_mode} deployments")

        if self.deployment_storage_region != self.object_storage_region:
            raise ValueError(
                "deployment_storage_region must match object_storage_region"
            )
        if self.deployment_sandbox_region != self.sandbox_provider_region:
            raise ValueError(
                "deployment_sandbox_region must match sandbox_provider_region"
            )

    def _validate_durable_backends(self, context: str, verb: str) -> None:
        invalid_backends = [
            f"{field_name}={expected}"
            for field_name, expected in DURABLE_DEPLOYMENT_BACKENDS.items()
            if getattr(self, field_name) != expected
        ]
        if invalid_backends:
            raise ValueError(
                f"{context} {verb} durable settings: {invalid_backends}"
            )

    def _validate_secret_management(self, context: str, verb: str) -> None:
        if self.deployment_secret_manager_type == "local":
            raise ValueError(f"{context} {verb} a non-local secret manager type")
        if self.secret_service_backend == "memory":
            raise ValueError(f"{context} {verb} a non-memory secret service backend")
        if self.secret_service_backend == "local":
            raise ValueError(f"{context} {verb} a non-local secret service backend")

    def _validate_enterprise_sandbox_provider(self, context: str, verb: str) -> None:
        if self.sandbox_provider not in ENTERPRISE_SANDBOX_PROVIDERS:
            raise ValueError(f"{context} {verb} an enterprise sandbox provider")

    def _validate_sandbox_controller_endpoint(self, context: str, verb: str) -> None:
        if self.sandbox_provider == "e2b" and self.e2b_api_key.strip():
            return
        if (
            self.sandbox_provider in ENTERPRISE_SANDBOX_PROVIDERS
            and not self.sandbox_controller_base_url.strip()
        ):
            raise ValueError(f"{context} {verb} a sandbox controller endpoint")

    def _validate_sandbox_controller_auth(self, context: str, verb: str) -> None:
        if self.sandbox_provider == "e2b" and self.e2b_api_key.strip():
            return
        if self.sandbox_provider not in ENTERPRISE_SANDBOX_PROVIDERS:
            return
        if not self.sandbox_controller_api_key.strip():
            raise ValueError(f"{context} {verb} a sandbox controller API key")
        self._validate_optional_operator_token(
            context,
            "sandbox_controller_api_key",
            self.sandbox_controller_api_key,
        )

    def _validate_browser_controller_endpoint(self, context: str, verb: str) -> None:
        if (
            self.browser_provider != "disabled"
            and not self.browser_controller_base_url.strip()
        ):
            raise ValueError(f"{context} {verb} a browser controller endpoint")

    def _validate_browser_controller_auth(self, context: str, verb: str) -> None:
        if self.browser_provider == "disabled":
            return
        if not self.browser_controller_api_key.strip():
            raise ValueError(f"{context} {verb} a browser controller API key")
        self._validate_optional_operator_token(
            context,
            "browser_controller_api_key",
            self.browser_controller_api_key,
        )

    def _validate_browser_navigation_allowlist(self) -> None:
        if self.browser_provider == "disabled":
            return
        allowed_hosts = [
            host.strip().lower()
            for host in self.browser_controller_navigation_allowed_hosts
            if host.strip()
        ]
        if not allowed_hosts:
            raise ValueError(
                "browser automation requires browser_controller_navigation_allowed_hosts"
            )
        if any(
            host in {"*", "*."}
            or "/" in host
            or "?" in host
            or "#" in host
            for host in allowed_hosts
        ):
            raise ValueError(
                "browser_controller_navigation_allowed_hosts must contain hostnames"
            )
        self.browser_controller_navigation_allowed_hosts = list(
            dict.fromkeys(allowed_hosts)
        )

    def _validate_auth_email_delivery(self, context: str) -> None:
        if not (
            self.auth_public_registration_enabled
            or self.auth_password_reset_enabled
        ):
            return
        if not self.deployment_external_url.strip():
            raise ValueError(f"{context} requires deployment_external_url for auth email")
        parsed_external_url = urlparse(self.deployment_external_url)
        if parsed_external_url.scheme != "https" or not parsed_external_url.hostname:
            raise ValueError(
                f"{context} requires an https deployment_external_url for auth email"
            )
        parsed_smtp_url = urlparse(self.auth_smtp_url)
        if parsed_smtp_url.scheme not in {"smtp", "smtps"} or not parsed_smtp_url.hostname:
            raise ValueError(f"{context} requires auth_smtp_url")
        if "@" not in self.auth_email_from:
            raise ValueError(f"{context} requires auth_email_from")

    def _validate_auth_secret_values(self, context: str) -> None:
        if self._is_default_auth_secret(self.access_token_secret):
            raise ValueError(f"{context} cannot use default access_token_secret")
        if self._is_default_auth_secret(self.password_hash_salt):
            raise ValueError(f"{context} cannot use default password_hash_salt")
        self._validate_auth_secret_length(
            context,
            "access_token_secret",
            self.access_token_secret,
        )
        self._validate_auth_secret_length(
            context,
            "password_hash_salt",
            self.password_hash_salt,
        )
        if (
            self.external_share_link_token_hash_secret.strip()
            and self._is_default_auth_secret(
                self.external_share_link_token_hash_secret
            )
        ):
            raise ValueError(
                f"{context} cannot use default external_share_link_token_hash_secret"
            )
        if self.external_share_link_token_hash_secret.strip():
            self._validate_auth_secret_length(
                context,
                "external_share_link_token_hash_secret",
                self.external_share_link_token_hash_secret,
            )
        self._validate_password_hash_iterations(context)

    @staticmethod
    def _validate_auth_secret_length(context: str, field_name: str, value: str) -> None:
        if len(value.strip()) < AUTH_SECRET_MIN_LENGTH:
            verb = Settings._context_verb(context)
            raise ValueError(
                f"{context} {verb} {field_name} to be at least {AUTH_SECRET_MIN_LENGTH} characters"
            )

    def _validate_password_hash_iterations(self, context: str) -> None:
        if self.password_hash_iterations < PASSWORD_HASH_MIN_ITERATIONS:
            verb = self._context_verb(context)
            raise ValueError(
                f"{context} {verb} password_hash_iterations to be at least {PASSWORD_HASH_MIN_ITERATIONS}"
            )

    @staticmethod
    def _context_verb(context: str) -> str:
        return "require" if context.endswith("deployments") else "requires"

    @staticmethod
    def _is_default_auth_secret(value: str) -> bool:
        return value.strip() in DEFAULT_AUTH_SECRET_VALUES

    def _validate_operator_token_values(self, context: str) -> None:
        self._validate_optional_operator_token(
            context,
            "tenant_bootstrap_token",
            self.tenant_bootstrap_token,
        )
        self._validate_optional_operator_token(
            context,
            "sandbox_secret_resolver_token",
            self.sandbox_secret_resolver_token,
        )

    @staticmethod
    def _validate_optional_operator_token(
        context: str,
        field_name: str,
        value: str,
    ) -> None:
        stripped = value.strip()
        if stripped and stripped in DEFAULT_OPERATOR_TOKEN_VALUES:
            raise ValueError(f"{context} cannot use default {field_name}")
        if stripped and len(stripped) < OPERATOR_TOKEN_MIN_LENGTH:
            verb = Settings._context_verb(context)
            raise ValueError(
                f"{context} {verb} {field_name} to be at least {OPERATOR_TOKEN_MIN_LENGTH} characters"
            )

    def _validate_air_gapped_deployment(self) -> None:
        if not self._is_internal_endpoint(self.model_gateway_base_url):
            raise ValueError(
                "air-gapped deployments require an internal model gateway endpoint"
            )
        if self.sandbox_provider == "e2b":
            raise ValueError(
                "air-gapped deployments cannot use the e2b sandbox provider"
            )
        if self.browser_provider == "browserbase":
            raise ValueError(
                "air-gapped deployments cannot use the browserbase browser provider"
            )
        if self.browser_provider != "disabled" and not self._is_internal_endpoint(
            self.browser_controller_base_url
        ):
            raise ValueError(
                "air-gapped deployments require an internal browser controller endpoint"
            )

    @staticmethod
    def _is_internal_endpoint(value: str) -> bool:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        if host == "localhost":
            return True
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            return address.is_private or address.is_loopback
        return host.endswith((".internal", ".cluster.local", ".svc")) or ".svc." in host

    def database_config(self) -> DatabaseConfig:
        return DatabaseConfig(
            url=self.database_url,
            pool_min_size=self.database_pool_min_size,
            pool_max_size=self.database_pool_max_size,
            pool_timeout_seconds=self.database_pool_timeout_seconds,
        )

    def license_signature_verifier(self) -> LicenseSignatureVerifier:
        return LicenseSignatureVerifier(
            trusted_public_keys=self.license_trusted_public_keys
        )

    @field_validator("guardrail_secret_detector_stages")
    @classmethod
    def validate_guardrail_secret_detector_stages(cls, value: list[str]) -> list[str]:
        return cls._validate_guardrail_stages(value)

    @field_validator("guardrail_prompt_threat_detector_stages")
    @classmethod
    def validate_guardrail_prompt_threat_detector_stages(
        cls, value: list[str]
    ) -> list[str]:
        return cls._validate_guardrail_stages(value)

    @field_validator("guardrail_http_detector_stages")
    @classmethod
    def validate_guardrail_http_detector_stages(cls, value: list[str]) -> list[str]:
        return cls._validate_guardrail_stages(value)

    @classmethod
    def _validate_guardrail_stages(cls, value: list[str]) -> list[str]:
        allowed = {
            "input",
            "retrieval",
            "model_request",
            "model_response",
            "tool_request",
            "tool_response",
            "artifact",
            "memory_write",
        }
        normalized = [stage.strip() for stage in value]
        invalid = [stage for stage in normalized if stage not in allowed]
        if invalid:
            raise ValueError(f"unsupported guardrail detector stages: {invalid}")
        return list(dict.fromkeys(normalized))


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    return Settings(_env_file=env_file)

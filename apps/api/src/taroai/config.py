from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taroai.model_gateway import ModelPolicyScope


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TAROAI_",
        extra="ignore",
    )

    api_title: str = "Taroai Control Plane API"
    environment: str = "local"
    database_url: str = "postgresql://taroai:taroai@localhost:5432/taroai"
    control_plane_store_backend: Literal["memory", "sql"] = "memory"
    identity_service_backend: Literal["memory", "sql"] = "memory"
    skill_registry_backend: Literal["memory", "sql"] = "memory"
    knowledge_service_backend: Literal["memory", "sql"] = "memory"
    long_term_memory_backend: Literal["memory", "sql"] = "memory"
    short_term_memory_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    short_term_memory_ttl_seconds: int = 3600
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_bucket: str = "taroai-artifacts"
    object_storage_region: str = "us-east-1"
    data_residency_primary_region: str = "us-east-1"
    data_residency_allowed_regions: list[str] = Field(default_factory=lambda: ["us-east-1"])
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
    storage_catalog_backend: Literal["memory", "sql"] = "memory"
    lifecycle_policy_backend: Literal["memory", "sql"] = "memory"
    sandbox_provider: Literal["disabled", "e2b", "k8s"] = "disabled"
    sandbox_provider_region: str = "us-east-1"
    sandbox_runtime_image: str = "python:3.12-slim"
    sandbox_timeout_seconds: int = 300
    sandbox_network_mode: Literal["disabled", "allowlist", "open"] = "disabled"
    browser_provider: Literal["disabled", "playwright", "browserbase"] = "disabled"
    model_gateway_base_url: str = "https://api.openai.com/v1"
    model_gateway_api_key: str = ""
    model_gateway_model: str | None = None
    model_gateway_timeout_seconds: int = 30
    model_gateway_allowed_models: list[str] = Field(default_factory=list)
    model_gateway_denied_models: list[str] = Field(default_factory=list)
    model_gateway_policy_scopes: list[ModelPolicyScope] = Field(default_factory=list)
    model_gateway_policy_store_backend: Literal["memory", "sql"] = "memory"
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
    guardrail_secret_detector_enabled: bool = False
    guardrail_secret_detector_action: Literal["warn", "redact", "require_approval", "block"] = "redact"
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
    auth_session_backend: Literal["auto", "memory", "sql"] = "auto"
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
    run_execution_queue_name: str = "runs.execute"
    billing_queue_name: str = "billing.aggregate"
    cleanup_queue_name: str = "system.cleanup"
    worker_job_lease_seconds: int = 300
    worker_job_retry_delay_seconds: int = 30
    worker_job_max_attempts: int = 3
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    event_stream_media_type: str = "text/event-stream"

    @field_validator("data_residency_allowed_regions")
    @classmethod
    def validate_data_residency_allowed_regions(cls, value: list[str]) -> list[str]:
        normalized = [region.strip() for region in value]
        if not normalized or any(not region for region in normalized):
            raise ValueError("data residency allowed regions must not be empty")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_data_residency_primary_region(self) -> "Settings":
        if self.data_residency_primary_region not in self.data_residency_allowed_regions:
            raise ValueError("data residency primary region must be in allowed regions")
        return self

    @field_validator("guardrail_secret_detector_stages")
    @classmethod
    def validate_guardrail_secret_detector_stages(cls, value: list[str]) -> list[str]:
        return cls._validate_guardrail_stages(value)

    @field_validator("guardrail_prompt_threat_detector_stages")
    @classmethod
    def validate_guardrail_prompt_threat_detector_stages(cls, value: list[str]) -> list[str]:
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

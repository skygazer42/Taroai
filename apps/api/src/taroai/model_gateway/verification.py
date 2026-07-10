import argparse
import json
import os
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.model_gateway.gateway import (
    ModelGatewayRouter,
    OpenAICompatibleModelGateway,
)
from taroai.model_gateway.models import (
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelMessage,
)
from taroai.model_gateway.providers import ModelProviderConfig, ModelProviderRegistry
from taroai.secrets import InMemorySecretService, SecretScope


OPENAI_COMPATIBLE_DEFAULT_BASE_URL = "https://api.openai.com/v1"
MODEL_GATEWAY_VERIFICATION_PROFILES = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "chat_request_options": {
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
    }
}
DEFAULT_VERIFICATION_SYSTEM_PROMPT = (
    "You are verifying an enterprise agent model gateway. "
    "Return only strict JSON. The JSON object must contain a non-empty steps array."
)
DEFAULT_VERIFICATION_USER_PROMPT = (
    'Return exactly one planning step: {"steps":[{"id":"step_provider_verify",'
    '"title":"Verify provider planning","tool_name":"planning.record",'
    '"tool_input":{"status":"ok"}}]}'
)


def env_value_or_none(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def verification_profile_defaults(profile: str | None) -> dict[str, object]:
    if profile is None or not profile.strip():
        return {}
    normalized = profile.strip().lower()
    defaults = MODEL_GATEWAY_VERIFICATION_PROFILES.get(normalized)
    if defaults is None:
        supported = ", ".join(sorted(MODEL_GATEWAY_VERIFICATION_PROFILES))
        raise ValueError(
            "unsupported model gateway verification profile: "
            f"{profile}; supported: {supported}"
        )
    return defaults


def resolve_cli_setting(
    explicit_value: str | None,
    env_name: str,
    profile_defaults: dict[str, object],
    profile_key: str,
    fallback: str | None = None,
) -> str | None:
    if explicit_value is not None and explicit_value.strip():
        return explicit_value.strip()
    env_value = env_value_or_none(env_name)
    if env_value is not None:
        return env_value
    profile_value = profile_defaults.get(profile_key)
    if isinstance(profile_value, str) and profile_value:
        return profile_value
    return fallback


def resolve_cli_chat_request_options(
    raw_value: str | None,
    profile_defaults: dict[str, object],
) -> dict[str, object]:
    if raw_value is not None and raw_value.strip():
        parsed = json.loads(raw_value)
        if not isinstance(parsed, dict):
            raise ValueError("model gateway chat request options must be a JSON object")
        return dict(parsed)
    env_value = env_value_or_none("TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS")
    if env_value is not None:
        parsed = json.loads(env_value)
        if not isinstance(parsed, dict):
            raise ValueError("model gateway chat request options must be a JSON object")
        return dict(parsed)
    profile_value = profile_defaults.get("chat_request_options")
    if isinstance(profile_value, dict):
        return dict(profile_value)
    return {}


def resolve_cli_api_key(
    explicit_value: str | None,
    api_key_env_var: str | None,
) -> str:
    if explicit_value is not None and explicit_value.strip():
        return explicit_value.strip()
    if api_key_env_var is not None and api_key_env_var.strip():
        return os.environ.get(api_key_env_var.strip(), "")
    return os.environ.get("TAROAI_MODEL_GATEWAY_API_KEY", "")


def parse_provider_configs(raw: str | None) -> list[ModelProviderConfig]:
    if raw is None or not raw.strip():
        return []
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("model gateway providers must be a JSON array")
    return [ModelProviderConfig.model_validate(provider) for provider in parsed]


def parse_secret_values(raw: str | None) -> dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("model gateway verification secret values must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def parse_secret_value_env_vars(raw: str | None) -> dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(
            "model gateway verification secret value env vars must be a JSON object"
        )
    values: dict[str, str] = {}
    for secret_ref_id, env_var_name in parsed.items():
        values[str(secret_ref_id)] = os.environ.get(str(env_var_name), "")
    return values


class OpenAICompatibleModelGatewayVerificationConfig(BaseModel):
    base_url: str = Field(
        default_factory=lambda: os.environ.get(
            "TAROAI_MODEL_GATEWAY_BASE_URL",
            OPENAI_COMPATIBLE_DEFAULT_BASE_URL,
        ),
        min_length=1,
    )
    api_key: str = Field(
        default_factory=lambda: os.environ.get("TAROAI_MODEL_GATEWAY_API_KEY", ""),
        exclude=True,
        repr=False,
    )
    model: str | None = Field(
        default_factory=lambda: env_value_or_none("TAROAI_MODEL_GATEWAY_MODEL"),
    )
    providers: list[ModelProviderConfig] = Field(
        default_factory=lambda: parse_provider_configs(
            os.environ.get("TAROAI_MODEL_GATEWAY_PROVIDERS", "")
        )
    )
    verification_secret_values: dict[str, str] = Field(
        default_factory=lambda: parse_secret_values(
            os.environ.get("TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUES", "")
        ),
        exclude=True,
        repr=False,
    )
    tenant_id: str = Field(default="tenant_provider_verify", min_length=1)
    workspace_id: str = Field(default="workspace_provider_verify", min_length=1)
    user_id: str = Field(default="user_provider_verify", min_length=1)
    run_id: str = Field(default_factory=lambda: f"run_provider_verify_{uuid4().hex[:12]}")
    timeout_seconds: int = Field(default=30, ge=1)
    max_output_tokens: int = Field(default=256, ge=1)
    chat_request_options: dict[str, object] = Field(default_factory=dict)
    expected_tool_name: str = Field(default="planning.record", min_length=1)
    system_prompt: str = Field(default=DEFAULT_VERIFICATION_SYSTEM_PROMPT, min_length=1)
    user_prompt: str = Field(default=DEFAULT_VERIFICATION_USER_PROMPT, min_length=1)

    @model_validator(mode="after")
    def require_direct_config_or_providers(self):
        if self.providers:
            for provider in self.providers:
                if provider.api_key:
                    continue
                secret_ref_id = provider.api_key_secret_ref_id
                if secret_ref_id is not None:
                    secret_value = self.verification_secret_values.get(secret_ref_id, "")
                    if secret_value:
                        continue
                    raise ValueError(
                        "model gateway verification secret value is not configured "
                        f"for provider {provider.id}"
                    )
                raise ValueError(
                    "model gateway provider api key is not configured "
                    f"for provider {provider.id}"
                )
            return self
        if not self.model:
            raise ValueError("model gateway model is not configured")
        if not self.api_key:
            raise ValueError("model gateway api key is not configured")
        return self


class OpenAICompatibleModelGatewayVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool
    base_url: str
    model: str
    provider_id: str | None = None
    response_id: str
    planned_step_count: int
    planned_tool_names: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def parse_args(
    argv: list[str] | None = None,
) -> OpenAICompatibleModelGatewayVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify a configured OpenAI-compatible model gateway provider."
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("TAROAI_MODEL_GATEWAY_VERIFICATION_PROFILE", ""),
        help="Optional verification profile. Currently supported: deepseek.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Provider API key. Prefer --api-key-env-var or TAROAI_MODEL_GATEWAY_API_KEY.",
    )
    parser.add_argument(
        "--api-key-env-var",
        default=os.environ.get("TAROAI_MODEL_GATEWAY_API_KEY_ENV_VAR", ""),
        help="Environment variable name containing the provider API key.",
    )
    parser.add_argument(
        "--model",
        default=None,
    )
    parser.add_argument(
        "--providers-json",
        default=os.environ.get("TAROAI_MODEL_GATEWAY_PROVIDERS", ""),
    )
    parser.add_argument(
        "--secret-values-json",
        default=os.environ.get("TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUES", ""),
    )
    parser.add_argument(
        "--secret-value-env-json",
        default=os.environ.get(
            "TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON",
            "",
        ),
        help="JSON object mapping provider secret refs to environment variable names.",
    )
    parser.add_argument("--tenant-id", default="tenant_provider_verify")
    parser.add_argument("--workspace-id", default="workspace_provider_verify")
    parser.add_argument("--user-id", default="user_provider_verify")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument(
        "--chat-request-options-json",
        default=os.environ.get("TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS", ""),
    )
    parser.add_argument("--expected-tool-name", default="planning.record")
    parsed = parser.parse_args(argv)
    profile_defaults = verification_profile_defaults(parsed.profile)
    verification_secret_values = parse_secret_values(parsed.secret_values_json)
    verification_secret_values.update(
        parse_secret_value_env_vars(parsed.secret_value_env_json)
    )
    config_data = {
        "base_url": resolve_cli_setting(
            parsed.base_url,
            "TAROAI_MODEL_GATEWAY_BASE_URL",
            profile_defaults,
            "base_url",
            OPENAI_COMPATIBLE_DEFAULT_BASE_URL,
        ),
        "api_key": resolve_cli_api_key(parsed.api_key, parsed.api_key_env_var),
        "model": resolve_cli_setting(
            parsed.model,
            "TAROAI_MODEL_GATEWAY_MODEL",
            profile_defaults,
            "model",
        ),
        "providers": parse_provider_configs(parsed.providers_json),
        "verification_secret_values": verification_secret_values,
        "tenant_id": parsed.tenant_id,
        "workspace_id": parsed.workspace_id,
        "user_id": parsed.user_id,
        "timeout_seconds": parsed.timeout_seconds,
        "max_output_tokens": parsed.max_output_tokens,
        "chat_request_options": resolve_cli_chat_request_options(
            parsed.chat_request_options_json,
            profile_defaults,
        ),
        "expected_tool_name": parsed.expected_tool_name,
    }
    if parsed.run_id is not None:
        config_data["run_id"] = parsed.run_id
    return OpenAICompatibleModelGatewayVerificationConfig(**config_data)


def verify_openai_compatible_model_gateway(
    config: OpenAICompatibleModelGatewayVerificationConfig,
    gateway=None,
) -> OpenAICompatibleModelGatewayVerificationResult:
    model_gateway = gateway or build_verification_gateway(config)
    response = model_gateway.create_plan(build_verification_request(config))
    return build_verification_result(config, response)


def build_verification_gateway(config: OpenAICompatibleModelGatewayVerificationConfig):
    if config.providers:
        providers, secret_service = prepare_provider_secret_refs(config)
        return ModelGatewayRouter(
            provider_registry=ModelProviderRegistry(providers=providers),
            secret_service=secret_service,
        )
    return OpenAICompatibleModelGateway(
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.model,
        timeout_seconds=config.timeout_seconds,
        chat_request_options=config.chat_request_options,
    )


def prepare_provider_secret_refs(
    config: OpenAICompatibleModelGatewayVerificationConfig,
) -> tuple[list[ModelProviderConfig], InMemorySecretService | None]:
    if not config.verification_secret_values:
        return config.providers, None
    secret_service = InMemorySecretService()
    providers: list[ModelProviderConfig] = []
    for provider in config.providers:
        secret_ref_id = provider.api_key_secret_ref_id
        if secret_ref_id is None or provider.api_key:
            providers.append(provider)
            continue
        secret_value = config.verification_secret_values.get(secret_ref_id)
        if secret_value is None:
            providers.append(provider)
            continue
        tenant_id = provider.tenant_id or config.tenant_id
        workspace_id = provider.workspace_id
        secret = secret_service.create_secret(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=f"model-gateway-verification-{provider.id}",
            value=secret_value,
            scope=SecretScope(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                allowed_tool_names=["model_gateway"],
                actions=["invoke"],
            ),
        )
        providers.append(
            provider.model_copy(update={"api_key_secret_ref_id": secret.id})
        )
    return providers, secret_service


def build_verification_request(
    config: OpenAICompatibleModelGatewayVerificationConfig,
) -> ModelGatewayRequest:
    return ModelGatewayRequest(
        tenant_id=config.tenant_id,
        workspace_id=config.workspace_id,
        user_id=config.user_id,
        run_id=config.run_id,
        model=config.model,
        messages=[
            ModelMessage(role="system", content=config.system_prompt),
            ModelMessage(role="user", content=config.user_prompt),
        ],
        temperature=0,
        max_output_tokens=config.max_output_tokens,
        metadata={"verification": "openai_compatible_model_gateway"},
    )


def build_verification_result(
    config: OpenAICompatibleModelGatewayVerificationConfig,
    response: ModelGatewayResponse,
) -> OpenAICompatibleModelGatewayVerificationResult:
    planned_tool_names = [step.tool_name for step in response.planned_steps]
    if not planned_tool_names:
        raise RuntimeError("model gateway verification did not return planned steps")
    if config.expected_tool_name not in planned_tool_names:
        raise RuntimeError("model gateway verification returned an unexpected tool plan")
    usage = response.usage
    provider = provider_for_result(config, response.provider)
    model = response.model or config.model or (provider.default_model if provider else "")
    return OpenAICompatibleModelGatewayVerificationResult(
        verified=True,
        base_url=provider.base_url if provider else config.base_url,
        model=model,
        provider_id=response.provider,
        response_id=response.id,
        planned_step_count=len(response.planned_steps),
        planned_tool_names=planned_tool_names,
        input_tokens=usage.input_tokens if usage is not None else 0,
        output_tokens=usage.output_tokens if usage is not None else 0,
        total_tokens=usage.total_tokens if usage is not None else 0,
    )


def provider_for_result(
    config: OpenAICompatibleModelGatewayVerificationConfig,
    provider_id: str | None,
) -> ModelProviderConfig | None:
    if provider_id is None:
        return None
    for provider in config.providers:
        if provider.id == provider_id:
            return provider
    return None


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_openai_compatible_model_gateway(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from pathlib import Path

import pytest

from taroai.model_gateway.models import (
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelUsage,
    PlannedToolCall,
)
from taroai.model_gateway.gateway import OpenAICompatibleModelGateway
from taroai.model_gateway.providers import ModelProviderConfig
from taroai.model_gateway.verification import (
    OpenAICompatibleModelGatewayVerificationConfig,
    parse_args,
    verify_openai_compatible_model_gateway,
)


class RecordingOpenAICompatibleGateway:
    def __init__(self):
        self.requests: list[ModelGatewayRequest] = []

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        self.requests.append(request)
        return ModelGatewayResponse(
            id="response_provider_verify",
            model=request.model,
            output_text='{"steps":[{"id":"step_provider_verify"}]}',
            planned_steps=[
                PlannedToolCall(
                    id="step_provider_verify",
                    title="Verify provider planning",
                    tool_name="planning.record",
                    tool_input={"status": "ok"},
                )
            ],
            usage=ModelUsage(input_tokens=12, output_tokens=16, total_tokens=28),
        )


def test_model_gateway_verification_cli_parses_provider_inputs():
    config = parse_args(
        [
            "--base-url",
            "https://model.example.com/v1",
            "--api-key",
            "sk-live-provider",
            "--model",
            "gpt-4.1",
            "--timeout-seconds",
            "12",
        ]
    )

    assert config.base_url == "https://model.example.com/v1"
    assert config.api_key == "sk-live-provider"
    assert config.model == "gpt-4.1"
    assert config.timeout_seconds == 12
    assert "sk-live-provider" not in str(config.model_dump(mode="json"))
    assert "sk-live-provider" not in repr(config)


def test_verify_model_gateway_script_wraps_python_cli():
    script = Path("scripts/verify-model-gateway.sh")

    text = script.read_text()

    assert "python -m taroai.model_gateway.verification" in text
    assert "--profile" in text
    assert "--api-key-env-var" in text
    assert "TAROAI_MODEL_GATEWAY_API_KEY" in text


def test_model_gateway_verification_cli_applies_deepseek_profile(monkeypatch):
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_MODEL", raising=False)
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-deepseek")

    config = parse_args(
        [
            "--profile",
            "deepseek",
            "--api-key-env-var",
            "DEEPSEEK_API_KEY",
        ]
    )

    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-v4-flash"
    assert config.api_key == "sk-live-deepseek"
    assert config.chat_request_options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    assert "sk-live-deepseek" not in str(config.model_dump(mode="json"))
    assert "sk-live-deepseek" not in repr(config)


def test_model_gateway_verification_cli_allows_profile_model_override(monkeypatch):
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_MODEL", raising=False)
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-deepseek")

    config = parse_args(
        [
            "--profile",
            "deepseek",
            "--api-key-env-var",
            "DEEPSEEK_API_KEY",
            "--model",
            "deepseek-v4-pro",
        ]
    )

    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-v4-pro"
    assert config.api_key == "sk-live-deepseek"
    assert config.chat_request_options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }


def test_model_gateway_verification_cli_reads_profile_from_environment(monkeypatch):
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("TAROAI_MODEL_GATEWAY_MODEL", raising=False)
    monkeypatch.setenv("TAROAI_MODEL_GATEWAY_VERIFICATION_PROFILE", "deepseek")
    monkeypatch.setenv("TAROAI_MODEL_GATEWAY_API_KEY", "sk-live-deepseek")

    config = parse_args([])

    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-v4-flash"
    assert config.api_key == "sk-live-deepseek"


def test_model_gateway_verification_cli_parses_provider_registry_inputs():
    providers_json = (
        '[{"id":"sales-openai","base_url":"https://model.example.com/v1",'
        '"api_key":"sk-provider","default_model":"gpt-4.1",'
        '"tenant_id":"tenant_verify","workspace_id":"workspace_verify"}]'
    )

    config = parse_args(
        [
            "--providers-json",
            providers_json,
            "--tenant-id",
            "tenant_verify",
            "--workspace-id",
            "workspace_verify",
            "--timeout-seconds",
            "12",
        ]
    )

    assert len(config.providers) == 1
    provider = config.providers[0]
    assert provider.id == "sales-openai"
    assert provider.base_url == "https://model.example.com/v1"
    assert provider.default_model == "gpt-4.1"
    assert provider.tenant_id == "tenant_verify"
    assert provider.workspace_id == "workspace_verify"
    assert config.timeout_seconds == 12
    assert "sk-provider" not in str(config.model_dump(mode="json"))
    assert "sk-provider" not in repr(config)


def test_model_gateway_verification_calls_openai_compatible_gateway():
    gateway = RecordingOpenAICompatibleGateway()
    config = OpenAICompatibleModelGatewayVerificationConfig(
        base_url="https://model.example.com/v1",
        api_key="sk-live-provider",
        model="gpt-4.1",
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        user_id="user_verify",
        run_id="run_verify",
    )

    result = verify_openai_compatible_model_gateway(config, gateway=gateway)

    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.tenant_id == "tenant_verify"
    assert request.workspace_id == "workspace_verify"
    assert request.user_id == "user_verify"
    assert request.run_id == "run_verify"
    assert request.model == "gpt-4.1"
    assert request.temperature == 0
    assert result.base_url == "https://model.example.com/v1"
    assert result.model == "gpt-4.1"
    assert result.response_id == "response_provider_verify"
    assert result.planned_step_count == 1
    assert result.planned_tool_names == ["planning.record"]
    assert result.total_tokens == 28
    assert result.verified is True
    assert "sk-live-provider" not in str(result.model_dump(mode="json"))


def test_model_gateway_verification_profile_sends_deepseek_chat_request_options(
    monkeypatch,
):
    calls = []

    def record_provider_call(self, payload, api_key):
        calls.append(payload)
        return {
            "id": "response_provider_verify",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"steps":[{"id":"step_provider_verify",'
                            '"title":"Verify provider planning",'
                            '"tool_name":"planning.record",'
                            '"tool_input":{"status":"ok"}}]}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        }

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-deepseek")
    monkeypatch.setattr(
        OpenAICompatibleModelGateway,
        "_post_chat_completions",
        record_provider_call,
    )
    config = parse_args(
        [
            "--profile",
            "deepseek",
            "--api-key-env-var",
            "DEEPSEEK_API_KEY",
        ]
    )

    result = verify_openai_compatible_model_gateway(config)

    assert result.verified is True
    assert calls[0]["model"] == "deepseek-v4-flash"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["thinking"] == {"type": "disabled"}


def test_model_gateway_verification_uses_provider_registry(monkeypatch):
    calls = []

    def record_provider_call(self, payload, api_key):
        calls.append(
            {
                "base_url": self.base_url,
                "default_model": self.default_model,
                "payload_model": payload["model"],
                "api_key": api_key,
            }
        )
        return {
            "id": "response_provider_verify",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"steps":[{"id":"step_provider_verify",'
                            '"title":"Verify provider planning",'
                            '"tool_name":"planning.record",'
                            '"tool_input":{"status":"ok"}}]}'
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 16,
                "total_tokens": 28,
            },
        }

    monkeypatch.setattr(
        OpenAICompatibleModelGateway,
        "_post_chat_completions",
        record_provider_call,
    )
    config = OpenAICompatibleModelGatewayVerificationConfig(
        providers=[
            ModelProviderConfig(
                id="sales-openai",
                base_url="https://model.example.com/v1",
                api_key="sk-provider",
                default_model="gpt-4.1",
                tenant_id="tenant_verify",
                workspace_id="workspace_verify",
            )
        ],
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        user_id="user_verify",
        run_id="run_verify",
    )

    result = verify_openai_compatible_model_gateway(config)

    assert calls == [
        {
            "base_url": "https://model.example.com/v1",
            "default_model": "gpt-4.1",
            "payload_model": "gpt-4.1",
            "api_key": "sk-provider",
        }
    ]
    assert result.provider_id == "sales-openai"
    assert result.base_url == "https://model.example.com/v1"
    assert result.model == "gpt-4.1"
    assert result.planned_tool_names == ["planning.record"]
    assert result.total_tokens == 28
    assert "sk-provider" not in str(result.model_dump(mode="json"))


def test_model_gateway_verification_resolves_provider_secret_ref_from_verification_values(
    monkeypatch,
):
    calls = []

    def record_provider_call(self, payload, api_key):
        calls.append(
            {
                "base_url": self.base_url,
                "payload_model": payload["model"],
                "api_key": api_key,
            }
        )
        return {
            "id": "response_provider_verify",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"steps":[{"id":"step_provider_verify",'
                            '"title":"Verify provider planning",'
                            '"tool_name":"planning.record",'
                            '"tool_input":{"status":"ok"}}]}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        }

    monkeypatch.setattr(
        OpenAICompatibleModelGateway,
        "_post_chat_completions",
        record_provider_call,
    )
    config = OpenAICompatibleModelGatewayVerificationConfig(
        providers=[
            ModelProviderConfig(
                id="sales-openai",
                base_url="https://model.example.com/v1",
                api_key_secret_ref_id="secret_sales_model_key",
                default_model="gpt-4.1",
                tenant_id="tenant_verify",
                workspace_id="workspace_verify",
            )
        ],
        verification_secret_values={
            "secret_sales_model_key": "sk-provider-from-secret-ref"
        },
        tenant_id="tenant_verify",
        workspace_id="workspace_verify",
        user_id="user_verify",
        run_id="run_verify",
    )

    result = verify_openai_compatible_model_gateway(config)

    assert calls == [
        {
            "base_url": "https://model.example.com/v1",
            "payload_model": "gpt-4.1",
            "api_key": "sk-provider-from-secret-ref",
        }
    ]
    assert result.provider_id == "sales-openai"
    assert result.total_tokens == 10
    assert "sk-provider-from-secret-ref" not in str(config.model_dump(mode="json"))
    assert "sk-provider-from-secret-ref" not in repr(config)
    assert "sk-provider-from-secret-ref" not in str(result.model_dump(mode="json"))


def test_model_gateway_verification_cli_parses_secret_ref_values_without_dumping_them():
    config = parse_args(
        [
            "--providers-json",
            (
                '[{"id":"sales-openai","base_url":"https://model.example.com/v1",'
                '"api_key_secret_ref_id":"secret_sales_model_key",'
                '"default_model":"gpt-4.1"}]'
            ),
            "--secret-values-json",
            '{"secret_sales_model_key":"sk-provider-from-secret-ref"}',
        ]
    )

    assert config.verification_secret_values == {
        "secret_sales_model_key": "sk-provider-from-secret-ref"
    }
    assert "sk-provider-from-secret-ref" not in str(config.model_dump(mode="json"))
    assert "sk-provider-from-secret-ref" not in repr(config)


def test_model_gateway_verification_cli_reads_secret_ref_values_from_env_map(
    monkeypatch,
):
    monkeypatch.setenv("SALES_MODEL_API_KEY", "sk-provider-from-env")

    config = parse_args(
        [
            "--providers-json",
            (
                '[{"id":"sales-openai","base_url":"https://model.example.com/v1",'
                '"api_key_secret_ref_id":"secret_sales_model_key",'
                '"default_model":"gpt-4.1"}]'
            ),
            "--secret-value-env-json",
            '{"secret_sales_model_key":"SALES_MODEL_API_KEY"}',
        ]
    )

    assert config.verification_secret_values == {
        "secret_sales_model_key": "sk-provider-from-env"
    }
    assert "sk-provider-from-env" not in str(config.model_dump(mode="json"))
    assert "sk-provider-from-env" not in repr(config)


def test_model_gateway_verification_rejects_provider_secret_refs_without_values():
    with pytest.raises(ValueError) as error:
        parse_args(
            [
                "--providers-json",
                (
                    '[{"id":"sales-openai","base_url":"https://model.example.com/v1",'
                    '"api_key_secret_ref_id":"secret_sales_model_key",'
                    '"default_model":"gpt-4.1"}]'
                ),
            ]
        )

    assert "verification secret value is not configured" in str(error.value)

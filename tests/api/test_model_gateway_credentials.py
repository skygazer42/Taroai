import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from pydantic import Field

from taroai.model_gateway import (
    ModelGatewayConfigurationError,
    ModelGatewayRequest,
    ModelGatewayResponseError,
    ModelMessage,
    OpenAICompatibleModelGateway,
)
from taroai.secrets import InMemorySecretService, SecretScope


class RecordingOpenAICompatibleGateway(OpenAICompatibleModelGateway):
    authorization_headers: list[str] = Field(default_factory=list, exclude=True, repr=False)
    payloads: list[dict] = Field(default_factory=list, exclude=True, repr=False)

    def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
        self.payloads.append(payload)
        self.authorization_headers.append(f"Bearer {api_key}")
        return {
            "id": "response_secret_ref",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "steps": [
                                    {
                                        "id": "step_plan",
                                        "title": "Plan with governed credentials",
                                        "tool_name": "planning.record",
                                        "tool_input": {"status": "ok"},
                                    }
                                ]
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "total_tokens": 10,
                "prompt_tokens_details": {
                    "cached_tokens": 2,
                },
            },
        }


def create_model_gateway_request() -> ModelGatewayRequest:
    return ModelGatewayRequest(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        user_id="user_1",
        run_id="run_1",
        messages=[ModelMessage(role="user", content="Plan this governed run.")],
    )


def test_openai_compatible_gateway_uses_secret_ref_without_config_leakage():
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id=None,
        name="model-gateway-api-key",
        value="sk-enterprise-model-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            allowed_tool_names=["model_gateway"],
            actions=["invoke"],
        ),
    )
    gateway = RecordingOpenAICompatibleGateway(
        api_key_secret_ref_id=secret.id,
        secret_service=secret_service,
        default_model="gpt-enterprise",
    )

    response = gateway.create_plan(create_model_gateway_request())

    leases = list(secret_service.leases.values())
    assert response.id == "response_secret_ref"
    assert response.usage.total_tokens == 10
    assert response.usage.cached_input_tokens == 2
    assert gateway.authorization_headers == ["Bearer sk-enterprise-model-key"]
    assert gateway.payloads[0]["model"] == "gpt-enterprise"
    assert len(leases) == 1
    assert leases[0].tenant_id == "tenant_acme"
    assert leases[0].workspace_id == "workspace_sales"
    assert leases[0].run_id == "run_1"
    assert leases[0].step_id == "model_gateway:plan"
    assert leases[0].tool_name == "model_gateway"
    assert leases[0].actions == ["invoke"]
    assert "sk-enterprise-model-key" not in str(gateway.model_dump(mode="json"))
    assert "sk-enterprise-model-key" not in repr(gateway)
    assert "sk-enterprise-model-key" not in str(leases[0].to_audit_metadata())


def test_openai_compatible_gateway_requires_secret_service_for_secret_ref():
    gateway = OpenAICompatibleModelGateway(
        api_key_secret_ref_id="secret_model_key",
        default_model="gpt-enterprise",
    )

    with pytest.raises(ModelGatewayConfigurationError, match="secret service"):
        gateway.create_plan(create_model_gateway_request())


def test_openai_compatible_gateway_hides_legacy_api_key_from_dump_and_repr():
    gateway = OpenAICompatibleModelGateway(
        api_key="sk-legacy-model-key",
        default_model="gpt-enterprise",
    )

    assert "sk-legacy-model-key" not in str(gateway.model_dump(mode="json"))
    assert "sk-legacy-model-key" not in repr(gateway)


def test_openai_compatible_gateway_redacts_provider_error_body_credentials(monkeypatch):
    leaked_key = "sk-live-provider-secret-1234567890"
    response_body = json.dumps(
        {
            "error": {
                "message": f"Incorrect API key provided: {leaked_key}.",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        }
    ).encode("utf-8")

    def raise_provider_error(*args, **kwargs):
        raise HTTPError(
            url="https://model.example.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(response_body),
        )

    monkeypatch.setattr("taroai.model_gateway.gateway.urlopen", raise_provider_error)
    gateway = OpenAICompatibleModelGateway(
        base_url="https://model.example.com/v1",
        api_key=leaked_key,
        default_model="gpt-enterprise",
    )

    with pytest.raises(ModelGatewayResponseError) as raised:
        gateway.create_plan(create_model_gateway_request())

    message = str(raised.value)
    assert "model gateway returned HTTP 401" in message
    assert "invalid_api_key" in message
    assert "[REDACTED]" in message
    assert leaked_key not in message


def test_openai_compatible_gateway_omits_tool_choice_when_no_tools_are_declared():
    gateway = RecordingOpenAICompatibleGateway(
        api_key="sk-enterprise-model-key",
        default_model="gpt-enterprise",
    )
    request = create_model_gateway_request().model_copy(update={"tool_choice": "auto"})

    gateway.create_plan(request)

    assert "tools" not in gateway.payloads[0]
    assert "tool_choice" not in gateway.payloads[0]


def test_openai_compatible_gateway_includes_tool_choice_with_declared_tools():
    gateway = RecordingOpenAICompatibleGateway(
        api_key="sk-enterprise-model-key",
        default_model="gpt-enterprise",
    )
    request = create_model_gateway_request().model_copy(
        update={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "planning_record",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "auto",
        }
    )

    gateway.create_plan(request)

    assert gateway.payloads[0]["tools"][0]["type"] == "function"
    assert gateway.payloads[0]["tool_choice"] == "auto"


def test_openai_compatible_gateway_includes_chat_request_options():
    gateway = RecordingOpenAICompatibleGateway(
        api_key="sk-enterprise-model-key",
        default_model="deepseek-v4-flash",
        chat_request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
    )

    gateway.create_plan(create_model_gateway_request())

    payload = gateway.payloads[0]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}


def test_openai_compatible_gateway_rejects_chat_request_options_that_override_core_payload():
    with pytest.raises(ValueError, match="chat_request_options cannot override"):
        OpenAICompatibleModelGateway(
            api_key="sk-enterprise-model-key",
            default_model="gpt-enterprise",
            chat_request_options={"messages": []},
        )


def test_openai_compatible_gateway_extracts_json_plan_from_fenced_provider_text():
    class FencedPlanningGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "id": "response_fenced_plan",
                "model": payload["model"],
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Here is the plan:\n"
                                "```json\n"
                                '{"steps":[{"id":"step_plan",'
                                '"title":"Plan from fenced provider text",'
                                '"tool_name":"planning.record",'
                                '"tool_input":{"status":"ok"}}]}\n'
                                "```"
                            )
                        }
                    }
                ],
            }

    gateway = FencedPlanningGateway(
        api_key="sk-enterprise-model-key",
        default_model="gpt-enterprise",
    )

    response = gateway.create_plan(create_model_gateway_request())

    assert response.id == "response_fenced_plan"
    assert response.planned_steps[0].tool_name == "planning.record"


def test_openai_compatible_gateway_normalizes_provider_step_ids_to_strings():
    class NumericStepIdGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "id": "response_numeric_step_id",
                "model": payload["model"],
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "steps": [
                                        {
                                            "id": 1,
                                            "title": "Record plan",
                                            "tool_name": "planning.record",
                                            "tool_input": {"status": "ok"},
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
            }

    gateway = NumericStepIdGateway(
        api_key="sk-enterprise-model-key",
        default_model="gpt-enterprise",
    )

    response = gateway.create_plan(create_model_gateway_request())

    assert response.id == "response_numeric_step_id"
    assert response.planned_steps[0].id == "1"
    assert response.planned_steps[0].tool_name == "planning.record"

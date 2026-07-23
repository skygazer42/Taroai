import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from pydantic import Field

from taroai.model_gateway import (
    ModelGatewayConfigurationError,
    ModelGatewayRequest,
    ModelGatewayResponseError,
    ModelSafetyRefusalError,
    ModelMessage,
    OpenAICompatibleModelGateway,
)
from taroai.secrets import InMemorySecretService, SecretScope


class RecordingOpenAICompatibleGateway(OpenAICompatibleModelGateway):
    authorization_headers: list[str] = Field(
        default_factory=list, exclude=True, repr=False
    )
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

    def _post_chat_completions_stream(self, payload: dict, api_key: str):
        self.payloads.append(payload)
        self.authorization_headers.append(f"Bearer {api_key}")
        yield "Final answer"


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


def test_openai_compatible_gateway_detects_http_safety_refusal(monkeypatch):
    response_body = json.dumps(
        {"error": {"code": "1301", "message": "Sensitive content."}}
    ).encode()

    def raise_provider_error(*args, **kwargs):
        raise HTTPError(
            url="https://model.example.com/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=BytesIO(response_body),
        )

    monkeypatch.setattr("taroai.model_gateway.gateway.urlopen", raise_provider_error)
    gateway = OpenAICompatibleModelGateway(
        base_url="https://model.example.com/v1",
        api_key="sk-provider",
        default_model="glm-5.2",
    )

    with pytest.raises(ModelSafetyRefusalError) as raised:
        gateway.create_plan(create_model_gateway_request())

    assert raised.value.model_id == "glm-5.2"
    assert raised.value.original_text == "Sensitive content."


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


def test_openai_compatible_gateway_removes_json_mode_from_final_response():
    gateway = RecordingOpenAICompatibleGateway(
        api_key="sk-enterprise-model-key",
        default_model="deepseek-v4-flash",
        chat_request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
    )

    chunks = list(gateway.stream_response(create_model_gateway_request()))

    assert chunks == ["Final answer"]
    assert "response_format" not in gateway.payloads[0]
    assert gateway.payloads[0]["thinking"] == {"type": "disabled"}


def test_openai_compatible_gateway_streams_native_tool_call_as_one_action():
    class StreamingToolGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _iter_chat_completion_deltas(self, payload: dict, api_key: str):
            self.payloads.append(payload)
            yield {"content": "I will search first."}
            if not payload.get("tools"):
                yield {"content": " Final answer."}
                return
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_search",
                        "function": {"name": "web__", "arguments": '{"query":'},
                    }
                ]
            }
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"name": "search", "arguments": '"latest"}'},
                    }
                ]
            }

    gateway = StreamingToolGateway(
        api_key="sk-provider",
        default_model="glm-5.2",
        chat_request_options={"response_format": {"type": "json_object"}},
    )
    request = create_model_gateway_request().model_copy(
        update={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web__search",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "tool_choice": "auto",
        }
    )

    events = list(gateway.stream_next_action(request))

    assert events[0] == "I will search first."
    assert events[1].tool_name == "web__search"
    assert events[1].tool_input == {"query": "latest"}
    assert list(gateway.stream_next_action(create_model_gateway_request())) == [
        "I will search first.",
        " Final answer.",
    ]
    assert gateway.payloads[0]["stream"] is True
    assert "response_format" not in gateway.payloads[0]


def test_openai_compatible_gateway_yields_text_before_stream_completion():
    class IncrementalGateway(OpenAICompatibleModelGateway):
        consumed_deltas: int = 0

        def _iter_chat_completion_deltas(self, payload: dict, api_key: str):
            self.consumed_deltas += 1
            yield {"content": "First"}
            self.consumed_deltas += 1
            yield {"content": " second"}

    gateway = IncrementalGateway(api_key="sk-provider", default_model="fast-model")
    stream = gateway.stream_next_action(create_model_gateway_request())

    assert next(stream) == "First"
    assert gateway.consumed_deltas == 1
    assert list(stream) == [" second"]


def test_openai_compatible_gateway_enforces_total_stream_timeout(monkeypatch):
    class StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter([b": keep-alive\n", b": keep-alive\n"])

    clock = iter([0.0, 0.5, 1.0])
    monkeypatch.setattr(
        "taroai.model_gateway.gateway.time.monotonic", lambda: next(clock)
    )
    monkeypatch.setattr(
        "taroai.model_gateway.gateway.urlopen",
        lambda *args, **kwargs: StreamResponse(),
    )
    gateway = OpenAICompatibleModelGateway(
        api_key="sk-provider",
        default_model="slow-model",
        timeout_seconds=1,
    )

    with pytest.raises(ModelGatewayResponseError, match="stream timed out") as raised:
        list(gateway.stream_response(create_model_gateway_request()))

    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("event", "original_text"),
    [
        (
            {
                "model": "glm-5.2",
                "choices": [{"delta": {"refusal": "Request declined."}}],
            },
            "Request declined.",
        ),
        (
            {
                "model": "glm-5.2",
                "choices": [{"delta": {}, "finish_reason": "content_filter"}],
            },
            "",
        ),
        (
            {
                "model": "glm-5.2",
                "choices": [{"delta": {}, "finish_reason": "sensitive"}],
            },
            "",
        ),
        (
            {"error": {"code": "1301", "message": "Sensitive content."}},
            "Sensitive content.",
        ),
    ],
)
def test_openai_compatible_gateway_detects_provider_safety_refusal(
    monkeypatch,
    event,
    original_text,
):
    class StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter(
                [
                    f"data: {json.dumps(event)}\n".encode(),
                    b"data: [DONE]\n",
                ]
            )

    monkeypatch.setattr(
        "taroai.model_gateway.gateway.urlopen",
        lambda *args, **kwargs: StreamResponse(),
    )
    gateway = OpenAICompatibleModelGateway(
        api_key="sk-provider",
        default_model="glm-5.2",
    )

    with pytest.raises(ModelSafetyRefusalError) as raised:
        list(gateway.stream_response(create_model_gateway_request()))

    assert raised.value.model_id == "glm-5.2"
    assert raised.value.original_text == original_text


def test_openai_compatible_gateway_normalizes_nullable_decision_lists():
    class NullableDecisionGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "action",
                                    "tool_name": "web.search",
                                    "tool_input": {"query": "current facts"},
                                    "response_options": None,
                                    "response_questions": None,
                                }
                            )
                        }
                    }
                ]
            }

    decision = NullableDecisionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.tool_name == "web.search"
    assert decision.response_options == []
    assert decision.response_questions == []


def test_openai_compatible_gateway_normalizes_string_response_questions():
    class StringQuestionGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "request_input",
                                    "response_questions": ["请问您想查询哪个城市？"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    decision = StringQuestionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.response_questions[0].question == "请问您想查询哪个城市？"


def test_openai_compatible_gateway_normalizes_string_web_search_input():
    class StringToolInputGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "action",
                                    "tool_name": "web__search",
                                    "tool_input": "明天 北京 上海 航班",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    decision = StringToolInputGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.tool_input == {"query": "明天 北京 上海 航班"}


def test_openai_compatible_gateway_normalizes_string_web_fetch_input():
    class StringToolInputGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "action",
                                    "tool_name": "web__fetch",
                                    "tool_input": "https://www.python.org/downloads/",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    decision = StringToolInputGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.tool_input == {"url": "https://www.python.org/downloads/"}


def test_openai_compatible_gateway_parses_optional_verification_flag():
    class GroundedResponseGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "respond",
                                    "response_text": "你好。",
                                    "verification_required": False,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    decision = GroundedResponseGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.verification_required is False


def test_openai_compatible_gateway_repairs_one_invalid_decision_response():
    class RepairingDecisionGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            self.payloads.append(payload)
            content = (
                '{"kind":"action"}'
                if len(self.payloads) == 1
                else '{"kind":"respond","response_text":"已修正。"}'
            )
            return {"choices": [{"message": {"content": content}}]}

    gateway = RepairingDecisionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    )
    request = create_model_gateway_request().model_copy(
        update={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web__search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "auto",
        }
    )

    decision = gateway.decide_next_action(request)

    assert decision.response_text == "已修正。"
    assert len(gateway.payloads) == 2
    assert "not valid" in gateway.payloads[1]["messages"][-1]["content"]
    assert "tools" not in gateway.payloads[1]
    assert "tool_choice" not in gateway.payloads[1]


def test_openai_compatible_gateway_repairs_empty_respond_decision():
    class RepairingDecisionGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            self.payloads.append(payload)
            content = (
                '{"kind":"respond"}'
                if len(self.payloads) == 1
                else '{"kind":"respond","response_text":"已根据证据回答。"}'
            )
            return {"choices": [{"message": {"content": content}}]}

    gateway = RepairingDecisionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    )

    decision = gateway.decide_next_action(create_model_gateway_request())

    assert decision.response_text == "已根据证据回答。"
    assert len(gateway.payloads) == 2
    assert "non-empty response_text" in gateway.payloads[1]["messages"][-1]["content"]


def test_openai_compatible_gateway_repairs_one_invalid_plan_response():
    class RepairingPlanGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            self.payloads.append(payload)
            content = (
                "I will split this into two steps."
                if len(self.payloads) == 1
                else json.dumps(
                    {
                        "steps": [
                            {
                                "id": "step_1",
                                "title": "整理数字",
                                "tool_name": "planning.record",
                                "tool_input": {},
                                "depends_on": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
            return {"choices": [{"message": {"content": content}}]}

    gateway = RepairingPlanGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    )

    response = gateway.create_plan(create_model_gateway_request())

    assert response.planned_steps[0].id == "step_1"
    assert len(gateway.payloads) == 2
    assert "valid workflow plan" in gateway.payloads[1]["messages"][-1]["content"]
    assert "tools" not in gateway.payloads[1]
    assert "tool_choice" not in gateway.payloads[1]


def test_openai_compatible_gateway_repairs_empty_request_input():
    class RepairingDecisionGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            self.payloads.append(payload)
            content = (
                '{"kind":"request_input"}'
                if len(self.payloads) == 1
                else json.dumps(
                    {
                        "kind": "request_input",
                        "response_text": "请补充旅行时间。",
                        "response_questions": [{"question": "你计划哪天出发？"}],
                    },
                    ensure_ascii=False,
                )
            )
            return {"choices": [{"message": {"content": content}}]}

    gateway = RepairingDecisionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    )

    decision = gateway.decide_next_action(create_model_gateway_request())

    assert decision.response_text == "请补充旅行时间。"
    assert decision.response_questions[0].question == "你计划哪天出发？"
    assert len(gateway.payloads) == 2
    assert "prompt or choices" in gateway.payloads[1]["messages"][-1]["content"]


def test_openai_compatible_gateway_returns_plain_text_as_a_verifiable_response():
    class PlainTextDecisionGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            self.payloads.append(payload)
            return {
                "choices": [
                    {
                        "message": {
                            "content": "I cannot perform that external action yet."
                        }
                    }
                ]
            }

    gateway = PlainTextDecisionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    )

    decision = gateway.decide_next_action(create_model_gateway_request())

    assert decision.response_text == "I cannot perform that external action yet."
    assert decision.verification_required is True
    assert len(gateway.payloads) == 1


def test_openai_compatible_gateway_repairs_one_plain_text_verification_response():
    class RepairingVerificationGateway(OpenAICompatibleModelGateway):
        payloads: list[dict] = Field(default_factory=list)

        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            self.payloads.append(payload)
            content = (
                "The evidence is sufficient."
                if len(self.payloads) == 1
                else '{"outcome":"complete","feedback":"Verified.","evidence":["result"]}'
            )
            return {"choices": [{"message": {"content": content}}]}

    gateway = RepairingVerificationGateway(
        api_key="sk-provider",
        default_model="grok-4.5",
    )
    request = create_model_gateway_request().model_copy(
        update={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web__search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "auto",
        }
    )

    result = gateway.verify_completion(request)

    assert result.outcome == "complete"
    assert len(gateway.payloads) == 2
    assert "not valid" in gateway.payloads[1]["messages"][-1]["content"]
    assert "tools" not in gateway.payloads[1]
    assert "tool_choice" not in gateway.payloads[1]


def test_openai_compatible_gateway_does_not_infer_questions_from_keywords():
    class QuestionGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "respond",
                                    "response_text": (
                                        "请补充以下信息：\n\n"
                                        "团队规模：大约多少人参加？\n"
                                        "复盘主题：针对哪个项目或周期？\n"
                                        "团队形式：远程还是线下？"
                                    ),
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    decision = QuestionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.response_questions == []
    assert decision.kind == "respond"


def test_openai_compatible_gateway_honors_structured_response_questions():
    class QuestionGateway(OpenAICompatibleModelGateway):
        def _post_chat_completions(self, payload: dict, api_key: str) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "respond",
                                    "response_text": "请补充城市。",
                                    "response_questions": [
                                        {
                                            "question": "你想查询哪个城市？",
                                            "options": ["北京", "上海"],
                                        },
                                        {"question": "哪一天？"},
                                        {"question": "什么时间？"},
                                        {"question": "是否需要提醒？"},
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    decision = QuestionGateway(
        api_key="sk-provider",
        default_model="deepseek-v4-flash",
    ).decide_next_action(create_model_gateway_request())

    assert decision.kind == "request_input"
    assert [item.question for item in decision.response_questions] == [
        "你想查询哪个城市？",
        "哪一天？",
        "什么时间？",
    ]


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
                                            "phase_id": 1,
                                        },
                                        {
                                            "id": 2,
                                            "title": "Summarize plan",
                                            "tool_name": "planning.record",
                                            "tool_input": {"status": "done"},
                                            "depends_on": [1],
                                            "phase_id": 2,
                                        },
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
    assert response.planned_steps[0].phase_id == "1"
    assert response.planned_steps[1].depends_on == ["1"]
    assert response.planned_steps[1].phase_id == "2"

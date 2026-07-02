import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import new_id
from taroai.model_gateway.models import (
    ModelGatewayConfigurationError,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelGatewayResponseError,
    ModelUsage,
    PlannedToolCall,
)


class ModelGateway(BaseModel):
    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        raise NotImplementedError


class OpenAICompatibleModelGateway(ModelGateway):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    default_model: str | None = None
    timeout_seconds: int = 30
    temperature: float | None = None
    max_output_tokens: int | None = None

    model_config = ConfigDict(extra="forbid")

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        model = request.model or self.default_model
        if not self.api_key:
            raise ModelGatewayConfigurationError("model gateway api key is not configured")
        if not model:
            raise ModelGatewayConfigurationError("model gateway model is not configured")

        payload = self._build_chat_payload(request=request, model=model)
        response_body = self._post_chat_completions(payload)
        return self._parse_chat_response(response_body)

    def _build_chat_payload(self, request: ModelGatewayRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "stream": False,
        }
        tools = request.tools
        if tools:
            payload["tools"] = tools
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        temperature = request.temperature if request.temperature is not None else self.temperature
        if temperature is not None:
            payload["temperature"] = temperature
        max_output_tokens = (
            request.max_output_tokens
            if request.max_output_tokens is not None
            else self.max_output_tokens
        )
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        return payload

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            raise ModelGatewayResponseError(f"model gateway returned HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError) as error:
            raise ModelGatewayResponseError(f"model gateway request failed: {error}") from error
        except json.JSONDecodeError as error:
            raise ModelGatewayResponseError("model gateway returned invalid JSON") from error

    def _parse_chat_response(self, body: dict[str, Any]) -> ModelGatewayResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelGatewayResponseError("model gateway response did not include choices")
        message = choices[0].get("message", {})
        output_text = message.get("content") or ""
        return ModelGatewayResponse(
            id=str(body.get("id") or new_id("model_response")),
            model=body.get("model"),
            output_text=output_text,
            planned_steps=self._parse_planned_steps(output_text),
            usage=self._parse_usage(body.get("usage")),
        )

    def _parse_planned_steps(self, output_text: str) -> list[PlannedToolCall]:
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise ModelGatewayResponseError("model gateway plan output must be strict JSON") from error
        if not isinstance(parsed, dict):
            raise ModelGatewayResponseError("model gateway plan output must be a JSON object")
        steps = parsed.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ModelGatewayResponseError("model gateway plan output must include non-empty steps")
        return [PlannedToolCall.model_validate(step) for step in steps]

    def _parse_usage(self, usage: Any) -> ModelUsage | None:
        if not isinstance(usage, dict):
            return None
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

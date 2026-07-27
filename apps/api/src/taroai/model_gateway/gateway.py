import json
import threading
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from taroai.domain import new_id
from taroai.model_gateway.models import (
    ModelGatewayConfigurationError,
    ModelGatewayError,
    ModelMessage,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelGatewayResponseError,
    ModelSafetyRefusalError,
    ModelProviderAttempt,
    ModelUsage,
    PlannedToolCall,
)
from taroai.model_gateway.providers import (
    ModelProviderConfig,
    ModelProviderRateLimitError,
    ModelProviderRateLimiter,
    ModelProviderRegistry,
    validate_chat_request_options,
)
from taroai.provider_errors import redact_provider_error_detail


if TYPE_CHECKING:
    from taroai.agent.models import AgentDecision, AgentVerificationResult


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client() -> httpx.Client:
    """Process-wide pooled HTTP client for model provider calls.

    httpx.Client is thread-safe and reuses TCP+TLS connections (keep-alive),
    so concurrent LLM calls avoid a fresh handshake per request. Per-call
    timeouts are passed explicitly on each request.
    """
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(30.0),
                    follow_redirects=True,
                )
    return _HTTP_CLIENT


class ModelGateway(BaseModel):
    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        raise NotImplementedError

    def decide_next_action(self, request: ModelGatewayRequest) -> "AgentDecision":
        raise NotImplementedError

    def verify_completion(
        self, request: ModelGatewayRequest
    ) -> "AgentVerificationResult":
        raise NotImplementedError

    def stream_response(self, request: ModelGatewayRequest) -> Iterator[str]:
        raise NotImplementedError

    def stream_next_action(self, request: ModelGatewayRequest) -> Iterator[Any]:
        """Stream a direct answer; providers with native tools may yield one AgentDecision."""

        yield from self.stream_response(request)


class OpenAICompatibleModelGateway(ModelGateway):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = Field(default="", exclude=True, repr=False)
    api_key_secret_ref_id: str | None = Field(default=None, repr=False)
    secret_service: Any | None = Field(default=None, exclude=True, repr=False)
    secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    default_model: str | None = None
    timeout_seconds: int = 30
    temperature: float | None = None
    max_output_tokens: int | None = None
    chat_request_options: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_provider_chat_request_options(self):
        validate_chat_request_options(self.chat_request_options)
        return self

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        model = request.model or self.default_model
        if not model:
            raise ModelGatewayConfigurationError(
                "model gateway model is not configured"
            )

        api_key = self._resolve_api_key(request)
        payload = self._build_chat_payload(request=request, model=model)
        response_body = self._post_chat_completions(payload, api_key)
        try:
            return self._parse_chat_response(response_body)
        except ModelGatewayResponseError:
            raw = str(self._assistant_message(response_body).get("content") or "")
            repair_request = request.model_copy(
                update={
                    "messages": [
                        *request.messages,
                        ModelMessage(role="assistant", content=raw),
                        ModelMessage(
                            role="user",
                            content=(
                                "That response was not a valid workflow plan. Return only "
                                "one corrected root JSON object with a non-empty steps array. "
                                "Every step must include non-empty id, title, and tool_name; "
                                "tool_input and each step must be JSON objects; depends_on must "
                                "be an array of earlier step ids or an empty array. Use only "
                                "read_only, standard, or code for tool_mode and only fast or "
                                "strong for model_hint."
                            ),
                        ),
                    ],
                    "tools": [],
                    "tool_choice": None,
                }
            )
            repaired_payload = self._build_chat_payload(
                request=repair_request,
                model=model,
            )
            return self._parse_chat_response(
                self._post_chat_completions(repaired_payload, api_key)
            )

    def decide_next_action(self, request: ModelGatewayRequest) -> "AgentDecision":
        model, response_body = self._complete_operation(request, "decide")
        del model
        try:
            return self._parse_agent_decision(response_body)
        except ModelGatewayResponseError as error:
            if not any(
                message in str(error)
                for message in (
                    "expected schema",
                    "must include a JSON object",
                    "must include valid JSON",
                )
            ):
                raise
            raw = str(self._assistant_message(response_body).get("content") or "")
            if raw.strip() and "must include a JSON object" in str(error):
                from taroai.agent.models import AgentDecision

                return AgentDecision(
                    kind="respond",
                    response_text=raw.strip(),
                    verification_required=True,
                )
            repair_request = request.model_copy(
                update={
                    "messages": [
                        *request.messages,
                        ModelMessage(role="assistant", content=raw),
                        ModelMessage(
                            role="user",
                            content=(
                                "That response was not valid AgentDecision JSON. "
                                "Return only one corrected root JSON object with kind equal to "
                                "action, respond, or request_input. Use arrays, not null, "
                                "for response_options, response_questions, and response_suggestions; use an object for "
                                "tool_input; include tool_name or skill_id when kind is action; and include "
                                "non-empty response_text when kind is respond; include "
                                "a prompt or choices when kind is request_input."
                            ),
                        ),
                    ],
                    "tools": [],
                    "tool_choice": None,
                }
            )
            _, repaired_body = self._complete_operation(repair_request, "decide")
            return self._parse_agent_decision(repaired_body)

    def verify_completion(
        self, request: ModelGatewayRequest
    ) -> "AgentVerificationResult":
        model, response_body = self._complete_operation(request, "verify")
        del model
        try:
            return self._parse_agent_verification(response_body)
        except ModelGatewayResponseError as error:
            if not any(
                message in str(error)
                for message in (
                    "expected schema",
                    "must include a JSON object",
                    "must include valid JSON",
                )
            ):
                raise
            raw = str(self._assistant_message(response_body).get("content") or "")
            repair_request = request.model_copy(
                update={
                    "messages": [
                        *request.messages,
                        ModelMessage(role="assistant", content=raw),
                        ModelMessage(
                            role="user",
                            content=(
                                "That response was not valid AgentVerificationResult JSON. "
                                "Return only one corrected root JSON object with outcome equal "
                                "to complete, repair, replan, wait_user, or fail; feedback as a "
                                "string; evidence as an array of strings; and optional confidence "
                                "from 0 to 1."
                            ),
                        ),
                    ],
                    "tools": [],
                    "tool_choice": None,
                }
            )
            _, repaired_body = self._complete_operation(repair_request, "verify")
            return self._parse_agent_verification(repaired_body)

    def stream_response(self, request: ModelGatewayRequest) -> Iterator[str]:
        model = request.model or self.default_model
        if not model:
            raise ModelGatewayConfigurationError(
                "model gateway model is not configured"
            )
        api_key = self._resolve_api_key(
            request.model_copy(
                update={"metadata": {**request.metadata, "operation": "respond"}}
            )
        )
        payload = self._build_chat_payload(request=request, model=model)
        # 最终回复是自然语言；结构化 JSON 只用于 plan/decide/verify。
        payload.pop("response_format", None)
        payload["stream"] = True
        yield from self._post_chat_completions_stream(payload, api_key)

    def stream_next_action(self, request: ModelGatewayRequest) -> Iterator[Any]:
        operation_request = request.model_copy(
            update={"metadata": {**request.metadata, "operation": "decide"}}
        )
        model = operation_request.model or self.default_model
        if not model:
            raise ModelGatewayConfigurationError(
                "model gateway model is not configured"
            )
        api_key = self._resolve_api_key(operation_request)
        payload = self._build_chat_payload(request=operation_request, model=model)
        payload.pop("response_format", None)
        payload["stream"] = True
        tool_calls: dict[int, dict[str, Any]] = {}
        for delta in self._iter_chat_completion_deltas(payload, api_key):
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield content
            for part in delta.get("tool_calls") or []:
                if not isinstance(part, dict):
                    continue
                index = int(part.get("index") or 0)
                call = tool_calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if part.get("id"):
                    call["id"] = str(part["id"])
                function = part.get("function")
                if not isinstance(function, dict):
                    continue
                for key in ("name", "arguments"):
                    if function.get(key):
                        call["function"][key] += str(function[key])
        if tool_calls:
            yield self._parse_agent_decision(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    tool_calls[index] for index in sorted(tool_calls)
                                ]
                            }
                        }
                    ]
                }
            )
            return

    def _complete_operation(
        self,
        request: ModelGatewayRequest,
        operation: Literal["decide", "verify"],
    ) -> tuple[str, dict[str, Any]]:
        model = request.model or self.default_model
        if not model:
            raise ModelGatewayConfigurationError(
                "model gateway model is not configured"
            )
        operation_request = request.model_copy(
            update={"metadata": {**request.metadata, "operation": operation}}
        )
        api_key = self._resolve_api_key(operation_request)
        payload = self._build_chat_payload(request=operation_request, model=model)
        return model, self._post_chat_completions(payload, api_key)

    def _build_chat_payload(
        self, request: ModelGatewayRequest, model: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                message.model_dump(mode="json") for message in request.messages
            ],
            "stream": False,
        }
        tools = request.tools
        if tools:
            payload["tools"] = tools
        if tools and request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        temperature = (
            request.temperature if request.temperature is not None else self.temperature
        )
        if temperature is not None:
            payload["temperature"] = temperature
        max_output_tokens = (
            request.max_output_tokens
            if request.max_output_tokens is not None
            else self.max_output_tokens
        )
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        payload.update(self.chat_request_options)
        return payload

    def _resolve_api_key(self, request: ModelGatewayRequest) -> str:
        if self.api_key_secret_ref_id is None:
            if not self.api_key:
                raise ModelGatewayConfigurationError(
                    "model gateway api key is not configured"
                )
            return self.api_key
        if self.secret_service is None:
            raise ModelGatewayConfigurationError(
                "model gateway secret service is required for api key secret ref"
            )
        operation = str(request.metadata.get("operation") or "plan")
        step_id = f"model_gateway:{operation}"
        try:
            lease = self.secret_service.create_lease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                secret_id=self.api_key_secret_ref_id,
                tool_name="model_gateway",
                actions=["invoke"],
                ttl_seconds=self.secret_lease_ttl_seconds,
                run_id=request.run_id,
                step_id=step_id,
            )
            return self.secret_service.resolve_lease_value(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                run_id=request.run_id,
                step_id=step_id,
                lease_token=lease.lease_token,
                tool_name="model_gateway",
                action="invoke",
                require_bound_context=True,
            )
        except Exception as error:
            raise ModelGatewayConfigurationError(
                "model gateway api key secret could not be resolved"
            ) from error

    def _post_chat_completions(
        self, payload: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Taroai/1.0",
        }
        try:
            response = _shared_http_client().post(
                endpoint,
                content=body,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                self._raise_for_http_status(response, payload, api_key)
            return json.loads(response.content.decode("utf-8"))
        except httpx.HTTPError as error:
            raise ModelGatewayResponseError(
                f"model gateway request failed: {error}", retryable=True
            ) from error
        except json.JSONDecodeError as error:
            raise ModelGatewayResponseError(
                "model gateway returned invalid JSON"
            ) from error

    def _raise_for_http_status(
        self,
        response: "httpx.Response",
        payload: dict[str, Any],
        api_key: str,
    ) -> None:
        """Map a non-2xx provider response to the internal error contract.

        Mirrors the urllib HTTPError handling: safety refusals surface as
        ModelSafetyRefusalError; everything else becomes a
        ModelGatewayResponseError whose retryable flag follows the status code.
        """
        detail = response.text
        try:
            self._raise_for_safety_refusal(
                json.loads(detail),
                model_id=str(payload.get("model") or "") or None,
            )
        except json.JSONDecodeError:
            pass
        safe_detail = redact_provider_error_detail(detail, api_key=api_key)
        status_code = response.status_code
        raise ModelGatewayResponseError(
            f"model gateway returned HTTP {status_code}: {safe_detail}",
            retryable=status_code in {408, 425, 429} or status_code >= 500,
        )

    def _post_chat_completions_stream(
        self,
        payload: dict[str, Any],
        api_key: str,
    ) -> Iterator[str]:
        for delta in self._iter_chat_completion_deltas(payload, api_key):
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield content

    def _iter_chat_completion_deltas(
        self,
        payload: dict[str, Any],
        api_key: str,
    ) -> Iterator[dict[str, Any]]:
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Taroai/1.0",
        }
        started_at = time.monotonic()
        try:
            with _shared_http_client().stream(
                "POST",
                endpoint,
                content=body,
                headers=headers,
                timeout=self.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for_http_status(response, payload, api_key)
                for raw_line in response.iter_lines():
                    if time.monotonic() - started_at >= self.timeout_seconds:
                        raise ModelGatewayResponseError(
                            "model gateway stream timed out", retryable=True
                        )
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise ModelGatewayResponseError(
                            "model gateway stream returned invalid JSON"
                        ) from error
                    self._raise_for_safety_refusal(
                        event,
                        model_id=str(payload.get("model") or "") or None,
                    )
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue
                    yield delta
        except httpx.HTTPError as error:
            raise ModelGatewayResponseError(
                f"model gateway request failed: {error}", retryable=True
            ) from error

    def _assistant_message(self, body: dict[str, Any]) -> dict[str, Any]:
        self._raise_for_safety_refusal(body)
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelGatewayResponseError(
                "model gateway response did not include choices"
            )
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelGatewayResponseError(
                "model gateway response did not include a message"
            )
        return message

    @staticmethod
    def _raise_for_safety_refusal(
        body: dict[str, Any],
        *,
        model_id: str | None = None,
    ) -> None:
        error = body.get("error")
        error_code = str(error.get("code") or "") if isinstance(error, dict) else ""
        error_text = str(error.get("message") or "") if isinstance(error, dict) else ""
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        if not isinstance(choice, dict):
            choice = {}
        message = choice.get("message")
        delta = choice.get("delta")
        refusal = next(
            (
                str(item.get("refusal") or "")
                for item in (message, delta)
                if isinstance(item, dict) and item.get("refusal")
            ),
            "",
        )
        finish_reason = str(choice.get("finish_reason") or "").split(":", 1)[0]
        if (
            not refusal
            and finish_reason not in {"content_filter", "sensitive"}
            and error_code != "1301"
        ):
            return
        raise ModelSafetyRefusalError(
            provider=str(body.get("provider") or "") or None,
            model_id=str(body.get("model") or model_id or "") or None,
            original_text=refusal or error_text,
        )

    def _parse_agent_decision(self, body: dict[str, Any]) -> "AgentDecision":
        from taroai.agent.models import AgentDecision

        message = self._assistant_message(body)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            tool_call = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
            function = tool_call.get("function")
            if not isinstance(function, dict):
                raise ModelGatewayResponseError(
                    "model tool call did not include a function"
                )
            arguments = function.get("arguments") or "{}"
            try:
                tool_input = (
                    json.loads(arguments) if isinstance(arguments, str) else arguments
                )
            except json.JSONDecodeError as error:
                raise ModelGatewayResponseError(
                    "model tool call arguments were not valid JSON"
                ) from error
            try:
                return AgentDecision(
                    kind="action",
                    action_key=str(tool_call.get("id") or new_id("action_key")),
                    tool_name=str(function.get("name") or ""),
                    tool_input=tool_input if isinstance(tool_input, dict) else {},
                )
            except ValidationError as error:
                raise ModelGatewayResponseError(
                    "model decision did not match the expected schema"
                ) from error
        content = message.get("content") or ""
        parsed = self._parse_plan_json(str(content))
        if not isinstance(parsed, dict):
            raise ModelGatewayResponseError(
                "model decision did not match the expected schema"
            )
        if isinstance(parsed.get("decision"), dict):
            parsed = parsed["decision"]
        elif isinstance(parsed.get("result"), dict):
            parsed = parsed["result"]
        tool_input = parsed.get("tool_input") or {}
        if isinstance(tool_input, str):
            input_key = {
                "web.search": "query",
                "web.fetch": "url",
                "sandbox.command": "command",
            }.get(str(parsed.get("tool_name") or "").replace("__", "."))
            if input_key:
                tool_input = {input_key: tool_input}
        parsed["tool_input"] = tool_input
        options = parsed.get("response_options") or []
        parsed["response_options"] = (
            [str(item) for item in options[:6]] if isinstance(options, list) else []
        )
        suggestions = parsed.get("response_suggestions") or []
        parsed["response_suggestions"] = (
            [str(item)[:200] for item in suggestions[:3]]
            if isinstance(suggestions, list)
            else []
        )
        questions = parsed.get("response_questions") or []
        if isinstance(questions, dict):
            questions = [questions]
        if isinstance(questions, list):
            questions = [
                {"question": item} if isinstance(item, str) else item
                for item in questions
            ]
        response_text = str(parsed.get("response_text") or "")
        if parsed.get("kind") == "respond" and not response_text.strip():
            raise ModelGatewayResponseError(
                "model decision did not match the expected schema "
                "(respond requires non-empty response_text)"
            )
        if parsed.get("kind") == "respond" and (
            questions or parsed["response_options"]
        ):
            parsed["kind"] = "request_input"
        parsed["response_questions"] = (
            [
                {
                    "question": str(item.get("question") or "").strip(),
                    "options": [
                        str(option) for option in (item.get("options") or [])[:6]
                    ],
                    "required": item.get("required", True),
                }
                for item in questions[:3]
                if isinstance(item, dict) and str(item.get("question") or "").strip()
            ]
            if isinstance(questions, list)
            else []
        )
        try:
            return AgentDecision.model_validate(parsed)
        except ValidationError as error:
            violations = ", ".join(
                f"{'.'.join(map(str, item['loc']))}:{item['type']}"
                for item in error.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )
            )
            raise ModelGatewayResponseError(
                f"model decision did not match the expected schema ({violations})"
            ) from error

    def _parse_agent_verification(
        self, body: dict[str, Any]
    ) -> "AgentVerificationResult":
        from taroai.agent.models import AgentVerificationResult

        content = self._assistant_message(body).get("content") or ""
        parsed = self._parse_plan_json(str(content))
        try:
            return AgentVerificationResult.model_validate(parsed)
        except ValidationError as error:
            raise ModelGatewayResponseError(
                "model verification did not match the expected schema"
            ) from error

    def _parse_chat_response(self, body: dict[str, Any]) -> ModelGatewayResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelGatewayResponseError(
                "model gateway response did not include choices"
            )
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
        parsed = self._parse_plan_json(output_text)
        if not isinstance(parsed, dict):
            raise ModelGatewayResponseError(
                "model gateway plan output must be a JSON object"
            )
        steps = parsed.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ModelGatewayResponseError(
                "model gateway plan output must include non-empty steps"
            )
        try:
            return [
                PlannedToolCall.model_validate(self._normalize_planned_step(step))
                for step in steps
            ]
        except ValidationError as error:
            raise ModelGatewayResponseError(
                "model gateway plan step did not match the expected schema"
            ) from error

    def _normalize_planned_step(self, step: Any) -> dict[str, Any]:
        if not isinstance(step, dict):
            raise ModelGatewayResponseError("model gateway plan steps must be objects")
        normalized = dict(step)
        for key in (
            "id",
            "title",
            "tool_name",
            "skill_id",
            "phase_id",
            "phase_title",
        ):
            value = normalized.get(key)
            if value is not None and not isinstance(value, str):
                normalized[key] = str(value)
        depends_on = normalized.get("depends_on")
        if isinstance(depends_on, list):
            normalized["depends_on"] = [str(item) for item in depends_on]
        tool_input = normalized.get("tool_input")
        if (
            isinstance(tool_input, str)
            and normalized.get("tool_name") == "sandbox.command"
        ):
            normalized["tool_input"] = {"command": tool_input}
        return normalized

    def _parse_plan_json(self, output_text: str) -> Any:
        try:
            return json.loads(output_text)
        except json.JSONDecodeError as error:
            extracted = self._extract_first_json_object(output_text)
            if extracted is None:
                raise ModelGatewayResponseError(
                    "model gateway plan output must include a JSON object"
                ) from error
            try:
                return json.loads(extracted)
            except json.JSONDecodeError as extracted_error:
                raise ModelGatewayResponseError(
                    "model gateway plan output must include valid JSON"
                ) from extracted_error

    def _extract_first_json_object(self, text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            character = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
                continue
            if character == "{":
                depth += 1
                continue
            if character == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    def _parse_usage(self, usage: Any) -> ModelUsage | None:
        if not isinstance(usage, dict):
            return None
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        prompt_tokens_details = usage.get("prompt_tokens_details")
        if not isinstance(prompt_tokens_details, dict):
            prompt_tokens_details = usage.get("input_tokens_details")
        cached_input_tokens = (
            int(prompt_tokens_details.get("cached_tokens") or 0)
            if isinstance(prompt_tokens_details, dict)
            else 0
        )
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
        )


class ModelGatewayRouter(ModelGateway):
    provider_registry: ModelProviderRegistry = Field(
        default_factory=ModelProviderRegistry
    )
    secret_service: Any | None = Field(default=None, exclude=True, repr=False)
    rate_limiter: ModelProviderRateLimiter = Field(
        default_factory=ModelProviderRateLimiter
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        candidates = self.provider_registry.candidates(request)
        if not candidates:
            raise ModelGatewayConfigurationError("no model provider matches request")
        last_error: ModelGatewayError | None = None
        provider_attempts: list[ModelProviderAttempt] = []
        for provider in candidates:
            routed_request = self._request_for_provider(provider, request)
            try:
                reservation = self.rate_limiter.reserve(provider, routed_request)
                response = self._call_provider(provider, routed_request)
                provider_attempts.append(
                    ModelProviderAttempt(
                        provider_id=provider.id,
                        model=routed_request.model,
                        status="succeeded",
                        invoked=True,
                    )
                )
                response = response.model_copy(
                    update={
                        "provider": response.provider or provider.id,
                        "model": response.model or routed_request.model,
                        "provider_attempts": provider_attempts,
                    }
                )
                self.rate_limiter.record_success(
                    provider_id=provider.id,
                    usage=response.usage,
                    request=routed_request,
                    reservation=reservation,
                )
                return response
            except ModelProviderRateLimitError as error:
                fallback_allowed = provider.allows_fallback("rate_limit")
                provider_attempts.append(
                    ModelProviderAttempt(
                        provider_id=provider.id,
                        model=routed_request.model,
                        status="rate_limited",
                        invoked=False,
                        fallback_allowed=fallback_allowed,
                        error_type=error.__class__.__name__,
                    )
                )
                last_error = error
                if not fallback_allowed:
                    raise
            except ModelGatewayResponseError as error:
                fallback_allowed = provider.allows_fallback("response_error")
                provider_attempts.append(
                    ModelProviderAttempt(
                        provider_id=provider.id,
                        model=routed_request.model,
                        status="response_error",
                        invoked=True,
                        fallback_allowed=fallback_allowed,
                        error_type=error.__class__.__name__,
                    )
                )
                last_error = error
                if not fallback_allowed:
                    raise
        if last_error is not None:
            raise last_error
        raise ModelGatewayConfigurationError("no model provider could create a plan")

    def decide_next_action(self, request: ModelGatewayRequest) -> "AgentDecision":
        return self._route_structured_operation(request, "decide_next_action")

    def verify_completion(
        self, request: ModelGatewayRequest
    ) -> "AgentVerificationResult":
        return self._route_structured_operation(request, "verify_completion")

    def stream_response(self, request: ModelGatewayRequest) -> Iterator[str]:
        yield from self._stream_operation(request, "stream_response")

    def stream_next_action(self, request: ModelGatewayRequest) -> Iterator[Any]:
        yield from self._stream_operation(request, "stream_next_action")

    def _stream_operation(
        self,
        request: ModelGatewayRequest,
        operation: Literal["stream_response", "stream_next_action"],
    ) -> Iterator[Any]:
        candidates = self.provider_registry.candidates(request)
        if not candidates:
            raise ModelGatewayConfigurationError("no model provider matches request")
        last_error: ModelGatewayError | None = None
        for provider in candidates:
            routed_request = self._request_for_provider(provider, request)
            try:
                reservation = self.rate_limiter.reserve(provider, routed_request)
                gateway = self._provider_gateway(provider)
                yielded = False
                for delta in getattr(gateway, operation)(routed_request):
                    yielded = True
                    yield delta
                self.rate_limiter.record_success(
                    provider_id=provider.id,
                    usage=None,
                    request=routed_request,
                    reservation=reservation,
                )
                return
            except ModelProviderRateLimitError as error:
                last_error = error
                if not provider.allows_fallback("rate_limit"):
                    raise
            except ModelGatewayResponseError as error:
                last_error = error
                if yielded or not provider.allows_fallback("response_error"):
                    raise
        if last_error is not None:
            raise last_error
        raise ModelGatewayConfigurationError(
            f"no model provider could execute {operation}"
        )

    def _route_structured_operation(
        self,
        request: ModelGatewayRequest,
        operation: Literal["decide_next_action", "verify_completion"],
    ) -> "AgentDecision | AgentVerificationResult":
        candidates = self.provider_registry.candidates(request)
        if not candidates:
            raise ModelGatewayConfigurationError("no model provider matches request")
        last_error: ModelGatewayError | None = None
        for provider in candidates:
            routed_request = self._request_for_provider(provider, request)
            try:
                reservation = self.rate_limiter.reserve(provider, routed_request)
                result = getattr(self._provider_gateway(provider), operation)(
                    routed_request
                )
                self.rate_limiter.record_success(
                    provider_id=provider.id,
                    usage=None,
                    request=routed_request,
                    reservation=reservation,
                )
                return result
            except ModelProviderRateLimitError as error:
                last_error = error
                if not provider.allows_fallback("rate_limit"):
                    raise
            except ModelGatewayResponseError as error:
                last_error = error
                if not provider.allows_fallback("response_error"):
                    raise
        if last_error is not None:
            raise last_error
        raise ModelGatewayConfigurationError(
            f"no model provider could execute {operation}"
        )

    def _request_for_provider(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
    ) -> ModelGatewayRequest:
        model = provider.model_for_request(request)
        reasoning_effort = request.reasoning_effort
        if reasoning_effort == "none" and not provider.reasoning_efforts:
            reasoning_effort = None
        if request.model == model and request.reasoning_effort == reasoning_effort:
            return request
        return request.model_copy(
            update={"model": model, "reasoning_effort": reasoning_effort}
        )

    def _call_provider(
        self,
        provider: ModelProviderConfig,
        request: ModelGatewayRequest,
    ) -> ModelGatewayResponse:
        if provider.provider_type != "openai_compatible":
            raise ModelGatewayConfigurationError(
                f"unsupported model provider type: {provider.provider_type}"
            )
        return self._provider_gateway(provider).create_plan(request)

    def _provider_gateway(
        self,
        provider: ModelProviderConfig,
    ) -> OpenAICompatibleModelGateway:
        if provider.provider_type != "openai_compatible":
            raise ModelGatewayConfigurationError(
                f"unsupported model provider type: {provider.provider_type}"
            )
        return OpenAICompatibleModelGateway(
            base_url=provider.base_url,
            api_key=provider.api_key,
            api_key_secret_ref_id=provider.api_key_secret_ref_id,
            secret_service=self.secret_service,
            secret_lease_ttl_seconds=provider.secret_lease_ttl_seconds,
            default_model=provider.default_model,
            timeout_seconds=provider.timeout_seconds,
            chat_request_options=provider.chat_request_options,
        )

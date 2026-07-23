from typing import Any, Literal

from pydantic import BaseModel, Field


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high"]


class ModelGatewayError(RuntimeError):
    pass


class ModelGatewayConfigurationError(ModelGatewayError):
    pass


class ModelGatewayResponseError(ModelGatewayError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ModelSafetyRefusalError(ModelGatewayError):
    def __init__(
        self,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        original_text: str = "",
    ):
        super().__init__("model safety filter declined the request")
        self.provider = provider
        self.model_id = model_id
        self.original_text = original_text


class ModelPolicyDeniedError(ModelGatewayError):
    def __init__(self, message: str, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class ModelMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0


class ModelProviderAttempt(BaseModel):
    provider_id: str = Field(min_length=1)
    model: str | None = None
    status: Literal["succeeded", "response_error", "rate_limited"]
    invoked: bool
    fallback_allowed: bool = False
    error_type: str | None = None


class PlannedToolCall(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    skill_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
    depends_on: list[str] | None = None
    phase_id: str | None = None
    phase_title: str | None = None
    tool_mode: Literal["read_only", "standard", "code"] = "standard"
    model_hint: Literal["fast", "strong"] = "strong"


class ModelGatewayRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    provider_id: str | None = None
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    sensitivity_level: int = Field(default=0, ge=0)
    messages: list[ModelMessage] = Field(min_length=1)
    input: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelCatalogEntry(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    reasoning_efforts: list[ReasoningEffort] = Field(default_factory=list)
    default_reasoning_effort: ReasoningEffort | None = None
    configured: bool = True


class ModelGatewayResponse(BaseModel):
    id: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    output_text: str = ""
    planned_steps: list[PlannedToolCall] = Field(default_factory=list)
    usage: ModelUsage | None = None
    provider_attempts: list[ModelProviderAttempt] = Field(default_factory=list)

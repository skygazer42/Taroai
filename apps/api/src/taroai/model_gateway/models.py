from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelGatewayError(RuntimeError):
    pass


class ModelGatewayConfigurationError(ModelGatewayError):
    pass


class ModelGatewayResponseError(ModelGatewayError):
    pass


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


class PlannedToolCall(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False


class ModelGatewayRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    model: str | None = None
    messages: list[ModelMessage] = Field(min_length=1)
    input: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelGatewayResponse(BaseModel):
    id: str = Field(min_length=1)
    model: str | None = None
    output_text: str = ""
    planned_steps: list[PlannedToolCall] = Field(default_factory=list)
    usage: ModelUsage | None = None

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EmbeddingGatewayError(RuntimeError):
    pass


class EmbeddingGatewayConfigurationError(EmbeddingGatewayError):
    pass


class EmbeddingGatewayResponseError(EmbeddingGatewayError):
    pass


class EmbeddingUsage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingVector(BaseModel):
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class EmbeddingGatewayRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    user_id: str = Field(min_length=1)
    input: list[str] = Field(min_length=1)
    purpose: Literal["knowledge_index", "knowledge_query"]
    run_id: str | None = None
    model: str | None = None
    dimensions: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("embedding input items must not be empty")
        return value


class EmbeddingGatewayResponse(BaseModel):
    model: str | None = None
    embeddings: list[EmbeddingVector] = Field(default_factory=list)
    usage: EmbeddingUsage | None = None

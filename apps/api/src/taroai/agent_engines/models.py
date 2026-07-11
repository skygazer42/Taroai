from datetime import datetime
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.domain import new_id, utc_now


class AgentEngineType(str, Enum):
    NATIVE = "native"
    OPENCODE = "opencode"
    CODEX = "codex"
    CLAUDE = "claude"


class AgentEngineConnectionCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    engine_type: AgentEngineType
    endpoint_url: str | None = Field(default=None, max_length=2000)
    secret_ref_id: str | None = Field(default=None, min_length=1)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Engine endpoint must be an HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            raise ValueError("Non-local Engine endpoints must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Engine endpoint cannot contain credentials, query, or fragment")
        return value.rstrip("/")


class AgentEngineConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint_url: str | None = Field(default=None, max_length=2000)
    secret_ref_id: str | None = None
    status: Literal["active", "disabled"] | None = None
    capabilities: list[str] | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        return AgentEngineConnectionCreate.validate_endpoint(value)


class AgentEngineConnection(BaseModel):
    id: str = Field(default_factory=lambda: new_id("engine_connection"))
    tenant_id: str
    workspace_id: str
    name: str
    engine_type: AgentEngineType
    endpoint_url: str | None = None
    secret_ref_id: str | None = None
    status: Literal["active", "disabled"] = "active"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentEngineSessionCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    connection_id: str = Field(min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    task: str = Field(min_length=1, max_length=100_000)
    cwd: str = Field(default="/workspace", min_length=1, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class AgentEngineSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("engine_session"))
    tenant_id: str
    workspace_id: str
    connection_id: str
    engine_type: AgentEngineType
    run_id: str | None = None
    external_session_id: str | None = None
    status: Literal[
        "starting", "running", "waiting_approval", "completed", "failed", "cancelled", "closed"
    ] = "starting"
    cwd: str = "/workspace"
    created_by_user_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None


class AgentEngineTurn(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)

    model_config = ConfigDict(extra="forbid")


class AgentEngineApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class AgentEngineEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("engine_event"))
    tenant_id: str
    workspace_id: str
    session_id: str
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

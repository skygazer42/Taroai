from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import utc_now


class AgentVersionSpec(BaseModel):
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    output_contract: dict[str, Any] = Field(default_factory=dict)
    instructions: str = Field(min_length=1)
    skill_bindings: list[dict[str, Any]] = Field(default_factory=list)
    connector_bindings: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_bindings: list[dict[str, Any]] = Field(default_factory=list)
    reference_files: list[dict[str, Any]] = Field(default_factory=list)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_snapshot: dict[str, Any] = Field(default_factory=dict)
    source_thread_id: str | None = None
    source_run_id: str | None = None
    change_note: str = ""

    model_config = ConfigDict(extra="forbid")


class AgentDefinitionCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    version: AgentVersionSpec

    model_config = ConfigDict(extra="forbid")


class AgentDefinitionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class AgentDefinition(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    description: str = ""
    status: Literal["draft", "published", "archived"] = "draft"
    latest_version: int = 0
    published_version: int | None = None
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentVersion(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    agent_id: str
    version: int = Field(ge=1)
    status: Literal["draft", "published", "superseded"] = "draft"
    spec: AgentVersionSpec
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None


class AgentDraft(BaseModel):
    workspace_id: str
    name: str
    description: str
    version: AgentVersionSpec
    source_thread_id: str
    source_run_id: str
    review_required: bool = True


class AgentExtractRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)


class AgentVersionCreate(BaseModel):
    version: AgentVersionSpec


class AgentRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class AgentInvocation(BaseModel):
    agent_id: str
    agent_version: int
    thread_id: str
    message_id: str
    run_id: str
    events_url: str


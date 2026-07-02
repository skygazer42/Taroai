from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SkillType(str, Enum):
    API = "api_skill"
    BROWSER = "browser_skill"
    DOCUMENT = "document_skill"
    DATA = "data_skill"
    COMMUNICATION = "communication_skill"
    WORKFLOW = "workflow_skill"
    AGENT_TEMPLATE = "agent_template"


class SkillVisibility(str, Enum):
    TENANT = "tenant"
    DEPARTMENT = "department"
    WORKSPACE = "workspace"
    PRIVATE = "private"


class SkillRuntime(BaseModel):
    sandbox: str
    timeout_seconds: int = Field(default=1800, ge=1)


class SkillManifest(BaseModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    type: SkillType
    owner: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_scopes: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    approval_required: list[str] = Field(default_factory=list)
    visibility: SkillVisibility = SkillVisibility.TENANT
    visible_to_department_ids: list[str] = Field(default_factory=list)
    visible_to_workspace_ids: list[str] = Field(default_factory=list)
    visible_to_user_ids: list[str] = Field(default_factory=list)
    runtime: SkillRuntime
    billing_meters: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    evals: list[str] = Field(default_factory=list)

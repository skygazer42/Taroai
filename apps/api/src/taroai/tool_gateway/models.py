from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from taroai.guardrails.models import GuardrailAction
from taroai.secrets import SecretLease


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolGatewayRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str | None = None
    step_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    skill_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    granted_scopes: list[str] = Field(default_factory=list)
    secret_leases: list[SecretLease] = Field(default_factory=list)
    approved: bool = False


class ToolSecretRequirement(BaseModel):
    secret_id: str = Field(min_length=1)
    actions: list[str] = Field(default_factory=list)
    ttl_seconds: int = Field(default=300, gt=0)


class ToolPolicy(BaseModel):
    tool_name: str = Field(min_length=1)
    description: str = ""
    required_scopes: list[str] = Field(default_factory=list)
    secret_requirements: list[ToolSecretRequirement] = Field(default_factory=list)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    approval_required: bool = False
    enabled: bool = True
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class ToolPolicyDecision(BaseModel):
    allowed: bool
    approval_required: bool = False
    reason: str | None = None
    missing_scopes: list[str] = Field(default_factory=list)


class ToolAuditRecord(BaseModel):
    event_type: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    skill_id: str | None = None
    reason: str | None = None
    missing_scopes: list[str] = Field(default_factory=list)
    risk_level: ToolRiskLevel | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    approved: bool = False
    tool_input: dict[str, Any] = Field(default_factory=dict)
    guardrail_action: GuardrailAction | None = None
    guardrail_rule_ids: list[str] = Field(default_factory=list)
    guardrail_detector_finding_ids: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    tool_name: str
    output: dict[str, Any] = Field(default_factory=dict)


ToolAuditRecorder = Callable[[ToolAuditRecord], None]
ToolHandler = Callable[[ToolGatewayRequest], ToolResult | dict[str, Any]]

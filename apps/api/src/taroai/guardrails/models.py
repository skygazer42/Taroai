import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import new_id


class GuardrailStage(str, Enum):
    INPUT = "input"
    RETRIEVAL = "retrieval"
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TOOL_REQUEST = "tool_request"
    TOOL_RESPONSE = "tool_response"
    ARTIFACT = "artifact"
    MEMORY_WRITE = "memory_write"


class GuardrailAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REDACT = "redact"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"
    QUARANTINE_RUN = "quarantine_run"


class GuardrailSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardrailCondition(BaseModel):
    text_contains: list[str] = Field(default_factory=list)
    attribute_equals: dict[str, Any] = Field(default_factory=dict)
    case_sensitive: bool = False

    def matches(self, content: str, attributes: dict[str, Any]) -> bool:
        if self.text_contains and not self._contains_any_text(content):
            return False
        for key, expected in self.attribute_equals.items():
            if attributes.get(key) != expected:
                return False
        return True

    def redact(self, content: str, replacement: str) -> str:
        redacted = content
        flags = 0 if self.case_sensitive else re.IGNORECASE
        for term in self.text_contains:
            redacted = re.sub(re.escape(term), replacement, redacted, flags=flags)
        return redacted

    def _contains_any_text(self, content: str) -> bool:
        if self.case_sensitive:
            return any(term in content for term in self.text_contains)
        normalized_content = content.lower()
        return any(term.lower() in normalized_content for term in self.text_contains)


class GuardrailRule(BaseModel):
    id: str = Field(default_factory=lambda: new_id("guardrail_rule"))
    tenant_id: str | None = Field(default=None, min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    stage: GuardrailStage
    condition: GuardrailCondition
    action: GuardrailAction
    severity: GuardrailSeverity = GuardrailSeverity.MEDIUM
    message: str = Field(min_length=1)
    audit_required: bool = True
    redaction_replacement: str = "[REDACTED]"


class GuardrailEvaluationRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    stage: GuardrailStage
    content: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class GuardrailDecision(BaseModel):
    action: GuardrailAction
    allowed: bool
    approval_required: bool = False
    blocked: bool = False
    redacted_content: str | None = None
    redactions: list["GuardrailRedaction"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    matched_rule_ids: list[str] = Field(default_factory=list)
    detector_finding_ids: list[str] = Field(default_factory=list)
    detector_findings: list["GuardrailDetectorFinding"] = Field(default_factory=list)
    severity: GuardrailSeverity | None = None
    message: str | None = None
    audit_required: bool = False

    @classmethod
    def allow(cls) -> "GuardrailDecision":
        return cls(
            action=GuardrailAction.ALLOW,
            allowed=True,
            audit_required=False,
        )


class GuardrailRedaction(BaseModel):
    text: str = Field(min_length=1)
    replacement: str = "[REDACTED]"
    case_sensitive: bool = False


class GuardrailDetectorFinding(BaseModel):
    id: str = Field(min_length=1)
    detector_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    action: GuardrailAction
    severity: GuardrailSeverity = GuardrailSeverity.HIGH
    message: str = Field(min_length=1)
    audit_required: bool = True
    redactions: list[GuardrailRedaction] = Field(default_factory=list, exclude=True)

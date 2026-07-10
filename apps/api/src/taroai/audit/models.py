from typing import Any

from pydantic import BaseModel, Field

from taroai.domain import AuditEvent


class AuditActor(BaseModel):
    tenant_id: str
    user_id: str | None = None
    actor_type: str = Field(default="user", min_length=1)
    ip_address: str | None = None
    user_agent: str | None = None


class AuditResource(BaseModel):
    resource_type: str = Field(min_length=1)
    resource_id: str | None = None
    workspace_id: str | None = None
    run_id: str | None = None


class AuditAction(BaseModel):
    event_type: str = Field(min_length=1)


class AuditEventCreate(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    user_id: str | None = None
    run_id: str | None = None
    event_type: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: AuditActor | None = None


class AuditCoverageRequirement(BaseModel):
    area: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    required_metadata_keys: set[str] = Field(default_factory=set)
    description: str = ""


class AuditCoverageFinding(BaseModel):
    area: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    missing_metadata_keys: list[str] = Field(default_factory=list)


class AuditCoverageReport(BaseModel):
    tenant_id: str
    total_requirements: int
    covered_event_types: list[str] = Field(default_factory=list)
    missing_requirements: list[AuditCoverageFinding] = Field(default_factory=list)
    is_complete: bool = False


DEFAULT_AUDIT_COVERAGE_REQUIREMENTS = [
    AuditCoverageRequirement(
        area="identity",
        event_type="identity.user.created",
        required_metadata_keys={"user_id", "status"},
        description="User creation must be traceable without password material.",
    ),
    AuditCoverageRequirement(
        area="identity",
        event_type="identity.user.disabled",
        required_metadata_keys={"user_id", "status"},
        description="User disablement must be traceable for access reviews.",
    ),
    AuditCoverageRequirement(
        area="run",
        event_type="run.cancelled",
        required_metadata_keys={"cancelled_by_user_id", "reason_code", "status"},
        description="Run cancellation must identify actor and structured reason.",
    ),
    AuditCoverageRequirement(
        area="run",
        event_type="run.retry_requested",
        required_metadata_keys={
            "requested_by_user_id",
            "reason_code",
            "previous_status",
            "status",
        },
        description="Run retry must identify actor, structured reason, and prior state.",
    ),
    AuditCoverageRequirement(
        area="rbac",
        event_type="identity.role.created",
        required_metadata_keys={"role_id", "permissions_count"},
        description="Role creation must expose permission blast radius.",
    ),
    AuditCoverageRequirement(
        area="rbac",
        event_type="identity.role.assigned",
        required_metadata_keys={"assigned_user_id", "role_id"},
        description="Role assignment must identify the affected user and role.",
    ),
    AuditCoverageRequirement(
        area="knowledge",
        event_type="knowledge.query.executed",
        required_metadata_keys={"query_length", "result_count", "document_ids"},
        description="Knowledge reads must record query shape and result IDs without raw excerpts.",
    ),
    AuditCoverageRequirement(
        area="embedding",
        event_type="embedding.gateway.called",
        required_metadata_keys={"purpose", "input_count", "embedding_count", "model"},
        description="Embedding gateway calls must record provider usage without raw text or vectors.",
    ),
    AuditCoverageRequirement(
        area="memory",
        event_type="memory.candidate_created",
        required_metadata_keys={"memory_id", "scope_type", "scope_id"},
        description="Memory writes must record scope and candidate ID.",
    ),
    AuditCoverageRequirement(
        area="tool",
        event_type="tool.executed",
        required_metadata_keys={"tool_name"},
        description="Tool execution must identify the invoked tool.",
    ),
    AuditCoverageRequirement(
        area="tool",
        event_type="tool.approval_required",
        required_metadata_keys={"tool_name", "reason"},
        description="Approval-gated tools must record why approval was required.",
    ),
    AuditCoverageRequirement(
        area="approval",
        event_type="approval.resolved",
        required_metadata_keys={"approval_id", "resolved_by_user_id", "status"},
        description="Approval resolution must identify decision and approver.",
    ),
    AuditCoverageRequirement(
        area="approval",
        event_type="approval.rejected",
        required_metadata_keys={"approval_id", "resolved_by_user_id", "status"},
        description="Approval rejection must identify decision and reviewer.",
    ),
    AuditCoverageRequirement(
        area="storage",
        event_type="storage.signed_url.created",
        required_metadata_keys={"storage_object_id", "operation", "expires_at"},
        description="Signed URL creation must record target object and operation without the URL.",
    ),
    AuditCoverageRequirement(
        area="storage",
        event_type="storage.uploaded",
        required_metadata_keys={"storage_object_id", "size_bytes"},
        description="Object upload must record object ID and declared byte size.",
    ),
    AuditCoverageRequirement(
        area="sandbox",
        event_type="sandbox.command.executed",
        required_metadata_keys={"session_id", "command", "exit_code"},
        description="Sandbox command execution must record command shape and result code.",
    ),
    AuditCoverageRequirement(
        area="browser",
        event_type="browser.action.performed",
        required_metadata_keys={"session_id", "action_type"},
        description="Browser automation must record action type without raw typed text.",
    ),
    AuditCoverageRequirement(
        area="billing",
        event_type="billing.metered",
        required_metadata_keys={"meter_id", "meter_type"},
        description="Billing meter creation must be available for audit review.",
    ),
    AuditCoverageRequirement(
        area="skill",
        event_type="skill.published",
        required_metadata_keys={"skill_id", "version"},
        description="Skill publication must identify package and version.",
    ),
    AuditCoverageRequirement(
        area="license",
        event_type="license.status_changed",
        required_metadata_keys={"license_id", "status", "deployment_mode"},
        description="License status changes must identify license, status, and deployment mode.",
    ),
    AuditCoverageRequirement(
        area="license",
        event_type="license.imported",
        required_metadata_keys={"license_id", "status", "deployment_mode"},
        description="License imports must identify license, status, and deployment mode without signature material.",
    ),
]

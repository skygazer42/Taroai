from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from taroai.domain import utc_now
from taroai.skills import SkillManifest


class SolutionPackStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"


class SolutionPackInstallationStatus(str, Enum):
    INSTALLED = "installed"
    ROLLED_BACK = "rolled_back"


class SolutionPackInstallAction(BaseModel):
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    workspace_id: str | None = None
    risk_level: str | None = None
    requires_approval: bool = False


class SolutionPackInstallIssue(BaseModel):
    kind: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    workspace_id: str | None = None
    message: str = Field(min_length=1)


class SolutionPackInstallPreview(BaseModel):
    tenant_id: str
    pack_id: str
    version: str
    workspace_ids: list[str] = Field(default_factory=list)
    dry_run: bool = True
    can_install: bool
    actions: list[SolutionPackInstallAction] = Field(default_factory=list)
    conflicts: list[SolutionPackInstallIssue] = Field(default_factory=list)
    missing_dependencies: list[SolutionPackInstallIssue] = Field(default_factory=list)
    required_approvals: list[SolutionPackInstallIssue] = Field(default_factory=list)
    skipped_resources: list[SolutionPackInstallAction] = Field(default_factory=list)


class SolutionPackManifest(BaseModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    industry: str = Field(default="general", min_length=1)
    use_cases: list[str] = Field(default_factory=list)
    skills: list[SkillManifest] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    rollout_checklist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_skills(self) -> "SolutionPackManifest":
        skill_ids = [skill.id for skill in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("solution pack skill ids must be unique")
        return self


class SolutionPackEntry(BaseModel):
    tenant_id: str
    manifest: SolutionPackManifest
    status: SolutionPackStatus = SolutionPackStatus.DRAFT
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SolutionPackInstallation(BaseModel):
    tenant_id: str
    pack_id: str
    version: str
    workspace_ids: list[str] = Field(min_length=1)
    installed_skill_ids: list[str] = Field(default_factory=list)
    status: SolutionPackInstallationStatus = SolutionPackInstallationStatus.INSTALLED
    installed_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_unique_workspace_ids(self) -> "SolutionPackInstallation":
        if len(self.workspace_ids) != len(set(self.workspace_ids)):
            raise ValueError("solution pack workspace ids must be unique")
        return self


class SolutionPackInstallRequest(BaseModel):
    workspace_ids: list[str] = Field(min_length=1)
    selected_resource_ids: list[str] = Field(default_factory=list)
    owner_user_id: str | None = None
    approval_mode: str = "manual"
    dry_run: bool = False

    @model_validator(mode="after")
    def validate_unique_workspace_ids(self) -> "SolutionPackInstallRequest":
        if len(self.workspace_ids) != len(set(self.workspace_ids)):
            raise ValueError("solution pack workspace ids must be unique")
        return self


class SolutionPackRollbackRecord(BaseModel):
    tenant_id: str
    pack_id: str
    version: str
    workspace_ids: list[str] = Field(default_factory=list)
    disabled_skill_ids: list[str] = Field(default_factory=list)
    status: SolutionPackInstallationStatus = SolutionPackInstallationStatus.ROLLED_BACK
    rolled_back_by_user_id: str
    reason_code: str
    created_at: datetime = Field(default_factory=utc_now)

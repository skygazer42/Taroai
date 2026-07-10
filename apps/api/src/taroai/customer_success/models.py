from enum import Enum

from pydantic import BaseModel, Field


class SuccessHealthBand(str, Enum):
    HEALTHY = "healthy"
    WATCH = "watch"
    AT_RISK = "at_risk"


class AdoptionMetrics(BaseModel):
    active_users: int = Field(ge=0)
    active_workspaces: int = Field(ge=0)
    runs_created: int = Field(ge=0)
    runs_completed: int = Field(ge=0)
    artifact_downloads: int = Field(ge=0)
    skills_used: int = Field(ge=0)
    approvals_resolved: int = Field(ge=0)
    feedback_submitted: int = Field(ge=0)
    repeated_workflows: int = Field(ge=0)


class SolutionPackOutcomeMetrics(BaseModel):
    pack_id: str
    version: str
    workspace_count: int = Field(ge=0)
    installed_skill_count: int = Field(ge=0)
    metric_values: dict[str, int] = Field(default_factory=dict)


class TenantSuccessHealth(BaseModel):
    tenant_id: str
    onboarding_score: int = Field(ge=0, le=100)
    adoption_score: int = Field(ge=0, le=100)
    reliability_score: int = Field(ge=0, le=100)
    value_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=100)
    band: SuccessHealthBand


class TenantSuccessSummary(BaseModel):
    tenant_id: str
    adoption: AdoptionMetrics
    solution_pack_outcomes: list[SolutionPackOutcomeMetrics] = Field(default_factory=list)
    health: TenantSuccessHealth

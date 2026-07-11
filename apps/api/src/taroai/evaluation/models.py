from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class EvaluationTargetKind(str, Enum):
    AGENT = "agent"
    SKILL = "skill"


class ScorerKind(str, Enum):
    EXACT = "exact"
    JSON_SCHEMA = "json_schema"
    CONTAINS = "contains"
    WEIGHTED_RUBRIC = "weighted_rubric"


class EvaluationCaseStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    BUDGET_EXCEEDED = "budget_exceeded"


class EvaluationRunStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class NumericTolerance(BaseModel):
    absolute: float = Field(default=0.0, ge=0)
    relative: float = Field(default=0.0, ge=0)
    minimum_match_ratio: float = Field(default=1.0, ge=0, le=1)
    case_sensitive: bool = True

    model_config = ConfigDict(extra="forbid", frozen=True)


class RubricCriterion(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    weight: float = Field(default=1.0, gt=0)
    minimum_score: float = Field(default=0.0, ge=0, le=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedOutputContract(BaseModel):
    scorer: ScorerKind
    exact_value: Any | None = None
    json_schema: dict[str, Any] | None = None
    contains: tuple[str, ...] = ()
    rubric: tuple[RubricCriterion, ...] = ()
    tolerance: NumericTolerance = Field(default_factory=NumericTolerance)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_scorer_payload(self) -> "ExpectedOutputContract":
        if self.scorer == ScorerKind.JSON_SCHEMA and self.json_schema is None:
            raise ValueError("json_schema scorer requires a schema")
        if self.scorer == ScorerKind.CONTAINS and not self.contains:
            raise ValueError("contains scorer requires at least one expected fragment")
        if self.scorer == ScorerKind.WEIGHTED_RUBRIC and not self.rubric:
            raise ValueError("weighted_rubric scorer requires criteria")
        rubric_ids = [criterion.id.casefold() for criterion in self.rubric]
        if len(rubric_ids) != len(set(rubric_ids)):
            raise ValueError("rubric criterion IDs must be unique")
        return self


class EvaluationBudget(BaseModel):
    max_tokens: int = Field(default=100_000, ge=0)
    max_cost: Decimal = Field(default=Decimal("100"), ge=0)
    max_duration_seconds: float = Field(default=1800, gt=0)
    max_sandbox_cost: Decimal = Field(default=Decimal("100"), ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class SideEffectPolicy(BaseModel):
    allowed: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    allow_external_writes: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_policy(self) -> "SideEffectPolicy":
        overlap = set(self.allowed) & set(self.forbidden)
        if overlap:
            raise ValueError(f"side effects cannot be both allowed and forbidden: {sorted(overlap)}")
        return self


class GoldenCase(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    expected: ExpectedOutputContract
    budget: EvaluationBudget = Field(default_factory=EvaluationBudget)
    side_effect_policy: SideEffectPolicy = Field(default_factory=SideEffectPolicy)
    weight: float = Field(default=1.0, gt=0)
    critical: bool = False
    tags: tuple[str, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True)


class RegressionPolicy(BaseModel):
    maximum_score_regression: float = Field(default=0.02, ge=0)
    maximum_success_rate_regression: float = Field(default=0.02, ge=0)
    maximum_tool_error_rate_increase: float = Field(default=0.01, ge=0)
    maximum_human_intervention_rate_increase: float = Field(default=0.05, ge=0)
    maximum_latency_regression: float = Field(default=0.10, ge=0)
    maximum_cost_regression: float = Field(default=0.10, ge=0)
    maximum_sandbox_cost_regression: float = Field(default=0.10, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationGatePolicy(BaseModel):
    minimum_score: float = Field(default=0.85, ge=0, le=1)
    minimum_success_rate: float = Field(default=0.90, ge=0, le=1)
    maximum_tool_error_rate: float = Field(default=0.01, ge=0, le=1)
    maximum_human_intervention_rate: float = Field(default=0.05, ge=0, le=1)
    maximum_p95_latency_seconds: float | None = Field(default=None, gt=0)
    regression: RegressionPolicy = Field(default_factory=RegressionPolicy)

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationSuite(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    target_kind: EvaluationTargetKind
    cases: tuple[GoldenCase, ...] = Field(min_length=1)
    gate: EvaluationGatePolicy = Field(default_factory=EvaluationGatePolicy)
    description: str = Field(default="", max_length=4000)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_cases(self) -> "EvaluationSuite":
        identities = [(case.id.casefold(), case.version) for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("golden case id/version pairs must be unique")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class EvaluationObservation(BaseModel):
    output: Any = None
    rubric_scores: dict[str, float] = Field(default_factory=dict)
    tokens: int = Field(default=0, ge=0)
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    sandbox_cost: Decimal = Field(default=Decimal("0"), ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    tool_errors: int = Field(default=0, ge=0)
    human_interventions: int = Field(default=0, ge=0)
    side_effects: tuple[str, ...] = ()
    external_writes: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid", frozen=True)


class CriterionScore(BaseModel):
    id: str
    score: float = Field(ge=0, le=1)
    passed: bool
    reason: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationCaseResult(BaseModel):
    case_id: str
    case_version: str
    status: EvaluationCaseStatus
    passed: bool
    score: float = Field(ge=0, le=1)
    scorer: ScorerKind
    criterion_scores: tuple[CriterionScore, ...] = ()
    reasons: tuple[str, ...] = ()
    budget_violations: tuple[str, ...] = ()
    side_effect_violations: tuple[str, ...] = ()
    tokens: int = Field(ge=0)
    cost: Decimal = Field(ge=0)
    sandbox_cost: Decimal = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tool_errors: int = Field(ge=0)
    human_interventions: int = Field(ge=0)
    output_summary: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationMetrics(BaseModel):
    weighted_score: float = Field(ge=0, le=1)
    success_rate: float = Field(ge=0, le=1)
    tool_error_rate: float = Field(ge=0, le=1)
    human_intervention_rate: float = Field(ge=0, le=1)
    p50_latency_seconds: float = Field(ge=0)
    p95_latency_seconds: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cost: Decimal = Field(ge=0)
    total_sandbox_cost: Decimal = Field(ge=0)
    total_duration_seconds: float = Field(ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricRegression(BaseModel):
    metric: str
    baseline: float
    current: float
    delta: float
    allowed_delta: float
    regressed: bool

    model_config = ConfigDict(extra="forbid", frozen=True)


class BaselineComparison(BaseModel):
    baseline_run_id: str
    regressions: tuple[MetricRegression, ...]
    passed: bool

    model_config = ConfigDict(extra="forbid", frozen=True)


class PromotionGateVerdict(BaseModel):
    allowed: bool
    reasons: tuple[str, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationRun(BaseModel):
    id: str = Field(default_factory=lambda: f"evaluation_run_{uuid4().hex}")
    tenant_id: str
    target_kind: EvaluationTargetKind
    target_id: str
    target_version: str
    target_digest: str
    suite_id: str
    suite_version: str
    suite_digest: str
    status: EvaluationRunStatus
    case_results: tuple[EvaluationCaseResult, ...]
    metrics: EvaluationMetrics
    baseline_comparison: BaselineComparison | None = None
    promotion_gate: PromotionGateVerdict
    executor_version: str
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)
    evidence_digest: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationBaseline(BaseModel):
    tenant_id: str
    target_kind: EvaluationTargetKind
    target_id: str
    target_version: str
    suite_id: str
    suite_version: str
    run_id: str
    metrics: EvaluationMetrics
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True)


class RedactionSafeEvidence(BaseModel):
    schema_version: str = "taroai.evaluation-evidence.v1"
    run_id: str
    tenant_id: str
    target_kind: EvaluationTargetKind
    target_id: str
    target_version: str
    target_digest: str
    suite_id: str
    suite_version: str
    suite_digest: str
    status: EvaluationRunStatus
    metrics: EvaluationMetrics
    promotion_gate: PromotionGateVerdict
    cases: tuple[dict[str, Any], ...]
    evidence_digest: str

    model_config = ConfigDict(extra="forbid", frozen=True)


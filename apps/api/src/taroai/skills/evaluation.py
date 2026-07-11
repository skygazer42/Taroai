from __future__ import annotations

import json
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.domain import new_id, utc_now
from taroai.skills.package import (
    SkillPackage,
    SkillPackageError,
    canonical_json_bytes,
    sha256_hex,
)


class SkillEvaluationStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class SkillEvaluationCase(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    expected_artifacts: list[str] = Field(default_factory=list)
    allowed_side_effects: list[str] = Field(default_factory=list)
    forbidden_side_effects: list[str] = Field(default_factory=list)
    max_cost: Decimal = Field(default=Decimal("0"), ge=0)
    max_duration_seconds: float = Field(default=300.0, gt=0)
    weight: float = Field(default=1.0, gt=0)
    critical: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillEvaluationSuite(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    minimum_score: float = Field(default=0.85, ge=0, le=1)
    cases: tuple[SkillEvaluationCase, ...] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "SkillEvaluationSuite":
        case_ids = [case.id.casefold() for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("skill evaluation case IDs must be unique")
        return self

    @property
    def suite_digest(self) -> str:
        return sha256_hex(canonical_json_bytes(self.model_dump(mode="json")))


class SkillEvaluationObservation(BaseModel):
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    error_type: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillEvaluationCaseResult(BaseModel):
    case_id: str
    passed: bool
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    side_effect_violations: list[str] = Field(default_factory=list)
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    output_summary: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillEvaluationRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("skill_eval"))
    tenant_id: str
    workspace_id: str | None = None
    skill_id: str
    version: str
    package_digest: str
    suite_digest: str
    evaluator_version: str
    status: SkillEvaluationStatus
    minimum_score: float = Field(ge=0, le=1)
    score: float | None = Field(default=None, ge=0, le=1)
    passed: bool | None = None
    side_effect_violations: list[str] = Field(default_factory=list)
    total_cost: Decimal = Field(default=Decimal("0"), ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    case_results: tuple[SkillEvaluationCaseResult, ...] = ()
    created_by_user_id: str
    created_at: Any = Field(default_factory=utc_now)
    completed_at: Any | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillEvaluationExecutor(Protocol):
    def execute(
        self,
        package: SkillPackage,
        case: SkillEvaluationCase,
    ) -> SkillEvaluationObservation: ...


class SkillEvaluationRunner:
    """Runs typed cases through an injected executor; it never executes package code itself."""

    def __init__(
        self,
        executor: SkillEvaluationExecutor,
        *,
        evaluator_version: str = "skill-evaluator.v1",
    ):
        self.executor = executor
        self.evaluator_version = evaluator_version

    def run(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None,
        created_by_user_id: str,
        package: SkillPackage,
        suite: SkillEvaluationSuite,
    ) -> SkillEvaluationRun:
        started_at = utc_now()
        results: list[SkillEvaluationCaseResult] = []
        for case in suite.cases:
            try:
                observation = self.executor.execute(package, case)
            except Exception as error:
                observation = SkillEvaluationObservation(
                    error_type=error.__class__.__name__,
                )
            results.append(score_evaluation_case(case, observation))
        total_weight = sum(case.weight for case in suite.cases)
        weighted_score = sum(
            case.weight * result.score
            for case, result in zip(suite.cases, results, strict=True)
        ) / total_weight
        violations = sorted(
            {
                violation
                for result in results
                for violation in result.side_effect_violations
            }
        )
        critical_failed = any(
            case.critical and not result.passed
            for case, result in zip(suite.cases, results, strict=True)
        )
        passed = (
            weighted_score >= suite.minimum_score
            and not critical_failed
            and not violations
        )
        completed_at = utc_now()
        return SkillEvaluationRun(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=package.skill_id,
            version=package.version,
            package_digest=package.package_digest,
            suite_digest=suite.suite_digest,
            evaluator_version=self.evaluator_version,
            status=(
                SkillEvaluationStatus.PASSED
                if passed
                else SkillEvaluationStatus.FAILED
            ),
            minimum_score=suite.minimum_score,
            score=weighted_score,
            passed=passed,
            side_effect_violations=violations,
            total_cost=sum((result.cost for result in results), Decimal("0")),
            duration_seconds=sum(result.duration_seconds for result in results),
            case_results=tuple(results),
            created_by_user_id=created_by_user_id,
            created_at=started_at,
            completed_at=completed_at,
        )


class SkillEvaluationGate:
    def assert_publishable(
        self,
        package: SkillPackage,
        evaluation_run: SkillEvaluationRun,
    ) -> None:
        if evaluation_run.skill_id != package.skill_id:
            raise ValueError("evaluation run skill does not match package")
        if evaluation_run.version != package.version:
            raise ValueError("evaluation run version does not match package")
        if evaluation_run.package_digest != package.package_digest:
            raise ValueError("evaluation run package digest does not match package")
        if evaluation_run.status != SkillEvaluationStatus.PASSED:
            raise ValueError("skill evaluation did not pass")
        if evaluation_run.passed is not True:
            raise ValueError("skill evaluation gate is not satisfied")
        if evaluation_run.score is None or evaluation_run.score < evaluation_run.minimum_score:
            raise ValueError("skill evaluation score is below the minimum")
        if evaluation_run.side_effect_violations:
            raise ValueError("skill evaluation contains side-effect violations")


def score_evaluation_case(
    case: SkillEvaluationCase,
    observation: SkillEvaluationObservation,
) -> SkillEvaluationCaseResult:
    checks: list[tuple[bool, str]] = []
    if observation.error_type is not None:
        checks.append((False, f"executor error: {observation.error_type}"))
    checks.append(
        (
            _contains_expected(observation.output, case.expected_output),
            "output does not contain the expected structure",
        )
    )
    missing_artifacts = sorted(set(case.expected_artifacts) - set(observation.artifacts))
    checks.append((not missing_artifacts, f"missing artifacts: {missing_artifacts}"))
    observed_side_effects = set(observation.side_effects)
    allowed_side_effects = set(case.allowed_side_effects)
    forbidden_side_effects = set(case.forbidden_side_effects)
    side_effect_violations = sorted(
        (observed_side_effects - allowed_side_effects)
        | (observed_side_effects & forbidden_side_effects)
    )
    checks.append(
        (
            not side_effect_violations,
            f"side-effect violations: {side_effect_violations}",
        )
    )
    checks.append(
        (
            observation.cost <= case.max_cost,
            f"cost {observation.cost} exceeds {case.max_cost}",
        )
    )
    checks.append(
        (
            observation.duration_seconds <= case.max_duration_seconds,
            (
                f"duration {observation.duration_seconds} exceeds "
                f"{case.max_duration_seconds}"
            ),
        )
    )
    score = sum(1 for passed, _reason in checks if passed) / len(checks)
    reasons = [reason for passed, reason in checks if not passed]
    return SkillEvaluationCaseResult(
        case_id=case.id,
        passed=not reasons,
        score=score,
        reasons=reasons,
        side_effect_violations=side_effect_violations,
        cost=observation.cost,
        duration_seconds=observation.duration_seconds,
        output_summary=_safe_output_summary(observation.output),
    )


def load_evaluation_suite(package: SkillPackage) -> SkillEvaluationSuite:
    config = package.taroai_config.get("spec", {})
    if not isinstance(config, dict):
        config = {}
    evaluation_config = config.get("evaluation", {})
    if not isinstance(evaluation_config, dict):
        evaluation_config = {}
    minimum_score = float(evaluation_config.get("minimumScore", 0.85))
    suite_path = str(evaluation_config.get("suite", "evals/cases.json"))
    try:
        suite_file = package.get_file(suite_path)
    except KeyError:
        try:
            suite_file = package.get_file("evals/cases.jsonl")
        except KeyError as error:
            raise SkillPackageError("skill package has no executable evaluation suite") from error
    try:
        text = suite_file.content.decode("utf-8-sig")
        if suite_file.path.endswith(".jsonl"):
            raw_cases = [json.loads(line) for line in text.splitlines() if line.strip()]
            raw_suite = {"version": package.version, "cases": raw_cases}
        else:
            raw_suite = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillPackageError("skill evaluation suite must be UTF-8 JSON or JSONL") from error
    if isinstance(raw_suite, list):
        raw_suite = {"version": package.version, "cases": raw_suite}
    if not isinstance(raw_suite, dict):
        raise SkillPackageError("skill evaluation suite must be a JSON object")
    raw_suite.setdefault("version", package.version)
    raw_suite.setdefault("minimum_score", minimum_score)
    return SkillEvaluationSuite.model_validate(raw_suite)


def _contains_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_expected(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_contains_expected(candidate, item) for candidate in actual)
            for item in expected
        )
    return actual == expected


def _safe_output_summary(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(output),
        "value_types": {
            key: type(value).__name__
            for key, value in sorted(output.items())
        },
    }


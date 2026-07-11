from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from taroai.evaluation.models import (
    BaselineComparison,
    EvaluationBaseline,
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationMetrics,
    EvaluationObservation,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    EvaluationTargetKind,
    GoldenCase,
    MetricRegression,
    PromotionGateVerdict,
    RedactionSafeEvidence,
    RegressionPolicy,
    canonical_digest,
    utc_now,
)
from taroai.evaluation.repository import EvaluationRepository
from taroai.evaluation.scorers import score_output, validate_json_schema
from taroai.evaluation.suite import EvaluationSuiteRecord, validate_suite


class EvaluationExecutionRequest(BaseModel):
    tenant_id: str
    target_kind: EvaluationTargetKind
    target_id: str
    target_version: str
    target_digest: str
    case: GoldenCase

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationExecutor(Protocol):
    @property
    def version(self) -> str: ...

    def execute(self, request: EvaluationExecutionRequest) -> EvaluationObservation: ...


class EvaluationService:
    """Unified Agent/Skill evaluator with an injected real execution boundary."""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        executor: EvaluationExecutor,
    ):
        self.repository = repository
        self.executor = executor

    def register_suite(
        self,
        *,
        tenant_id: str,
        suite: EvaluationSuite,
        created_by_user_id: str,
    ) -> EvaluationSuiteRecord:
        validate_suite(suite)
        return self.repository.save_suite(
            EvaluationSuiteRecord(
                tenant_id=tenant_id,
                suite=suite,
                suite_digest=suite.digest,
                created_by_user_id=created_by_user_id,
            )
        )

    def run_registered_suite(
        self,
        *,
        tenant_id: str,
        target_kind: EvaluationTargetKind,
        target_id: str,
        target_version: str,
        target_digest: str,
        suite_id: str,
        suite_version: str,
        created_by_user_id: str,
        compare_to_latest_baseline: bool = True,
    ) -> EvaluationRun:
        suite = self.repository.get_suite(
            tenant_id,
            suite_id,
            suite_version,
        ).suite
        return self.run(
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            target_version=target_version,
            target_digest=target_digest,
            suite=suite,
            created_by_user_id=created_by_user_id,
            compare_to_latest_baseline=compare_to_latest_baseline,
        )

    def run(
        self,
        *,
        tenant_id: str,
        target_kind: EvaluationTargetKind,
        target_id: str,
        target_version: str,
        target_digest: str,
        suite: EvaluationSuite,
        created_by_user_id: str,
        compare_to_latest_baseline: bool = True,
    ) -> EvaluationRun:
        validate_suite(suite)
        if suite.target_kind != target_kind:
            raise ValueError("evaluation suite target kind does not match execution target")
        if not target_digest:
            raise ValueError("evaluation target digest is required")
        started_at = utc_now()
        case_results = tuple(
            self._execute_case(
                EvaluationExecutionRequest(
                    tenant_id=tenant_id,
                    target_kind=target_kind,
                    target_id=target_id,
                    target_version=target_version,
                    target_digest=target_digest,
                    case=case,
                )
            )
            for case in suite.cases
        )
        metrics = aggregate_metrics(suite, case_results)
        baseline = None
        comparison = None
        if compare_to_latest_baseline:
            baseline = self.repository.latest_baseline(
                tenant_id,
                target_kind.value,
                target_id,
                target_version,
                suite.id,
                suite.version,
            )
            if baseline is not None:
                comparison = compare_metrics(
                    baseline,
                    metrics,
                    suite.gate.regression,
                )
        verdict = evaluate_promotion_gate(
            suite,
            case_results,
            metrics,
            comparison,
        )
        completed_at = utc_now()
        run_id = f"evaluation_run_{uuid4().hex}"
        status = (
            EvaluationRunStatus.PASSED
            if verdict.allowed
            else (
                EvaluationRunStatus.ERROR
                if all(result.status == EvaluationCaseStatus.ERROR for result in case_results)
                else EvaluationRunStatus.FAILED
            )
        )
        evidence_payload = _evidence_payload(
            run_id=run_id,
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            target_version=target_version,
            target_digest=target_digest,
            suite=suite,
            status=status,
            metrics=metrics,
            verdict=verdict,
            case_results=case_results,
        )
        run = EvaluationRun(
            id=run_id,
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            target_version=target_version,
            target_digest=target_digest,
            suite_id=suite.id,
            suite_version=suite.version,
            suite_digest=suite.digest,
            status=status,
            case_results=case_results,
            metrics=metrics,
            baseline_comparison=comparison,
            promotion_gate=verdict,
            executor_version=self.executor.version,
            created_by_user_id=created_by_user_id,
            created_at=started_at,
            completed_at=completed_at,
            evidence_digest=canonical_digest(evidence_payload),
        )
        return self.repository.save_run(run)

    def promote_to_baseline(
        self,
        *,
        tenant_id: str,
        run_id: str,
        created_by_user_id: str,
    ) -> EvaluationBaseline:
        run = self.repository.get_run(tenant_id, run_id)
        self.assert_publishable(run)
        baseline = EvaluationBaseline(
            tenant_id=run.tenant_id,
            target_kind=run.target_kind,
            target_id=run.target_id,
            target_version=run.target_version,
            suite_id=run.suite_id,
            suite_version=run.suite_version,
            run_id=run.id,
            metrics=run.metrics,
            created_by_user_id=created_by_user_id,
        )
        return self.repository.save_baseline(baseline)

    def assert_publishable(self, run: EvaluationRun) -> None:
        if run.status != EvaluationRunStatus.PASSED:
            raise ValueError("evaluation run did not pass")
        if not run.promotion_gate.allowed:
            raise ValueError(
                f"evaluation promotion gate blocked publication: {run.promotion_gate.reasons}"
            )
        if run.baseline_comparison is not None and not run.baseline_comparison.passed:
            raise ValueError("evaluation run contains a baseline regression")

    def evidence(self, tenant_id: str, run_id: str) -> RedactionSafeEvidence:
        run = self.repository.get_run(tenant_id, run_id)
        cases = tuple(
            {
                "case_id": result.case_id,
                "case_version": result.case_version,
                "status": result.status.value,
                "passed": result.passed,
                "score": result.score,
                "scorer": result.scorer.value,
                "reasons": list(result.reasons),
                "budget_violations": list(result.budget_violations),
                "side_effect_violations": list(result.side_effect_violations),
                "tokens": result.tokens,
                "cost": str(result.cost),
                "sandbox_cost": str(result.sandbox_cost),
                "duration_seconds": result.duration_seconds,
                "tool_calls": result.tool_calls,
                "tool_errors": result.tool_errors,
                "human_interventions": result.human_interventions,
                "output_summary": result.output_summary,
            }
            for result in run.case_results
        )
        return RedactionSafeEvidence(
            run_id=run.id,
            tenant_id=run.tenant_id,
            target_kind=run.target_kind,
            target_id=run.target_id,
            target_version=run.target_version,
            target_digest=run.target_digest,
            suite_id=run.suite_id,
            suite_version=run.suite_version,
            suite_digest=run.suite_digest,
            status=run.status,
            metrics=run.metrics,
            promotion_gate=run.promotion_gate,
            cases=cases,
            evidence_digest=run.evidence_digest,
        )

    def _execute_case(
        self,
        request: EvaluationExecutionRequest,
    ) -> EvaluationCaseResult:
        case = request.case
        input_violations = validate_json_schema(case.input, case.input_schema)
        if input_violations:
            return _error_case_result(case, tuple(input_violations), "input_contract")
        try:
            observation = self.executor.execute(request)
        except Exception as error:
            observation = EvaluationObservation(error_type=error.__class__.__name__)
        output_score = score_output(
            case.expected,
            observation.output,
            rubric_scores=observation.rubric_scores,
        )
        budget_violations = _budget_violations(case, observation)
        side_effect_violations = _side_effect_violations(case, observation)
        reasons = list(output_score.reasons)
        reasons.extend(budget_violations)
        reasons.extend(side_effect_violations)
        if observation.error_type is not None:
            reasons.append(f"executor error: {observation.error_type}")
        passed = (
            output_score.passed
            and not budget_violations
            and not side_effect_violations
            and observation.error_type is None
        )
        status = (
            EvaluationCaseStatus.PASSED
            if passed
            else (
                EvaluationCaseStatus.ERROR
                if observation.error_type is not None
                else (
                    EvaluationCaseStatus.BUDGET_EXCEEDED
                    if budget_violations
                    else EvaluationCaseStatus.FAILED
                )
            )
        )
        return EvaluationCaseResult(
            case_id=case.id,
            case_version=case.version,
            status=status,
            passed=passed,
            score=output_score.score,
            scorer=case.expected.scorer,
            criterion_scores=output_score.criterion_scores,
            reasons=tuple(reasons),
            budget_violations=tuple(budget_violations),
            side_effect_violations=tuple(side_effect_violations),
            tokens=observation.tokens,
            cost=observation.cost,
            sandbox_cost=observation.sandbox_cost,
            duration_seconds=observation.duration_seconds,
            tool_calls=observation.tool_calls,
            tool_errors=observation.tool_errors,
            human_interventions=observation.human_interventions,
            output_summary=_safe_output_summary(observation.output),
        )


def aggregate_metrics(
    suite: EvaluationSuite,
    case_results: tuple[EvaluationCaseResult, ...],
) -> EvaluationMetrics:
    total_weight = sum(case.weight for case in suite.cases)
    weighted_score = sum(
        case.weight * result.score
        for case, result in zip(suite.cases, case_results, strict=True)
    ) / total_weight
    success_rate = sum(result.passed for result in case_results) / len(case_results)
    total_tool_calls = sum(result.tool_calls for result in case_results)
    total_tool_errors = sum(result.tool_errors for result in case_results)
    tool_error_rate = (
        total_tool_errors / total_tool_calls if total_tool_calls else 0.0
    )
    human_intervention_rate = (
        sum(result.human_interventions > 0 for result in case_results)
        / len(case_results)
    )
    latencies = sorted(result.duration_seconds for result in case_results)
    return EvaluationMetrics(
        weighted_score=weighted_score,
        success_rate=success_rate,
        tool_error_rate=tool_error_rate,
        human_intervention_rate=human_intervention_rate,
        p50_latency_seconds=_percentile(latencies, 0.50),
        p95_latency_seconds=_percentile(latencies, 0.95),
        total_tokens=sum(result.tokens for result in case_results),
        total_cost=sum((result.cost for result in case_results), Decimal("0")),
        total_sandbox_cost=sum(
            (result.sandbox_cost for result in case_results), Decimal("0")
        ),
        total_duration_seconds=sum(result.duration_seconds for result in case_results),
    )


def compare_metrics(
    baseline: EvaluationBaseline,
    current: EvaluationMetrics,
    policy: RegressionPolicy,
) -> BaselineComparison:
    pairs = (
        ("weighted_score", baseline.metrics.weighted_score, current.weighted_score, policy.maximum_score_regression, True),
        ("success_rate", baseline.metrics.success_rate, current.success_rate, policy.maximum_success_rate_regression, True),
        ("tool_error_rate", baseline.metrics.tool_error_rate, current.tool_error_rate, policy.maximum_tool_error_rate_increase, False),
        ("human_intervention_rate", baseline.metrics.human_intervention_rate, current.human_intervention_rate, policy.maximum_human_intervention_rate_increase, False),
        ("p95_latency_seconds", baseline.metrics.p95_latency_seconds, current.p95_latency_seconds, baseline.metrics.p95_latency_seconds * policy.maximum_latency_regression, False),
        ("total_cost", float(baseline.metrics.total_cost), float(current.total_cost), float(baseline.metrics.total_cost) * policy.maximum_cost_regression, False),
        ("total_sandbox_cost", float(baseline.metrics.total_sandbox_cost), float(current.total_sandbox_cost), float(baseline.metrics.total_sandbox_cost) * policy.maximum_sandbox_cost_regression, False),
    )
    regressions: list[MetricRegression] = []
    for metric, baseline_value, current_value, allowed_delta, higher_is_better in pairs:
        delta = current_value - baseline_value
        regressed = (
            delta < -allowed_delta
            if higher_is_better
            else delta > allowed_delta
        )
        regressions.append(
            MetricRegression(
                metric=metric,
                baseline=baseline_value,
                current=current_value,
                delta=delta,
                allowed_delta=allowed_delta,
                regressed=regressed,
            )
        )
    return BaselineComparison(
        baseline_run_id=baseline.run_id,
        regressions=tuple(regressions),
        passed=not any(item.regressed for item in regressions),
    )


def evaluate_promotion_gate(
    suite: EvaluationSuite,
    case_results: tuple[EvaluationCaseResult, ...],
    metrics: EvaluationMetrics,
    comparison: BaselineComparison | None,
) -> PromotionGateVerdict:
    reasons: list[str] = []
    if metrics.weighted_score < suite.gate.minimum_score:
        reasons.append("weighted score is below the suite minimum")
    if metrics.success_rate < suite.gate.minimum_success_rate:
        reasons.append("success rate is below the suite minimum")
    if metrics.tool_error_rate > suite.gate.maximum_tool_error_rate:
        reasons.append("tool error rate exceeds the suite maximum")
    if metrics.human_intervention_rate > suite.gate.maximum_human_intervention_rate:
        reasons.append("human intervention rate exceeds the suite maximum")
    if (
        suite.gate.maximum_p95_latency_seconds is not None
        and metrics.p95_latency_seconds > suite.gate.maximum_p95_latency_seconds
    ):
        reasons.append("p95 latency exceeds the suite maximum")
    for case, result in zip(suite.cases, case_results, strict=True):
        if case.critical and not result.passed:
            reasons.append(f"critical golden case failed: {case.id}@{case.version}")
        if result.side_effect_violations:
            reasons.append(f"side-effect policy failed: {case.id}@{case.version}")
    if comparison is not None and not comparison.passed:
        reasons.append("baseline regression policy failed")
    return PromotionGateVerdict(allowed=not reasons, reasons=tuple(reasons))


def _budget_violations(
    case: GoldenCase,
    observation: EvaluationObservation,
) -> list[str]:
    violations: list[str] = []
    if observation.tokens > case.budget.max_tokens:
        violations.append("token budget exceeded")
    if observation.cost > case.budget.max_cost:
        violations.append("cost budget exceeded")
    if observation.sandbox_cost > case.budget.max_sandbox_cost:
        violations.append("sandbox cost budget exceeded")
    if observation.duration_seconds > case.budget.max_duration_seconds:
        violations.append("duration budget exceeded")
    return violations


def _side_effect_violations(
    case: GoldenCase,
    observation: EvaluationObservation,
) -> list[str]:
    observed = set(observation.side_effects)
    allowed = set(case.side_effect_policy.allowed)
    forbidden = set(case.side_effect_policy.forbidden)
    violations = sorted((observed - allowed) | (observed & forbidden))
    if observation.external_writes and not case.side_effect_policy.allow_external_writes:
        violations.append("external_write")
    return sorted(set(violations))


def _error_case_result(
    case: GoldenCase,
    reasons: tuple[str, ...],
    error_type: str,
) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        case_id=case.id,
        case_version=case.version,
        status=EvaluationCaseStatus.ERROR,
        passed=False,
        score=0,
        scorer=case.expected.scorer,
        reasons=reasons + (f"error type: {error_type}",),
        tokens=0,
        cost=Decimal("0"),
        sandbox_cost=Decimal("0"),
        duration_seconds=0,
        tool_calls=0,
        tool_errors=0,
        human_interventions=0,
        output_summary={"kind": "none"},
    )


def _safe_output_summary(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return {
            "kind": "object",
            "keys": sorted(str(key) for key in output),
            "value_types": {
                str(key): type(value).__name__
                for key, value in sorted(output.items(), key=lambda item: str(item[0]))
            },
        }
    if isinstance(output, list):
        return {
            "kind": "array",
            "length": len(output),
            "item_types": sorted({type(item).__name__ for item in output}),
        }
    return {"kind": type(output).__name__}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
    return values[index]


def _evidence_payload(
    *,
    run_id: str,
    tenant_id: str,
    target_kind: EvaluationTargetKind,
    target_id: str,
    target_version: str,
    target_digest: str,
    suite: EvaluationSuite,
    status: EvaluationRunStatus,
    metrics: EvaluationMetrics,
    verdict: PromotionGateVerdict,
    case_results: tuple[EvaluationCaseResult, ...],
) -> dict[str, Any]:
    return {
        "schema_version": "taroai.evaluation-evidence.v1",
        "run_id": run_id,
        "tenant_id": tenant_id,
        "target_kind": target_kind.value,
        "target_id": target_id,
        "target_version": target_version,
        "target_digest": target_digest,
        "suite_id": suite.id,
        "suite_version": suite.version,
        "suite_digest": suite.digest,
        "status": status.value,
        "metrics": metrics.model_dump(mode="json"),
        "promotion_gate": verdict.model_dump(mode="json"),
        "cases": [
            result.model_dump(mode="json")
            for result in case_results
        ],
    }

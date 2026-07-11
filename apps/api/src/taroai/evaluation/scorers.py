from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.evaluation.models import (
    CriterionScore,
    ExpectedOutputContract,
    NumericTolerance,
    ScorerKind,
)


class OutputScore(BaseModel):
    score: float = Field(ge=0, le=1)
    passed: bool
    reasons: tuple[str, ...] = ()
    criterion_scores: tuple[CriterionScore, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True)


def score_output(
    contract: ExpectedOutputContract,
    actual: Any,
    *,
    rubric_scores: dict[str, float] | None = None,
) -> OutputScore:
    if contract.scorer == ScorerKind.EXACT:
        passed = values_equal(actual, contract.exact_value, contract.tolerance)
        return OutputScore(
            score=1.0 if passed else 0.0,
            passed=passed,
            reasons=() if passed else ("output does not exactly match expected value",),
        )
    if contract.scorer == ScorerKind.JSON_SCHEMA:
        violations = validate_json_schema(actual, contract.json_schema or {})
        return OutputScore(
            score=1.0 if not violations else 0.0,
            passed=not violations,
            reasons=tuple(violations),
        )
    if contract.scorer == ScorerKind.CONTAINS:
        return score_contains(contract, actual)
    if contract.scorer == ScorerKind.WEIGHTED_RUBRIC:
        return score_weighted_rubric(contract, rubric_scores or {})
    raise ValueError(f"unsupported scorer: {contract.scorer}")


def score_contains(contract: ExpectedOutputContract, actual: Any) -> OutputScore:
    if isinstance(actual, str):
        haystack = actual
    else:
        try:
            haystack = json.dumps(actual, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            haystack = str(actual)
    fragments = list(contract.contains)
    if not contract.tolerance.case_sensitive:
        haystack = haystack.casefold()
        fragments = [fragment.casefold() for fragment in fragments]
    matched = [fragment for fragment in fragments if fragment in haystack]
    score = len(matched) / len(fragments)
    passed = score >= contract.tolerance.minimum_match_ratio
    missing = [fragment for fragment in fragments if fragment not in matched]
    return OutputScore(
        score=score,
        passed=passed,
        reasons=() if passed else (f"missing required fragments: {missing}",),
    )


def score_weighted_rubric(
    contract: ExpectedOutputContract,
    supplied_scores: dict[str, float],
) -> OutputScore:
    criterion_results: list[CriterionScore] = []
    total_weight = sum(criterion.weight for criterion in contract.rubric)
    weighted_score = 0.0
    for criterion in contract.rubric:
        raw_score = float(supplied_scores.get(criterion.id, 0.0))
        score = max(0.0, min(1.0, raw_score))
        passed = score >= criterion.minimum_score
        criterion_results.append(
            CriterionScore(
                id=criterion.id,
                score=score,
                passed=passed,
                reason=(
                    "criterion satisfied"
                    if passed
                    else f"score {score} below minimum {criterion.minimum_score}"
                ),
            )
        )
        weighted_score += score * criterion.weight
    weighted_score /= total_weight
    passed = all(result.passed for result in criterion_results)
    reasons = tuple(
        result.reason for result in criterion_results if not result.passed
    )
    return OutputScore(
        score=weighted_score,
        passed=passed,
        reasons=reasons,
        criterion_scores=tuple(criterion_results),
    )


def values_equal(actual: Any, expected: Any, tolerance: NumericTolerance) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=tolerance.relative,
            abs_tol=tolerance.absolute,
        )
    if isinstance(actual, str) and isinstance(expected, str):
        if tolerance.case_sensitive:
            return actual == expected
        return actual.casefold() == expected.casefold()
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            values_equal(actual[key], expected[key], tolerance) for key in actual
        )
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            values_equal(left, right, tolerance)
            for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
) -> list[str]:
    violations: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        return [f"{path}: expected type {expected_type}"]
    if "const" in schema and value != schema["const"]:
        violations.append(f"{path}: value does not match const")
    if "enum" in schema and value not in schema["enum"]:
        violations.append(f"{path}: value is not in enum")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                violations.append(f"{path}: missing required property {key}")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                violations.extend(
                    validate_json_schema(value[key], child_schema, path=f"{path}.{key}")
                )
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                violations.append(f"{path}: additional properties forbidden: {extras}")
        minimum = schema.get("minProperties")
        maximum = schema.get("maxProperties")
        if minimum is not None and len(value) < minimum:
            violations.append(f"{path}: fewer than {minimum} properties")
        if maximum is not None and len(value) > maximum:
            violations.append(f"{path}: more than {maximum} properties")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            violations.append(f"{path}: fewer than {minimum} items")
        if maximum is not None and len(value) > maximum:
            violations.append(f"{path}: more than {maximum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                violations.extend(
                    validate_json_schema(item, item_schema, path=f"{path}[{index}]")
                )
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            violations.append(f"{path}: shorter than {minimum} characters")
        if maximum is not None and len(value) > maximum:
            violations.append(f"{path}: longer than {maximum} characters")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(pattern, value) is not None
            except re.error:
                violations.append(f"{path}: schema contains an invalid pattern")
            else:
                if not matched:
                    violations.append(f"{path}: string does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            violations.append(f"{path}: value below minimum {minimum}")
        if maximum is not None and value > maximum:
            violations.append(f"{path}: value above maximum {maximum}")
    for keyword, require_match in (("allOf", True), ("anyOf", False), ("oneOf", None)):
        choices = schema.get(keyword)
        if not isinstance(choices, list):
            continue
        matches = sum(
            not validate_json_schema(value, choice, path=path)
            for choice in choices
            if isinstance(choice, dict)
        )
        if require_match is True and matches != len(choices):
            violations.append(f"{path}: allOf constraint failed")
        elif require_match is False and matches == 0:
            violations.append(f"{path}: anyOf constraint failed")
        elif require_match is None and matches != 1:
            violations.append(f"{path}: oneOf constraint failed")
    return violations


def _matches_type(value: Any, expected_type: str | list[str]) -> bool:
    types = [expected_type] if isinstance(expected_type, str) else expected_type
    return any(
        {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(candidate, False)
        for candidate in types
    )


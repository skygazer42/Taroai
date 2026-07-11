from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from taroai.evaluation.models import EvaluationSuite
from taroai.evaluation.scorers import validate_json_schema


class EvaluationSuiteRecord(BaseModel):
    tenant_id: str
    suite: EvaluationSuite
    suite_digest: str
    created_by_user_id: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationSuiteRegistry:
    """Tenant-scoped immutable suite/version registry."""

    def __init__(self):
        self._records: dict[str, EvaluationSuiteRecord] = {}

    def register(
        self,
        *,
        tenant_id: str,
        suite: EvaluationSuite,
        created_by_user_id: str,
    ) -> EvaluationSuiteRecord:
        validate_suite(suite)
        key = self._key(tenant_id, suite.id, suite.version)
        if key in self._records:
            existing = self._records[key]
            if existing.suite_digest != suite.digest:
                raise ValueError("evaluation suite version is immutable")
            raise ValueError(f"evaluation suite already exists: {suite.id}@{suite.version}")
        record = EvaluationSuiteRecord(
            tenant_id=tenant_id,
            suite=suite,
            suite_digest=suite.digest,
            created_by_user_id=created_by_user_id,
        )
        self._records[key] = record
        return record

    def get(self, tenant_id: str, suite_id: str, version: str) -> EvaluationSuiteRecord:
        record = self._records.get(self._key(tenant_id, suite_id, version))
        if record is None:
            raise KeyError(f"evaluation suite not found: {suite_id}@{version}")
        return record

    def list(self, tenant_id: str, suite_id: str | None = None) -> list[EvaluationSuiteRecord]:
        return sorted(
            [
                record
                for record in self._records.values()
                if record.tenant_id == tenant_id
                and (suite_id is None or record.suite.id == suite_id)
            ],
            key=lambda record: (record.suite.id, record.suite.version),
        )

    def _key(self, tenant_id: str, suite_id: str, version: str) -> str:
        return f"{tenant_id}:{suite_id}:{version}"


def load_suite_json(content: bytes | str) -> EvaluationSuite:
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("evaluation suite must be UTF-8 JSON") from error
    else:
        text = content
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as error:
        raise ValueError("evaluation suite is not valid JSON") from error
    suite = EvaluationSuite.model_validate(payload)
    validate_suite(suite)
    return suite


def validate_suite(suite: EvaluationSuite) -> None:
    for case in suite.cases:
        violations = validate_json_schema(case.input, case.input_schema)
        if violations:
            raise ValueError(
                f"golden case {case.id}@{case.version} input violates its schema: {violations}"
            )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


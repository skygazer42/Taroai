import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.evaluation.models import EvaluationBaseline, EvaluationRun
from taroai.evaluation.suite import EvaluationSuiteRecord


EVALUATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_suites (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    suite_id TEXT NOT NULL,
    version TEXT NOT NULL,
    suite_digest TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, suite_id, version),
    UNIQUE (tenant_id, suite_digest)
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_version TEXT NOT NULL,
    target_digest TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    promotion_allowed BOOLEAN NOT NULL,
    evidence_digest TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_target
    ON evaluation_runs (
        tenant_id, target_kind, target_id, target_version, created_at
    );

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_suite
    ON evaluation_runs (tenant_id, suite_id, suite_version, created_at);

CREATE TABLE IF NOT EXISTS evaluation_baselines (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_version TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
    payload JSONB NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, target_kind, target_id, target_version, suite_id, suite_version, run_id)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_baselines_latest
    ON evaluation_baselines (
        tenant_id, target_kind, target_id, target_version,
        suite_id, suite_version, created_at
    );

ALTER TABLE evaluation_suites ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_suites FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evaluation_suites_tenant_isolation ON evaluation_suites;
CREATE POLICY evaluation_suites_tenant_isolation
    ON evaluation_suites
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE evaluation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evaluation_runs_tenant_isolation ON evaluation_runs;
CREATE POLICY evaluation_runs_tenant_isolation
    ON evaluation_runs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE evaluation_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_baselines FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evaluation_baselines_tenant_isolation ON evaluation_baselines;
CREATE POLICY evaluation_baselines_tenant_isolation
    ON evaluation_baselines
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
"""


class EvaluationRepository(Protocol):
    def save_suite(self, record: EvaluationSuiteRecord) -> EvaluationSuiteRecord: ...

    def get_suite(
        self, tenant_id: str, suite_id: str, version: str
    ) -> EvaluationSuiteRecord: ...

    def list_suites(self, tenant_id: str) -> list[EvaluationSuiteRecord]: ...

    def save_run(self, run: EvaluationRun) -> EvaluationRun: ...

    def get_run(self, tenant_id: str, run_id: str) -> EvaluationRun: ...

    def list_runs(
        self,
        tenant_id: str,
        target_id: str,
    ) -> list[EvaluationRun]: ...

    def save_baseline(self, baseline: EvaluationBaseline) -> EvaluationBaseline: ...

    def latest_baseline(
        self,
        tenant_id: str,
        target_kind: str,
        target_id: str,
        target_version: str,
        suite_id: str,
        suite_version: str,
    ) -> EvaluationBaseline | None: ...


class InMemoryEvaluationRepository:
    def __init__(self):
        self.suites: dict[str, EvaluationSuiteRecord] = {}
        self.runs: dict[str, EvaluationRun] = {}
        self.baselines: list[EvaluationBaseline] = []

    def save_suite(self, record: EvaluationSuiteRecord) -> EvaluationSuiteRecord:
        key = self._suite_key(record.tenant_id, record.suite.id, record.suite.version)
        if key in self.suites:
            raise ValueError("evaluation suite version already exists")
        self.suites[key] = record
        return record

    def get_suite(
        self, tenant_id: str, suite_id: str, version: str
    ) -> EvaluationSuiteRecord:
        record = self.suites.get(self._suite_key(tenant_id, suite_id, version))
        if record is None:
            raise KeyError(f"evaluation suite not found: {suite_id}@{version}")
        return record

    def list_suites(self, tenant_id: str) -> list[EvaluationSuiteRecord]:
        return sorted(
            [item for item in self.suites.values() if item.tenant_id == tenant_id],
            key=lambda item: (item.suite.id, item.suite.version),
        )

    def save_run(self, run: EvaluationRun) -> EvaluationRun:
        key = f"{run.tenant_id}:{run.id}"
        if key in self.runs:
            raise ValueError(f"evaluation run already exists: {run.id}")
        self.runs[key] = run
        return run

    def get_run(self, tenant_id: str, run_id: str) -> EvaluationRun:
        run = self.runs.get(f"{tenant_id}:{run_id}")
        if run is None:
            raise KeyError(f"evaluation run not found: {run_id}")
        return run

    def list_runs(self, tenant_id: str, target_id: str) -> list[EvaluationRun]:
        return sorted(
            [
                run
                for run in self.runs.values()
                if run.tenant_id == tenant_id and run.target_id == target_id
            ],
            key=lambda run: run.created_at,
        )

    def save_baseline(self, baseline: EvaluationBaseline) -> EvaluationBaseline:
        self.baselines.append(baseline)
        return baseline

    def latest_baseline(
        self,
        tenant_id: str,
        target_kind: str,
        target_id: str,
        target_version: str,
        suite_id: str,
        suite_version: str,
    ) -> EvaluationBaseline | None:
        matches = [
            baseline
            for baseline in self.baselines
            if baseline.tenant_id == tenant_id
            and baseline.target_kind.value == target_kind
            and baseline.target_id == target_id
            and baseline.target_version == target_version
            and baseline.suite_id == suite_id
            and baseline.suite_version == suite_version
        ]
        return max(matches, key=lambda baseline: baseline.created_at, default=None)

    def _suite_key(self, tenant_id: str, suite_id: str, version: str) -> str:
        return f"{tenant_id}:{suite_id}:{version}"


class SqlEvaluationRepository(BaseModel):
    config: DatabaseConfig

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def save_suite(self, record: EvaluationSuiteRecord) -> EvaluationSuiteRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_suites (
                    tenant_id, suite_id, version, suite_digest, payload,
                    created_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.tenant_id,
                    record.suite.id,
                    record.suite.version,
                    record.suite_digest,
                    self._json(record.model_dump(mode="json")),
                    record.created_by_user_id,
                ),
            )
        return record

    def get_suite(
        self, tenant_id: str, suite_id: str, version: str
    ) -> EvaluationSuiteRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM evaluation_suites
                WHERE tenant_id = ? AND suite_id = ? AND version = ?
                """,
                (tenant_id, suite_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"evaluation suite not found: {suite_id}@{version}")
        return EvaluationSuiteRecord.model_validate(self._loads(row["payload"]))

    def list_suites(self, tenant_id: str) -> list[EvaluationSuiteRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM evaluation_suites WHERE tenant_id = ? ORDER BY suite_id, version",
                (tenant_id,),
            ).fetchall()
        return [EvaluationSuiteRecord.model_validate(self._loads(row["payload"])) for row in rows]

    def save_run(self, run: EvaluationRun) -> EvaluationRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs (
                    id, tenant_id, target_kind, target_id, target_version,
                    target_digest, suite_id, suite_version, suite_digest,
                    status, promotion_allowed, evidence_digest, payload,
                    created_by_user_id, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.tenant_id,
                    run.target_kind.value,
                    run.target_id,
                    run.target_version,
                    run.target_digest,
                    run.suite_id,
                    run.suite_version,
                    run.suite_digest,
                    run.status.value,
                    run.promotion_gate.allowed,
                    run.evidence_digest,
                    self._json(run.model_dump(mode="json")),
                    run.created_by_user_id,
                    run.created_at.isoformat(),
                    run.completed_at.isoformat(),
                ),
            )
        return run

    def get_run(self, tenant_id: str, run_id: str) -> EvaluationRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM evaluation_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"evaluation run not found: {run_id}")
        return EvaluationRun.model_validate(self._loads(row["payload"]))

    def list_runs(self, tenant_id: str, target_id: str) -> list[EvaluationRun]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM evaluation_runs
                WHERE tenant_id = ? AND target_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id, target_id),
            ).fetchall()
        return [EvaluationRun.model_validate(self._loads(row["payload"])) for row in rows]

    def save_baseline(self, baseline: EvaluationBaseline) -> EvaluationBaseline:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_baselines (
                    tenant_id, target_kind, target_id, target_version,
                    suite_id, suite_version, run_id, payload,
                    created_by_user_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    baseline.tenant_id,
                    baseline.target_kind.value,
                    baseline.target_id,
                    baseline.target_version,
                    baseline.suite_id,
                    baseline.suite_version,
                    baseline.run_id,
                    self._json(baseline.model_dump(mode="json")),
                    baseline.created_by_user_id,
                    baseline.created_at.isoformat(),
                ),
            )
        return baseline

    def latest_baseline(
        self,
        tenant_id: str,
        target_kind: str,
        target_id: str,
        target_version: str,
        suite_id: str,
        suite_version: str,
    ) -> EvaluationBaseline | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM evaluation_baselines
                WHERE tenant_id = ? AND target_kind = ? AND target_id = ?
                  AND target_version = ? AND suite_id = ? AND suite_version = ?
                ORDER BY created_at DESC, run_id DESC
                LIMIT 1
                """,
                (
                    tenant_id,
                    target_kind,
                    target_id,
                    target_version,
                    suite_id,
                    suite_version,
                ),
            ).fetchone()
        if row is None:
            return None
        return EvaluationBaseline.model_validate(self._loads(row["payload"]))

    def _connect(self):
        return connect_database(self.config)

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    def _loads(self, value):
        return json.loads(value) if isinstance(value, str) else value


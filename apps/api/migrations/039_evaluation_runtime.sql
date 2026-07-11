CREATE TABLE IF NOT EXISTS evaluation_suites (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    suite_id TEXT NOT NULL,
    version TEXT NOT NULL,
    suite_digest TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
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
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_target ON evaluation_runs (tenant_id, target_kind, target_id, target_version, created_at);

CREATE TABLE IF NOT EXISTS evaluation_baselines (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_version TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
    payload JSONB NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, target_kind, target_id, target_version, suite_id, suite_version, run_id)
);

-- taroai:postgresql-only-start
ALTER TABLE evaluation_suites ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_suites FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evaluation_suites_tenant_isolation ON evaluation_suites;
CREATE POLICY evaluation_suites_tenant_isolation ON evaluation_suites USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE evaluation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evaluation_runs_tenant_isolation ON evaluation_runs;
CREATE POLICY evaluation_runs_tenant_isolation ON evaluation_runs USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE evaluation_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_baselines FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evaluation_baselines_tenant_isolation ON evaluation_baselines;
CREATE POLICY evaluation_baselines_tenant_isolation ON evaluation_baselines USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

CREATE TABLE IF NOT EXISTS restore_drill_schedules (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_by_user_id TEXT,
    service_account_id TEXT,
    status TEXT NOT NULL,
    interval_days INTEGER NOT NULL,
    max_catch_up_runs INTEGER NOT NULL,
    runbook_ref TEXT NOT NULL,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS restore_drill_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    runbook_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_object_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_restore_drill_schedules_due
    ON restore_drill_schedules (tenant_id, status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_restore_drill_runs_schedule
    ON restore_drill_runs (tenant_id, schedule_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_restore_drill_runs_schedule_time
    ON restore_drill_runs (tenant_id, schedule_id, scheduled_for);

-- taroai:postgresql-only-start
ALTER TABLE restore_drill_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE restore_drill_schedules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS restore_drill_schedules_tenant_isolation ON restore_drill_schedules;
CREATE POLICY restore_drill_schedules_tenant_isolation
    ON restore_drill_schedules
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE restore_drill_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE restore_drill_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS restore_drill_runs_tenant_isolation ON restore_drill_runs;
CREATE POLICY restore_drill_runs_tenant_isolation
    ON restore_drill_runs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

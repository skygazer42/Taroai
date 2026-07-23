CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    parent_run_id TEXT NOT NULL REFERENCES runs(id),
    parent_thread_id TEXT REFERENCES chat_threads(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL,
    spec JSONB NOT NULL,
    approval_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, parent_run_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_parent
    ON workflow_runs (tenant_id, parent_run_id);

CREATE TABLE IF NOT EXISTS workflow_tasks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    workflow_id TEXT NOT NULL REFERENCES workflow_runs(id),
    task_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    child_thread_id TEXT REFERENCES chat_threads(id),
    child_run_id TEXT REFERENCES runs(id),
    summary TEXT NOT NULL DEFAULT '',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (workflow_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_tasks_status
    ON workflow_tasks (tenant_id, workflow_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_tasks_child_run
    ON workflow_tasks (tenant_id, child_run_id)
    WHERE child_run_id IS NOT NULL;

ALTER TABLE approval_requests ADD COLUMN kind TEXT NOT NULL DEFAULT 'action';
ALTER TABLE approval_requests ADD COLUMN subject_type TEXT;
ALTER TABLE approval_requests ADD COLUMN subject_id TEXT;
ALTER TABLE approval_requests ADD COLUMN preview_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE approval_requests ADD COLUMN validation_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE approval_requests ADD COLUMN execution_status TEXT NOT NULL DEFAULT 'not_started';
ALTER TABLE approval_requests ADD COLUMN error TEXT;

ALTER TABLE agent_definitions ADD COLUMN app_kind TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE agent_definitions ADD COLUMN write_autonomy TEXT NOT NULL DEFAULT 'approval_required';

CREATE TABLE IF NOT EXISTS secret_capture_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    tool_name TEXT,
    connector_id TEXT,
    action_id TEXT,
    actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    secret_ref_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (tenant_id, run_id, name, status)
);

-- taroai:postgresql-only-start
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_runs_tenant_isolation ON workflow_runs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE workflow_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_tasks_tenant_isolation ON workflow_tasks
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE secret_capture_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE secret_capture_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY secret_capture_requests_tenant_isolation ON secret_capture_requests
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

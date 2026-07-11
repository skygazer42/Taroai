CREATE TABLE IF NOT EXISTS repository_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    repository_url TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'main',
    connector_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_repository_bindings_workspace ON repository_bindings (tenant_id, workspace_id, status, name);

CREATE TABLE IF NOT EXISTS coding_workspaces (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    repository_id TEXT NOT NULL REFERENCES repository_bindings(id),
    run_id TEXT NOT NULL,
    engine_session_id TEXT,
    branch TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    base_revision TEXT,
    head_revision TEXT,
    status TEXT NOT NULL DEFAULT 'preparing',
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_coding_workspaces_workspace ON coding_workspaces (tenant_id, workspace_id, status, created_at);

CREATE TABLE IF NOT EXISTS coding_changes (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    coding_workspace_id TEXT NOT NULL REFERENCES coding_workspaces(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    additions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    patch TEXT NOT NULL DEFAULT '',
    "binary" BOOLEAN NOT NULL DEFAULT FALSE,
    previous_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coding_changes_workspace ON coding_changes (tenant_id, coding_workspace_id, path);

CREATE TABLE IF NOT EXISTS coding_test_results (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    coding_workspace_id TEXT NOT NULL REFERENCES coding_workspaces(id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    output_artifact_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS coding_checkpoints (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    coding_workspace_id TEXT NOT NULL REFERENCES coding_workspaces(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    revision TEXT NOT NULL,
    snapshot_id TEXT,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS coding_deliveries (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    coding_workspace_id TEXT NOT NULL REFERENCES coding_workspaces(id) ON DELETE CASCADE,
    commit_sha TEXT,
    commit_message TEXT,
    pull_request_url TEXT,
    pull_request_number TEXT,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- taroai:postgresql-only-start
ALTER TABLE repository_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE repository_bindings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS repository_bindings_tenant_isolation ON repository_bindings;
CREATE POLICY repository_bindings_tenant_isolation ON repository_bindings USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE coding_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE coding_workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS coding_workspaces_tenant_isolation ON coding_workspaces;
CREATE POLICY coding_workspaces_tenant_isolation ON coding_workspaces USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE coding_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE coding_changes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS coding_changes_tenant_isolation ON coding_changes;
CREATE POLICY coding_changes_tenant_isolation ON coding_changes USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE coding_test_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE coding_test_results FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS coding_test_results_tenant_isolation ON coding_test_results;
CREATE POLICY coding_test_results_tenant_isolation ON coding_test_results USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE coding_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE coding_checkpoints FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS coding_checkpoints_tenant_isolation ON coding_checkpoints;
CREATE POLICY coding_checkpoints_tenant_isolation ON coding_checkpoints USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
ALTER TABLE coding_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE coding_deliveries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS coding_deliveries_tenant_isolation ON coding_deliveries;
CREATE POLICY coding_deliveries_tenant_isolation ON coding_deliveries USING (tenant_id = current_setting('taroai.tenant_id', true)) WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

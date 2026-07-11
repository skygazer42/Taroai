CREATE TABLE IF NOT EXISTS agent_engine_connections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    engine_type TEXT NOT NULL,
    endpoint_url TEXT,
    secret_ref_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_engine_connections_workspace
    ON agent_engine_connections (tenant_id, workspace_id, status, engine_type);

CREATE TABLE IF NOT EXISTS agent_engine_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    connection_id TEXT NOT NULL REFERENCES agent_engine_connections(id),
    engine_type TEXT NOT NULL,
    run_id TEXT,
    external_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'starting',
    cwd TEXT NOT NULL DEFAULT '/workspace',
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_engine_sessions_workspace
    ON agent_engine_sessions (tenant_id, workspace_id, status, created_at);

CREATE TABLE IF NOT EXISTS agent_engine_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    session_id TEXT NOT NULL REFERENCES agent_engine_sessions(id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, session_id, sequence)
);

CREATE TABLE IF NOT EXISTS agent_engine_approvals (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    session_id TEXT NOT NULL REFERENCES agent_engine_sessions(id),
    external_approval_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    decision_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);

-- taroai:postgresql-only-start
ALTER TABLE agent_engine_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_engine_connections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_engine_connections_tenant_isolation ON agent_engine_connections;
CREATE POLICY agent_engine_connections_tenant_isolation ON agent_engine_connections
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_engine_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_engine_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_engine_sessions_tenant_isolation ON agent_engine_sessions;
CREATE POLICY agent_engine_sessions_tenant_isolation ON agent_engine_sessions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_engine_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_engine_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_engine_events_tenant_isolation ON agent_engine_events;
CREATE POLICY agent_engine_events_tenant_isolation ON agent_engine_events
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_engine_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_engine_approvals FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_engine_approvals_tenant_isolation ON agent_engine_approvals;
CREATE POLICY agent_engine_approvals_tenant_isolation ON agent_engine_approvals
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

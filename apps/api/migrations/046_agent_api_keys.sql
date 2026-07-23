CREATE TABLE IF NOT EXISTS agent_api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    agent_id TEXT NOT NULL REFERENCES agent_definitions(id),
    name TEXT NOT NULL,
    token_prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_api_keys_agent
    ON agent_api_keys (tenant_id, agent_id, created_at);

-- taroai:postgresql-only-start
ALTER TABLE agent_api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_api_keys FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_api_keys_tenant_isolation ON agent_api_keys;
CREATE POLICY agent_api_keys_tenant_isolation ON agent_api_keys
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

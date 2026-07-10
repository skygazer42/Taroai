CREATE TABLE IF NOT EXISTS connector_definitions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    credential_ref JSONB,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensitivity_level INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    sync_state JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_connector_definitions_tenant_workspace
    ON connector_definitions (tenant_id, workspace_id, updated_at);

-- taroai:postgresql-only-start
ALTER TABLE connector_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connector_definitions_tenant_isolation ON connector_definitions;
CREATE POLICY connector_definitions_tenant_isolation
    ON connector_definitions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

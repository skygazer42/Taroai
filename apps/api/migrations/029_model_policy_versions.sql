CREATE TABLE IF NOT EXISTS model_policy_versions (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL,
    default_model TEXT,
    allowed_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    denied_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_sensitivity_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    change_type TEXT NOT NULL,
    change_request_id TEXT,
    created_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, version)
);

CREATE INDEX IF NOT EXISTS idx_model_policy_versions_scope
    ON model_policy_versions (tenant_id, workspace_id, version);

-- taroai:postgresql-only-start
ALTER TABLE model_policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_policy_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_policy_versions_tenant_isolation ON model_policy_versions;
CREATE POLICY model_policy_versions_tenant_isolation
    ON model_policy_versions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

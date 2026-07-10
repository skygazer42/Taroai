CREATE TABLE IF NOT EXISTS sso_provider_configs (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    config JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_sso_provider_configs_tenant
    ON sso_provider_configs (tenant_id, updated_at);

-- taroai:postgresql-only-start
ALTER TABLE sso_provider_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sso_provider_configs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sso_provider_configs_tenant_isolation ON sso_provider_configs;
CREATE POLICY sso_provider_configs_tenant_isolation
    ON sso_provider_configs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

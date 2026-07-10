CREATE TABLE IF NOT EXISTS model_provider_rate_limit_samples (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_provider_rate_limit_samples_window
    ON model_provider_rate_limit_samples (tenant_id, provider_id, created_at);

-- taroai:postgresql-only-start
ALTER TABLE model_provider_rate_limit_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_provider_rate_limit_samples FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_provider_rate_limit_samples_tenant_isolation
    ON model_provider_rate_limit_samples;
CREATE POLICY model_provider_rate_limit_samples_tenant_isolation
    ON model_provider_rate_limit_samples
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

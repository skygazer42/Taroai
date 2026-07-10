CREATE TABLE IF NOT EXISTS model_provider_records (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    updated_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_model_provider_records_tenant_status
    ON model_provider_records (tenant_id, status);

-- taroai:postgresql-only-start
ALTER TABLE model_provider_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_provider_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_provider_records_tenant_isolation ON model_provider_records;
CREATE POLICY model_provider_records_tenant_isolation
    ON model_provider_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

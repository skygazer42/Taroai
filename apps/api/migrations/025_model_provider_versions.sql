ALTER TABLE model_provider_records
    ADD COLUMN current_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS model_provider_versions (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    change_type TEXT NOT NULL,
    created_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id, version)
);

CREATE INDEX IF NOT EXISTS idx_model_provider_versions_provider
    ON model_provider_versions (tenant_id, provider_id, version);

INSERT INTO model_provider_versions (
    tenant_id, provider_id, version, config, status,
    change_type, created_by_user_id, created_at
)
SELECT
    tenant_id, provider_id, 1, config, status,
    'upsert', updated_by_user_id, updated_at
FROM model_provider_records
WHERE current_version = 0;

UPDATE model_provider_records
SET current_version = 1
WHERE current_version = 0;

-- taroai:postgresql-only-start
ALTER TABLE model_provider_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_provider_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_provider_versions_tenant_isolation ON model_provider_versions;
CREATE POLICY model_provider_versions_tenant_isolation
    ON model_provider_versions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

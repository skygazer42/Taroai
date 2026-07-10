CREATE TABLE IF NOT EXISTS license_validations (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    license_id TEXT NOT NULL,
    status TEXT NOT NULL,
    validation JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_license_validations_tenant_status
    ON license_validations (tenant_id, status, updated_at);

-- taroai:postgresql-only-start
ALTER TABLE license_validations ENABLE ROW LEVEL SECURITY;
ALTER TABLE license_validations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS license_validations_tenant_isolation ON license_validations;
CREATE POLICY license_validations_tenant_isolation
    ON license_validations
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

CREATE TABLE IF NOT EXISTS share_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    expires_at TIMESTAMPTZ,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_by_user_id TEXT,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_share_grants_resource
    ON share_grants (tenant_id, resource_type, resource_id, status);
CREATE INDEX IF NOT EXISTS idx_share_grants_subject
    ON share_grants (tenant_id, subject_type, subject_id, status);

-- taroai:postgresql-only-start
ALTER TABLE share_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_grants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS share_grants_tenant_isolation ON share_grants;
CREATE POLICY share_grants_tenant_isolation
    ON share_grants
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

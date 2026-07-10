CREATE TABLE IF NOT EXISTS model_policy_change_requests (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    reviewed_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_model_policy_change_requests_status
    ON model_policy_change_requests (tenant_id, status, created_at);

-- taroai:postgresql-only-start
ALTER TABLE model_policy_change_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_policy_change_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_policy_change_requests_tenant_isolation
    ON model_policy_change_requests;
CREATE POLICY model_policy_change_requests_tenant_isolation
    ON model_policy_change_requests
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

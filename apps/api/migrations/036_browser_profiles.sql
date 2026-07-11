CREATE TABLE IF NOT EXISTS browser_profiles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    secret_ref_id TEXT,
    secret_backend TEXT,
    secret_external_name TEXT,
    allowed_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    revision INTEGER NOT NULL DEFAULT 0,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_browser_profiles_workspace
    ON browser_profiles (tenant_id, workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS browser_profile_sessions (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    profile_id TEXT REFERENCES browser_profiles(id),
    run_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    current_url TEXT,
    created_by_user_id TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_browser_profile_sessions_workspace
    ON browser_profile_sessions (tenant_id, workspace_id, status, started_at);

-- taroai:postgresql-only-start
ALTER TABLE browser_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE browser_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS browser_profiles_tenant_isolation ON browser_profiles;
CREATE POLICY browser_profiles_tenant_isolation ON browser_profiles
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE browser_profile_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE browser_profile_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS browser_profile_sessions_tenant_isolation ON browser_profile_sessions;
CREATE POLICY browser_profile_sessions_tenant_isolation ON browser_profile_sessions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

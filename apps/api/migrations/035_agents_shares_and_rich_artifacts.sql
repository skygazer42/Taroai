ALTER TABLE artifacts ADD COLUMN workspace_id TEXT REFERENCES workspaces(id);
ALTER TABLE artifacts ADD COLUMN thread_id TEXT REFERENCES chat_threads(id);
ALTER TABLE artifacts ADD COLUMN message_id TEXT REFERENCES chat_messages(id);
ALTER TABLE artifacts ADD COLUMN storage_object_id TEXT REFERENCES storage_objects(id);
ALTER TABLE artifacts ADD COLUMN content_type TEXT;
ALTER TABLE artifacts ADD COLUMN size_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE artifacts ADD COLUMN preview_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE artifacts ADD COLUMN dashboard_payload JSONB;
ALTER TABLE artifacts ADD COLUMN render_policy JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE artifacts ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_artifacts_thread_created
    ON artifacts (tenant_id, thread_id, created_at);

CREATE TABLE IF NOT EXISTS agent_definitions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    latest_version INTEGER NOT NULL DEFAULT 0,
    published_version INTEGER,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_definitions_workspace
    ON agent_definitions (tenant_id, workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS agent_versions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    agent_id TEXT NOT NULL REFERENCES agent_definitions(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
    instructions TEXT NOT NULL,
    skill_bindings JSONB NOT NULL DEFAULT '[]'::jsonb,
    connector_bindings JSONB NOT NULL DEFAULT '[]'::jsonb,
    knowledge_bindings JSONB NOT NULL DEFAULT '[]'::jsonb,
    reference_files JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    runtime_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_thread_id TEXT REFERENCES chat_threads(id),
    source_run_id TEXT REFERENCES runs(id),
    change_note TEXT NOT NULL DEFAULT '',
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    UNIQUE (agent_id, version)
);

CREATE INDEX IF NOT EXISTS idx_agent_versions_agent
    ON agent_versions (tenant_id, agent_id, version);

CREATE TABLE IF NOT EXISTS agent_workspace_assignments (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    agent_id TEXT NOT NULL REFERENCES agent_definitions(id),
    version INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, agent_id)
);

CREATE TABLE IF NOT EXISTS agent_reference_files (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    agent_id TEXT NOT NULL REFERENCES agent_definitions(id),
    version INTEGER NOT NULL,
    storage_object_id TEXT NOT NULL REFERENCES storage_objects(id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_runtime_profiles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    engine_type TEXT NOT NULL DEFAULT 'taroai_native',
    runtime_image_digest TEXT,
    sandbox_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    approval_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS thread_share_links (
    id TEXT PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    thread_id TEXT NOT NULL REFERENCES chat_threads(id),
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    redaction_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_by_user_id TEXT,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_thread_share_links_thread
    ON thread_share_links (tenant_id, thread_id, status, expires_at);

-- taroai:postgresql-only-start
ALTER TABLE agent_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_definitions_tenant_isolation ON agent_definitions;
CREATE POLICY agent_definitions_tenant_isolation ON agent_definitions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_versions_tenant_isolation ON agent_versions;
CREATE POLICY agent_versions_tenant_isolation ON agent_versions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_workspace_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_workspace_assignments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_workspace_assignments_tenant_isolation ON agent_workspace_assignments;
CREATE POLICY agent_workspace_assignments_tenant_isolation ON agent_workspace_assignments
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_reference_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_reference_files FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_reference_files_tenant_isolation ON agent_reference_files;
CREATE POLICY agent_reference_files_tenant_isolation ON agent_reference_files
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_runtime_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_runtime_profiles_tenant_isolation ON agent_runtime_profiles;
CREATE POLICY agent_runtime_profiles_tenant_isolation ON agent_runtime_profiles
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE thread_share_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE thread_share_links FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS thread_share_links_tenant_isolation ON thread_share_links;
CREATE POLICY thread_share_links_tenant_isolation ON thread_share_links
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

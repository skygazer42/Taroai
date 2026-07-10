CREATE TABLE IF NOT EXISTS scim_provider_configs (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    config JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id)
);

CREATE TABLE IF NOT EXISTS scim_group_role_mappings (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    group_external_id TEXT NOT NULL,
    role_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id, group_external_id)
);

CREATE TABLE IF NOT EXISTS scim_user_links (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id, external_id)
);

CREATE TABLE IF NOT EXISTS scim_import_records (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    import_id TEXT NOT NULL,
    users_seen INTEGER NOT NULL,
    users_created INTEGER NOT NULL,
    users_linked INTEGER NOT NULL,
    users_disabled INTEGER NOT NULL,
    roles_assigned INTEGER NOT NULL,
    imported_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id, import_id)
);

CREATE INDEX IF NOT EXISTS idx_scim_provider_configs_tenant
    ON scim_provider_configs (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scim_group_role_mappings_provider
    ON scim_group_role_mappings (tenant_id, provider_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scim_user_links_provider_email
    ON scim_user_links (tenant_id, provider_id, email);
CREATE INDEX IF NOT EXISTS idx_scim_import_records_provider
    ON scim_import_records (tenant_id, provider_id, created_at);

-- taroai:postgresql-only-start
ALTER TABLE scim_provider_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_provider_configs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_provider_configs_tenant_isolation ON scim_provider_configs;
CREATE POLICY scim_provider_configs_tenant_isolation
    ON scim_provider_configs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_group_role_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_group_role_mappings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_group_role_mappings_tenant_isolation ON scim_group_role_mappings;
CREATE POLICY scim_group_role_mappings_tenant_isolation
    ON scim_group_role_mappings
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_user_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_user_links FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_user_links_tenant_isolation ON scim_user_links;
CREATE POLICY scim_user_links_tenant_isolation
    ON scim_user_links
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_import_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_import_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_import_records_tenant_isolation ON scim_import_records;
CREATE POLICY scim_import_records_tenant_isolation
    ON scim_import_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

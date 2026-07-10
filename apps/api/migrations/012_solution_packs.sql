CREATE TABLE IF NOT EXISTS solution_pack_entries (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pack_id)
);

CREATE TABLE IF NOT EXISTS solution_pack_versions (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pack_id, version)
);

CREATE TABLE IF NOT EXISTS solution_pack_installations (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    workspace_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    installed_skill_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL,
    installed_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pack_id)
);

CREATE INDEX IF NOT EXISTS idx_solution_pack_entries_tenant
    ON solution_pack_entries (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_solution_pack_versions_pack
    ON solution_pack_versions (tenant_id, pack_id, created_at);
CREATE INDEX IF NOT EXISTS idx_solution_pack_installations_tenant
    ON solution_pack_installations (tenant_id, updated_at);

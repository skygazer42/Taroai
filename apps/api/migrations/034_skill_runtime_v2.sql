CREATE TABLE IF NOT EXISTS skill_packages (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    package_kind TEXT NOT NULL DEFAULT 'package'
        CHECK (package_kind IN ('package', 'legacy_manifest')),
    manifest JSONB NOT NULL,
    frontmatter JSONB NOT NULL DEFAULT '{}'::jsonb,
    skill_md TEXT NOT NULL,
    taroai_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'disabled')),
    package_digest TEXT NOT NULL CHECK (length(package_digest) = 64),
    source_type TEXT NOT NULL CHECK (source_type IN ('zip', 'github')),
    source_url TEXT,
    source_ref TEXT,
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
    resolved_dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    release_notes TEXT,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, skill_id, version),
    UNIQUE (tenant_id, package_digest),
    FOREIGN KEY (tenant_id, skill_id, version)
        REFERENCES skill_registry_versions (tenant_id, skill_id, version)
);

CREATE INDEX IF NOT EXISTS idx_skill_packages_marketplace
    ON skill_packages (tenant_id, status, updated_at, skill_id, version);

CREATE INDEX IF NOT EXISTS idx_skill_packages_source_digest
    ON skill_packages (tenant_id, source_digest);

CREATE TABLE IF NOT EXISTS skill_package_files (
    tenant_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'instructions',
            'governance',
            'script',
            'reference',
            'asset',
            'example',
            'evaluation',
            'release_notes',
            'other'
        )
    ),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, skill_id, version, path),
    FOREIGN KEY (tenant_id, skill_id, version)
        REFERENCES skill_packages (tenant_id, skill_id, version)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_skill_package_files_kind
    ON skill_package_files (tenant_id, skill_id, version, kind, path);

CREATE TABLE IF NOT EXISTS skill_evaluation_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT REFERENCES workspaces(id),
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    package_digest TEXT NOT NULL CHECK (length(package_digest) = 64),
    suite_digest TEXT NOT NULL CHECK (length(suite_digest) = 64),
    evaluator_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed', 'error')),
    minimum_score NUMERIC NOT NULL CHECK (minimum_score >= 0 AND minimum_score <= 1),
    score NUMERIC CHECK (score >= 0 AND score <= 1),
    passed BOOLEAN,
    side_effect_violations JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_cost NUMERIC NOT NULL DEFAULT 0 CHECK (total_cost >= 0),
    duration_seconds NUMERIC NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    case_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id, skill_id, version)
        REFERENCES skill_packages (tenant_id, skill_id, version)
);

CREATE INDEX IF NOT EXISTS idx_skill_evaluation_runs_package
    ON skill_evaluation_runs (
        tenant_id,
        skill_id,
        version,
        package_digest,
        created_at
    );

CREATE INDEX IF NOT EXISTS idx_skill_evaluation_runs_gate
    ON skill_evaluation_runs (tenant_id, status, passed, created_at);

ALTER TABLE skill_installations ADD COLUMN installed_version TEXT;
ALTER TABLE skill_installations ADD COLUMN package_digest TEXT;
ALTER TABLE skill_installations ADD COLUMN source_digest TEXT;
ALTER TABLE skill_installations
    ADD COLUMN resolved_dependencies JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE skill_installations
    ADD COLUMN package_kind TEXT NOT NULL DEFAULT 'legacy_manifest';

UPDATE skill_installations
SET installed_version = (
    SELECT skill_registry_entries.version
    FROM skill_registry_entries
    WHERE skill_registry_entries.tenant_id = skill_installations.tenant_id
      AND skill_registry_entries.skill_id = skill_installations.skill_id
)
WHERE installed_version IS NULL;

CREATE INDEX IF NOT EXISTS idx_skill_installations_pinned_version
    ON skill_installations (
        tenant_id,
        workspace_id,
        skill_id,
        installed_version,
        package_digest
    );

-- taroai:postgresql-only-start
ALTER TABLE skill_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_packages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS skill_packages_tenant_isolation ON skill_packages;
CREATE POLICY skill_packages_tenant_isolation
    ON skill_packages
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE skill_package_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_package_files FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS skill_package_files_tenant_isolation ON skill_package_files;
CREATE POLICY skill_package_files_tenant_isolation
    ON skill_package_files
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE skill_evaluation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_evaluation_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS skill_evaluation_runs_tenant_isolation ON skill_evaluation_runs;
CREATE POLICY skill_evaluation_runs_tenant_isolation
    ON skill_evaluation_runs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

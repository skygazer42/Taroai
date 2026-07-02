CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    email TEXT NOT NULL,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS role_assignments (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, role_id),
    FOREIGN KEY (tenant_id, role_id) REFERENCES roles(tenant_id, id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    agent_id TEXT,
    message TEXT NOT NULL,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS storage_objects (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT REFERENCES workspaces(id),
    run_id TEXT REFERENCES runs(id),
    purpose TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    acl_subjects JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensitivity_level INTEGER NOT NULL DEFAULT 0,
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    retention_expires_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    description TEXT,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
    source_uri TEXT NOT NULL,
    source_document_id TEXT NOT NULL,
    uploaded_by_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    acl_subjects JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensitivity_level INTEGER NOT NULL DEFAULT 0,
    document_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    storage_object_id TEXT REFERENCES storage_objects(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, content_hash)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
    document_id TEXT NOT NULL REFERENCES knowledge_documents(id),
    source_document_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    content TEXT NOT NULL,
    citation JSONB NOT NULL DEFAULT '{}'::jsonb,
    acl_subjects JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensitivity_level INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    step_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by_user_id TEXT,
    resolved_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    run_id TEXT,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_run_id TEXT,
    content TEXT NOT NULL,
    created_by TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    sensitivity_level INTEGER NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS short_term_memory_reviews (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    run_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    ttl_seconds INTEGER NOT NULL,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    approved_by_user_id TEXT,
    approved_at TIMESTAMPTZ,
    rejected_by_user_id TEXT,
    rejected_at TIMESTAMPTZ,
    activated_entry_expires_at TIMESTAMPTZ,
    guardrail_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS skill_registry_entries (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, skill_id)
);

CREATE TABLE IF NOT EXISTS skill_registry_versions (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, skill_id, version)
);

CREATE TABLE IF NOT EXISTS skill_installations (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    skill_id TEXT NOT NULL,
    status TEXT NOT NULL,
    installed_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, skill_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT,
    user_id TEXT,
    run_id TEXT,
    event_type TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing_meter_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    agent_id TEXT,
    skill_id TEXT,
    meter_type TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    cost_estimate DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runtime_states (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    plan JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_step_id TEXT,
    completed_step_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    approved_step_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    approved_guardrail_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    pending_guardrail_approval_key TEXT,
    pending_guardrail_approval_stage TEXT,
    tool_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieved_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    approval_id TEXT,
    failure_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lifecycle_policies (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    retention_days INTEGER NOT NULL,
    deletion_behavior TEXT NOT NULL,
    exportable BOOLEAN NOT NULL,
    residency_region TEXT NOT NULL,
    backup_class TEXT NOT NULL,
    legal_hold_supported BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, category)
);

CREATE TABLE IF NOT EXISTS legal_holds (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    category TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_policy_scopes (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL DEFAULT '',
    default_model TEXT,
    allowed_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    denied_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id)
);

CREATE TABLE IF NOT EXISTS tenant_offboarding_plans (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    requested_by_user_id TEXT NOT NULL,
    state TEXT NOT NULL,
    approval_required BOOLEAN NOT NULL,
    approval_status TEXT NOT NULL,
    next_state_after_approval TEXT,
    export_before_delete BOOLEAN NOT NULL,
    categories JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason_length INTEGER NOT NULL,
    blocked_reason TEXT,
    blocking_legal_hold_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    deletion_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
    approved_by_user_id TEXT,
    approved_at TIMESTAMPTZ,
    export_bundle_id TEXT,
    export_storage_object_id TEXT,
    export_completed_by_user_id TEXT,
    export_completed_at TIMESTAMPTZ,
    deleted_by_user_id TEXT,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workspaces_tenant_id ON workspaces (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_tenant_user ON auth_sessions (tenant_id, user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_role_assignments_user ON role_assignments (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_runs_tenant_workspace ON runs (tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events (tenant_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_storage_objects_run_id ON storage_objects (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_storage_objects_retention ON storage_objects (tenant_id, workspace_id, retention_expires_at, deleted_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_tenant_workspace ON knowledge_bases (tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_base ON knowledge_documents (tenant_id, knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_workspace ON knowledge_chunks (tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_approval_requests_run_id ON approval_requests (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_memory_records_scope ON memory_records (tenant_id, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_skill_registry_entries_tenant ON skill_registry_entries (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_skill_registry_versions_skill ON skill_registry_versions (tenant_id, skill_id, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_installations_workspace ON skill_installations (tenant_id, workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_created ON audit_events (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_billing_meter_events_tenant_created ON billing_meter_events (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_states_tenant_run ON runtime_states (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_policies_tenant_workspace_category ON lifecycle_policies (tenant_id, workspace_id, category);
CREATE INDEX IF NOT EXISTS idx_legal_holds_scope ON legal_holds (tenant_id, category, scope_type, scope_id, expires_at, released_at);
CREATE INDEX IF NOT EXISTS idx_model_policy_scopes_tenant_workspace ON model_policy_scopes (tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_tenant_offboarding_plans_tenant_state ON tenant_offboarding_plans (tenant_id, state, created_at);

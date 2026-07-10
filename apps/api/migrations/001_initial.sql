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
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'pending', 'deleted')),
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
    sequence INTEGER NOT NULL,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    key TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response_body JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, key, method, path)
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
    embedding_vector JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding_model TEXT,
    embedding_provider TEXT,
    embedded_at TIMESTAMPTZ,
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

CREATE TABLE IF NOT EXISTS customer_feedback_records (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    submitted_by_user_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rating INTEGER,
    comment TEXT,
    run_id TEXT,
    artifact_id TEXT,
    skill_id TEXT,
    solution_pack_id TEXT,
    onboarding_step_id TEXT,
    missing_skill_name TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_feedback_evaluation_candidates (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_run_id TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    proposed_eval_name TEXT NOT NULL,
    status TEXT NOT NULL,
    human_reviewed_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    reviewed_by_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    evaluation_case_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_solution_pack_feedback_candidates (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    solution_pack_id TEXT NOT NULL,
    requested_skill_name TEXT NOT NULL,
    proposed_change_summary TEXT NOT NULL,
    status TEXT NOT NULL,
    human_reviewed_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    reviewed_by_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    publication_draft_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_feedback_evaluation_cases (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_candidate_id TEXT NOT NULL,
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_run_id TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    proposed_eval_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_solution_pack_publication_drafts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_candidate_id TEXT NOT NULL,
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    solution_pack_id TEXT NOT NULL,
    requested_skill_name TEXT NOT NULL,
    proposed_change_summary TEXT NOT NULL,
    proposed_pack_version TEXT,
    proposed_skill_manifest JSONB,
    proposed_skill_manifests JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sso_provider_configs (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    config JSONB NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id)
);

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

CREATE TABLE IF NOT EXISTS trigger_definitions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    agent_id TEXT,
    created_by_user_id TEXT,
    service_account_id TEXT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_template JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_profile TEXT,
    budget_profile TEXT,
    schedule JSONB,
    connector_event JSONB,
    agent_handoff JSONB,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
    run_id TEXT REFERENCES runs(id),
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

CREATE TABLE IF NOT EXISTS billing_pricing_rules (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL DEFAULT '',
    skill_id TEXT NOT NULL DEFAULT '',
    meter_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    price_per_unit REAL NOT NULL,
    pricing_unit_quantity REAL NOT NULL DEFAULT 1,
    updated_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency)
);

CREATE TABLE IF NOT EXISTS billing_invoices (
    invoice_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    currency TEXT NOT NULL DEFAULT 'USD',
    group_by TEXT NOT NULL,
    meter_event_count INTEGER NOT NULL,
    unpriced_event_count INTEGER NOT NULL,
    total_cost_estimate REAL,
    invoice JSONB NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    sandbox_session_id TEXT,
    browser_session_id TEXT,
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

CREATE TABLE IF NOT EXISTS restore_drill_schedules (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_by_user_id TEXT,
    service_account_id TEXT,
    status TEXT NOT NULL,
    interval_days INTEGER NOT NULL,
    max_catch_up_runs INTEGER NOT NULL,
    runbook_ref TEXT NOT NULL,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS restore_drill_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    runbook_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_object_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_policy_scopes (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL DEFAULT '',
    default_model TEXT,
    allowed_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    denied_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_sensitivity_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id)
);

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

CREATE TABLE IF NOT EXISTS model_policy_versions (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL,
    default_model TEXT,
    allowed_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    denied_models JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_sensitivity_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    change_type TEXT NOT NULL,
    change_request_id TEXT,
    created_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, version)
);

CREATE TABLE IF NOT EXISTS model_provider_records (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    updated_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider_id)
);

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

CREATE TABLE IF NOT EXISTS model_provider_change_requests (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    request_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    reviewed_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, request_id)
);

CREATE TABLE IF NOT EXISTS model_provider_rate_limit_samples (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider_id TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS license_validations (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    license_id TEXT NOT NULL,
    status TEXT NOT NULL,
    validation JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS connector_definitions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    credential_ref JSONB,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensitivity_level INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    sync_state JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_lower_email ON users (tenant_id, lower(trim(email)));
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
CREATE INDEX IF NOT EXISTS idx_solution_pack_entries_tenant ON solution_pack_entries (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_solution_pack_versions_pack ON solution_pack_versions (tenant_id, pack_id, created_at);
CREATE INDEX IF NOT EXISTS idx_solution_pack_installations_tenant ON solution_pack_installations (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_customer_feedback_records_tenant ON customer_feedback_records (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_feedback_eval_candidates_tenant ON customer_feedback_evaluation_candidates (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_solution_pack_candidates_tenant ON customer_solution_pack_feedback_candidates (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_feedback_eval_cases_tenant ON customer_feedback_evaluation_cases (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_solution_pack_drafts_tenant ON customer_solution_pack_publication_drafts (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sso_provider_configs_tenant ON sso_provider_configs (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scim_provider_configs_tenant ON scim_provider_configs (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scim_group_role_mappings_provider ON scim_group_role_mappings (tenant_id, provider_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scim_user_links_provider_email ON scim_user_links (tenant_id, provider_id, email);
CREATE INDEX IF NOT EXISTS idx_scim_import_records_provider ON scim_import_records (tenant_id, provider_id, created_at);
CREATE INDEX IF NOT EXISTS idx_trigger_definitions_tenant ON trigger_definitions (tenant_id, workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_trigger_definitions_schedule ON trigger_definitions (type, status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_created ON audit_events (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_billing_meter_events_tenant_created ON billing_meter_events (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_billing_pricing_rules_tenant_workspace ON billing_pricing_rules (tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant_created ON billing_invoices (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_share_grants_resource ON share_grants (tenant_id, resource_type, resource_id, status);
CREATE INDEX IF NOT EXISTS idx_share_grants_subject ON share_grants (tenant_id, subject_type, subject_id, status);
CREATE INDEX IF NOT EXISTS idx_runtime_states_tenant_run ON runtime_states (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_policies_tenant_workspace_category ON lifecycle_policies (tenant_id, workspace_id, category);
CREATE INDEX IF NOT EXISTS idx_legal_holds_scope ON legal_holds (tenant_id, category, scope_type, scope_id, expires_at, released_at);
CREATE INDEX IF NOT EXISTS idx_restore_drill_schedules_due ON restore_drill_schedules (tenant_id, status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_restore_drill_runs_schedule ON restore_drill_runs (tenant_id, schedule_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_restore_drill_runs_schedule_time ON restore_drill_runs (tenant_id, schedule_id, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_model_policy_scopes_tenant_workspace ON model_policy_scopes (tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_model_policy_change_requests_status ON model_policy_change_requests (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_model_policy_versions_scope ON model_policy_versions (tenant_id, workspace_id, version);
CREATE INDEX IF NOT EXISTS idx_model_provider_records_tenant_status ON model_provider_records (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_model_provider_versions_provider ON model_provider_versions (tenant_id, provider_id, version);
CREATE INDEX IF NOT EXISTS idx_model_provider_change_requests_status ON model_provider_change_requests (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_model_provider_rate_limit_samples_window ON model_provider_rate_limit_samples (tenant_id, provider_id, created_at);
CREATE INDEX IF NOT EXISTS idx_license_validations_tenant_status ON license_validations (tenant_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_connector_definitions_tenant_workspace ON connector_definitions (tenant_id, workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_tenant_offboarding_plans_tenant_state ON tenant_offboarding_plans (tenant_id, state, created_at);

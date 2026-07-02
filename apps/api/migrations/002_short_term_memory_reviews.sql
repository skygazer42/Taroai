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

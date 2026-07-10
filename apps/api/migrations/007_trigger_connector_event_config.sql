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
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE trigger_definitions
ADD COLUMN connector_event JSONB;

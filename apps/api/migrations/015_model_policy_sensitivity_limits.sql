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

ALTER TABLE model_policy_scopes
    ADD COLUMN model_sensitivity_limits JSONB NOT NULL DEFAULT '{}'::jsonb;

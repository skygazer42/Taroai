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

CREATE INDEX IF NOT EXISTS idx_billing_pricing_rules_tenant_workspace
    ON billing_pricing_rules (tenant_id, workspace_id);

-- taroai:postgresql-only-start
ALTER TABLE billing_pricing_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_pricing_rules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_pricing_rules_tenant_isolation ON billing_pricing_rules;
CREATE POLICY billing_pricing_rules_tenant_isolation
    ON billing_pricing_rules
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

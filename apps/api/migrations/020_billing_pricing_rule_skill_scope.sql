-- taroai:sqlite-only-start
CREATE TABLE IF NOT EXISTS billing_pricing_rules (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    meter_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    price_per_unit REAL NOT NULL,
    pricing_unit_quantity REAL NOT NULL DEFAULT 1,
    updated_by_user_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, workspace_id, meter_type, unit, provider, model, currency)
);

CREATE TABLE IF NOT EXISTS billing_pricing_rules_skill_scope (
    tenant_id TEXT NOT NULL,
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency)
);

INSERT OR IGNORE INTO billing_pricing_rules_skill_scope (
    tenant_id, workspace_id, skill_id, meter_type, unit, provider, model,
    currency, price_per_unit, pricing_unit_quantity,
    updated_by_user_id, created_at, updated_at
)
SELECT
    tenant_id, workspace_id, '', meter_type, unit, provider, model,
    currency, price_per_unit, pricing_unit_quantity,
    updated_by_user_id, created_at, updated_at
FROM billing_pricing_rules;

DROP TABLE billing_pricing_rules;

ALTER TABLE billing_pricing_rules_skill_scope RENAME TO billing_pricing_rules;

CREATE INDEX IF NOT EXISTS idx_billing_pricing_rules_tenant_workspace
    ON billing_pricing_rules (tenant_id, workspace_id);
-- taroai:sqlite-only-end

-- taroai:postgresql-only-start
ALTER TABLE billing_pricing_rules ADD COLUMN IF NOT EXISTS skill_id TEXT NOT NULL DEFAULT '';
ALTER TABLE billing_pricing_rules DROP CONSTRAINT IF EXISTS billing_pricing_rules_pkey;
ALTER TABLE billing_pricing_rules
    ADD PRIMARY KEY (tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency);
CREATE INDEX IF NOT EXISTS idx_billing_pricing_rules_tenant_workspace
    ON billing_pricing_rules (tenant_id, workspace_id);
-- taroai:postgresql-only-end

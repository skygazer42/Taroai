-- taroai:postgresql-only-start
ALTER TABLE billing_meter_events
    ALTER COLUMN run_id DROP NOT NULL;
-- taroai:postgresql-only-end

-- taroai:sqlite-only-start
CREATE TABLE IF NOT EXISTS billing_meter_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT,
    agent_id TEXT,
    skill_id TEXT,
    meter_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    cost_estimate REAL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TABLE IF EXISTS billing_meter_events_operation_level;

CREATE TABLE billing_meter_events_operation_level (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT,
    agent_id TEXT,
    skill_id TEXT,
    meter_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    cost_estimate REAL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO billing_meter_events_operation_level (
    id, tenant_id, workspace_id, user_id, run_id, agent_id, skill_id,
    meter_type, quantity, unit, provider, model, cost_estimate,
    metadata, created_at
)
SELECT
    id, tenant_id, workspace_id, user_id, run_id, agent_id, skill_id,
    meter_type, quantity, unit, provider, model, cost_estimate,
    metadata, created_at
FROM billing_meter_events;

DROP TABLE billing_meter_events;

ALTER TABLE billing_meter_events_operation_level
    RENAME TO billing_meter_events;

CREATE INDEX IF NOT EXISTS idx_billing_meter_events_tenant_created
    ON billing_meter_events (tenant_id, created_at);
-- taroai:sqlite-only-end

CREATE INDEX IF NOT EXISTS idx_billing_meter_events_tenant_run
    ON billing_meter_events (tenant_id, run_id);

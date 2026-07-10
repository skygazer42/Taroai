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

CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant_created
    ON billing_invoices (tenant_id, created_at);

-- taroai:postgresql-only-start
ALTER TABLE billing_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_invoices FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_invoices_tenant_isolation ON billing_invoices;
CREATE POLICY billing_invoices_tenant_isolation
    ON billing_invoices
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end

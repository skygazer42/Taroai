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

CREATE INDEX IF NOT EXISTS idx_idempotency_records_tenant_created_at
    ON idempotency_records (tenant_id, created_at);

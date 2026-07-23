-- taroai:postgresql-only-start
ALTER TABLE run_events DISABLE ROW LEVEL SECURITY;
-- taroai:postgresql-only-end

UPDATE run_events
SET sequence = CAST(ranked_events.new_sequence AS INTEGER)
FROM (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY tenant_id, run_id
            ORDER BY sequence, created_at, id
        ) AS new_sequence
    FROM run_events
) AS ranked_events
WHERE ranked_events.id = run_events.id;

-- taroai:postgresql-only-start
ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_events FORCE ROW LEVEL SECURITY;
-- taroai:postgresql-only-end

DROP INDEX IF EXISTS idx_run_events_tenant_run_sequence;

CREATE UNIQUE INDEX idx_run_events_tenant_run_sequence
    ON run_events (tenant_id, run_id, sequence);

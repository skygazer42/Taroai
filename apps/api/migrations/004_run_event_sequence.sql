ALTER TABLE run_events ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0;

UPDATE run_events
SET sequence = (
    SELECT COUNT(*)
    FROM run_events AS earlier
    WHERE earlier.run_id = run_events.run_id
      AND (
        earlier.created_at < run_events.created_at
        OR (
            earlier.created_at = run_events.created_at
            AND earlier.id <= run_events.id
        )
      )
)
WHERE sequence = 0;

CREATE INDEX IF NOT EXISTS idx_run_events_tenant_run_sequence
    ON run_events (tenant_id, run_id, sequence);

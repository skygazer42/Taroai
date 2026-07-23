-- taroai:postgresql-only-start
ALTER TABLE artifacts DISABLE ROW LEVEL SECURITY;

DELETE FROM artifacts
WHERE name = 'agent-result.md'
  AND storage_object_id IS NULL
  AND size_bytes = 0
  AND preview_payload = '{}'::jsonb
  AND uri ~ '^s3://tenant_[^/]+/runs/[^/]+/agent-result[.]md$';

ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts FORCE ROW LEVEL SECURITY;
-- taroai:postgresql-only-end

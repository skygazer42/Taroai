-- taroai:postgresql-only-start
DELETE FROM artifacts
WHERE name = 'agent-result.md'
  AND storage_object_id IS NULL
  AND size_bytes = 0
  AND preview_payload = '{}'::jsonb
  AND uri ~ '^s3://tenant_[^/]+/runs/[^/]+/agent-result[.]md$';
-- taroai:postgresql-only-end

-- taroai:sqlite-only-start
DELETE FROM artifacts
WHERE name = 'agent-result.md'
  AND storage_object_id IS NULL
  AND size_bytes = 0
  AND preview_payload = '{}'
  AND uri GLOB 's3://tenant_*/runs/*/agent-result.md';
-- taroai:sqlite-only-end

-- taroai:postgresql-only-start
ALTER TABLE roles NO FORCE ROW LEVEL SECURITY;
UPDATE roles
SET permissions = permissions || jsonb_build_array(
    jsonb_build_object(
        'action', 'connectors.invoke',
        'resource', 'tenant:' || tenant_id
    )
)
WHERE id = 'tenant_owner'
  AND NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements(permissions) AS permission
      WHERE permission ->> 'action' = 'connectors.invoke'
        AND permission ->> 'resource' = 'tenant:' || roles.tenant_id
  );
ALTER TABLE roles FORCE ROW LEVEL SECURITY;
-- taroai:postgresql-only-end

-- taroai:sqlite-only-start
UPDATE roles
SET permissions = json_insert(
    permissions,
    '$[#]',
    json_object(
        'action', 'connectors.invoke',
        'resource', 'tenant:' || tenant_id
    )
)
WHERE id = 'tenant_owner'
  AND NOT EXISTS (
      SELECT 1
      FROM json_each(permissions) AS permission
      WHERE json_extract(permission.value, '$.action') = 'connectors.invoke'
        AND json_extract(permission.value, '$.resource') = 'tenant:' || roles.tenant_id
  );
-- taroai:sqlite-only-end

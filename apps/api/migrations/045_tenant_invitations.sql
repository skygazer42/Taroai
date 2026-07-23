CREATE TABLE IF NOT EXISTS tenant_invitations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    invited_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    accepted_by_user_id TEXT REFERENCES users(id),
    UNIQUE (tenant_id, token_hash)
);

CREATE INDEX IF NOT EXISTS idx_tenant_invitations_tenant_created
    ON tenant_invitations (tenant_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tenant_invitations_tenant_email
    ON tenant_invitations (tenant_id, email);

-- taroai:postgresql-only-start
ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_invitations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_invitations_tenant_isolation ON tenant_invitations;
CREATE POLICY tenant_invitations_tenant_isolation ON tenant_invitations
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE roles NO FORCE ROW LEVEL SECURITY;
UPDATE roles
SET permissions = permissions || jsonb_build_array(
    jsonb_build_object(
        'action', 'organization.manage',
        'resource', 'tenant:' || tenant_id
    )
)
WHERE id = 'tenant_owner'
  AND NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements(permissions) AS permission
      WHERE permission ->> 'action' = 'organization.manage'
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
        'action', 'organization.manage',
        'resource', 'tenant:' || tenant_id
    )
)
WHERE id = 'tenant_owner'
  AND NOT EXISTS (
      SELECT 1
      FROM json_each(permissions) AS permission
      WHERE json_extract(permission.value, '$.action') = 'organization.manage'
        AND json_extract(permission.value, '$.resource') = 'tenant:' || roles.tenant_id
  );
-- taroai:sqlite-only-end

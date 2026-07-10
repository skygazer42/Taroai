# License Import Contract

This contract defines the operator API for importing a signed offline license envelope into a tenant control plane.

## Endpoint

```http
POST /api/licenses/import
Authorization: Bearer <access_token>
```

Development request headers remain available only when enabled by settings:

```http
X-Tenant-ID: <tenant_id>
X-User-ID: <user_id>
```

The caller must have `licenses.manage` on `tenant:<tenant_id>`.

## Request

```json
{
  "deployment_mode": "private",
  "envelope": {
    "algorithm": "ed25519",
    "key_id": "creao-license-2026-01",
    "payload": {},
    "signature": "base64-signature"
  }
}
```

The `envelope` is the signed offline license envelope. `payload` is verified through the configured trusted public key and is not trusted until signature verification passes.

## Activation Rules

- The API verifies the Ed25519 signature before evaluating the license.
- A tenant mismatch between the request context and license payload returns `403 tenant_access_denied`.
- A license that does not allow the requested deployment mode is not activated.
- Only `active` validation results are persisted as the active tenant license.

## Response

Successful imports return `201`:

```json
{
  "license_id": "license_acme_enterprise",
  "tenant_id": "tenant_acme",
  "customer_name": "Acme Inc",
  "status": "active",
  "deployment_mode": "private",
  "source": "signed_offline_file",
  "entitlements_count": 5,
  "activated": true
}
```

Operators should require `status=active` and `activated=true` before enabling runtime enforcement.

## Audit

Successful imports emit `license.imported` with the license id, status, deployment mode, source, entitlement count, and operator actor metadata.

License status transitions emit `license.status_changed`.

Audit metadata, responses, logs, and support bundles must not persist signature material, trusted public keys, license private material, or raw secret values.

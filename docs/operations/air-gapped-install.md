# Air-Gapped Install

This runbook defines constraints that must be reviewed before sales commits to an air-gapped deployment. Air-gapped means no outbound internet from the customer runtime environment.

## Pre-Sales Constraints

Confirm these constraints before committing delivery:

- no outbound internet is available from API, workers, browser controller, sandbox provider, database, Redis, object storage, or secret manager.
- all model calls must use an internal model gateway.
- sandbox execution must use an internal sandbox provider.
- browser automation, when enabled, must use an internal browser controller.
- license validation must support license file import.
- support workflow must support support bundle redaction inside the customer boundary.

## Package Transfer

package transfer must include:

- deployment package manifest.
- Helm chart and values templates.
- API, worker, migration, browser controller, and sandbox images required by the selected profile.
- database migration files and checksums.
- install validation report schema and runbook.
- upgrade matrix.
- license file import instructions.

The transfer medium and checksum verification process must be approved by the customer security team.
Every transferred archive must include detached release signature evidence:

```bash
scripts/verify-release-package.sh \
  --output dist/taroai-release.zip \
  --expected-sha256 <release-zip-sha256> \
  --signature dist/taroai-release.zip.sig.json \
  --trusted-public-key creao-release-2026-01=<base64-public-key>
```

The transfer packet should also include `release-transfer-evidence.json`
generated before the package crosses the boundary:

```bash
scripts/build-release-transfer-evidence.sh \
  --package dist/taroai-release.zip \
  --signature dist/taroai-release.zip.sig.json \
  --trusted-public-key creao-release-2026-01=<base64-public-key> \
  --output dist/release-transfer-evidence.json
```

The release signing private key must not enter the air-gapped runtime,
installation logs, or support bundles.

## Image Import

image import must load all required images into the internal registry before deployment:

1. import API and worker images.
2. import migration job image.
3. import browser controller image when browser provider is enabled.
4. import sandbox runtime image and sandbox controller image when sandbox provider requires images.
5. record imported image digests in the install evidence packet.

## License File Import

license file import must happen before installation validation:

1. copy the approved license file into the secret manager or mounted license location.
2. configure the trusted public key for the package.
3. submit the signed envelope to `POST /api/licenses/import` with `deployment_mode=air_gapped` using an operator account with `licenses.manage`.
4. confirm the response status is `active` and `activated=true`.
5. validate required entitlements for SSO, private connector count, sandbox concurrency, solution packs, and audit retention.
6. enable runtime checks with `TAROAI_LICENSE_RUNTIME_ENFORCEMENT_ENABLED=true` once the active license has been imported.
7. record the license id and validation status without copying signature material, public keys, or license private material into logs or support bundles.

## Offline Dependency Mirrors

offline dependency mirrors must be available before installation:

- container image registry mirror.
- operating system package mirror for approved maintenance windows.
- Python package mirror for build or migration tooling when images are rebuilt inside the customer boundary.
- model gateway endpoint and allowed model catalog.
- documentation and runbooks needed by operators.

## Install Validation

Run private install validation after package import and before customer traffic:

- database migration.
- Redis connectivity.
- object storage read/write.
- secret manager read.
- OpenAI-compatible Model Gateway health through the internal model gateway.
- sandbox health through the internal sandbox provider.
- API health.
- event stream.
- worker queue.
- audit write.

Production web health for air-gapped packages is added after the static local workspace is promoted into the private deployment package.

## Support Bundle

support bundle collection must run inside the customer boundary. The bundle must include package manifest, upgrade matrix, install validation report, relevant API/worker/migration logs, and redaction evidence.

support bundle redaction must remove secret values, access tokens, signed URLs,
HTTP URLs with embedded credentials or sensitive token query parameters,
connection strings, raw connector payloads, and model prompts before review
outside the runtime environment.
Run the packaged redaction tool inside the air-gapped boundary:

```bash
scripts/redact-support-bundle.sh \
  --input support-bundle.zip \
  --output support-bundle-redacted.zip \
  --evidence-output support-bundle-redaction.json
```

Only `support-bundle-redacted.zip` and `support-bundle-redaction.json` may leave
the boundary after customer approval. The evidence report records entry names,
redaction categories, and counts only.
The redactor writes both files through temporary outputs and atomically replaces
any existing approved files only after a successful write.
The redactor handles both JSON log lines and plain-text assignments such as
`prompt=...`, `connector_payload=...`, and `access_token=...`.
It also redacts sensitive header-style fields such as `Authorization`,
`X-API-Key`, `Cookie`, and `Set-Cookie`.

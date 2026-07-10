# Private Upgrade And Rollback

This runbook defines the operator path for BYOC, VPC, private, and air-gapped upgrades. It is a release readiness contract; automated upgrade execution and actual restore environment orchestration remain separate implementation work, while private install validation can consume customer-approved restore drill evidence. The scheduler/due-worker path can enqueue restore drill due jobs, create restore drill request records, and expose lifecycle API review/status endpoints; explicitly queued restore-drill execution jobs can invoke the restore verifier through the first-pass execution worker and hand verifier output to managed data-export evidence storage.

## Upgrade Prerequisites

Operators must complete these checks before applying a package:

1. backup: capture database backup, object storage backup or version marker, Redis rebuild plan, and current package manifest.
2. migration compatibility: compare current app version, target app version, migration range, and PostgreSQL version against `infra/package/upgrade-matrix.md`.
3. license check: validate the license file or configured license source for the target deployment mode and required entitlements.
4. package integrity: verify the target archive checksum and detached Ed25519 signature before image import or Helm rendering.
5. image availability: confirm API, worker, migration, and dependency images are available in the target registry or offline image archive.
6. downtime window: record expected API, worker, migration, and browser/sandbox interruption window before customer approval.
7. install validation baseline: keep the last passing `InstallValidationReport`, including `backup_restore_drill` evidence when required by customer policy, before applying changes.

Do not start an upgrade when backup, migration compatibility, license check, package integrity, image availability, or downtime window approval is missing.

Verify package integrity with the release evidence supplied by the package
producer:

```bash
scripts/verify-release-package.sh \
  --output dist/taroai-release.zip \
  --expected-sha256 <release-zip-sha256> \
  --signature dist/taroai-release.zip.sig.json \
  --trusted-public-key creao-release-2026-01=<base64-public-key>
```

The package producer should also provide `release-transfer-evidence.json`
created by:

```bash
scripts/build-release-transfer-evidence.sh \
  --package dist/taroai-release.zip \
  --signature dist/taroai-release.zip.sig.json \
  --trusted-public-key creao-release-2026-01=<base64-public-key> \
  --output dist/release-transfer-evidence.json
```

The transfer evidence JSON is not a substitute for local verification. It gives
operators a stable record of package checksum, package/app versions, signature
key id, public key, image count, migration count, and required service count.

## Upgrade Order

1. Freeze new autonomous trigger execution when policy requires a quiet window.
2. Confirm queue depth and allow currently running worker jobs to finish or be cancelled by policy.
3. Generate and archive a migration plan for the target package:

```bash
PYTHONPATH=/app/src python -m taroai.db.migration_cli \
  --database-url "${TAROAI_DATABASE_URL}" \
  --migrations-path /app/migrations
```

4. Review `pending_versions` and `unknown_applied_versions` against
   `infra/package/upgrade-matrix.md`; do not proceed when unknown versions are
   present unless the customer-approved rollback plan accounts for them.
5. Apply database migrations only after approval, using explicit apply mode:

```bash
PYTHONPATH=/app/src python -m taroai.db.migration_cli \
  --database-url "${TAROAI_DATABASE_URL}" \
  --migrations-path /app/migrations \
  --apply
```

6. Deploy API and worker images from the target package.
7. Restart workers after the API is ready.
8. Run private install validation and save the report.
9. Re-enable triggers and scheduled workers after validation passes.

## rollback prerequisites

Rollback prerequisites must be prepared before upgrade starts:

- backup must be restorable by database version and tenant scope.
- previous image set must remain available in the registry or offline archive.
- previous Helm chart values and runtime ConfigMap must be retained.
- license file used by the previous version must still be available.
- rollback operator must understand the migration rollback boundary in `infra/package/upgrade-matrix.md`.

## Rollback Path

1. Stop new trigger scheduling and worker consumption.
2. Decide whether rollback is code-only or data-restore based on migration state.
3. Restore previous chart values and previous API/worker images.
4. If the migration boundary is not backward compatible, restore database backup instead of running a partial downgrade.
5. Run private install validation before reopening customer traffic.
6. Record rollback reason, package versions, restored backup id, and validation result in the incident timeline.

## data migration caveats

Data migration caveats must be reviewed before approval:

- irreversible data migrations require backup restore rather than code-only rollback.
- schema additions may be backward compatible only when older code ignores new columns.
- schema rewrites, destructive cleanup, or retention job changes require explicit downtime approval.
- queue payload formats must be compatible with both old and new workers during rolling deployment.

## support bundle redaction

Collect a support bundle only after customer approval. Include:

- package manifest and upgrade matrix version.
- Helm values with secret references only.
- install validation report.
- migration job logs with connection strings redacted.
- API and worker logs with access tokens, secret values, signed URLs,
  credentialed HTTP URLs, connector payloads, and model prompts redacted.

Support bundle redaction is required before the bundle leaves the customer environment.
Run the packaged redaction tool inside the customer boundary and attach the
evidence report to the support handoff:

```bash
scripts/redact-support-bundle.sh \
  --input support-bundle.zip \
  --output support-bundle-redacted.zip \
  --evidence-output support-bundle-redaction.json
```

The evidence report must contain only archive entry names, redaction categories,
and counts. It must not contain API keys, bearer tokens, signed URLs,
credentialed HTTP URLs, connection strings, connector payload values, model
prompts, or secret values.
The redactor writes the sanitized archive and evidence report through temporary
files and atomically replaces existing outputs only after a successful write.
The redactor handles both JSON log lines and plain-text assignments such as
`prompt=...`, `connector_payload=...`, and `access_token=...`.
It also redacts sensitive header-style fields such as `Authorization`,
`X-API-Key`, `Cookie`, and `Set-Cookie`.

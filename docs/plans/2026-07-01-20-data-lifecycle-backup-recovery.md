# Data Lifecycle, Backup, and Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Define and implement the data lifecycle controls required for enterprise deployment: retention, deletion, export, backup, restore, disaster recovery, data residency, and tenant offboarding.

**Architecture:** Data lifecycle policy is tenant-scoped and enforced across PostgreSQL records, Redis short-term memory, object storage artifacts, knowledge documents, vector indexes, audit logs, billing records, run traces, and sandbox snapshots. Backup and restore paths are tested with drills, not only documented. Deletion is policy-aware: some data can be deleted immediately, some must be tombstoned, and audit/billing records may require retention.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, Redis, S3/MinIO, pgvector later, pytest, migration scripts, operations runbooks.

---

## Summary

Enterprise customers will ask how data is retained, exported, deleted, restored, and isolated by region. This plan makes those answers operational rather than ad hoc.

Current storage lifecycle implementation has started with `retention_expires_at` and `deleted_at` metadata, a retention-aware object delete API, SQL metadata tombstones, S3/MinIO-compatible delete adapter boundary, `storage.deleted` audit metadata, a first-pass object storage cleanup worker that deletes expired active objects through the adapter and records worker/audit events, and a first-pass `taroai/lifecycle` package with Pydantic lifecycle policy models, in-memory/SQL policy stores, tenant default plus workspace override policy resolution, active legal-hold checks wired into storage cleanup, storage cleanup preview API, storage-object export manifest generation, tenant/workspace/run scoped JSON export bundle upload, safe backup manifest generation, data residency report generation, tenant offboarding request planning with legal-hold blocking, persisted offboarding plans with approval state advancement, approved tenant-scoped offboarding export bundle execution, first-pass offboarding storage-object deletion execution, memory tombstone/delete execution, knowledge metadata/chunk deletion execution, and lifecycle policy/legal-hold/export/backup-manifest/data-residency/offboarding APIs behind `lifecycle.read`/`lifecycle.manage`. Remaining cleanup categories beyond memory, tenant-wide asynchronous export orchestration, backup execution, restore drills, broader offboarding deletion orchestration, and physical cross-region infrastructure remain planned work.

## Task 1: Data Inventory and Lifecycle Policy

**Files:**

- Create: `apps/api/src/taroai/lifecycle/__init__.py`
- Create: `apps/api/src/taroai/lifecycle/models.py`
- Create: `apps/api/src/taroai/lifecycle/service.py`
- Create: `apps/api/src/taroai/lifecycle/repository.py`
- Create: `docs/security/data-inventory.md`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_lifecycle_policy.py`
- Test: `tests/api/test_lifecycle_api.py`

**Steps:**

1. Define `DataCategory`: identity, run, event, artifact, memory, knowledge, vector, audit, billing, sandbox_snapshot, connector_credential_ref, and trace.
2. Define `LifecyclePolicy` with retention duration, deletion behavior, exportability, residency region, backup class, and legal hold support.
3. Persist lifecycle policies and legal holds in the SQL migration path.
4. Expose lifecycle policy upsert/read/effective-read, legal-hold create/list/release APIs behind `lifecycle.read` and `lifecycle.manage`.
5. Emit lifecycle policy and legal-hold audit events without raw legal-hold reason text.
6. Define tenant default policy and workspace overrides with effective fallback resolution.
7. Document every current data category and planned storage backend.
8. Add tests for policy validation and illegal retention combinations.

**Acceptance Criteria:**

- Data categories are explicit.
- Retention and deletion behavior can differ by tenant and data type.

## Task 2: Retention and Cleanup Jobs

**Files:**

- Add/modify: `apps/api/src/taroai/storage/lifecycle.py`
- Add/modify: `apps/api/src/taroai/workers/cleanup_worker.py`
- Test: `tests/api/test_storage_lifecycle.py`

**Steps:**

1. Keep the current `CleanupJob` tenant/workspace scope and resource type list as the queue contract.
2. Implement object storage cleanup for expired active `storage_objects` rows using adapter delete plus metadata tombstones.
3. Emit system `storage.deleted` events and worker `started`/`succeeded`/`failed` events with counts and affected object IDs.
4. Block cleanup for active legal holds at storage-object, workspace, run, or tenant scope and emit `storage.retention_skipped` audit metadata without raw hold reason text.
5. Add a storage cleanup preview path that returns would-delete IDs without adapter delete or metadata tombstones.
6. Keep non-storage categories as the next lifecycle-policy layer.
7. Add tests for object cleanup, worker job processing, future-retention protection, tombstone exclusion, tenant scope, legal-hold skip behavior, and storage cleanup preview.

**Acceptance Criteria:**

- Cleanup jobs are repeatable and auditable.
- Retention does not break audit and billing references.

## Task 3: Tenant Data Export

**Files:**

- Create: `apps/api/src/taroai/lifecycle/export.py`
- Modify: `apps/api/src/taroai/storage/catalog.py`
- Modify: `apps/api/src/taroai/storage/repository.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_lifecycle_export.py`

**Steps:**

1. Define `DataExportRequest`, `DataExportManifest`, and export bundle models.
2. Support first-pass tenant, workspace, and run scoped manifests for active storage-object metadata.
3. Include category, resource ID, workspace/run scope, URI, content type, size, created timestamp, filename, purpose, ACL count, sensitivity, and retention expiry metadata.
4. Exclude storage objects whose effective lifecycle policy is not exportable.
5. Expose manifest creation and tenant/workspace/run scoped JSON bundle creation through FastAPI behind `lifecycle.read`, storing bundles through the object storage adapter.
6. Emit summary-only audit metadata for manifest and bundle creation, without item details.
7. Keep user scope, knowledge space, artifact collections, redacted packages, hashes, multi-file archives, and asynchronous export orchestration as follow-up work.

**Acceptance Criteria:**

- Enterprise customers can receive structured export manifests and first-pass JSON bundles.
- Export manifests prove what was included.

## Task 4: Tenant Offboarding and Deletion

**Files:**

- Create: `apps/api/src/taroai/lifecycle/offboarding.py`
- Create: `docs/operations/tenant-offboarding-runbook.md`
- Modify: `apps/api/src/taroai/app.py`
- Modify: `apps/api/migrations/001_initial.sql`
- Test: `tests/api/test_tenant_offboarding.py`

**Steps:**

1. Define offboarding states: requested, export pending, export completed, deletion pending, deleted, and blocked.
2. Create a first-pass offboarding request plan that requires tenant owner or platform admin approval before export or deletion.
3. Route approved plans to export pending before deletion when policy requires it.
4. Block offboarding plans when tenant-scoped active legal holds exist.
5. Persist offboarding plans through the selected lifecycle backend.
6. Execute approved tenant-scoped storage-object export bundles before deletion and record bundle IDs on the offboarding plan.
7. Execute first-pass storage-object deletion for approved `deletion_pending` plans through the object storage adapter and metadata tombstones, preserving offboarding export bundles and blocking on active legal holds.
8. Execute first-pass memory deletion: redact long-term memory records into expired tombstones and remove short-term memory entries by tenant scope.
9. Execute first-pass knowledge deletion: remove tenant knowledge bases, document metadata, and chunks while leaving source-content object deletion to the storage-object category.
10. Delete or tombstone remaining tenant-scoped data across runs, skills, connectors, triggers, billing, and audit according to policy.
11. Add tests for required approval, export-before-delete, legal hold block, export bundle completion, deletion state gating, memory deletion, knowledge deletion, and final deleted state.

**Acceptance Criteria:**

- Customer offboarding planning, approval advancement, approved export bundle execution, and first-pass storage-object, memory, and knowledge deletion execution are repeatable and auditable.
- Deletion semantics are clear and tested.

## Task 5: Backup and Restore

**Files:**

- Create: `docs/operations/backup-restore.md`
- Create: `infra/backup/README.md`
- Create: `apps/api/src/taroai/lifecycle/backup.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_lifecycle_backup.py`

**Steps:**

1. Define backup manifest for PostgreSQL/SQLite control-plane data, object storage snapshot, Redis persistence where needed, and config snapshot.
2. Keep sensitive connection strings out of manifest and audit output; expose env-var references instead.
3. Define restore order: database, object storage, Redis when configured, config, then workers.
4. Expose backup manifest creation through FastAPI behind `lifecycle.read` and emit summary-only audit metadata.
5. Add contract tests for backup manifest shape and safe output.
6. Keep local backup commands, cloud backup automation, vector index snapshots, and restore drill runbooks as follow-up work.

**Acceptance Criteria:**

- Backup artifacts have a documented manifest.
- Restore is not just a command; it has validation steps.

## Task 6: Disaster Recovery and Residency

**Files:**

- Create: `docs/operations/disaster-recovery.md`
- Create: `docs/security/data-residency.md`
- Modify: `apps/api/src/taroai/config.py`
- Create: `apps/api/src/taroai/lifecycle/residency.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_data_residency_config.py`
- Test: `tests/api/test_lifecycle_api.py`

**Steps:**

1. Define RPO/RTO targets for PoC, business, and enterprise tiers.
2. Add settings for primary region, allowed storage regions, and cross-region replication mode.
3. Ensure tenant region can check object storage, vector indexes, and sandbox provider region.
4. Document DR failover steps and degraded-mode behavior.
5. Add tests that region mismatch is rejected by policy/config validation.
6. Expose data residency report creation through FastAPI behind `lifecycle.read` and emit summary-only audit metadata.
7. Keep physical multi-region provisioning, backup replication, vector backend enforcement, and sandbox provider region enforcement as deployment follow-up work.

**Acceptance Criteria:**

- Enterprise deployment can state recovery targets.
- Data residency is represented in config, report output, API, and audit metadata.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_lifecycle_policy.py tests/api/test_lifecycle_api.py tests/api/test_storage_lifecycle.py -q
python -m pytest tests/api/test_lifecycle_export.py -q
python -m pytest tests/api/test_tenant_offboarding.py -q
python -m pytest tests/api/test_lifecycle_backup.py -q
python -m pytest tests/api/test_data_residency_config.py -q
python -m pytest -q
```

Expected final result: enterprise data retention, deletion, export, backup, restore, disaster recovery, and residency are defined as enforceable platform behavior.

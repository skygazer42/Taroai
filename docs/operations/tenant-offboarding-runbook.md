# Tenant Offboarding Runbook

This runbook defines the first platform workflow for customer offboarding.

## Request

`POST /api/lifecycle/tenant-offboarding-requests` requires `lifecycle.manage`.
`GET /api/lifecycle/tenant-offboarding-requests/{plan_id}` requires `lifecycle.read`.
`POST /api/lifecycle/tenant-offboarding-requests/{plan_id}/approve` requires `lifecycle.manage`.
`POST /api/lifecycle/tenant-offboarding-requests/{plan_id}/export-bundles` requires `lifecycle.manage`.
`POST /api/lifecycle/tenant-offboarding-requests/{plan_id}/delete` requires `lifecycle.manage`.

The request captures:

- reason length, not raw reason text in audit metadata
- whether export must happen before deletion
- data categories included in the deletion scope

The first response is a plan, not a destructive action.

## States

- `requested`: the plan is waiting for tenant owner or platform admin approval.
- `export_pending`: approval has been granted and export must run before deletion.
- `export_completed`: reserved for export-complete reporting paths.
- `deletion_pending`: required export is complete, or export was skipped by policy, and deletion or tombstoning can be scheduled.
- `deleted`: final terminal state after deletion work completes.
- `blocked`: active legal hold or policy block prevents offboarding.

## Legal Hold Blocking

Tenant-scoped active legal holds block the plan before approval. The API returns `blocked` with a count of blocking holds in audit metadata. Raw legal-hold reasons are not copied into offboarding audit metadata.

## Export

After approval moves a plan to `export_pending`, `POST /api/lifecycle/tenant-offboarding-requests/{plan_id}/export-bundles` creates a tenant-scoped JSON export bundle through the configured object storage adapter. The export uses the plan categories, stores the bundle as a managed storage object, records the bundle and storage object IDs on the offboarding plan, and moves the plan to `deletion_pending`.

The endpoint rejects plans that are not in `export_pending` before any bundle upload is attempted. Export audit metadata is summary-only: it includes plan state, category count, bundle IDs, item count, byte counts, and timestamps, but omits the raw reason, manifest items, and legal-hold IDs.

## Deletion

After a plan reaches `deletion_pending`, `POST /api/lifecycle/tenant-offboarding-requests/{plan_id}/delete` executes the first-pass deletion path for storage objects, memory, and knowledge. It deletes active tenant storage objects through the configured object storage adapter and records metadata tombstones in the storage catalog. Offboarding export bundles recorded on the plan are preserved so the customer export is not removed by the same deletion step.

For memory, long-term memory records are retained as redacted tombstones with empty content, empty metadata, and `expired` status. Short-term memory entries are removed by tenant scope from the configured short-term memory backend.

For knowledge, knowledge bases, document metadata, and chunks are removed by tenant scope from the configured knowledge service. Source content stored as managed objects is deleted by the storage-object path when `storage_object` is included in the plan categories.

Deletion is state-gated and rejects plans that are not in `deletion_pending` before any delete is attempted. If an active legal hold appears after approval, deletion is not started; the plan moves to `blocked` with a hold count in summary metadata.

Deletion audit metadata is summary-only: it includes plan state, deleted storage-object count, skipped storage-object count, memory record count, short-term memory entry count, deleted knowledge base count, deleted knowledge document count, deleted knowledge chunk count, legal-hold count, and preserved storage-object count. It omits the raw reason, deleted object IDs, deleted memory record IDs, deleted knowledge document IDs, and legal-hold IDs.

## Current Boundary

This implementation creates a repeatable, auditable offboarding plan, persists it through the selected lifecycle backend, supports approval advancing to `export_pending` or `deletion_pending`, supports approved tenant-scoped storage-object export bundle execution, and supports first-pass storage-object, memory, and knowledge deletion execution with final `deleted` transition. Remaining categories, broader tombstone policy, asynchronous deletion workers, and post-delete tenant account archival remain follow-up implementation work.

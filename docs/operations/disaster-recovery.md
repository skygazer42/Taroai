# Disaster Recovery

This runbook defines first-pass recovery targets and the current operational boundary.

## Recovery Targets

| Tier | RPO | RTO | Notes |
| --- | --- | --- | --- |
| PoC | 24 hours | 8 hours | Manual restore from database/object storage snapshots is acceptable. |
| Business | 4 hours | 2 hours | Scheduled database backups, object storage versioning, and Redis rebuild plan required. |
| Enterprise | 1 hour | 30 minutes | Automated backup verification, approved-region replication, and restore drills required. |

## Restore Order

1. Restore control-plane database.
2. Restore object storage bucket contents.
3. Restore or rebuild Redis-backed short-term state and queues where policy requires it.
4. Load Pydantic settings from approved environment values.
5. Start workers after stores and object storage are reachable.
6. Run lifecycle backup manifest verification checks.
7. Run `python -m taroai.deployment.restore_drill_verification` in the restore
   environment, then pass its output to private install validation with
   `--restore-drill-verification`.
8. Run data residency report and confirm checked regions are approved.

## Scheduled Drill Automation

The first automation boundary is a Pydantic restore drill schedule,
`restore_drill.due` worker job contract, and due-worker intake path. The
scheduler worker can enqueue due restore drill jobs, advance `next_run_at`, and
record safe `restore_drill.schedule.evaluated` audit metadata without embedding
restored environment paths or verifier inputs in audit payloads. The due worker
claims due jobs, creates a restore drill run request record, records
`restore_drill.requested`, and acknowledges or dead-letters the queue job with
safe worker audit metadata. Claimed due jobs emit `worker.job.started` before
`worker.job.succeeded` or `worker.job.failed`, including jobs rejected before a
schedule can be resolved. Before creating a request record, it validates that
the due job workspace, runbook, and accountable actor still match the stored
schedule; mismatch failures are audited against the stored schedule context
instead of the untrusted due-job payload context. Schedule and run-request records can now be backed
by SQL through `TAROAI_RESTORE_DRILL_SCHEDULE_BACKEND=sql`, so independently
deployed scheduler and due-worker processes share the same durable state.
Operators can manage the schedule intake through:

- `POST /api/lifecycle/restore-drill-schedules`
- `GET /api/lifecycle/restore-drill-schedules`
- `PATCH /api/lifecycle/restore-drill-schedules/{schedule_id}`
- `GET /api/lifecycle/restore-drill-schedules/{schedule_id}/runs`
- `POST /api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}/execute`
- `POST /api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}/evidence`
- `PATCH /api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}`

Operators can use the schedule PATCH endpoint to enable or disable future due
job intake without deleting the schedule history. If a due job was already
queued before the schedule was disabled, the due worker acknowledges it as a
safe skip, records `restore_drill.skipped`, and does not create a new run
record. The due worker also treats duplicate jobs for the same schedule and
`scheduled_for` timestamp as idempotent skips with `skip_reason=run_record_exists`,
so queue retries or duplicate enqueue events do not create duplicate restore
drill requests. The schedule store also enforces that same schedule/timestamp
boundary when creating run records, with SQL deployments backed by a unique
index on `(tenant_id, schedule_id, scheduled_for)`.

The current due-worker run record is still an operations handoff for the actual
restore environment. A first-pass `restore_drill.execute` worker can consume an
already requested run record plus a Pydantic restore verifier config, invoke the
restore drill verifier, and enqueue `restore_drill.evidence` so the existing
evidence worker stores or fails the record through the governed evidence path.
The lifecycle execute endpoint enqueues that worker job, returns the job ID and
queue name, and records `restore_drill.execution_queued` audit metadata without
storing verifier input paths. Clients can retry the execute request with the
same `Idempotency-Key` to receive the original queued-job response without
creating another execution job.
Operators can also mark the run record `evidence_ready` with a storage evidence
object or `failed` through the lifecycle API after customer-approved restore
evidence is collected.
For successful restore drills, operators can now post the
`RestoreDrillVerificationResult` JSON directly to the evidence intake endpoint;
the API writes it as a `data-exports` storage object, validates the stored
content, and marks the run record `evidence_ready` in one audited operation.
The PATCH endpoint remains available for externally managed evidence objects.
The `requested` state is created by the due-worker intake path and is not a
valid operator update target.
After a run record is marked `evidence_ready` or `failed`, the lifecycle API
does not allow the same record to be rewritten; any correction must create a
new audited follow-up path.
The evidence object must resolve through the tenant storage catalog and belong
to the same workspace as the restore drill schedule, with `data-exports`
storage purpose, `application/json` content type, non-empty catalog size,
unexpired retention metadata, and retrievable non-empty exported evidence
content in object storage whose byte length matches the catalog size and whose
JSON matches the restore drill verification schema emitted by the evidence
builder with all restore verification checks passing.
The actual environment restore execution and customer approval workflow remain
operator-controlled; the execute worker covers verifier invocation and evidence
job handoff, not database/object-store/Redis restore orchestration.

## Degraded Mode

If model gateway, sandbox provider, browser provider, or Redis queue are unavailable, the control plane should keep tenant/auth/audit reads available and disable the affected execution path through settings or provider configuration.

## Current Boundary

The repository now has backup manifests, data residency reports, and a private
install validation evidence gate for customer-approved restore drills. It also
has the first scheduled restore drill due-job scheduler plus due-worker request
record intake with SQL-backed state, lifecycle API status update, first-pass
restore verifier execution worker handoff, and audited evidence intake that
stores verifier output as a managed data-export object. Automated cloud backup
jobs, actual restore orchestration, cross-region replication, and live provider
failover remain implementation work.

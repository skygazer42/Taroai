# Trigger Operations Runbook

This runbook covers the first operational path for scheduled, API, webhook, connector-event, and agent-handoff triggers.

## Read Status

Use `GET /api/triggers/operations` with `triggers.read` permission.

The response groups every tenant trigger into:

- `healthy`: no current operational issue is visible from trigger state and audit events.
- `stuck`: a schedule trigger has `next_run_at` older than the configured overdue window.
- `failing`: the latest trigger audit event is `trigger.failed` and it has not been followed by `trigger.invoked`.
- `disabled`: the trigger is currently disabled.

`TAROAI_TRIGGER_OPERATIONS_STUCK_AFTER_SECONDS` controls the stuck schedule threshold.

## Workers

`trigger_scheduler` scans enabled schedule triggers, records `trigger.schedule.evaluated`, and enqueues due work.

`trigger_due` consumes due trigger jobs, creates runs, records `trigger.invoked`, and writes billing meters for trigger invocations.

If the operations endpoint reports `stuck`, check:

1. `trigger_scheduler` process health and deployment replicas.
2. Redis queue connectivity when `TAROAI_JOB_QUEUE_BACKEND=redis`.
3. Audit event presence for `trigger.schedule.evaluated`.
4. The trigger `next_run_at` value and schedule timezone.

## Failure Triage

If the operations endpoint reports `failing`, inspect the most recent `trigger.failed` audit event for the trigger.

Common reason codes:

- `webhook_signature_invalid`: confirm the webhook signing secret, timestamp tolerance, and client signature header.
- `trigger_disabled`: confirm the trigger state before re-enabling.
- `trigger_execution_failed`: inspect the run event stream and worker logs for the related run id.

After a successful retry, the latest event should become `trigger.invoked`.

## Access And Tenant Boundary

The endpoint is tenant scoped through request context and requires `triggers.read`. It reads the configured trigger store and audit service only for the current tenant.

Operational metadata should stay summary-only. Do not copy raw connector payloads, webhook bodies, or customer secret values into status summaries.

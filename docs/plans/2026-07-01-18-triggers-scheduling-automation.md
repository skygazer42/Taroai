# Triggers, Scheduling, and Automation Implementation Plan


**Goal:** Add governed automation so agents can run from schedules, webhooks, API triggers, connector events, and approved agent-to-agent handoffs without turning the platform into uncontrolled background execution.

**Architecture:** Triggers create runs through the same API/control-plane path as chat. A trigger stores source, schedule or event filter, tenant/workspace scope, creator, enabled status, idempotency behavior, approval policy, and budget limits. A worker evaluates due triggers and enqueues run creation; every automatic run is auditable, rate-limited, and cancellable.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, Redis/queue later, cron expressions, pytest, optional webhook signature verification.

---

## Summary

Enterprise agents need more than manual chat: scheduled reports, SLA checks, connector sync follow-ups, API-initiated jobs, and webhook-driven automations. This plan defines those triggers without bypassing tenant policy or approval gates.

## Task 1: Trigger Package and Models

**Files:**

- Create: `apps/api/src/taroai/triggers/__init__.py`
- Create: `apps/api/src/taroai/triggers/models.py`
- Create: `apps/api/src/taroai/triggers/service.py`
- Test: `tests/api/test_trigger_models.py`

**Steps:**

1. Define `TriggerType`: `schedule`, `webhook`, `api`, `connector_event`, and `agent_handoff`.
2. Define `TriggerDefinition` with tenant, workspace, agent ID, created by, type, status, input template, policy profile, budget profile, and next run time.
3. Define `TriggerRunRequest` as the normalized payload that creates a run.
4. Validate that triggers always include tenant/workspace/user or service-account context.
5. Add tests for valid trigger creation, missing tenant context, and disabled status.

**Acceptance Criteria:**

- Trigger definitions are Pydantic and tenant-scoped.
- Automatic runs cannot be created without accountable identity.

**Current Implementation Notes:**

- `apps/api/src/taroai/triggers/` is started with Pydantic trigger type/status/definition/create/invoke/run-request models and an in-memory service boundary.
- Trigger definitions require tenant, workspace, and either user or service-account accountability before an automatic run request can be built.
- Disabled triggers cannot build run requests.
- FastAPI trigger admin endpoints are started for create, list, get, enable, disable, and invoke behind `triggers.manage`, `triggers.read`, and `triggers.invoke`.
- Trigger invocation creates an accountable autonomous run through the existing control-plane run creation path and records `trigger.invoked` audit metadata plus `trigger_invocation_count` billing meters without storing raw invocation payload values.
- Schedule configuration is now part of trigger Pydantic contracts, schedule evaluation emits governed `triggers.due` job payloads with timezone, start/end, and catch-up cap handling, the scheduler worker can enqueue due schedule jobs while advancing `next_run_at`, the `TriggerDue` worker consumes due jobs into accountable autonomous runs plus `runs.execute` queue jobs, `SqlTriggerStore` persists trigger definitions through Settings, and `trigger_scheduler`/`trigger_due` worker modes are wired into CLI plus Kubernetes worker deployments. Signed webhook verification is started with HMAC-SHA256 raw-body signatures, timestamp replay tolerance, Settings-managed signing secrets, a webhook-specific API entry point, safe audit metadata, and delivery-id idempotent replay/conflict handling. Connector event matching is started with Pydantic connector-event trigger config, workspace-scoped event filtering, SQL persistence, safe audit metadata, and a governed ingest API. Agent handoff triggers are started with Pydantic target-agent config, max-depth enforcement, required permission checks, source/target run events, safe audit metadata, and accountable target-run creation. Trigger operations visibility is started with a Pydantic status summary service, `GET /api/triggers/operations`, Settings-managed stuck threshold, deployment config wiring, and `docs/operations/triggers-runbook.md`. Provider-specific connector adapters, tenant/workspace-level signing secret rotation policy, advanced handoff approval policy, and automated remediation remain planned work.

## Task 2: Schedule Evaluation

**Files:**

- Create: `apps/api/src/taroai/triggers/scheduler.py`
- Create: `apps/api/src/taroai/workers/scheduler_worker.py`
- Test: `tests/api/test_schedule_evaluation.py`

**Steps:**

1. Define schedule config with cron expression, timezone, start time, end time, and max catch-up runs.
2. Calculate next run time repeatably for tests.
3. Prevent runaway catch-up after downtime.
4. Emit `TriggerDue` job payload instead of executing inline.
5. Add tests for daily schedule, disabled trigger, expired schedule, and catch-up cap.

**Acceptance Criteria:**

- Schedules can be evaluated without creating duplicate runs.
- Timezone and catch-up behavior are explicit.

**Current Implementation Notes:**

- `TriggerScheduleConfig` is included in trigger create/definition models for `schedule` triggers only.
- `apps/api/src/taroai/triggers/scheduler.py` evaluates five-field cron expressions with explicit timezone, optional start/end window, disabled-trigger handling, and max catch-up runs after downtime.
- Schedule evaluation returns `TriggerScheduleEvaluation` with formal `TriggerDueJob` payloads using `JobType.TRIGGER_DUE`; it does not execute runs inline.
- `apps/api/src/taroai/workers/scheduler_worker.py` scans schedule triggers, enqueues due `triggers.due` jobs through `JobQueue`, advances `next_run_at`, and records safe `trigger.schedule.evaluated` audit metadata without input templates.
- `apps/api/src/taroai/workers/trigger_worker.py` consumes `triggers.due` jobs, resolves the trigger through `TriggerService`, creates an accountable autonomous run, records safe `trigger.invoked` audit and `trigger_invocation_count` meter events, enqueues `runs.execute`, and acknowledges or retries the source job through `JobQueue`.
- `apps/api/src/taroai/triggers/repository.py` provides SQLite/PostgreSQL-compatible trigger definition persistence; `trigger_store_backend=sql` wires FastAPI and trigger due workers to the shared store, and the initial migration includes `trigger_definitions` plus PostgreSQL RLS protection.
- `trigger_scheduler` and `trigger_due` worker kinds are exposed by `taroai.workers.runner`, and `infra/k8s/worker.yaml` deploys independent trigger scheduler and trigger due worker processes using the shared runtime ConfigMap/Secret boundaries.
- Tests cover daily schedule evaluation, disabled schedules, expired schedules, catch-up caps, scheduler queue insertion, duplicate prevention through `next_run_at`, scheduler audit metadata, due-job run creation, run execution queue insertion, trigger invocation billing/audit metadata, SQL trigger store persistence, API restart persistence, worker builder SQL store selection, CLI worker-kind parsing, and Kubernetes worker manifests.
- Remaining work: add provider-specific connector adapters and ACL mapping, advanced handoff approval policy for higher-risk or broader-scope delegation, tenant/workspace-level signing secret rotation policy, and automated remediation for stuck or repeatedly failing triggers.

## Task 3: Webhook and API Triggers

**Files:**

- Create: `apps/api/src/taroai/triggers/webhook.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_webhook_api_triggers.py`

**Steps:**

1. Add `POST /api/triggers/{trigger_id}/invoke` for API and webhook triggers.
2. Define signed webhook payload model with timestamp, signature, and body hash.
3. Reject unsigned webhook triggers unless tenant policy explicitly allows them for PoC.
4. Support idempotency key to prevent duplicate automatic runs.
5. Add tests for valid signature, invalid signature, and duplicate invocation.

**Acceptance Criteria:**

- External systems can start approved agent runs.
- Webhook invocations are signed, scoped, and idempotent.

**Current Implementation Notes:**

- `apps/api/src/taroai/triggers/webhook.py` verifies `sha256=` HMAC-SHA256 signatures over `timestamp.raw_body` using Settings-managed signing secrets and configurable replay tolerance.
- `POST /api/triggers/{trigger_id}/webhook` accepts signed external webhook deliveries with tenant scope, creates an accountable autonomous run without requiring a user header, records only payload keys, body hash, signature status, and algorithm in `trigger.invoked` audit metadata, and supports `X-Taroai-Webhook-Delivery-ID`/`Idempotency-Key` replay without duplicate runs.
- `TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS` is treated as a secret in `.env.example` and Kubernetes Secret examples; non-sensitive webhook tolerance/unsigned-PoC controls stay in Pydantic Settings and Kubernetes ConfigMap.
- `docs/contracts/idempotency-contract.md` documents webhook delivery-id precedence, body-hash request matching, replay response behavior, and conflict handling without storing raw webhook payload values.
- Remaining work for this task: tenant/workspace-level signing secret rotation policy and connector-specific signature adapters where needed.

## Task 4: Connector Event Triggers

**Files:**

- Modify: `apps/api/src/taroai/connectors/models.py`
- Create: `apps/api/src/taroai/triggers/events.py`
- Test: `tests/api/test_connector_event_triggers.py`

**Steps:**

1. Define connector event types: document changed, ticket created, CRM account updated, order changed, and sync failed.
2. Define event filter model with connector ID, event type, fields, and condition expressions.
3. Convert matching connector events into trigger run requests.
4. Apply connector ACL mapping before run context is built.
5. Add tests that cross-workspace connector events do not trigger runs.

**Acceptance Criteria:**

- Connector changes can start runs without ignoring data permissions.
- Failed sync events can generate operational follow-up runs.

**Current Implementation Notes:**

- `TriggerConnectorEventConfig` is part of trigger create/definition models and is required only for `connector_event` triggers.
- `apps/api/src/taroai/triggers/events.py` matches enabled connector-event triggers by tenant, workspace, connector id, event type, and scalar `payload_equals` conditions with dotted payload paths.
- `POST /api/triggers/connector-events` accepts governed connector event ingest requests behind `triggers.invoke`, creates accountable autonomous runs for matched triggers, records only payload keys plus connector identifiers in `trigger.invoked` audit metadata, and records `trigger_invocation_count` billing meters.
- `SqlTriggerStore` persists connector-event config through the `connector_event` JSON column, with `007_trigger_connector_event_config.sql` covering existing databases.
- Remaining work for this task: provider-specific connector adapters, connector ACL-to-platform subject mapping, connector event deduplication policy, and provider-specific connector operations guidance.

## Task 5: Agent-to-Agent Handoff Triggers

**Files:**

- Modify: `apps/api/src/taroai/agent/runtime.py`
- Modify: `apps/api/src/taroai/agent/state.py`
- Test: `tests/api/test_agent_handoff_triggers.py`

**Steps:**

1. Define handoff trigger payload with source run, source agent, target agent, reason, input, required permissions, and max depth.
2. Require policy approval for handoffs to higher-risk agents or broader data scopes.
3. Enforce max handoff depth to prevent loops.
4. Record handoff events on both source and target runs.
5. Add tests for allowed same-scope handoff, denied scope escalation, and loop prevention.

**Acceptance Criteria:**

- Multi-agent automation stays bounded and auditable.
- Agent-to-agent delegation cannot expand permissions silently.

**Current Implementation Notes:**

- `TriggerAgentHandoffConfig` is part of trigger create/definition models and is required only for `agent_handoff` triggers.
- `apps/api/src/taroai/triggers/handoff.py` defines the handoff request/response boundary, max-depth guard, source-run workspace check, and source-agent consistency check.
- `POST /api/triggers/{trigger_id}/agent-handoff` creates an accountable autonomous run for the configured target agent through the existing control-plane path, checks `triggers.invoke` plus configured required permission actions, records bounded handoff events on both source and target runs, records safe `trigger.invoked` metadata, and emits `trigger_invocation_count` billing.
- `SqlTriggerStore` persists handoff config through the `agent_handoff` JSON column, with `008_trigger_agent_handoff_config.sql` covering existing databases.
- Remaining work for this task: high-risk delegation approval policy, richer cross-scope policy evaluation, and handoff-specific remediation guidance.

## Task 6: Trigger Admin APIs and Operations

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Create: `docs/operations/triggers-runbook.md`
- Test: `tests/api/test_trigger_admin_api.py`

**Steps:**

1. Add APIs to create, update, enable, disable, list, and inspect triggers.
2. Add audit events for trigger created, updated, enabled, disabled, invoked, failed, and run created.
3. Add billing meters for automatic run creation and trigger invocation volume.
4. Document operational steps for stuck triggers and failed webhook signatures.
5. Add tests for admin permission requirements.

**Acceptance Criteria:**

- Admins can control automation lifecycle.
- Trigger behavior is visible in audit and billing.

**Current Implementation Notes:**

- Trigger admin APIs now cover create, list, get, enable, disable, invoke, signed webhook invocation, connector-event ingest, and agent handoff execution behind trigger permissions.
- Trigger operations visibility is started through `TriggerOperationsService`, `GET /api/triggers/operations`, and `TAROAI_TRIGGER_OPERATIONS_STUCK_AFTER_SECONDS`.
- The operations view classifies tenant triggers as `healthy`, `stuck`, `failing`, or `disabled` from trigger state and audit events, including recent `trigger.failed`, `trigger.invoked`, and `trigger.schedule.evaluated` metadata.
- `docs/operations/triggers-runbook.md` documents the first triage path for `trigger_scheduler`, `trigger_due`, stuck schedules, and webhook signature failures such as `webhook_signature_invalid`.
- Remaining work for this task: update APIs, bulk operations, richer retry/deduplication policy, alert integration, and automated remediation workflows.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_trigger_models.py -q
python -m pytest tests/api/test_schedule_evaluation.py -q
python -m pytest tests/api/test_webhook_api_triggers.py -q
python -m pytest tests/api/test_connector_event_triggers.py -q
python -m pytest tests/api/test_agent_handoff_triggers.py -q
python -m pytest tests/api/test_trigger_admin_api.py -q
python -m pytest -q
```

Expected final result: schedules, webhooks, API calls, connector events, and handoffs can create governed runs through one auditable automation path.

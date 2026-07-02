# Triggers, Scheduling, and Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

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

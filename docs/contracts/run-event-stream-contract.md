# Run Event Stream Contract

The run event stream is the frontend contract for reconnectable run timelines.

## Endpoint

```http
GET /api/runs/{run_id}/events
Accept: text/event-stream
Authorization: Bearer <access_token>
```

Development request headers remain available only when enabled by settings:

```http
X-Tenant-ID: <tenant_id>
X-User-ID: <user_id>
```

## Replay

Every run event has a per-run monotonic `sequence` starting at `1`. The SSE `id:` field is the same value.

Clients can replay missed events in either form:

```http
GET /api/runs/{run_id}/events?after_sequence=12
Last-Event-ID: 12
```

`after_sequence` takes priority over `Last-Event-ID` when both are present. The response includes only events with `sequence > after_sequence`.

## SSE Frame

```text
id: 3
event: run.status_changed
data: {"id":"event_...","sequence":3,"tenant_id":"tenant_acme","workspace_id":"workspace_sales","run_id":"run_...","type":"run.status_changed","payload":{"status":"running"},"created_at":"2026-07-02T00:00:00Z"}
```

## Event Payload

Each `data:` JSON object uses the `RunEvent` shape:

- `id`: globally unique event ID.
- `sequence`: per-run sequence for reconnect and replay.
- `tenant_id`: tenant boundary.
- `workspace_id`: workspace boundary.
- `run_id`: run boundary.
- `type`: event type.
- `payload`: event-specific structured metadata.
- `created_at`: server timestamp.

## Current Event Types

Current backend events include:

- `run.created`
- `run.status_changed`
- `run.execution_queued`
- `run.cancelled`
- `run.retry_requested`
- `billing.metered`
- `audit.recorded`

Runtime, tool, approval, storage, sandbox, browser, memory, and guardrail flows can add additional typed events through the same shape.

## Workspace Runtime Events

The minimal workspace currently consumes these runtime events:

- `tool_call.completed`: safe tool result summary. For `sandbox.command`, `payload.result.output` may include `session_id`, `exit_code`, `stdout_length`, `stderr_length`, and `output_uri`; it must not include raw stdout or stderr text.
- `sandbox.command.executed`: canonical terminal event with `step_id`, `session_id`, `exit_code`, `stdout_length`, and `stderr_length`. The web terminal uses this event for command status and byte counts, and may merge in the matching safe `tool_call.completed` summary for `output_uri`.
- `browser.action.performed`: browser observation with `session_id`, `action_type`, `current_url`, and optional `screenshot_uri`.
- `artifact.created`: artifact metadata for the artifact panel.
- `approval.requested`, `approval.approved`, and `approval.rejected`: approval panel state.

## Client Rules

- Treat `sequence` as the only replay cursor.
- Store the latest processed `sequence` per `run_id`.
- On reconnect, pass `after_sequence=<latest_sequence>` or rely on browser `Last-Event-ID`.
- De-duplicate by `(run_id, sequence)` if the client retries a request.
- Do not display raw audit or guardrail metadata without applying the UI's sensitivity rules.
- Do not depend on raw sandbox stdout/stderr in run events. Display the safe byte counts and storage object links instead.

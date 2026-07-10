# API Idempotency Contract

This contract defines retry-safe write behavior for API clients.

## Implemented Routes

- `POST /api/runs`
- `POST /api/runs/{run_id}/approvals`
- `POST /api/runs/{run_id}/approvals/reject`
- `POST /api/triggers/{trigger_id}/webhook`
- `POST /api/lifecycle/restore-drill-schedules/{schedule_id}/runs/{run_record_id}/execute`

## Request Header

Clients may send:

```http
Idempotency-Key: run-create-001
```

Signed webhook clients may send a provider delivery identifier:

```http
X-Taroai-Webhook-Delivery-ID: delivery-001
```

For webhook delivery replay, `X-Taroai-Webhook-Delivery-ID` takes precedence over `Idempotency-Key` when both are provided.

The key scope is:

- tenant id
- HTTP method
- route path
- key value

The request body is converted through the Pydantic request model and hashed with stable JSON ordering.

For `POST /api/triggers/{trigger_id}/webhook`, the stored request hash contains the trigger id and raw-body SHA-256 hash. Raw webhook payload values are not stored in the idempotency record.

## Replay Behavior

When the same tenant sends the same key, method, path, and request body again:

- the API returns the original response body
- the API returns the original status code
- run creation retry: no additional run, run event stream, billing meter, or audit record is created
- approval retry: no additional approval resolution/rejection, run event, or approval audit record is created
- webhook delivery retry: no additional autonomous run, trigger audit event, or trigger billing meter is created
- restore drill execution retry: no additional execution job or execution-queued audit record is created

## Conflict Behavior

When the same tenant reuses the same key, method, and path with a different request body:

- the API returns `409 Conflict`
- the error code is `idempotency_key_conflict`

The same key can be used independently by different tenants.

## Storage

Idempotency records are persisted through the control-plane store:

- process-local store: `InMemoryControlPlaneStore.idempotency_records`
- SQL store: `idempotency_records`

The SQL table is introduced in `apps/api/migrations/005_idempotency_records.sql` and is also present in the initial schema.

## Remaining Routes

The same contract should be applied later to skill publication and tenant bootstrap before external SDKs depend on retry-safe writes for those operations.

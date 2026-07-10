# API Contract and SDK Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the backend API into a stable integration surface for the client portal, enterprise admins, solution engineers, custom skills, and future SDK users.

**Architecture:** FastAPI and Pydantic models remain the source of truth. OpenAPI is generated from routes, contract tests validate schema stability, and lightweight TypeScript/Python SDKs wrap run creation, event streaming, artifact retrieval, skill registry, knowledge, memory, approvals, billing, and admin APIs. API versioning, error format, pagination, idempotency, and webhooks are standardized before external integrations depend on them.

**Tech Stack:** FastAPI, Pydantic, OpenAPI, pytest, TypeScript later, Python SDK package later, Server-Sent Events, optional webhooks.

---

## Summary

The platform already has early API endpoints, but enterprise delivery needs a contract that can survive frontend changes, custom skill development, and customer integrations. This plan defines the API surface as a product.

## Task 1: API Versioning and Error Model

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Create: `apps/api/src/taroai/api/__init__.py`
- Create: `apps/api/src/taroai/api/errors.py`
- Test: `tests/api/test_api_error_contract.py`

**Steps:**

1. Add version prefix plan for `/api/v1`.
2. Define `ApiError` Pydantic model with `code`, `message`, `request_id`, `details`, and `retryable`.
3. Map known errors: not found, forbidden, tenant access, validation, approval required, quota exceeded, and conflict.
4. Ensure errors do not leak secrets or internal stack traces.
5. Add tests for error JSON shape and HTTP status codes.

**Acceptance Criteria:**

- All API errors use one predictable shape.
- Client code can branch on stable error codes.

## Task 2: Pagination, Filtering, and Idempotency

**Files:**

- Create: `apps/api/src/taroai/api/pagination.py`
- Create: `apps/api/src/taroai/api/idempotency.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_api_pagination_idempotency.py`

**Steps:**

1. Define `PageRequest`, `PageResult`, `SortDirection`, and cursor fields as Pydantic models.
2. Add pagination contract to list runs, artifacts, audit events, billing events, skills, knowledge documents, and memory records.
3. Define idempotency key handling for run creation, skill publication, tenant onboarding, and approval resolution.
4. Add conflict response for reused idempotency key with different payload hash.
5. Test stable ordering and cursor continuation.

**Acceptance Criteria:**

- List APIs can scale beyond in-memory PoC.
- Retried writes are safe.

**Current Implementation Notes:**

- `POST /api/runs`, `POST /api/runs/{run_id}/approvals`, `POST /api/runs/{run_id}/approvals/reject`, `POST /api/triggers/{trigger_id}/webhook`, and restore drill execution enqueue now accept retry-safe idempotency semantics.
- The key is scoped by tenant, HTTP method, route path, and key value.
- The request body is hashed from the Pydantic request model using stable JSON ordering.
- Webhook delivery replay uses `X-Taroai-Webhook-Delivery-ID` before `Idempotency-Key` and stores only trigger id plus raw-body SHA-256 hash for conflict detection.
- Run-creation replays with the same request body return the original `201` response without creating another run or run event stream.
- Approval resolve/reject replays with the same request body return the original `200` response without resolving the same approval twice or writing duplicate approval audit records.
- Webhook delivery replays with the same body return the original `202` response without creating another autonomous run, trigger audit event, or trigger billing meter.
- Restore drill execution enqueue replays with the same request body return the original `202` response without creating another execution job or execution-queued audit record.
- Reuse with a different request body returns `409` with `idempotency_key_conflict`.
- Idempotency records are backed by the control-plane store and SQL migration `005_idempotency_records.sql`.
- `docs/contracts/idempotency-contract.md` documents the implemented routes and remaining write routes.
- `apps/api/src/taroai/api/pagination.py` now defines Pydantic `PageRequest`, `PageResult`, `PageCursor`, and `SortDirection` models.
- `GET /api/runs` now returns the shared page shape with `limit`, `cursor`, `sort_direction`, `workspace_id`, and `status` support.
- Run list pagination is tenant-scoped and uses `created_at + id` cursor ordering.
- `GET /api/billing/meters` and `GET /api/audit-events` now return the shared page shape when pagination parameters are supplied, while preserving the existing array response for callers that have not migrated.
- `GET /api/skills`, `GET /api/memory`, and `GET /api/memory/short-term` now return the shared page shape when pagination parameters are supplied, while preserving the existing array response for callers that have not migrated.
- `GET /api/runs/{run_id}/artifacts`, `GET /api/runs/{run_id}/storage-objects`, `GET /api/knowledge-bases`, and `GET /api/knowledge-documents` now return the shared page shape when pagination parameters are supplied.
- Knowledge base and knowledge document list routes were added to close the management API gap for knowledge packages.
- `docs/contracts/api-pagination-contract.md` documents the implemented routes, shared response shape, and migration compatibility.
- `docs/contracts/license-import-contract.md` documents the signed license import endpoint, permission boundary, activation rules, sanitized response shape, and audit requirements.
- The main backend list routes now have a shared pagination contract; generated SDKs and clients still need to migrate to always request the page shape.
- Idempotency for skill publication and tenant onboarding remains open.

## Task 3: Run and Event Streaming Contract

**Files:**

- Modify: `apps/api/src/taroai/domain.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_run_event_stream_contract.py`

**Steps:**

1. Define event types for run created, plan updated, tool call started, tool call finished, approval requested, artifact created, billing meter recorded, memory candidate created, run completed, and run failed.
2. Keep Server-Sent Events payloads as JSON serialized Pydantic models.
3. Include monotonic sequence numbers per run.
4. Add replay API using `after_sequence`.
5. Test that frontend can fetch events after reconnect.

**Current Implementation Notes:**

- `RunEvent` now carries a per-run monotonic `sequence`.
- `GET /api/runs/{run_id}/events` emits SSE `id: <sequence>` and supports `after_sequence` plus `Last-Event-ID` replay.
- `docs/contracts/run-event-stream-contract.md` documents the reconnect contract for the static workspace and later full portal.

**Acceptance Criteria:**

- The CREAO-compatible chat UI can render a run timeline without guessing.
- Dropped connections can resume without losing state.

## Task 4: OpenAPI Export and Contract Tests

**Files:**

- Create: `tests/api/test_openapi_contract.py`
- Create: `docs/api/openapi.md`
- Future generated: `docs/api/openapi.json`

**Steps:**

1. Add test that OpenAPI schema can be generated.
2. Assert core routes exist for runs, events, artifacts, approvals, skills, knowledge, memory, billing, audit, tenants, workspaces, users, and readiness.
3. Assert request and response schemas are named and not anonymous blobs where practical.
4. Document how to export OpenAPI locally.
5. Keep generated schema out of source control until routes stabilize, unless the team wants snapshot diffs.

**Acceptance Criteria:**

- API contract drift is caught by tests.
- Integration engineers can inspect the API without reading FastAPI code.

## Task 5: Client SDK Shape

**Files:**

- Create: `docs/api/sdk-design.md`
- Future: `packages/sdk-python/README.md`
- Future: `packages/sdk-ts/README.md`
- Test: documentation plus future SDK unit tests.

**Steps:**

1. Define SDK client methods: `createRun`, `streamRunEvents`, `listArtifacts`, `approveAction`, `installSkill`, `queryKnowledge`, `writeMemoryCandidate`, `getBillingSummary`, and `checkTenantReadiness`.
2. Define authentication inputs: API token, user session token, tenant ID, workspace ID.
3. Define retry behavior for retryable errors and idempotent writes.
4. Define streaming iterator interface.
5. Document examples for frontend and custom solution scripts.

**Acceptance Criteria:**

- SDK work has a clear target before implementation.
- Custom enterprise integrations use stable platform concepts.

## Task 6: Webhooks and External Callbacks

**Files:**

- Create: `apps/api/src/taroai/webhooks/__init__.py`
- Create: `apps/api/src/taroai/webhooks/models.py`
- Create: `apps/api/src/taroai/webhooks/service.py`
- Test: `tests/api/test_webhook_contract.py`

**Steps:**

1. Define webhook subscriptions for run completed, run failed, approval requested, artifact created, skill published, quota exceeded, and evaluation candidate created.
2. Add signed delivery payload model with event ID, tenant ID, event type, created timestamp, and payload.
3. Add retry policy model.
4. Add tests for signature generation and tenant scoping.
5. Defer actual HTTP delivery worker to operations phase if needed.

**Acceptance Criteria:**

- Enterprise systems can react to agent outcomes.
- Webhook data is signed and tenant-scoped.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_api_error_contract.py -q
python -m pytest tests/api/test_api_pagination_idempotency.py -q
python -m pytest tests/api/test_run_event_stream_contract.py -q
python -m pytest tests/api/test_openapi_contract.py -q
python -m pytest tests/api/test_webhook_contract.py -q
python -m pytest -q
```

Expected final result: API behavior is versioned, typed, retry-safe, streamable, documented, and ready for frontend/SDK/custom integration work.

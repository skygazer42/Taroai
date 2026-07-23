# Storage, Identity, and Memory Backbone Implementation Plan


**Goal:** Replace the current in-memory storage, identity, and memory foundations with production-shaped service boundaries for PostgreSQL, Redis, S3/MinIO, password login, RBAC, and long-term memory.

**Architecture:** Keep `taroai/storage`, `taroai/identity`, and `taroai/memory` as bounded-context packages. Use Pydantic models at all service boundaries, PostgreSQL as source of truth, Redis for TTL run-scoped short-term memory, and S3/MinIO for large objects with PostgreSQL metadata.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, Redis, S3/MinIO, pytest, future SQLAlchemy/Alembic or equivalent migration runner.

---

## Summary

Current state already has Pydantic in-memory services for:

- `taroai/storage`: tenant-scoped object metadata catalog, SQLite-compatible SQL metadata catalog, S3/MinIO-compatible object storage adapter boundary, upload/download/delete/signed URL contracts, FastAPI metadata/signed URL/upload/download/delete endpoints, `storage.read`/`storage.write` permission checks, object ACL/sensitivity checks, configurable content scanning, retention-aware metadata tombstones, storage lifecycle cleanup service, `storage_bytes` billing meter recording, `storage.uploaded` audit metadata, `storage.content_rejected` audit metadata, `storage.downloaded` audit metadata, `storage.signed_url.created` audit metadata, and `storage.deleted` audit metadata.
- `taroai/identity`: user account, password hash, roles, permissions.
- `taroai/memory`: short-term TTL memory, Redis-backed short-term put/get/list/delete with TTL, long-term scoped memory, and SQLite-compatible SQL long-term memory persistence selectable through settings.
- `taroai/db`: Pydantic database config, migration runner, runtime state table, shared SQLite/PostgreSQL connection factory, psycopg-backed PostgreSQL URL support, process-level PostgreSQL connection pools configured through Pydantic min/max/timeout settings, and SQL repository tests for run/event/meter/audit/runtime-state persistence.

This plan turns those foundations into real backend infrastructure while keeping the same public service interfaces.

Current long-term memory implementation notes:

- `InMemoryLongTermMemoryService` and `SqlLongTermMemoryService` share the same `write` and `list_by_scope` service shape.
- `SqlLongTermMemoryService` writes to `memory_records` and preserves tenant/scope isolation, metadata, sensitivity, confidence, source run, and status.
- `TAROAI_LONG_TERM_MEMORY_BACKEND=sql` selects SQL long-term memory in the FastAPI app.

Current short-term memory implementation notes:

- `InMemoryShortTermMemoryService` and `RedisShortTermMemoryService` share the same `put` and `get` service shape.
- `RedisShortTermMemoryService` stores tenant/run-scoped Pydantic entries with Redis TTL through `TAROAI_SHORT_TERM_MEMORY_BACKEND=redis`.
- Guarded short-term memory writes that require approval are held in a review queue and stay out of active run memory until approved or rejected.
- `SqlShortTermMemoryReviewStore` persists short-term review state in `short_term_memory_reviews` when the SQL control-plane backend is selected.
- Long-term memory candidate creation, approve/reject review, active scoped reads, API endpoints, and audit metadata emission are started.
- Live Redis-backed short-term memory verification and Redis worker queue verification exist for the local cloud PoC.
- CI/private-deployment release gates for PostgreSQL migration/RLS verification, richer memory review policy, and runtime memory context loading remain implementation work.

## Task 1: Storage Service Contract

**Files:**

- Modify: `apps/api/src/taroai/storage/models.py`
- Modify: `apps/api/src/taroai/storage/catalog.py`
- Add/modify: `apps/api/src/taroai/storage/repository.py`
- Test: `tests/api/test_storage_identity_memory.py`
- Test: `tests/api/test_storage_repository.py`
- Test: `tests/api/test_storage_service_contract.py`

**Steps:**

1. Add tests for storage object registration, tenant/workspace/run key format, duplicate filename handling, and cross-tenant isolation.
2. Keep `StorageObjectCreate` and `StorageObject` as Pydantic request/result models.
3. Add an abstract storage catalog protocol or base service with `register`, `get`, `list_for_run`, and `build_signed_url`.
4. Keep the in-memory catalog as the test implementation.
5. Keep S3/MinIO access behind an adapter boundary without requiring live credentials in unit tests.
6. Keep SQL storage metadata persistence behind the same catalog method shape.

**Current Implementation Notes:**

- Storage metadata registration and run-scoped listing are exposed through FastAPI endpoints behind `storage.write` and `storage.read`.
- Signed URL creation is exposed through the storage adapter contract, requires `storage.read` for read URLs and `storage.write` for write URLs, and emits audit metadata without storing the generated URL.
- Object content upload is exposed through `PUT /api/storage/objects/{storage_object_id}/content`, requires `storage.write`, validates uploaded bytes against declared object size, writes through the object storage adapter, records a `storage_bytes` meter, and emits `storage.uploaded` audit metadata without raw object content.
- Object content download is exposed through `GET /api/storage/objects/{storage_object_id}/content`, requires `storage.read`, reads through the object storage adapter, and emits `storage.downloaded` audit metadata without raw object content.
- Object delete is exposed through `DELETE /api/storage/objects/{storage_object_id}`, requires `storage.write`, blocks deletion before `retention_expires_at`, deletes through the object storage adapter, hides tombstoned metadata from active reads, and emits `storage.deleted` audit metadata.
- Internal platform objects such as knowledge documents can be tenant/workspace scoped without `run_id`; run-produced artifacts remain listable by run.
- Storage catalog now exposes active object listing by tenant with optional workspace/run scope for lifecycle export manifests while excluding tombstoned objects.
- Storage objects carry `acl_subjects` and `sensitivity_level`; read signed URLs and content downloads enforce object ACL/sensitivity after `storage.read`, and audit metadata records ACL counts and sensitivity without raw content.
- Uploads route through a configurable content scanner using `TAROAI_OBJECT_STORAGE_CONTENT_SCAN_BLOCKED_TERMS`; rejected content emits `storage.content_rejected` audit metadata with hit counts, not raw content or rule text.
- First-pass retention cleanup lists expired active storage objects by tenant/workspace, deletes through the object storage adapter, writes metadata tombstones, can preview would-delete IDs without deletion, emits system audit metadata, and is reachable through the cleanup worker job path.
- Live MinIO/S3-compatible object storage verification exists for bucket access, upload, download byte comparison, signed URL generation, delete, and post-delete visibility checks.
- IdP/SCIM-backed subject mapping, multipart upload, production DLP/AV scanning adapters, non-storage lifecycle cleanup categories, and broader read-side billing coverage remain implementation work.

**Acceptance Criteria:**

- Object keys are tenant/workspace scoped and include a run segment when the object belongs to a run.
- API users never receive raw bucket internals except through a controlled URI or signed URL result.
- Cross-tenant list/read returns no data or a tenant access error.

## Task 2: Redis Short-Term Memory

**Files:**

- Modify: `apps/api/src/taroai/memory/models.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_storage_identity_memory.py`
- Test: `tests/api/test_short_term_memory_contract.py`

**Steps:**

1. Add tests for `put`, `get`, `delete`, `list_for_run`, and expiry.
2. Keep `ShortTermMemoryWrite` and `ShortTermMemoryEntry` as Pydantic models.
3. Preserve `InMemoryShortTermMemoryService` for unit tests.
4. Add a Redis-backed implementation behind the same interface: keys should stay tenant/run scoped.
5. Store only temporary run scratchpad data, planner notes, tool observations, and streaming cursors.

**Acceptance Criteria:**

- Every short-term memory entry has TTL.
- Expired entries are not returned.
- Short-term memory cannot be used as source of truth for audit, billing, user account, or long-term memory.

## Task 3: Long-Term Memory Service

**Files:**

- Modify: `apps/api/src/taroai/memory/models.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Modify: `apps/api/migrations/001_initial.sql`
- Test: `tests/api/test_storage_identity_memory.py`
- Test: `tests/api/test_long_term_memory_contract.py`

**Steps:**

1. Add tests for user/team/company/agent/task scoped reads.
2. Add tests for tenant isolation and sensitivity filtering.
3. Extend `MemoryWriteRequest` with source metadata, sensitivity level, confidence, and status.
4. Keep direct writes behind a memory service method; Agent Runtime must not write memory directly.
5. PostgreSQL implementation should write to `memory_records`; vector embedding is a separate later adapter.

**Acceptance Criteria:**

- Long-term memory reads are scoped by tenant and scope type.
- Memory records include source run, creator, timestamp, status, confidence, and sensitivity.
- Memory write candidates can be reviewed before becoming active.

## Task 4: Identity and Password Login

**Files:**

- Modify: `apps/api/src/taroai/identity/models.py`
- Modify: `apps/api/src/taroai/identity/service.py`
- Modify: `apps/api/src/taroai/config.py`
- Modify: `apps/api/migrations/001_initial.sql`
- Test: `tests/api/test_storage_identity_memory.py`
- Test: `tests/api/test_identity_auth_contract.py`

**Steps:**

1. Add tests proving raw passwords are never stored.
2. Add tests for login success/failure and disabled user rejection.
3. Replace test salt behavior with production-safe salt strategy before production deployment.
4. Add account status values: `active`, `disabled`, `pending`, `deleted`.
5. Add service methods for `create_user`, `verify_password`, `disable_user`, and `get_user`.

**Current Implementation Notes:**

- In-memory identity service supports password hashing, password verification, `disable_user`, and role lookup.
- Password hashing now uses a per-password random salt for new hashes, keeps the Settings-managed `password_hash_salt` as a server-side pepper, verifies legacy static-salt hashes for existing PoC data, and the Settings profile gate rejects production/customer-operated deployments with `password_hash_iterations` below `600000`.
- `UserAccount.status` is constrained to `active`, `disabled`, `pending`, or `deleted` at the Pydantic model and initial database migration boundary; in-memory and SQL identity services support pending, activation, disable, and soft-delete transitions, and permission checks plus existing-token validation require the user to remain `active`.
- Identity models normalize emails with trim/lower semantics, and SQL identity storage enforces tenant-scoped uniqueness with a `lower(trim(email))` unique index, matching the login lookup semantics and preventing duplicate principal records for the same mailbox.
- `taroai/auth` provides signed PoC access tokens with session IDs, `/api/auth/login`, `/api/auth/logout`, and server-side session revocation.
- SQL identity repository is started for users, roles, role assignments, and account status transitions with SQLite and PostgreSQL URL support through the shared connection factory; SQL-backed auth session persistence/revocation is used when SQL identity is configured. Invite orchestration, CI/private-deployment PostgreSQL verification gates, SSO handoff, MFA, and support-access controls remain implementation work.

**Acceptance Criteria:**

- Database has `password_hash`, not `password`.
- Password verification uses constant-time comparison.
- Password hashing settings come from Pydantic settings.
- Enterprise SSO can later bypass password login without changing RBAC.

## Task 5: RBAC and Permission Checks

**Files:**

- Modify: `apps/api/src/taroai/identity/models.py`
- Modify: `apps/api/src/taroai/identity/service.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_identity_rbac_contract.py`
- Test: `tests/api/test_app.py`

**Steps:**

1. Add tests for role assignment and permission lookup.
2. Add tests that protected run, skill, billing, audit, storage, and memory operations require permissions.
3. Represent permissions as `action` + `resource`, e.g. `runs.read` on `workspace:workspace_sales`.
4. Add FastAPI dependency for request context resolution with tenant, user, roles, and permissions.
5. Keep current `X-Tenant-ID`/`X-User-ID` headers as PoC auth only; isolate it behind a dependency that can be swapped for JWT/SSO.

**Acceptance Criteria:**

- Cross-tenant access is blocked.
- Same-tenant access still requires role permission for protected operations.
- Admin and employee roles can be represented without hard-coded if/else checks.

## Task 6: API Endpoints

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_app.py`
- Optional later split: `apps/api/src/taroai/routes/identity.py`, `routes/storage.py`, `routes/memory.py`

**Steps:**

1. Add identity endpoints for local PoC: create user, login, list roles, assign role.
2. Add storage metadata endpoints: register object, list run objects, request signed URL, upload object content, and download object content.
3. Add memory endpoints: write long-term memory candidate, list memory by scope, short-term put/get for runtime only.
4. Add RBAC checks to each endpoint before accessing service data.
5. Emit audit events for identity changes, role assignments, memory writes, storage uploads/downloads, and signed URL creation.

**Acceptance Criteria:**

- APIs expose the same service boundaries as the backend packages.
- Sensitive operations emit audit events.
- Tests cover success, forbidden, and cross-tenant cases.

## Verification

Run after each task:

```bash
python -m pytest tests/api/test_storage_identity_memory.py -q
python -m pytest tests/api/test_storage_repository.py -q
python -m pytest tests/api/test_backend_architecture_contract.py -q
python -m pytest tests/api/test_migration_contract.py -q
python -m pytest -q
```

Expected final result: all tests pass, no `from __future__ import annotations`, no top-level `taroai/runtime.py`, no `password TEXT` column in migrations.

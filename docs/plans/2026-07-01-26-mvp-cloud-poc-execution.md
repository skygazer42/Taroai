# MVP Cloud PoC Execution Implementation Plan


**Goal:** Turn the current in-memory backend foundation and 01-25 plan set into a concrete backend-first MVP cloud PoC execution sequence for the first enterprise Agent Workspace release.

**Architecture:** Keep the current modular package layout and Pydantic management boundaries. Product flow must call real service boundaries: Model Gateway uses an OpenAI-compatible interface, Tool Gateway owns tool execution, and Sandbox Adapter owns virtual environment execution. Local contract fixtures stay under `tests/` or contract verification and are never runtime defaults or MVP business-flow dependencies.

**Tech Stack:** FastAPI, Pydantic, pytest, OpenAI-compatible Model Gateway contract, PostgreSQL, Redis, S3/MinIO, frontend contracts later, LangGraph later, sandbox adapter, OTel-compatible traces.

---

## Summary

This plan is the first implementation milestone derived from `2026-07-01-25-roadmap-coverage-matrix.md`.

It assumes the current repo state already includes:

- `apps/api/src/taroai/app.py` with run creation, run read, runtime-state read, replayable SSE events with per-run sequence IDs, execute, cancellation, retry, approval approve/reject, artifacts, billing meter/summary/invoice/pricing-rule management/invoice snapshot, and audit endpoints.
- `apps/api/src/taroai/api/errors.py` with centralized API error mapping.
- Pydantic settings in `apps/api/src/taroai/config.py`.
- `apps/api/src/taroai/model_gateway/` with OpenAI-compatible request/response models and runtime boundary wiring.
- `apps/api/src/taroai/tool_gateway/` with Pydantic request/policy/result models, scope checks, approval-required decisions, and runtime context invocation.
- `apps/api/src/taroai/connectors/` with Pydantic connector models, credential-reference-only auth metadata, tenant/workspace-scoped memory and SQL registries, create/list/get/update/enable/disable APIs, safe registration and management audit metadata, persisted connector sync state/cursor records, sync ACL planning into knowledge document payloads, `connectors.sync` job enqueue/worker execution into knowledge with `connector_sync_document_count` billing and an independent `connector_sync` worker process, run-scoped connector capability invocation decisions with safe audit plus `connector_invocation_count` billing for ready decisions, approval-required connector invocation linkage to persisted run approval requests with approved execution gating, internal API HTTP dispatch with method/path allowlists plus API-key/OAuth2 bearer access-token injection through short-lived secret leases, read-only database connector dispatch with secret-referenced DSNs, SELECT-only enforcement, table allowlists, safe failure audit, and OAuth authorize/callback/refresh management that rotates token values through secret references without returning raw tokens.
- `apps/api/src/taroai/knowledge/` with Pydantic knowledge base/document/chunk/retrieval models, in-memory and SQLite-compatible registration, managed source-content object storage references, configurable automatic source-content chunking, ACL-aware retrieval, citations, and API endpoints.
- `apps/api/src/taroai/sandbox/` with Pydantic sandbox/browser models, disabled default sandbox/browser provider boundaries, explicit `local_process` sandbox provider for local cloud PoC command execution inside per-session workspaces, first-pass Docker provider selection through Pydantic settings and the shared factory path, Docker `--network none` container creation, Settings-managed memory/CPU/pids limits, non-root `--user`, read-only rootfs, `cap-drop=ALL`, `security-opt`, tmpfs mounts, per-session workspace bind mounts with Docker user namespace/rootless-compatible write permissions, `docker exec` command execution, file upload/download/list/snapshot, destroy lifecycle, and live Docker provider verifier coverage, Settings-backed HTTP sandbox controller adapter for `k8s`/`e2b` enterprise providers, Settings-backed HTTP browser provider adapter for `playwright`/`browserbase` controller services, first-pass Playwright HTTP browser controller service, Tool Gateway command handler, runtime `sandbox.command` and `browser.action` session creation/session-id injection, declared `/workspace/artifacts/**` sandbox file promotion or automatic `/workspace/artifacts/**` discovery to storage-backed run artifacts, rejection of declared artifact paths outside `/workspace/artifacts/`, artifact-stage guardrail content evaluation and storage content scanning before runtime artifact upload, safe `tool_call.completed` summaries without raw sandbox stdout/stderr or browser text, success/failure/approval-rejection/cancellation cleanup for runtime-created sandbox sessions, sandbox/browser session IDs persisted in runtime state snapshots, sandbox command lease-handle environment delivery without raw secret values, sandbox-scoped lease resolution API with run/step/session validation, optional provider token enforcement, and safe audit metadata, config fields, API endpoints for session create, command execution, file upload/download, snapshot, destroy, and browser actions, sandbox/browser API permission checks, session/command/file/snapshot/destroy/browser audit metadata, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload, command `sandbox_minutes` metering, file-upload `artifact_bytes` metering, and browser action `browser_action_count` metering. Local contract adapters exist only under `tests/` for isolated contract coverage.
- `apps/api/src/taroai/db/` with Pydantic database config, migration runner, runtime state table, shared SQLite/PostgreSQL connection factory, psycopg-backed PostgreSQL URL support, process-level PostgreSQL connection pools configured through Pydantic min/max/timeout settings, SQL compatibility tests for repository parameter/upsert translation, SQLite-compatible SQL repository tests for run/event/status/artifact/approval/meter/audit/runtime-state persistence, a shared in-memory/SQL persistent-store lifecycle contract, and settings-based FastAPI plus worker runner wiring for the SQL control-plane store.
- `apps/api/src/taroai/workers/` with Pydantic run execution, billing aggregation, cleanup, trigger-due, trigger-scheduler, and connector-sync job contracts, queue claim/ack/fail/reject lifecycle tests, retry/dead-letter policy, Redis-backed queue adapter boundary with expired lease recovery for crashed workers, configurable API enqueue mode for run execution, worker job lifecycle audit events with actor attribution, agent worker runner/entrypoint with default runtime Tool Gateway handlers, cleanup worker runner wiring for storage lifecycle cleanup, and connector sync worker runner wiring for Settings-built control-plane, knowledge, and connector-registry services.
- `apps/api/src/taroai/triggers/` with Pydantic trigger definitions for schedule, webhook, API, connector event, and agent handoff sources, accountable user or service-account validation, trigger create/list/get/enable/disable/invoke/webhook/connector-events/agent-handoff/operations API wiring, autonomous run creation through the existing control-plane path, safe invocation audit metadata, trigger operations summaries, and trigger invocation billing meters.
- `apps/api/src/taroai/auth/` with signed PoC access tokens, `/api/auth/login`, `/api/auth/logout`, Bearer request-context resolution, disabled-user rejection, dev request headers behind settings, and SQL-backed identity/session service selection through Pydantic settings.
- `apps/api/src/taroai/onboarding/` with Pydantic tenant bootstrap/readiness models/service, slug-derived idempotent first-owner bootstrap, starter workspace/knowledge/skill-pack seeding, and `/api/tenants/current/readiness` for authenticated readiness checks.
- `apps/api/src/taroai/memory/` with Pydantic short-term TTL memory, Redis-backed short-term put/get/list/delete with TTL, long-term scoped memory, and SQLite-compatible SQL long-term memory persistence selectable through settings.
- `apps/api/src/taroai/storage/` with Pydantic tenant-scoped metadata catalog, SQLite-compatible SQL metadata catalog, S3/MinIO-compatible object storage adapter boundary, signed URL contract, upload/download/delete contracts, storage read/write permission checks, object content upload/download APIs, internal tenant/workspace-scoped objects for platform assets, object ACL/sensitivity metadata with read-side enforcement, configurable upload content scanning, retention-aware object delete API, first-pass expired-object cleanup lifecycle service with preview mode, knowledge document source object upload, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload, upload billing/audit records, rejected-content audit records, download audit records, signed URL audit records, and delete audit records.
- `apps/api/src/taroai/lifecycle/` with Pydantic data category, deletion behavior, lifecycle policy, legal hold, legal-hold scope, data export manifest/bundle, backup manifest, restore drill schedule, and restore drill run-record models, in-memory and SQLite-compatible SQL stores, tenant default plus workspace override policy resolution, Settings-backed worker wiring, active legal-hold checks for storage cleanup, FastAPI management APIs, storage cleanup preview API, storage-object export manifest/bundle APIs, backup manifest API, restore drill schedule create/list/status update, disabled-schedule queued-job skip handling, due-job payload validation against the stored schedule with schedule-context failure audit, duplicate due-job idempotency by schedule timestamp, store-level run-record idempotency with SQL uniqueness on `(tenant_id, schedule_id, scheduled_for)`, run-record list, and run-record status/evidence update APIs with tenant/workspace/data-export/application-json/unexpired/retrievable non-empty size-matched passed restore-drill-verification evidence-object validation, RBAC permissions, and audit metadata without raw legal-hold reason text, export item details, backup component details, or restore verifier input paths.
- Modular backend packages for `agent`, `memory`, `skills`, `storage`, `lifecycle`, `identity`, and `triggers`.
- Runtime state snapshot persistence in the current in-memory store for runtime-state reads, retry, cancellation plus approval resume and rejection recovery.
- In-memory services and migration contract tests.
- Test suite under `tests/api`.

The MVP outcome is not a full enterprise platform. It is a cloud PoC where a tenant can onboard, create a run, retrieve governed context, execute through a bounded runtime and sandbox seam, produce artifacts, cancel or retry a run, pause for approval, accept approval to resume, reject approval to fail safely, and expose audit/billing events to an admin.

## MVP Non-Goals

Do not include in this milestone:

- Private/BYOC/air-gapped packaging.
- Full self-evolving publication pipeline.
- Full marketplace billing and third-party skill ecosystem.
- Broad connector catalog.
- Production microVM manager.
- Advanced SLO/incident/support operations.
- Multi-industry solution pack library.

## Task 1: Freeze MVP Contracts and Route Boundaries

**Files:**

- Modify: `docs/plans/2026-07-01-25-roadmap-coverage-matrix.md`
- Create: `docs/mvp/api-contract-checklist.md`
- Test: `tests/api/test_openapi_contract.py`

**Steps:**

1. List MVP routes: runs, run events, execute, approvals, artifacts, billing meters, audit events, tenant readiness, knowledge query, skill registry, and auth/session.
2. Add route ownership notes so `app.py` can later split into `routes/` without changing public paths.
3. Write OpenAPI contract test for current and planned MVP routes.
4. Keep current `/api/*` paths stable until `/api/v1` migration is explicitly planned.
5. Add a migration note for future API versioning from plan 14.

**Acceptance Criteria:**

- MVP route list is explicit.
- Missing planned route tests fail before implementation.
- Existing routes remain backward-compatible.

**Current Implementation Notes:**

- `docs/mvp/api-contract-checklist.md` lists the first MVP route contract for auth/session, tenant readiness, runs, event replay, approvals, artifacts, knowledge query, billing meters, audit events, skill registry, and workspace skill invocation.
- `tests/api/test_openapi_contract.py` verifies the method/path pairs are present in FastAPI OpenAPI output, keeps the MVP route set on unversioned `/api/*` paths, and requires checklist route-owner documentation.
- `/api/v1` migration remains a future plan 14 decision before SDK release or external customer integration.

## Task 2: Persist Core Control-Plane Data

**Files:**

- Modify: `apps/api/src/taroai/db/__init__.py`
- Modify: `apps/api/src/taroai/db/models.py`
- Modify: `apps/api/src/taroai/db/migrations.py`
- Modify: `apps/api/src/taroai/db/repository.py`
- Modify: `apps/api/src/taroai/store.py`
- Modify: `apps/api/migrations/001_initial.sql`
- Test: `tests/api/test_db_repository.py`
- Future: `tests/api/test_persistent_store_contract.py`

**Steps:**

1. Define repository interfaces for tenants, workspaces, users, roles, runs, run events, artifacts, approvals, memory records, audit events, and billing meters.
2. Keep existing `InMemoryControlPlaneStore` for fast tests.
3. Add a persistent store implementation behind the same service behavior.
4. Ensure every table has tenant ID and workspace ID where appropriate.
5. Add tests proving persistent and in-memory stores return the same run lifecycle behavior.
6. Wire the persistent control-plane store through Pydantic settings for local SQL-backed API runs.

**Acceptance Criteria:**

- The MVP can restart without losing runs, events, artifacts, approvals, audit, or billing data.
- Store behavior stays tenant-isolated.

**Current Implementation Notes:**

- `SqlControlPlaneRepository` persists runs, run events with per-run sequence values, artifacts, approval requests and resolutions, runtime snapshots, audit events, billing meters, idempotency records, and active license validations through the migration-backed SQL store.
- API and worker startup can select the SQL control-plane store through Pydantic settings using `control_plane_store_backend="sql"` and `database_url`.
- `tests/api/test_persistent_store_contract.py` now runs the same core run lifecycle against the in-memory store and SQLite-compatible SQL repository, including JSON-safe event/audit/billing payloads, runtime state snapshots, artifact/approval readback, and tenant isolation.

## Task 3: Add PoC Auth, Sessions, and Tenant Readiness

**Files:**

- Modify: `apps/api/src/taroai/identity/models.py`
- Modify: `apps/api/src/taroai/identity/service.py`
- Create: `apps/api/src/taroai/auth/__init__.py`
- Create: `apps/api/src/taroai/auth/service.py`
- Create: `apps/api/src/taroai/onboarding/readiness.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_auth_session_contract.py`
- Test: `tests/api/test_tenant_readiness.py`

**Steps:**

1. Keep password hash storage only; never store raw passwords.
2. Add login request/result Pydantic models.
3. Issue signed PoC access tokens with tenant, user, roles, and expiration.
4. Keep `X-Tenant-ID`/`X-User-ID` headers only as dev-mode fallback behind settings.
5. Add readiness report covering tenant, workspace, owner, roles, quotas, audit, billing, and starter skills.

**Current Implementation Notes:**

- `apps/api/src/taroai/auth/` provides signed PoC access tokens, session IDs, validation, and revocation.
- `/api/auth/login` can issue Bearer tokens that replace `X-Tenant-ID`/`X-User-ID` for API calls; `/api/auth/logout` revokes the current session.
- `TAROAI_DEV_REQUEST_HEADERS_ENABLED` gates manual dev request headers.
- `apps/api/src/taroai/onboarding/` provides Pydantic tenant readiness checks for owner, roles, auth mode, quota profile, audit read, billing read, object storage config, job queue config, starter skills, and knowledge spaces.
- `/api/tenants/bootstrap` is available for the first owner seed when `TAROAI_TENANT_BOOTSTRAP_TOKEN` is configured; it can derive tenant/workspace IDs from `tenant_slug`, reuses existing owner/role/resource records for repeated calls, seeds `tenant_owner` permissions, creates a starter knowledge space, registers and installs the general starter skill pack, and records safe bootstrap audit metadata once.
- `/api/tenants/current/readiness` returns readiness through the authenticated request context.
- Full tenant onboarding orchestration, selectable industry starter packs, starter quotas enforcement, production PostgreSQL identity rollout, OIDC/SAML login, full SCIM v2 service-provider compatibility, MFA, and support-access controls remain implementation work.

**Acceptance Criteria:**

- PoC login can replace manual headers.
- Enterprise OIDC/SAML login can later plug into the same request-context resolution.
- Readiness endpoint proves a tenant can invite pilot users.

## Task 4: Implement MVP Knowledge and Memory Path

**Files:**

- Modify: `apps/api/src/taroai/knowledge/__init__.py`
- Modify: `apps/api/src/taroai/knowledge/models.py`
- Modify: `apps/api/src/taroai/knowledge/service.py`
- Modify: `apps/api/src/taroai/knowledge/retrieval.py`
- Modify: `apps/api/src/taroai/memory/models.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_knowledge.py`
- Future: `tests/api/test_mvp_knowledge_memory.py`

**Steps:**

1. Extend Pydantic models for knowledge document, chunk, query, and result.
2. Extend the internal no-network retrieval contract; leave pgvector adapter as a later implementation.
3. Require tenant, workspace, user, ACL subjects, and sensitivity on every query.
4. Add reviewed memory write flow: candidate, approve, reject.
5. Expose MVP endpoints for knowledge query and memory candidate review.

**Acceptance Criteria:**

- Runtime can load tenant-scoped context.
- Query-time ACL filtering is tested.
- Memory is not self-written into shared scope without approval.

**Current Implementation Notes:**

- Knowledge bases, documents, and chunks have in-memory and SQLite-compatible SQL implementations behind the same service shape.
- `TAROAI_KNOWLEDGE_SERVICE_BACKEND=sql` selects SQL knowledge persistence in the FastAPI app.
- Knowledge document upload stores the source object and automatically creates retrievable chunks from uploaded content when explicit chunks are omitted, controlled by `TAROAI_KNOWLEDGE_CHUNK_MAX_CHARACTERS` and `TAROAI_KNOWLEDGE_CHUNK_OVERLAP_CHARACTERS`.
- `TAROAI_EMBEDDING_GATEWAY_ENABLED=true` enables an OpenAI-compatible Embedding Gateway for API document chunk indexing, knowledge query vectors, and conservative cross-language long-term-memory recall; chunk embedding metadata is persisted in the knowledge service, embedding usage emits safe `embedding.gateway.called` audit records without raw text or vectors, and standalone knowledge API plus Agent Runtime retrieval calls emit `embedding_call_count` plus `embedding_tokens` meters when provider usage is returned. The selected durable vector backend remains a review decision.
- Long-term scoped memory has in-memory and SQLite-compatible SQL implementations behind the same `write` and `list_by_scope` service shape.
- `TAROAI_LONG_TERM_MEMORY_BACKEND=sql` selects SQL long-term memory in the FastAPI app.
- Short-term memory has in-memory and Redis-backed implementations behind the same `put` and `get` service shape.
- `TAROAI_SHORT_TERM_MEMORY_BACKEND=redis` selects Redis-backed short-term memory in the FastAPI app.
- Memory candidate creation, approve/reject review, active scoped reads, API endpoints, and audit metadata emission are started.
- Agent Runtime context loading is started for ACL/sensitivity-filtered knowledge and approved long-term memory. `context.loaded` events expose counts and IDs, not context content.
- Retrieval-stage guardrails are applied to runtime knowledge excerpts before model planning; blocked or approval-required excerpts are excluded, redacted excerpts are rewritten, and summary-only audit metadata records the rule/action/resource IDs.
- Live Redis-backed short-term memory verification exists for ping, TTL-backed writes, tenant/run visibility, list, single-key delete, tenant delete, and cleanup behavior.
- Runtime proposal wiring, richer review policy, advanced context policy, and context quality evaluation remain implementation work.

## Task 5: Implement MVP Skill and Tool Gateway Path

**Files:**

- Modify: `apps/api/src/taroai/tool_gateway/__init__.py`
- Modify: `apps/api/src/taroai/tool_gateway/models.py`
- Modify: `apps/api/src/taroai/tool_gateway/service.py`
- Modify: `apps/api/src/taroai/skills/manifest.py`
- Modify: `apps/api/src/taroai/skills/registry.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_tool_gateway.py`
- Future: `tests/api/test_mvp_skill_tool_gateway.py`

**Steps:**

1. Extend Tool Gateway request/result/policy decision Pydantic models.
2. Skill manifests declare tool scopes, risk level, approval requirement, billing meter type, timeout, and output schema.
3. Add governed MVP tool contracts: `knowledge.search`, `artifact.write`, `email.draft`, and `browser.open`.
4. Require approval for external writes and high-risk actions.
5. Expose endpoints for listing, installing, enabling, disabling, and invoking skills in dev mode.

**Acceptance Criteria:**

- Agents call tools through a governed gateway.
- Skills are reusable platform assets, not hard-coded runtime branches.
- High-risk skill calls pause for approval.

**Current Implementation Notes:**

- `ToolGateway` now validates tool inputs and outputs against schema definitions carried by `ToolPolicy`.
- `AgentRuntime` still invokes tools only through `ToolGateway`.
- Tenant-scoped `/api/skills` register/list/get/publish/disable endpoints and `GET /api/skills/{skill_id}/versions` version history lookup are started behind identity permissions.
- Workspace-scoped skill install/list/enable/disable endpoints are started behind identity permissions.
- SQLite-compatible SQL skill registry persistence is available through `TAROAI_SKILL_REGISTRY_BACKEND`.
- Dev-mode workspace skill invocation is started through `POST /api/workspaces/{workspace_id}/skills/{skill_id}/invoke`, gated by dev request headers, `skills.invoke`, workspace installation status, manifest-required scopes, Tool Gateway execution, safe audit metadata, and `skill_call_count` billing. SaaS/file/MCP connector adapters, provider-specific OAuth edge cases, broader database dialect/query governance, sandbox provider network isolation and mTLS/IAM hardening, tenant-specific KMS/IAM policy hardening, and additional secret backend providers remain implementation work.

## Task 6: Add Model Gateway Seam and Runtime Integration

**Files:**

- Create: `apps/api/src/taroai/model_gateway/__init__.py`
- Create: `apps/api/src/taroai/model_gateway/models.py`
- Create: `apps/api/src/taroai/model_gateway/gateway.py`
- Modify: `apps/api/src/taroai/agent/planning.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Test: `tests/api/test_mvp_model_gateway_runtime.py`

**Steps:**

1. Add Model Gateway request/response Pydantic models with OpenAI-compatible fields for chat/response calls.
2. Add tenant model policy with allowed model IDs, provider references, sensitivity constraints, and max steps.
3. Runtime uses Model Gateway rather than direct provider access.
4. Record model usage meters for every gateway call.
5. Add tests that runtime execution records model call, tool call, billing, audit, and trace events.

**Acceptance Criteria:**

- MVP model flow is already behind an OpenAI-compatible gateway boundary.
- Model calls have policy and billing hooks from day one.

**Current Implementation Notes:**

- Runtime planning uses the OpenAI-compatible Model Gateway boundary, writes the policy-resolved model back into the provider request, records model usage billing/audit when usage is returned, blocks exhausted run/tenant/workspace/user/agent model budgets before provider calls, enforces first-pass Settings-backed plus API/SQL-managed global and tenant/workspace-scoped allowed/denied model lists and scoped default models, enforces model-request/model-response guardrails with safe audit metadata, records safe `model.policy_denied` audit metadata for policy rejections, exposes `/readyz.checks.model_gateway` configuration readiness without key disclosure or provider calls, exposes `/readyz.checks.sandbox` provider/controller configuration readiness for local and enterprise sandbox preflight including separate controller endpoint/auth booleans and missing labels without key disclosure, and now treats enterprise sandbox controller capability discovery failure as `configured=false` with `missing=["sandbox_controller_capabilities"]` so URL/key presence alone cannot pass readiness, redacts upstream provider HTTP error bodies before surfacing `ModelGatewayResponseError`, and records safe `model.gateway_failed` audit metadata while failing the run when gateway configuration or response errors occur.
- The OpenAI-compatible gateway can now resolve model API keys through `SecretService` using a settings-managed secret reference and a short-lived tenant/workspace/run-bound lease; legacy direct env API keys remain supported but are excluded from gateway dumps/reprs and provider-error messages. `TAROAI_MODEL_GATEWAY_PROVIDERS` can enable a Pydantic provider registry with tenant/workspace/model-specific OpenAI-compatible endpoints, provider-level secret references, provider IDs on model meters, API and worker router wiring, typed fallback policy for provider response errors and provider rate-limit skips, and first-pass process-local request/token-per-minute provider limits. `TAROAI_MODEL_GATEWAY_PROVIDER_RATE_LIMIT_BACKEND=sql` now enables SQL-backed tenant/provider rate-limit samples shared by API and worker router instances; `redis` enables shared Redis-backed samples with pre-call request reservations and `max_output_tokens` token reservations so concurrent API/worker calls cannot all pass request-per-minute or output-token-per-minute gates before usage is recorded. Successful reserved calls append only the true usage delta above reserved output tokens, avoiding double-counted request or token samples. Model budget guards now support a Settings-managed rolling window through `TAROAI_MODEL_GATEWAY_BUDGET_WINDOW_SECONDS`, with `0` preserving cumulative-history behavior. `GET /api/model-providers` now lists safe tenant provider metadata, and first-pass direct provider write/enable/disable/credential-rotation/version-list/version-rollback APIs plus staged provider change-request create/list/approve/reject APIs persist tenant-scoped provider records in memory or SQL through `TAROAI_MODEL_GATEWAY_PROVIDER_STORE_BACKEND`; pending provider changes do not reach runtime until approved, and the API/worker runtime loads active records without accepting or returning direct API-key values. Model policy scopes now also support staged change-request create/list/approve/reject APIs backed by memory or SQL; pending policy changes do not update active scopes or runtime policy until a `model_policy.approve` actor approves them. Provider fallback attempt summaries and model policy version history are now recorded without prompt content, provider response bodies, or raw error detail. Broader distributed budget governance remains implementation work. First-pass model sensitivity limits now block sensitive retrieved context or memory from reaching models that lack an explicit approved sensitivity limit.

## Task 7: Implement Sandbox Adapter Seam

**Files:**

- Modify: `apps/api/src/taroai/sandbox/__init__.py`
- Modify: `apps/api/src/taroai/sandbox/models.py`
- Modify: `apps/api/src/taroai/sandbox/adapter.py`
- Modify: `apps/api/src/taroai/sandbox/browser.py`
- Modify: `apps/api/src/taroai/sandbox/tools.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Test: `tests/api/test_sandbox.py`
- Future: `tests/api/test_mvp_sandbox_adapter.py`

**Steps:**

1. Extend sandbox create, command, file, snapshot, browser, and session models.
2. Store sandbox session metadata by tenant/workspace/run.
3. Keep default sandbox/browser providers disabled unless explicitly configured; `local_process` can power the local cloud PoC, `docker` can run Settings-hardened disabled-network containers where a Docker daemon is available, and the HTTP browser controller can connect to a managed browser service while Kubernetes, E2B, or microVM-backed isolation remains the shared enterprise execution path.
4. Add tests for tenant scoping, command result, file handoff, browser action permission/audit/billing records, and session cleanup marker.

**Acceptance Criteria:**

- MVP has a virtual environment seam without committing to one provider.
- Sandbox outputs can become artifacts.

**Current Implementation Notes:**

- Runtime can now auto-create a sandbox session for `sandbox.command`, inject `session_id`, execute through Tool Gateway, download files declared in `artifact_paths`/`artifact_path` only when they are under `/workspace/artifacts/`, discover generated files under `/workspace/artifacts/**` when no explicit artifact path is declared, reject declared artifact paths outside `/workspace/artifacts/`, evaluate artifact-stage guardrails against sandbox file content plus safe metadata, pause and resume approval-gated sandbox artifact publication back to the original sandbox file, scan artifact content with the configured storage content scanner before upload, upload allowed content through the storage adapter, create run artifacts with the storage URI, avoid leaving active unuploaded storage catalog objects on guardrail pauses or blocks, emit safe `tool_call.completed`, `sandbox.command.executed`, `sandbox.artifacts.discovered`, `sandbox.artifact.promoted`, and `sandbox.artifact.rejected` plus store-level `artifact.created` events without raw stdout/stderr in run events, and destroy runtime-created sessions after successful finalization or failure.
- Runtime browser screenshots now pass screenshot bytes through the Tool Gateway to the runtime, upload them as `browser` storage objects, replace transient browser URIs with storage-backed `screenshot_uri`, and expose `storage_object_id` on safe run events without persisting screenshot bytes in runtime state.
- Customer-operated and `prod`/`production` Settings profiles now reject `local_process`, `docker`, and `disabled` sandbox providers, keeping local execution scoped to PoC validation while shared enterprise execution uses the current accepted provider gate of `k8s` or `e2b`; those enterprise providers must configure `TAROAI_SANDBOX_CONTROLLER_BASE_URL` plus a generated `TAROAI_SANDBOX_CONTROLLER_API_KEY`, and use the HTTP sandbox controller adapter for lifecycle, command, file, snapshot, and destroy operations. The standalone Kubernetes sandbox-controller Settings now also reject `kubernetes`/`k8s` startup unless runtime-class enforcement is explicitly enabled and a non-empty runtime class name is configured, and the cloud/BYOC/private env examples carry the controller-side Kubernetes runtime-class, image allowlist, TTL, capacity, and resource limit knobs. Air-gapped mode still rejects external E2B and requires an internal provider path.
- Snapshot retention policy, Kubernetes/E2B/microVM-backed shared-enterprise isolation implementation, streaming browser live-view frontend, production SSO/MFA user flows, and full admin/skill-marketplace frontend remain planned work.

## Task 8: Build MVP Billing, Audit, Trace, and Guardrail Hooks

**Files:**

- Create: `apps/api/src/taroai/billing/__init__.py`
- Create: `apps/api/src/taroai/billing/models.py`
- Create: `apps/api/src/taroai/billing/service.py`
- Create: `apps/api/src/taroai/audit/__init__.py`
- Create: `apps/api/src/taroai/audit/models.py`
- Create: `apps/api/src/taroai/observability/__init__.py`
- Create: `apps/api/src/taroai/observability/models.py`
- Create: `apps/api/src/taroai/guardrails/__init__.py`
- Create: `apps/api/src/taroai/guardrails/models.py`
- Test: `tests/api/test_mvp_governance_hooks.py`

**Steps:**

1. Move current domain billing/audit concepts into dedicated packages while preserving existing API responses.
2. Add trace span model for model calls, tool calls, sandbox commands, retrieval, memory write, approval, and artifact creation.
3. Add minimal guardrail rules: block raw secret output, require approval for external write, block cross-tenant context.
4. Ensure API errors go through `ApiExceptionManager`.
5. Add tests for every MVP action emitting audit/billing/trace where required.

**Acceptance Criteria:**

- MVP has explainability and cost visibility.
- Guardrails are wired to runtime/tool boundaries, not only documented.

**Current Implementation Notes:**

- Successful runtime tool calls now emit `tool_call_count` billing meters and `tool.executed` audit events through both in-memory and SQLite-compatible SQL control-plane stores.
- Tool Gateway policy-required approvals now pause runtime execution, run cancellation cancels pending approvals, retry can re-enter direct or queued execution from retryable terminal states, approval rejection fails the run without executing the pending step, and failed runtime tool calls emit `tool.failed` audit records with sensitive input redaction.
- Tool Gateway service calls can emit `tool.blocked` and `tool.approval_required` audit records through injected `AuditService` before blocked or approval-gated handlers run, with sensitive input redaction.
- Tool Gateway service calls can enforce guardrail block, approval-required, and redaction decisions before handler execution, and default API/worker runtime Tool Gateways receive a guardrail service.
- Agent Runtime can enforce retrieval-stage guardrail decisions before model planning and records safe `guardrail.retrieval_blocked` or `guardrail.retrieval_approval_required` audit metadata when guarded excerpts are excluded.
- Agent Runtime can enforce model-request and model-response guardrail decisions around the OpenAI-compatible Model Gateway boundary, redacting guarded content before provider calls or planned-step execution and failing runs on blocked model guardrails without storing prompt/response text in audit.
- Agent Runtime can enforce artifact-stage guardrail decisions before generated run artifact creation, redacting guarded artifact metadata, evaluating sandbox artifact file content for block/approval decisions, pausing and resuming persisted approval for approval-gated artifact publication back to the original artifact path, or failing the run on blocked artifact publication without storing raw artifact names, URIs, or sandbox artifact content in guardrail audit.
- Long-term and short-term memory services can enforce memory-write guardrail decisions before persistence, redacting guarded memory content, holding approval-required long-term candidate writes and short-term run-memory writes for review, or rejecting blocked writes through unified API errors and summary-only audit metadata.
- Dedicated `billing`, `audit`, and `observability` packages have started with billing filters/summaries, first-pass billing invoice views and persisted invoice snapshots, run-scoped and operation-level meter support, Settings-backed plus in-memory/SQL-managed global/tenant/workspace/skill pricing rules for first-pass `cost_estimate` calculation, tenant-scoped pricing rule read/manage APIs, skill-backed runtime tool-call meters, worker startup loading for persisted pricing rules, first-pass `AuditService` redaction, `GET /api/audit-events/coverage` default enterprise audit coverage reports, FastAPI business audit write/list routing with request actor attribution, Agent Runtime model/tool/embedding audit routing with actor attribution, standalone knowledge API and run-scoped embedding usage meters, Tool Gateway service-level blocked/approval-required audit routing with actor attribution, tenant bootstrap completion audit routing, identity user/role audit routing, billing meter audit routing, run-cancellation/retry audit routing, approval-resolution/rejection audit routing, skill-publication audit routing, and first-pass run trace aggregation/export including runtime stage spans, sanitized guardrail findings, HTTP exporter boundary, timeline events, OTLP HTTP trace collector deployment verification, and install-validation ingestion of trace collector evidence.
- Dedicated `guardrails` package foundation has started with Pydantic rule, detector finding, and decision evaluation plus Settings-backed built-in secret-pattern, prompt-threat, and HTTP detector boundaries, Tool Gateway request enforcement, runtime retrieval-context enforcement, runtime model request/response enforcement, persisted model-planning guardrail approval resume, runtime artifact metadata and sandbox artifact content publication enforcement with persisted approval resume, long-term memory candidate guardrail approval review, SQL-backed short-term memory review queue approval/rejection, long-term/short-term memory-write enforcement, model planning latency and cached-token meters, and sanitized guardrail finding summaries in run traces. Distributed provider/rate-limit budget governance, remaining audit matrix event producers, bootstrap/identity/system actor attribution, provider-specific guardrail integration policy, and broader semantic threat coverage remain implementation work.

## Task 9: Create Enterprise Onboarding Seed Flow

**Files:**

- Create: `apps/api/src/taroai/onboarding/__init__.py`
- Create: `apps/api/src/taroai/onboarding/models.py`
- Create: `apps/api/src/taroai/onboarding/service.py`
- Create: `docs/operations/mvp-tenant-onboarding.md`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_mvp_enterprise_onboarding.py`

**Steps:**

1. Define tenant onboarding request/result models.
2. Seed one tenant, one owner, one workspace, default roles, starter quotas, starter knowledge space, and starter skill pack.
3. Make onboarding idempotent by tenant slug.
4. Add readiness check after seed.
5. Document manual PoC onboarding flow.

**Acceptance Criteria:**

- A pilot tenant can be created without ad hoc database edits.
- Default roles and starter resources are repeatable.

**Current Implementation Notes:**

- `/api/tenants/bootstrap` now seeds the first owner, `tenant_owner` role, starter workspace ID, starter knowledge space, and general starter skill pack behind `TAROAI_TENANT_BOOTSTRAP_TOKEN`; repeated requests for the same `tenant_slug` reuse the same starter resources.
- This is not yet the full onboarding flow: selectable industry starter packs, starter quotas enforcement, invite workflow, and runbook automation remain planned.

## Task 10: Add Minimal Workspace Frontend Slice

**Files:**

- Create: `docs/contracts/frontend-api-contract.md`
- Create: `docs/contracts/run-event-stream-contract.md`
- Create: `docs/contracts/creao-chat-ui-contract.md`
- Create: `docs/contracts/frontend-final-phase-handoff.md`
- Create: `apps/web/index.html`
- Create: `apps/web/assets/styles.css`
- Create: `apps/web/assets/main.js`
- Modify: `docs/plans/2026-07-01-08-client-portal-creao-ui.md`
- Modify: `infra/docker-compose.yml`
- Test: `tests/web/test_workspace_frontend_contract.py`

**Steps:**

1. Implement only the first chat workspace slice.
2. Call the real backend routes for run creation, execution, event replay, artifacts, and approvals.
3. Preserve CREAO requirements: `data-testid="chat-column"`, composer hint, Enter-to-send, Shift+Enter-new-line, and lower composer selector.
4. Package the static workspace with the local Compose PoC.
5. Keep full portal, admin console, skill marketplace, production SSO/MFA user flows, and streaming live browser display as later frontend phases while the local PoC keeps the static first-tenant bootstrap, Bearer login/logout controls, and storage-backed browser capture preview/download.

**Acceptance Criteria:**

- The local PoC has a usable workspace at `http://localhost:3000`.
- The workspace can create and execute runs through API routes instead of local product-flow fixtures.
- The workspace can bootstrap the first local tenant from the connection strip without persisting the bootstrap token.
- Timeline, terminal, artifacts, and approval controls are present on the first screen.
- Full portal work remains out of this slice.

## Task 11: Add Local Cloud-PoC Deployment

**Files:**

- Create: `infra/docker-compose.yml`
- Create: `apps/api/Dockerfile`
- Create: `apps/api/entrypoint.sh`
- Create: `docs/operations/mvp-local-cloud-poc.md`
- Modify: `.env.example`
- Test: `tests/api/test_settings.py`

**Steps:**

1. Add services for API, PostgreSQL, Redis, and MinIO.
2. Keep all config in `.env` and Pydantic settings.
3. Add migration runner command.
4. Add object storage smoke checks for upload, signed URL generation, and retention-aware delete.
5. Add health checks.
6. Document local startup and smoke-test flow.

**Acceptance Criteria:**

- A reviewer can start local dependencies consistently.
- Environment variables are documented and validated.

**Current Implementation Notes:**

- Local cloud PoC deployment is started with `infra/docker-compose.yml` for API, PostgreSQL, Redis, MinIO, and MinIO bucket initialization services, with health checks for long-running services.
- `apps/api/Dockerfile` and `apps/api/entrypoint.sh` are started; the entrypoint runs the existing migration runner when `TAROAI_RUN_MIGRATIONS=true`, and the API exposes `/healthz` plus `/readyz` with model gateway configuration readiness and sandbox provider/controller readiness.
- `.env.example` is now the Pydantic-backed local cloud PoC template for SQL-compatible local persistence, Redis short-term memory/job queue, MinIO object storage, auth/session, bootstrap, guardrails, model gateway, and trace settings; `local_process` remains a local PoC sandbox setting and is rejected by customer-operated Settings profiles and by `prod`/`production` environments, while dev request headers are explicitly disabled by default and rejected for `prod`/`production` plus customer-operated modes. The Settings profile gate also rejects in-memory backend defaults in `prod`/`production` and customer-operated modes, requiring SQL-backed control-plane/catalog stores plus Redis-backed short-term memory and job queues. It also rejects local secret-manager settings, non-enterprise sandbox providers, missing sandbox controller endpoints, missing/default/short sandbox controller API keys, default or local-PoC authentication secrets for `access_token_secret` and `password_hash_salt`, authentication/share-link secret values shorter than 32 characters, password hash iterations below `600000`, plus local/placeholder or short bootstrap, sandbox resolver, and browser-controller operator tokens, in those deployment contexts. `infra/config/cloud.env.example` is now production-ready by default with `TAROAI_ENVIRONMENT=production`, SQL-backed platform stores, Redis-backed short-term memory/job queues, `TAROAI_SANDBOX_PROVIDER=k8s`, `TAROAI_SANDBOX_CONTROLLER_BASE_URL`, `TAROAI_SANDBOX_CONTROLLER_API_KEY`, and an external secret-service backend.
- `docs/operations/mvp-local-cloud-poc.md` documents startup, alternate host-port startup, health/readiness checks, tenant bootstrap, login, OpenAI-compatible model provider verification, local PoC smoke verification, Docker sandbox provider verification, migration execution, Redis worker queue verification including expired-lease recovery, Redis short-term memory verification, MinIO/S3-compatible object storage verification, alternate-port dependency verifier commands, redacted verifier evidence output with `--output`, machine-readable `demo_ready` rollups, and shutdown. `taroai.model_gateway.verification` sends a real `/chat/completions` planning request to a configured OpenAI-compatible provider or provider-registry entry, requires a strict JSON plan containing the expected `planning.record` tool, supports `--providers-json` with the same shape as `TAROAI_MODEL_GATEWAY_PROVIDERS`, supports verification-only secret-ref values through `--secret-values-json`, excludes direct/provider API keys and verification-only secret values from config/result output, and reports the selected provider ID; private install validation also rejects model-gateway evidence that does not include `planning.record` in `planned_tool_names`, whose direct `base_url`/`model` does not match `/readyz.checks.model_gateway`, or whose provider-registry `provider_id` is not one of the configured provider IDs. It also rejects event-stream and audit-write evidence whose recorded `api_base_url` does not match the install validation `--api-base-url`, and requires matching `run_id` values when both event-stream and audit-write evidence are supplied, preventing stale or mixed-run evidence from completing the release gate. The OpenAI-compatible payload builder omits `tool_choice` when no tool schema is declared, avoiding provider-side rejection for text-JSON planning requests. The static workspace now keeps the CREAO-compatible chat selector contract while using URL-parameter connection prefill with URL secret scrubbing, Bearer login/logout, `/readyz` model/sandbox preflight display, recent workspace run history through `/api/runs`, run trace evidence through `/api/runs/{run_id}/trace`, runtime snapshots through `/api/runs/{run_id}/state`, storage-backed artifact text previews/downloads, storage-backed browser-capture preview/download with resolved storage object IDs, safe sandbox terminal summaries, and run polling through `GET /api/runs/{run_id}`, event replay, artifacts, and storage objects until a terminal status is reached. `taroai.deployment.local_cloud_poc_verification` now verifies API readiness, `/readyz.checks.sandbox.configured` before run creation, workspace HTML plus CREAO-compatible chat/composer selectors, frontend URL config prefill and URL secret scrubbing, frontend login controls, frontend run history controls, frontend run trace controls, frontend runtime state controls, frontend artifact preview controls, frontend browser-capture storage object contract, frontend `/readyz` preflight script behavior, Bearer-auth script behavior, bootstrap/login, tenant readiness, predictable model-gateway diagnostics when no provider is configured, direct sandbox command execution with storage-backed output URI, API browser screenshot upload to a `browser` storage object with PNG download through `/api/storage/objects/{id}/content`, real browser-controller navigation/extract behavior, optional browser-controller loading of the actual workspace through `--browser-workspace-url`, and optional Chromium-driven workspace bootstrap/login through `--browser-workspace-api-base-url` with UI `Tenant ready`, cleared bootstrap-token input, bootstrapped tenant/user/workspace ID sync, `Bearer` auth-status, plus UI preflight status/model/sandbox extraction; strict model execution with browser workspace verification now also requires the UI strip to reach `Preflight ready`, `Model ready`, and a loaded sandbox status (`Sandbox PoC: <provider>`, `Sandbox isolated: <provider>`, or legacy `Sandbox ready: <provider>`), and requires browser Workspace configuration to avoid one-sided URL/API-base inputs, requires the browser-visible API base URL whenever strict model execution loads the Workspace, and requires a submit message before `succeeded` can be accepted as UI execution evidence. A live Docker Compose smoke run has verified the Web/API/browser-controller/PostgreSQL/Redis/MinIO stack with temporary host ports, local_process sandbox command output stored in MinIO, API browser screenshot stored in MinIO and downloaded as PNG, and containerized Playwright extraction. When `--require-model-execution` is set, the verifier first checks `/readyz.checks.model_gateway` and fails before run creation if readiness reports missing model gateway fields. With a configured provider, the default run request asks the model to use `sandbox.command` to create `/workspace/artifacts/report.md`, then requires a succeeded run, the required `report.md` artifact, storage-object resolution and non-empty content download for every run artifact, the expected text in the required artifact, run events proving `sandbox.command.executed` with `exit_code=0` plus a `sandbox.artifact.promoted` event that matches the required artifact's downloaded storage object even when later extra artifacts are also promoted, and event payload safety that rejects raw `stdout` or `stderr` fields.
- The local cloud demo gate is now intentionally stricter than a basic health check: `demo_ready` requires both `local_smoke_ready` and `strict_model_ready`, `local_smoke_ready` requires downloadable storage-backed direct sandbox output, downloadable API browser screenshot evidence, browser session list/read/delete scope probes, browser extraction evidence, sandbox destroy confirmation, and post-destroy command rejection, and `strict_model_ready` now requires the run execution API to return HTTP 200 with no execution error code before accepting `succeeded`, promoted artifacts, billing, audit, trace, and safe-event evidence.
- Private install validation now has a `runtime_closed_loop` check that consumes the strict demo gate report through `--runtime-closed-loop-evidence`; cloud, production, and customer-operated acceptance fails without this evidence, requires `demo_ready`, `workspace_execution_ready`, `skill_reuse_ready`, and `browser_controller_governance_ready`, and additionally requires `sandbox_governance_ready` for production/customer-operated modes. This ties install acceptance to the actual Workspace -> Runtime -> sandbox command -> artifact -> event/trace evidence path and the enterprise skill reuse path instead of treating dependency health as the product demo.
- Runtime `plan.created` events now carry safe model route evidence (`provider`, `model`, `usage`, and provider-attempt status summaries without upstream error bodies), and the Workspace execution loop plus strict browser Workspace verifier surface and cross-check that route from either `plan.created` or `model.plan.created` so a demo can show which OpenAI-compatible provider actually planned the run. The same strict verifier now also cross-checks the model route strip after selecting a skill run from Run History when that selected run's API event stream contains planning-route evidence.
- The local cloud verifier's default strict model event fixture now includes `plan.created` before `sandbox.command.executed`, `sandbox.artifact.promoted`, and `run.succeeded`, so the browser event-integrity gate exercises the same planning-to-artifact order as Runtime.
- The Workspace event-integrity closure now renders the full planning-to-delivery chain as `plan -> command -> artifact -> succeeded` whenever `plan.created` evidence is present, renders selected skill runs with invocation evidence as `skill -> command -> artifact -> succeeded`, rejects selected skill-run event streams where `sandbox.command.executed` appears before `skill.workflow_invoked`, includes `browser.action.performed` in observed order for browser-assisted runs, and preserves the legacy command/artifact/success closure for older non-skill streams without planning events.
- Approval resolution in the Workspace now shows the approval decision together with `approval_id` and `resolved_by_user_id` when those event payload fields are available, keeping approval/rejection UI evidence traceable back to the run event stream.
- Strict browser Workspace verification now also checks that current-run thumbs feedback is persisted in `/api/customer-success/feedback` with matching `run_id` and `target_id`, that repeated missing-skill requests are persisted as enough `missing_skill` records for the configured solution pack and skill name, that evaluation candidate generation produces a pending `/api/customer-success/evaluation-candidates` record for the current run before human review is accepted, that accepted evaluation review updates the same API candidate to `accepted` with the Workspace-visible evaluation case ID, that solution-pack candidate generation produces a pending `/api/customer-success/solution-pack-candidates` record for the configured solution pack and skill name before draft review, that accepted solution-pack review updates that API candidate to `accepted` with the Workspace-visible publication draft ID, that the draft save/submit/approve/apply flow leaves `/api/customer-success/solution-pack-drafts` with an `applied`, production-applied draft carrying the configured skill manifest and pack version, that Workspace solution-pack installation is backed by `/api/solution-pack-installations` plus `/api/workspaces/{workspace_id}/skills` before installed UI status is accepted, and that the UI-reported skill invocation run belongs to the bootstrapped workspace, records the invoked skill as `agent_id`, and emits `skill.workflow_invoked` with the same `skill_id` before artifact/event evidence is accepted. The Customer Success loop is no longer accepted from UI status text alone.
- `scripts/verify-compose-strict-e2e.sh` now resolves effective model settings from both shell environment and `TAROAI_COMPOSE_ENV_FILE`, then fails before `docker compose up` when the strict gate lacks a direct API key, secret-ref credential, or provider registry configuration. It also passes the Compose browser-controller bearer token into the strict verifier by default, writes redacted strict-gate JSON to `TAROAI_COMPOSE_STRICT_E2E_OUTPUT` or `dist/local-cloud-poc-strict-e2e-result.json`, and then runs `scripts/verify-local-cloud-demo-ready.sh --require-workspace-execution --require-skill-reuse --require-browser-controller-governance` against that evidence file while writing the demo gate report to `TAROAI_COMPOSE_STRICT_E2E_DEMO_GATE_OUTPUT` or `dist/local-cloud-poc-demo-gate-result.json`. That report records `required_gates`, `failed_required_gates`, and `gate_results`, making release evidence explicit about whether workspace execution, skill reuse, browser-controller governance, and hardened sandbox governance were enforced and which required gates failed; malformed verifier evidence produces a machine-readable failed gate report with validation errors redacted for secret-shaped values, and final report writing plus stdout formatting apply the same redaction boundary. Setting `TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_OUTPUT` additionally runs `scripts/validate-install.sh`, exports the demo gate report as `TAROAI_RUNTIME_CLOSED_LOOP_EVIDENCE_PATH`, and passes it through `--runtime-closed-loop-evidence`, so strict Workspace -> Runtime -> sandbox -> artifact evidence can feed the private install report without making the default Compose gate depend on unrelated release evidence. Setting `TAROAI_COMPOSE_STRICT_E2E_REQUIRE_SANDBOX_GOVERNANCE=1` adds `--require-sandbox-governance` for hardened Docker/controller-backed sandbox profiles without breaking the local-process PoC. This keeps the DeepSeek profile useful for non-secret defaults without allowing an empty env-file key, missing browser-controller verifier token, missing browser-controller auth/TTL/session-limit evidence, missing hardened sandbox isolation/resource/TTL evidence when requested, or a false `demo_ready` rollup to spend release-gate time booting a stack that cannot demonstrate the closed loop.
- Strict model execution verification now also reads `/api/runs/{run_id}/state` and requires the runtime snapshot to report the succeeded status, sandbox session ID, completed steps, and promoted `/workspace/artifacts/report.md` path.
- Local cloud PoC browser-controller smoke verification now requires both the API browser screenshot storage object and the external browser-controller navigation/extract smoke before `local_smoke_ready` can pass. It deletes its external browser session after smoke and optional Workspace checks finish with the original tenant/workspace/run scope, then confirms the scoped deleted-session read returns 404 and the session is absent from the tenant session list; the HTTP browser adapter also confirms the deleted session is absent from the tenant session list before runtime cleanup is accepted. Browser-controller lifecycle evidence now records `session_read_scope_enforced` and `session_delete_scope_enforced` by probing the same tenant/session with a different workspace/run before the normal action probe. The same HTTP browser adapter now calls controller `GET /capabilities` before opening sessions, rejects controllers that do not declare auth/TTL/global/tenant/run capacity controls, checks global, tenant, and run session counts before `POST /sessions`, and local verifier auth evidence now records separate tenant session-list, global session-list, and `GET /capabilities` challenge fields while requiring all three unauthenticated browser-controller probes to be rejected. `/readyz.checks.browser` now also reads controller capabilities for enabled controller providers, reports auth/TTL/capacity/navigation declarations, and install validation rejects controller-required browser readiness unless `capabilities_checked=true` and the readiness capability fields declare auth, TTL, and global/tenant/run capacity controls; install validation also rejects browser-controller lifecycle evidence whose provider does not match `/readyz.checks.browser.provider`, so a Playwright deployment cannot pass with Browserbase evidence or the other way around. The local verifier config and result schemas reject unknown fields, so release-gate JSON cannot hide typos in browser/sandbox option or evidence names.
- Local cloud PoC direct sandbox smoke verification now destroys its sandbox session, requires the destroy response body to report `status=destroyed`, and probes the destroyed session with another command before recording `sandbox_post_destroy_command_blocked`; the demo gate now requires that lifecycle evidence when `--require-sandbox-governance` is enabled, so hardened sandbox acceptance cannot pass from capability declarations alone. Sandbox lifecycle install evidence now also records `session_destroy_confirmed` after re-reading tenant sessions, `post_destroy_command_blocked` after probing command execution on the destroyed session, and `file_read_scope_enforced` after probing `GET /files` with the same tenant/session but a different workspace/run. It fails validation if a destroyed session remains active, can still execute commands, or can list/download files across run scope. The HTTP sandbox adapter now checks declared global, tenant, and run capacity before controller-backed `POST /sessions`; it also rejects session creation when controller capabilities do not declare runtime isolation, image-policy enforcement, and at least one allowed image. The sandbox controller contract supports authenticated `GET /sessions` for the provider-visible global capacity view plus known-tenant fallback and `GET /sessions?tenant_id=...` for tenant-scoped capacity, so existing provider sessions still count after a controller restart. Lifecycle auth evidence now records separate tenant session-list, global session-list, and `GET /capabilities` challenge fields and requires all three unauthenticated probes to be rejected. The standalone sandbox controller rejects command, file-write, file-list/download, and snapshot operations after destroy before dispatching to the provider adapter, and the HTTP sandbox adapter also applies post-destroy active-list confirmation during runtime cleanup. The kubectl-backed Kubernetes provider reads back the ready Pod after creation so returned session metadata reflects the live Pod spec, confirms the pod list after destroy so a non-terminating session pod cannot be accepted as cleaned up, declares TTL enforcement, carries controller `session_ttl_seconds` into `max_session_ttl_seconds`, rejects over-TTL session creates before applying a Pod manifest, blocks command/file/list/download/snapshot operations after a tracked session expires, and only reports `image_policy_enforced=true` when the allowed-image patterns pass the same approved-registry/digest and non-`latest` policy used by session creation. Wildcard tag allowlists such as `registry.example.com/sandbox-runtime:*` now fail that policy and cannot produce release evidence. Kubernetes provider verification now also reads the actual sandbox session `serviceAccountName`, `runtimeClassName`, CPU/memory/ephemeral-storage limits, and run-as user/group, the sandbox-controller and sandbox-runner ServiceAccounts plus the sandbox-controller `Role` and `RoleBinding`, fails non-least-privilege RBAC, rejects runner ServiceAccount token automount, requires the RoleBinding to contain only the configured controller ServiceAccount subject in the verified namespace, and install validation rejects evidence that does not bind the configured controller ServiceAccount to the controller Role or whose verified sandbox session Pod ran in a namespace other than the verified runtime-policy namespace or used a ServiceAccount other than the verified runner. Customer-operated install validation also requires the lifecycle evidence provider to match `/readyz.checks.sandbox.provider`, with `k8s` and `kubernetes` treated as aliases, so a configured E2B install cannot pass with Kubernetes evidence or vice versa. Enterprise `/readyz` now surfaces controller capability evidence when the HTTP sandbox controller is configured, including runtime-isolation and image-policy declarations; install validation rejects controller-required sandbox readiness unless `capabilities_checked=true` and the readiness capability fields declare runtime isolation, image policy, network/filesystem isolation, resource controls, destroy support, TTL, and capacity limits, and `--require-sandbox-governance` requires those declarations plus at least one allowed image, so hardened sandbox release evidence cannot pass from generic isolation/TTL/capacity flags alone.
- Kubernetes provider verification now also reads the live per-session NetworkPolicy after the ready Pod is created and records its sandbox-session selector, policy types, and deny-all status. The verifier rejects evidence when the session selector does not match the created sandbox session, `Ingress`/`Egress` policy types are missing, or allow rules are present, so customer-operated release evidence cannot rely on the requested manifest alone for per-session network isolation.
- The current SQL repository layer accepts PostgreSQL URLs through the shared connection adapter, uses process-level psycopg pools configured through Pydantic settings, sets PostgreSQL tenant session context before tenant-scoped SQL, includes PostgreSQL-only RLS migration blocks for tenant-scoped tables, and has a live PostgreSQL migration/RLS verifier that runs against a non-superuser app role; the alternate-port Compose run verified RLS on tenant-scoped tables and confirmed cross-tenant workspace isolation plus no-context invisibility. Production migration release tooling remains planned.
- Redis worker queue verification now checks Redis ping, enqueue/claim/ack, expired-lease recovery, dead-letter movement, and prefix cleanup against the live Compose Redis service; Redis short-term memory verification checks TTL-backed write/read/list, tenant/run isolation, single-key delete, tenant delete, and cleanup behavior against the same service.
- MinIO/S3-compatible object storage verification now checks bucket access, upload, download byte comparison, signed URL generation, delete, and post-delete visibility through the same S3 adapter boundary used by API storage paths; the alternate-port Compose run verified those operations against the `taroai-artifacts` bucket.
- Kubernetes deployment manifests are started for the API, database migration Job, independently scalable agent, cleanup, connector sync, trigger due, trigger scheduler, restore drill due, restore drill execution, restore drill evidence, and restore drill scheduler workers, PostgreSQL, Redis, MinIO, MinIO bucket initialization, and namespace-scoped NetworkPolicy controls, using a shared non-secret runtime ConfigMap, placeholder-only runtime Secret example, and `infra/k8s/kustomization.yaml` entrypoint. Helm packaging has started with `infra/helm/taroai` chart metadata, values, API/worker/migration/config/external-secret-reference/ingress/HPA/service-account/network-policy templates, and defaults that avoid literal secret values. Deployment package manifest, config profile, licensing, install validation report, private upgrade/rollback, and air-gapped install work has started with Pydantic models, JSON Schema, operator README, deployment mode settings, cloud/BYOC/private env examples, air-gapped external-provider rejection, offline license file validation, Ed25519 signed offline license envelope validation, signed license import API, SQL-backed active license validation persistence through the control-plane store, connector-count, sandbox-concurrency, audit-retention, solution-pack-install, SSO-provider-config, and SCIM-provider/import runtime enforcement, entitlement decisions, license status/import audit events, private install validation runbook, upgrade matrix through `032_solution_pack_publication_draft_multi_manifest`, offline transfer constraints, strict release package build/sign/verify/report models that reject unknown option/evidence fields, atomic release zip, detached signature, and transfer-evidence writing that preserves any existing package/signature/evidence if output fails, release package build-time source scanning for secret-shaped keys/private-key blocks/credentialed URLs before an archive is written, release package signature verification, restore drill evidence intake, a restore drill evidence builder that derives the install validation JSON from restored-environment verifier outputs, and first-pass scheduled restore drill due-job generation plus SQL-backed due-worker request-record intake and restore-drill execution worker verifier handoff and evidence worker ingestion, including safe skips for disabled schedules, duplicate schedule timestamps, and store-level duplicate run-record writes, with control-plane APIs for schedule and run-record review. Ingress/TLS configuration, cloud-managed service overlays, autoscaling rollout policy, OIDC/SAML login, full SCIM v2 service-provider compatibility, MFA, customer restore environment orchestration, customer-specific compatibility expansion, and live cluster startup verification remain planned.

## Task 12: MVP End-to-End Acceptance Scenario

**Files:**

- Create: `tests/api/test_mvp_end_to_end.py`
- Create: `docs/mvp/acceptance-scenario.md`

**Steps:**

1. Onboard tenant.
2. Login as owner or pilot user.
3. Create run with workspace and message.
4. Retrieve knowledge context.
5. Execute a plan with one safe tool and one approval-required tool.
6. Approve the risky step.
7. Create artifact.
8. Read run events, artifacts, billing, audit, and trace.
9. Verify cross-tenant reads fail.
10. Document expected output.

**Acceptance Criteria:**

- One test proves the MVP value path from tenant setup to governed agent output.
- Cross-tenant and approval gates are part of the happy-path scenario.

**Current Implementation Notes:**

- `tests/api/test_mvp_end_to_end.py` now proves the API-level MVP path: tenant bootstrap, owner Bearer login, readiness check, knowledge base/document creation, ACL-aware knowledge query, run creation, OpenAI-compatible gateway planning, `sandbox.command` artifact generation under `/workspace/artifacts/`, approval-required notification pause, approval resume, artifact download, run event stream, billing meters, audit events, trace retrieval, and cross-tenant read rejection.
- `docs/mvp/acceptance-scenario.md` documents the same acceptance flow, expected evidence, and boundary between API-level acceptance, live Compose smoke verification, and the strict live model gate that still requires a real configured provider.

## Review Gates Before Starting Implementation

- Confirm MVP sandbox provider path: `local_process` for local cloud PoC, `docker` for a first-pass Settings-hardened disabled-network container adapter where the daemon is explicitly available, and Kubernetes, E2B, or microVM-backed execution for shared enterprise isolation.
- Confirm full portal work remains deferred beyond the minimal static workspace slice.
- Confirm first knowledge backend: internal retrieval contract first, then selected durable vector backend.
- Confirm auth scope: password PoC plus dev headers, or password PoC only.
- Confirm first starter pack: general, ecommerce, sales, support, or operations.
- Confirm whether OpenAPI `/api/v1` migration happens before generated SDK or full portal work.

## Verification

Run after each task:

```bash
python -m pytest -q
```

Run before declaring the MVP milestone ready:

```bash
python -m pytest tests/api/test_mvp_end_to_end.py -q
python -m pytest -q
```

Expected final result: a cloud PoC tenant can be onboarded through backend/API contracts, a user can create and execute a governed agent run, approvals and artifacts work, audit/billing/trace records exist, and the local Workspace UI can display the first execution loop.

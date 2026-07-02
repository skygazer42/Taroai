# MVP Cloud PoC Execution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current in-memory backend foundation and 01-25 plan set into a concrete backend-first MVP cloud PoC execution sequence for the first enterprise Agent Workspace release.

**Architecture:** Keep the current modular package layout and Pydantic management boundaries. Product flow must call real service boundaries: Model Gateway uses an OpenAI-compatible interface, Tool Gateway owns tool execution, and Sandbox Adapter owns virtual environment execution. Local contract fixtures stay under `tests/` or contract verification and are never runtime defaults or MVP business-flow dependencies.

**Tech Stack:** FastAPI, Pydantic, pytest, OpenAI-compatible Model Gateway contract, PostgreSQL, Redis, S3/MinIO, frontend contracts later, LangGraph later, sandbox adapter, OTel-compatible traces.

---

## Summary

This plan is the first implementation milestone derived from `2026-07-01-25-roadmap-coverage-matrix.md`.

It assumes the current repo state already includes:

- `apps/api/src/taroai/app.py` with run creation, run read, SSE events, execute, approval approve/reject, artifacts, billing, and audit endpoints.
- `apps/api/src/taroai/api/errors.py` with centralized API error mapping.
- Pydantic settings in `apps/api/src/taroai/config.py`.
- `apps/api/src/taroai/model_gateway/` with OpenAI-compatible request/response models and runtime boundary wiring.
- `apps/api/src/taroai/tool_gateway/` with Pydantic request/policy/result models, scope checks, approval-required decisions, and runtime context invocation.
- `apps/api/src/taroai/knowledge/` with Pydantic knowledge base/document/chunk/retrieval models, in-memory and SQLite-compatible registration, managed source-content object storage references, ACL-aware retrieval, citations, and API endpoints.
- `apps/api/src/taroai/sandbox/` with Pydantic sandbox/browser models, disabled default sandbox/browser provider boundaries, Tool Gateway command handler, config fields, API endpoints for session create, command execution, file upload/download, snapshot, destroy, and browser actions, sandbox/browser API permission checks, session/command/file/snapshot/destroy/browser audit metadata, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload, command `sandbox_minutes` metering, file-upload `artifact_bytes` metering, and browser action `browser_action_count` metering. Local contract adapters exist only under `tests/` for isolated contract coverage.
- `apps/api/src/taroai/db/` with Pydantic database config, migration runner, runtime state table, SQLite-compatible SQL repository tests for run/event/status/artifact/approval/meter/audit/runtime-state persistence, and settings-based FastAPI plus worker runner wiring for the SQL control-plane store.
- `apps/api/src/taroai/workers/` with Pydantic run execution, billing aggregation, and cleanup job contracts, queue claim/ack/fail/reject lifecycle tests, retry/dead-letter policy, Redis-backed queue adapter boundary, configurable API enqueue mode for run execution, worker job lifecycle audit events with actor attribution, agent worker runner/entrypoint with default runtime Tool Gateway handlers, and cleanup worker runner wiring for storage lifecycle cleanup.
- `apps/api/src/taroai/auth/` with signed PoC access tokens, `/api/auth/login`, `/api/auth/logout`, Bearer request-context resolution, disabled-user rejection, dev request headers behind settings, and SQL-backed identity/session service selection through Pydantic settings.
- `apps/api/src/taroai/onboarding/` with Pydantic tenant readiness models/service and `/api/tenants/current/readiness` for authenticated readiness checks.
- `apps/api/src/taroai/memory/` with Pydantic short-term TTL memory, Redis-backed short-term put/get/list/delete with TTL, long-term scoped memory, and SQLite-compatible SQL long-term memory persistence selectable through settings.
- `apps/api/src/taroai/storage/` with Pydantic tenant-scoped metadata catalog, SQLite-compatible SQL metadata catalog, S3/MinIO-compatible object storage adapter boundary, signed URL contract, upload/download/delete contracts, storage read/write permission checks, object content upload/download APIs, internal tenant/workspace-scoped objects for platform assets, object ACL/sensitivity metadata with read-side enforcement, configurable upload content scanning, retention-aware object delete API, first-pass expired-object cleanup lifecycle service with preview mode, knowledge document source object upload, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload, upload billing/audit records, rejected-content audit records, download audit records, signed URL audit records, and delete audit records.
- `apps/api/src/taroai/lifecycle/` with Pydantic data category, deletion behavior, lifecycle policy, legal hold, legal-hold scope, data export manifest/bundle, and backup manifest models, in-memory and SQLite-compatible SQL stores, tenant default plus workspace override policy resolution, Settings-backed worker wiring, active legal-hold checks for storage cleanup, FastAPI management APIs, storage cleanup preview API, storage-object export manifest/bundle APIs, backup manifest API, RBAC permissions, and audit metadata without raw legal-hold reason text, export item details, or backup component details.
- Modular backend packages for `agent`, `memory`, `skills`, `storage`, `lifecycle`, and `identity`.
- Runtime state snapshot persistence in the current in-memory store for approval resume and rejection recovery.
- In-memory services and migration contract tests.
- Test suite under `tests/api`.

The MVP outcome is not a full enterprise platform. It is a cloud PoC where a tenant can onboard, create a run, retrieve governed context, execute through a bounded runtime and sandbox seam, produce artifacts, pause for approval, accept approval to resume, reject approval to fail safely, and expose audit/billing events to an admin.

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
- `/api/tenants/bootstrap` is available for the first owner seed when `TAROAI_TENANT_BOOTSTRAP_TOKEN` is configured; it creates the owner account, seeds `tenant_owner` permissions, and records safe bootstrap audit metadata.
- `/api/tenants/current/readiness` returns readiness through the authenticated request context.
- Full tenant onboarding orchestration, starter workspace and starter pack seeding, production PostgreSQL identity rollout, SSO/OIDC/SAML, SCIM, MFA, and support-access controls remain implementation work.

**Acceptance Criteria:**

- PoC login can replace manual headers.
- Enterprise SSO can later plug into the same request-context resolution.
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
- Long-term scoped memory has in-memory and SQLite-compatible SQL implementations behind the same `write` and `list_by_scope` service shape.
- `TAROAI_LONG_TERM_MEMORY_BACKEND=sql` selects SQL long-term memory in the FastAPI app.
- Short-term memory has in-memory and Redis-backed implementations behind the same `put` and `get` service shape.
- `TAROAI_SHORT_TERM_MEMORY_BACKEND=redis` selects Redis-backed short-term memory in the FastAPI app.
- Memory candidate creation, approve/reject review, active scoped reads, API endpoints, and audit metadata emission are started.
- Agent Runtime context loading is started for ACL/sensitivity-filtered knowledge and approved long-term memory. `context.loaded` events expose counts and IDs, not context content.
- Retrieval-stage guardrails are applied to runtime knowledge excerpts before model planning; blocked or approval-required excerpts are excluded, redacted excerpts are rewritten, and summary-only audit metadata records the rule/action/resource IDs.
- Runtime proposal wiring, richer review policy, live Redis deployment verification, advanced context policy, and context quality evaluation remain implementation work.

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
- Dev-mode tool invocation endpoint and connector-backed tools remain implementation work.

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

- Runtime planning uses the OpenAI-compatible Model Gateway boundary, records model usage billing/audit when usage is returned, blocks exhausted run/tenant/workspace/user/agent model budgets before provider calls, enforces first-pass Settings-backed plus API/SQL-managed global and tenant/workspace-scoped allowed/denied model lists and scoped default models, enforces model-request/model-response guardrails with safe audit metadata, records safe `model.policy_denied` audit metadata for policy rejections, and records safe `model.gateway_failed` audit metadata while failing the run when gateway configuration or response errors occur.
- Provider references, sensitivity constraints, fallback routing, rate limits, budget windows, policy versioning/approval workflow, and live trace collector deployment verification remain implementation work.

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
3. Keep default sandbox/browser providers disabled until real providers are approved; test code can inject local contract adapters from `tests/`, but product runtime never imports or defaults to them.
4. Add tests for tenant scoping, command result, file handoff, browser action permission/audit/billing records, and session cleanup marker.

**Acceptance Criteria:**

- MVP has a virtual environment seam without committing to one provider.
- Sandbox outputs can become artifacts.

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
- Tool Gateway policy-required approvals now pause runtime execution, approval rejection fails the run without executing the pending step, and failed runtime tool calls emit `tool.failed` audit records with sensitive input redaction.
- Tool Gateway service calls can emit `tool.blocked` and `tool.approval_required` audit records through injected `AuditService` before blocked or approval-gated handlers run, with sensitive input redaction.
- Tool Gateway service calls can enforce guardrail block, approval-required, and redaction decisions before handler execution, and default API/worker runtime Tool Gateways receive a guardrail service.
- Agent Runtime can enforce retrieval-stage guardrail decisions before model planning and records safe `guardrail.retrieval_blocked` or `guardrail.retrieval_approval_required` audit metadata when guarded excerpts are excluded.
- Agent Runtime can enforce model-request and model-response guardrail decisions around the OpenAI-compatible Model Gateway boundary, redacting guarded content before provider calls or planned-step execution and failing runs on blocked model guardrails without storing prompt/response text in audit.
- Agent Runtime can enforce artifact-stage guardrail decisions before generated run artifact creation, redacting guarded artifact metadata, pausing and resuming persisted approval for approval-gated artifact publication, or failing the run on blocked artifact publication without storing raw artifact names or URIs in guardrail audit.
- Long-term and short-term memory services can enforce memory-write guardrail decisions before persistence, redacting guarded memory content, holding approval-required long-term candidate writes and short-term run-memory writes for review, or rejecting blocked writes through unified API errors and summary-only audit metadata.
- Dedicated `billing`, `audit`, and `observability` packages have started with billing filters/summaries, first-pass `AuditService` redaction, `GET /api/audit-events/coverage` default enterprise audit coverage reports, FastAPI business audit write/list routing with request actor attribution, Agent Runtime model/tool audit routing with actor attribution, Tool Gateway service-level blocked/approval-required audit routing with actor attribution, tenant bootstrap completion audit routing, identity user/role audit routing, billing meter audit routing, approval-resolution/rejection audit routing, skill-publication audit routing, and first-pass run trace aggregation/export including runtime stage spans, sanitized guardrail findings, HTTP exporter boundary, and timeline events.
- Dedicated `guardrails` package foundation has started with Pydantic rule, detector finding, and decision evaluation plus Settings-backed built-in secret-pattern, prompt-threat, and HTTP detector boundaries, Tool Gateway request enforcement, runtime retrieval-context enforcement, runtime model request/response enforcement, persisted model-planning guardrail approval resume, runtime artifact publication enforcement with persisted approval resume, long-term memory candidate guardrail approval review, SQL-backed short-term memory review queue approval/rejection, long-term/short-term memory-write enforcement, and sanitized guardrail finding summaries in run traces. Live trace collector deployment verification, provider/rate-limit budget governance, latency/cached-token model metering, skill-specific meters, remaining audit matrix event producers, bootstrap/identity/system actor attribution, retention metadata, provider-specific guardrail integration policy, broader semantic threat coverage, and production PostgreSQL hardening for review storage remain implementation work.

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

- A minimal `/api/tenants/bootstrap` API now seeds the first owner and `tenant_owner` role behind `TAROAI_TENANT_BOOTSTRAP_TOKEN`.
- This is not yet the full onboarding flow: workspace seed, starter knowledge spaces, starter skill packs, idempotency-by-slug, and runbook automation remain planned.

## Task 10: Freeze Frontend Contract and Defer Implementation

**Files:**

- Create: `docs/contracts/frontend-api-contract.md`
- Create: `docs/contracts/run-event-stream-contract.md`
- Create: `docs/contracts/creao-chat-ui-contract.md`
- Create: `docs/contracts/frontend-final-phase-handoff.md`
- Modify: `docs/plans/2026-07-01-08-client-portal-creao-ui.md`
- Test: `tests/api/test_openapi_contract.py`

**Steps:**

1. Do not create frontend application files in this MVP milestone.
2. Document the future frontend API contract for runs, events, approvals, artifacts, billing, audit, skills, knowledge, and readiness.
3. Preserve future CREAO requirements: `data-testid="chat-column"`, composer hint, Enter-to-send, Shift+Enter-new-line, and lower composer selector.
4. Document final-phase handoff gates: stable API contracts, event stream contract, auth/session contract, artifact contract, approval contract, and explicit human approval.
5. Add backend contract tests for the API/event shape needed by the future frontend.

**Acceptance Criteria:**

- No frontend app is scaffolded in this MVP milestone.
- Future UI requirements are preserved as backend/API contracts.
- Frontend implementation waits for the final user-managed phase.

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

## Review Gates Before Starting Implementation

- Confirm MVP sandbox provider seam: contract-only until a real provider is selected, E2B-first, or Kubernetes Docker-first.
- Confirm frontend remains deferred to the final user-managed phase.
- Confirm first knowledge backend: internal retrieval contract first, then selected durable vector backend.
- Confirm auth scope: password PoC plus dev headers, or password PoC only.
- Confirm first starter pack: general, ecommerce, sales, support, or operations.
- Confirm whether OpenAPI `/api/v1` migration happens before the final frontend phase.

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

Expected final result: a cloud PoC tenant can be onboarded through backend/API contracts, a user can create and execute a governed agent run, approvals and artifacts work, audit/billing/trace records exist, and the future frontend has a frozen contract for the final user-managed phase.

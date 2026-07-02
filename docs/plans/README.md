# Taroai Plans Index

These plans should be read and implemented in order.

Before expanding any plan that references LangGraph, LangChain, LlamaIndex, MCP, E2B, pgvector, Redis, PostgreSQL RLS, Next.js, OpenTelemetry, or other external terms, read `research-grounding.md` and verify the current code state. Planned adapter seams must not be described as implemented capabilities.

Review artifacts:

- `research-grounding.md`  
  Official-source terminology notes and current-code grounding for framework, protocol, sandbox, storage, observability, and frontend terms.

- `review-readiness-audit.md`  
  Requirement-by-requirement evidence map for plan coverage, current-code facts, open decisions, and review readiness.

- `completion-audit.md`  
  Final audit that distinguishes completed plan-writing scope from open human review decisions.

- `mvp-review-packet.md`  
  Short review packet for approving or revising the MVP cloud PoC scope.

- `review-status.md`  
  Current status for each plan and review group.

- `review-decisions.md`  
  Accepted/rejected/superseded decisions from plan review.

- `open-questions.md`  
  Open decisions that must be answered before MVP milestone approval or later milestones.

1. `2026-07-01-01-product-logic.md`  
   Product logic, enterprise-vs-consumer positioning, run lifecycle, client surfaces, skills, memory, billing, approvals, and self-evolving boundaries.

2. `2026-07-01-02-technical-architecture.md`  
   Overall architecture, service boundaries, API contract, data model, agent loop, sandbox strategy, deployment, and acceptance criteria.

3. `2026-07-01-03-storage-identity-memory.md`  
   PostgreSQL/Redis/S3 placement, password hashing, RBAC, short-term memory, long-term memory, storage catalog, and API endpoints.

4. `2026-07-01-04-knowledge-rag-memory.md`  
   Knowledge package, document ingestion, ACL-aware retrieval, candidate LlamaIndex adapter, memory candidate review, and runtime context loading.

5. `2026-07-01-05-skills-tool-gateway.md`  
   Skill manifest validation, skill registry persistence, Tool Gateway, policy, approvals, billing, audit, and skill APIs.

6. `2026-07-01-06-agent-runtime-sandbox.md`  
   Runtime state persistence, sandbox adapter, sandbox execution, browser session seam, bounded multi-agent delegation, and candidate LangGraph execution path.

7. `2026-07-01-07-billing-audit-observability.md`  
   Billing service, pricing, append-only audit, trace spans, cross-service integration, and admin query APIs.

8. `2026-07-01-08-client-portal-creao-ui.md`  
   Frontend contract and final-phase UI handoff for CREAO-compatible chat, run timeline, artifacts, admin console, skill marketplace, accessibility, and responsive tests. No frontend implementation in the current milestone.

9. `2026-07-01-09-deployment-operations.md`  
   Docker Compose, API container, migration runner, Kubernetes cloud PoC manifests, worker separation, and operations runbooks.

10. `2026-07-01-10-security-compliance.md`  
    Policy package, tenant isolation, secrets boundary, data classification, tool/network guardrails, and compliance audit coverage.

11. `2026-07-01-11-testing-release-quality.md`  
    Test taxonomy, backend and frontend quality gates, integration harness, CI pipeline, release checklist, and rollback documentation.

12. `2026-07-01-12-self-evolving-evaluations.md`  
    Evaluation service, failure taxonomy, improvement candidates, approval flow, versioned publication, eval APIs, and hard safety boundaries.

13. `2026-07-01-13-enterprise-onboarding.md`  
    Enterprise tenant onboarding, default roles, SSO/SCIM planning, starter packs, quotas, readiness checks, and rollout runbooks.

14. `2026-07-01-14-api-sdk-contracts.md`  
    API versioning, error model, pagination, idempotency, run event streaming, OpenAPI export, SDK shape, and webhooks.

15. `2026-07-01-15-enterprise-connectors.md`  
    Enterprise SaaS/database/internal API/MCP connectors, credential boundaries, sync jobs, ACL mapping, Tool Gateway integration, audit, and billing.

16. `2026-07-01-16-sharing-collaboration-artifacts.md`  
    Share grants, artifact delivery, run collaboration, output promotion, sharing UX, audit, billing, and retention controls.

17. `2026-07-01-17-model-gateway-provider-governance.md`  
    Model Gateway, provider adapters, tenant model policy, credential boundaries, budgets, rate limits, metering, fallback, and runtime integration.

18. `2026-07-01-18-triggers-scheduling-automation.md`  
    Schedules, webhooks, API triggers, connector event triggers, agent-to-agent handoffs, trigger admin APIs, audit, and billing.

19. `2026-07-01-19-agent-builder-workflow-templates.md`  
    Agent templates, workflow graph model, input forms, preview runs, publication, run-to-template conversion, and builder API/UI contracts.

20. `2026-07-01-20-data-lifecycle-backup-recovery.md`  
    Data inventory, lifecycle policy, retention jobs, tenant export, offboarding, backup/restore, disaster recovery, and data residency.

21. `2026-07-01-21-prompt-guardrail-governance.md`  
    Prompt registry, prompt versioning, variable/secret handling, guardrail rules, prompt-injection checks, runtime/model integration, and publication gates.

22. `2026-07-01-22-incident-slo-support-operations.md`  
    Incident models, SLO/error budgets, alert routing, run quarantine, kill switches, support access, and postmortem improvement linkage.

23. `2026-07-01-23-solution-packs-customer-success.md`  
    Solution pack registry, tenant seeding, industry baselines, rollout playbooks, training assets, adoption metrics, and feedback loops.

24. `2026-07-01-24-private-deployment-packaging.md`  
    BYOC/private package manifest, config profiles, Helm path, license entitlements, install validation, upgrades, rollback, and air-gapped runbooks.

25. `2026-07-01-25-roadmap-coverage-matrix.md`  
    Review roadmap, source-requirement coverage matrix, phased execution order, dependency map, MVP recommendation, and evidence gates.

26. `2026-07-01-26-mvp-cloud-poc-execution.md`  
    First backend-focused implementation milestone for the cloud PoC: route contracts, persistence, auth, knowledge/memory, skill/tool gateway, model gateway, sandbox seam, governance hooks, onboarding, frontend contract freeze, deployment, and end-to-end acceptance.

27. `2026-07-01-27-plan-review-approval-workflow.md`  
    Plan review status model, review groups, reviewer checklist, decision log, open questions, MVP approval gate, change control, and implementation handoff.

Current implementation status:

- Phase 1 API foundation is started.
- Agent runtime foundation is started, including runtime state snapshots for approval resume/rejection after process-local pending state is lost, SQL-backed approval/status/artifact persistence through the control-plane repository, and persisted model-planning plus artifact-publication guardrail approval resume.
- OpenAI-compatible Model Gateway contract and runtime boundary are started, including runtime planning `model_call_count`, input-token, output-token, safe model-plan audit records when usage is returned, first-pass Settings-backed plus API/SQL-managed global and tenant/workspace-scoped allowed/denied model policy plus scoped default models, safe model-policy admin audit records, safe `model.policy_denied` audit records for policy rejections, run/tenant/workspace/user/agent model call/token budget guard, safe `model.gateway_failed` audit records when gateway configuration or response failures fail a run, and model request/response guardrail enforcement at the runtime gateway boundary.
- Tool Gateway foundation is started with Pydantic request/policy/result/audit-record/secret-requirement models, registered-handler execution, scope checks, approval-required decisions, input/output schema validation, runtime context invocation, short-lived secret lease injection, unified API error mapping, service-level `tool.blocked` and `tool.approval_required` audit recording through injected `AuditService` with sensitive input redaction, guardrail block/approval/redaction enforcement before handler execution, runtime policy-approval pause, successful tool-call audit/billing records, and failed runtime tool-call audit with sensitive input redaction.
- Knowledge/RAG foundation is started with Pydantic models, in-memory and SQLite-compatible SQL knowledge metadata/chunk persistence, tenant-scoped knowledge deletion, ACL-aware retrieval, citation results, API endpoints behind `knowledge.write`/`knowledge.read`, safe audit events for base/document/query operations, and Agent Runtime context loading with sanitized `context.loaded` events plus retrieval guardrail filtering before model planning.
- Sandbox/Browser foundation is started with Pydantic session/command/file/snapshot/browser models, disabled default sandbox/browser provider boundaries, Tool Gateway command handler, API endpoints for session create, command execution, file upload/download, snapshot, destroy, and browser actions, `sandbox.create`/`sandbox.execute`/`browser.act` permission checks, session/command/file/snapshot/destroy/browser audit metadata, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload with non-serialized screenshot bytes, `sandbox_minutes` command metering, `artifact_bytes` file-upload metering, and `browser_action_count` metering when a run exists. Local contract adapters stay under `tests/` and are injected only by tests; browser action audit records typed-text presence and length without raw typed text.
- Secrets boundary foundation is started with Pydantic `SecretRef`, `SecretLease`, and `SecretScope` models, scoped short-lived lease checks, Tool Gateway short-lived lease injection before handler execution, long-lived values kept out of model dumps, and audit metadata that excludes secret values and lease tokens.
- Policy foundation is started with Pydantic `PolicyRequest`, `PolicyDecision`, and `PolicyEffect` models, an `IdentityPolicyService` that centralizes first-pass RBAC decisions, and FastAPI tenant-operation permission checks routed through `app.state.policy_service`.
- Guardrail foundation is started with Pydantic stage/action/severity/rule/condition/evaluation/decision/finding models, tenant/workspace-scoped in-memory rule evaluation for allow, warn, redact, approval-required, block, and quarantine decisions, Settings-backed built-in secret-pattern, prompt-threat, and HTTP guardrail detector boundaries, Tool Gateway request enforcement, Agent Runtime retrieval-context enforcement, model request/response enforcement, persisted model-planning guardrail approval resume, artifact publication enforcement with persisted approval resume, long-term memory candidate guardrail approval review, SQL-backed short-term memory review queue approval/rejection, long-term/short-term memory-write enforcement with safe audit metadata, and sanitized guardrail finding summaries in run traces, plus default API/worker runtime guardrail wiring. Remaining guardrail work: provider-specific guardrail integration policy, broader semantic threat coverage, and production PostgreSQL hardening for review storage.
- Audit foundation is started with Pydantic `AuditEventCreate`, `AuditActor`, `AuditResource`, `AuditAction`, and audit coverage matrix/report models, an `AuditService` that records through the control-plane store, recursive sensitive metadata redaction, defensive audit-event copies, request actor attribution for FastAPI business audit writes, Agent Runtime, Tool Gateway, and worker job actor attribution from tenant/user context, tenant coverage checks against default enterprise requirements, `GET /api/audit-events/coverage` behind `audit.read`, FastAPI business audit writes/list reads routed through `app.state.audit_service`, Agent Runtime model/tool audit events, Tool Gateway service-level blocked/approval-required audit events, worker job started/succeeded/failed audit events, tenant bootstrap completion audit events, identity user/role lifecycle audit events, billing meter audit events, approval-resolution/rejection audit events, and skill-publication audit events through injected service boundaries.
- DB persistence foundation is started with Pydantic database config, migration runner, runtime state table, SQLite-compatible SQL repository coverage for run/event/status/artifact/approval/meter/audit/runtime-state persistence, and settings-based FastAPI app plus worker runner wiring for the SQL control-plane store.
- Worker/queue foundation is started with Pydantic run execution, billing aggregation, and cleanup job contracts, queue claim/ack/fail/reject lifecycle tests, retry/dead-letter policy, Redis-backed queue adapter boundary, configurable API enqueue mode for run execution, worker job lifecycle audit events with actor attribution, a Pydantic agent worker runner/entrypoint with settings-based store selection, injected `AuditService`, and default runtime tool handlers, plus a cleanup worker runner for storage lifecycle cleanup.
- Storage foundation is started with a tenant-scoped metadata catalog, SQLite-compatible SQL metadata catalog wired through Pydantic settings, S3/MinIO-compatible object storage adapter boundary, upload/download/delete/signed URL contracts, FastAPI endpoints for metadata registration, run-scoped listing, signed URL creation, object content upload with declared-size validation, object content download, tenant/workspace-scoped internal objects, object `acl_subjects`/`sensitivity_level` with read-side enforcement, configurable upload content scanning, knowledge document source object upload, retention-aware object delete, first-pass expired-object cleanup lifecycle service with preview mode, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, and browser screenshot object upload. Storage endpoints enforce `storage.read`/`storage.write`; upload writes through the object storage adapter and records `storage_bytes` billing plus `storage.uploaded` audit metadata without raw object content; rejected uploads record `storage.content_rejected` metadata without raw content or rule text; download reads through the object storage adapter and records `storage.downloaded` audit metadata without raw object content; signed URL creation records `storage.signed_url.created` audit metadata without the generated URL; delete writes metadata tombstones and `storage.deleted` audit metadata; cleanup deletes expired active objects through the adapter, skips active legal holds, supports preview without adapter delete or metadata tombstone, and records system audit metadata. Tenant-wide billing/audit/trace reads now require `billing.read`/`audit.read`; billing reads support first-pass filters and grouped summaries through `apps/api/src/taroai/billing/`, and run trace aggregation/export with first-pass `TraceSpan`/`TraceEvent` entries, runtime stage spans, sanitized guardrail findings, HTTP exporter boundary, and error classification has started through `apps/api/src/taroai/observability/`.
- Lifecycle governance foundation is started with Pydantic data category, deletion behavior, lifecycle policy, legal hold, data export manifest/bundle, backup manifest, data residency report, and tenant offboarding plan models plus in-memory and SQLite-compatible SQL stores selectable through Settings, tenant default plus workspace override policy resolution, FastAPI lifecycle policy/legal-hold/export/backup-manifest/data-residency/offboarding APIs, storage cleanup preview API, RBAC permissions, storage-object export manifests, tenant/workspace/run scoped JSON export bundles uploaded through the object storage adapter, safe backup manifests without raw connection strings, data residency settings for primary/allowed regions and replication mode, tenant offboarding request planning with legal-hold blocking, approval advancement, approved tenant-scoped export bundle execution, first-pass storage-object deletion execution, first-pass memory tombstone/delete execution, first-pass knowledge metadata/chunk deletion execution, and audit events that omit raw legal-hold reason text, export item details, backup component details, full residency check details, raw offboarding reason text, deleted storage object IDs, deleted memory record IDs, deleted knowledge document IDs, and legal-hold IDs.
- Identity/auth/onboarding foundation is started with Pydantic identity services, password hashing, disabled-user handling, SQL-backed users/roles/role assignments through settings, signed PoC access tokens with server-side session IDs, `/api/auth/login`, `/api/auth/logout`, Bearer request-context resolution, SQL-backed auth session persistence/revocation when SQL identity is used, dev request headers behind settings, and token-protected `/api/tenants/bootstrap` for first-owner seed.
- Tenant readiness foundation is started with Pydantic readiness report/check models, owner/role/auth/quota/audit/billing/storage/queue checks, and `/api/tenants/current/readiness` behind authenticated request context. Skill registry foundation is started with tenant-scoped lifecycle records, tenant/department/workspace/private visibility filtering, version history lookup, workspace install/enable/disable records, SQLite-compatible SQL persistence through settings, `/api/skills` register/list/get/publish/disable endpoints, `GET /api/skills/{skill_id}/versions`, and workspace skill install/list/enable/disable endpoints behind identity permissions. Memory foundation is started with Pydantic short-term TTL memory, Redis-backed short-term put/get/list/delete with TTL through settings, long-term scoped memory, candidate approve/reject review, memory read/write/review APIs with audit metadata, runtime context loading, and SQLite-compatible SQL long-term memory persistence through settings.
- The plan set now covers product logic, technical architecture, storage/identity/memory, knowledge/RAG, skills/tool gateway, agent runtime/sandbox, billing/audit/observability, client portal UI, deployment, security, quality gates, self-evolving evaluations, enterprise onboarding, API/SDK contracts, enterprise connectors, sharing/collaboration, model gateway, triggers/scheduling, builder/workflow templates, data lifecycle/DR, prompt/guardrail governance, incident/SLO operations, solution packs/customer success, private deployment packaging, roadmap/coverage review, MVP cloud PoC execution, and plan review/approval workflow.
- Production PostgreSQL adapter rollout, SSO/OIDC/SAML, SCIM, MFA, full tenant onboarding orchestration and starter pack seeding, live MinIO/S3 deployment verification, IdP/SCIM-backed storage subject mapping, multipart upload, production DLP/AV scanning adapters, non-storage lifecycle cleanup categories beyond memory, tenant-wide asynchronous export orchestration, broader offboarding deletion orchestration, backup execution, restore drills, Redis-backed queue deployment verification, worker process deployment manifests, production PostgreSQL connection pooling/migrations tooling, full Knowledge/RAG integrations for durable connector ingestion, embeddings, vector backend, connector sync, and advanced context policy, full Tool Gateway integrations for connectors, external secret manager integration, automatic Sandbox credential lease injection, skill-specific meters, remaining audit matrix event producers, bootstrap/identity/system actor attribution, retention metadata, real sandbox isolated execution provider, secure filesystem, live browser provider, sandbox-generated file discovery and artifact promotion, physical multi-region data residency enforcement, model provider references, model policy versioning/approval workflow, ABAC/risk/sensitivity constraints, budget windows, rate limits, multi-provider routing, advanced billing/observability services, deployment manifests, evaluation workflows, API SDKs, connectors, sharing workflows, trigger workers, builder API, prompt registry, provider-specific guardrail integration policy, production PostgreSQL hardening for review storage, incident ops, support access, solution packs, licensing, and private packaging remain implementation work. Frontend implementation is intentionally deferred to a final user-managed phase.

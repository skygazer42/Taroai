# Security and Compliance Implementation Plan


**Goal:** Add the security foundation required for an enterprise Agent Workspace: tenant isolation, RBAC/ABAC policy checks, secrets management, data classification, audit coverage, and safe sandbox/tool execution.

**Architecture:** Security checks must be centralized, testable, and called before data access or tool execution. Identity resolves user and role context; Policy Service decides whether an action is allowed; Tool Gateway and Sandbox Adapter enforce credential and network boundaries; Audit Service records sensitive actions.

**Tech Stack:** FastAPI dependencies, Pydantic policy models, PostgreSQL, Redis, secret manager seam, pytest.

---

## Summary

This plan makes enterprise trust boundaries explicit. It builds on existing `identity`, `storage`, `memory`, `skills`, and `agent` packages.

Current state has Pydantic identity/auth models, password hashing, RBAC role assignments, disabled-user rejection, signed PoC access tokens, `/api/auth/login`, `/api/auth/logout`, Bearer request-context resolution, dev request headers behind settings, SQL-backed users/roles/role assignments, SQL-backed auth session persistence/revocation when SQL identity is used, a `taroai/sso` provider configuration boundary with Pydantic OIDC/SAML metadata, memory/SQL registries, tenant-scoped configure/list/get/enable/disable APIs behind RBAC, SQL/RLS persistence, SSO entitlement checks on configure/enable, sanitized provider audit events, and password-login rejection for SSO-only domains, a `taroai/scim` provisioning boundary with Pydantic provider config, User/Group import resources, group-to-role mapping, memory/SQL stores, external user links, import records, tenant-scoped management/import APIs, SCIM entitlement checks, SQL/RLS persistence, and sanitized import audit events, a first-pass `taroai/policy` boundary with centralized RBAC-backed decisions for API tenant operations, PostgreSQL tenant session context setting before tenant-scoped SQL, PostgreSQL-only RLS migration blocks for tenant-scoped tables, live PostgreSQL RLS verification against a non-superuser app role, a first-pass `taroai/guardrails` boundary with tenant/workspace-scoped Pydantic rule evaluation, Settings-backed built-in secret-pattern, prompt-threat, and HTTP detector boundaries, Tool Gateway request, Agent Runtime retrieval-context, Agent Runtime model request/response with persisted model-planning guardrail approval resume, Agent Runtime artifact publication with persisted guardrail approval resume, long-term memory candidate guardrail approval review, SQL-backed short-term memory review queue approval/rejection, and memory-write enforcement, a first-pass `taroai/audit` boundary with API audit writes, identity user/role lifecycle events, sensitive metadata redaction, request actor attribution, Agent Runtime, Tool Gateway, and worker job actor attribution from tenant/user context, a default enterprise coverage matrix routed through `AuditService`, a first-pass `taroai/secrets` boundary with `SecretRef`, `SecretLease`, `SecretScope`, scoped short-lived lease checks, Tool Gateway secret lease injection with run/step/session context, sandbox command lease-handle environment delivery without raw values, sandbox-scoped lease resolution API with run/step/session validation, optional Settings-backed provider token enforcement, and safe audit metadata, AWS Secrets Manager value storage behind Pydantic settings, safe audit metadata, and a first-pass `taroai/lifecycle` boundary with tenant/workspace policy resolution, legal-hold metadata enforced by storage cleanup, cleanup preview, storage-object export manifests/bundles with summary-only audit, safe backup manifests without raw connection strings, and management APIs behind RBAC. Remaining security work includes ABAC, OIDC/SAML protocol login and assertion validation, full SCIM v2 service-provider compatibility, MFA, sandbox provider network isolation and mTLS/IAM hardening, provider-specific guardrail integration policy, full audit call-site enforcement, bootstrap/identity/support actor attribution, additional secret backend providers, tenant-specific KMS/IAM policy hardening, and production support-access controls.

## Task 1: Policy Package

**Files:**

- Create: `apps/api/src/taroai/policy/__init__.py`
- Create: `apps/api/src/taroai/policy/models.py`
- Create: `apps/api/src/taroai/policy/service.py`
- Test: `tests/api/test_policy.py`

**Steps:**

1. Define Pydantic models: `PolicyRequest`, `PolicyDecision`, `PolicyEffect`, `PolicyReason`.
2. Policy request includes tenant, user, roles, action, resource, sensitivity, risk level, tool scopes, and cost estimate.
3. Policy decision is `allow`, `deny`, or `approval_required`.
4. Use tests-only policy fixtures outside product source.
5. Keep RBAC as first decision source; ABAC attributes can extend it.

**Acceptance Criteria:**

- Security decisions are centralized.
- Call sites do not implement ad hoc permission logic.

**Current Implementation Notes:**

- `apps/api/src/taroai/policy/` defines Pydantic `PolicyRequest`, `PolicyDecision`, and `PolicyEffect` models.
- `IdentityPolicyService` centralizes first-pass RBAC decisions by delegating permission checks to the identity service.
- FastAPI tenant-operation permission checks now route through `app.state.policy_service`, so future ABAC, risk, sensitivity, and approval logic can be added at the policy boundary instead of route handlers.
- ABAC attributes, policy reasons taxonomy, tool scopes, cost estimate decisions, and support-access policy remain implementation work.

## Task 2: Tenant Isolation Enforcement

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Modify: `apps/api/src/taroai/store.py`
- Modify: `apps/api/src/taroai/identity/service.py`
- Test: `tests/api/test_tenant_isolation_contract.py`

**Steps:**

1. Add tests for cross-tenant run, artifact, memory, storage, skill, billing, and audit access.
2. Add tenant-aware helper functions where needed.
3. Ensure every business model has `tenant_id`.
4. Ensure request context carries tenant and user.
5. Extend PostgreSQL RLS verification into CI/private deployment release gates.

**Acceptance Criteria:**

- Cross-tenant access is blocked consistently.
- No service lists data across tenants unless explicitly admin-scoped and authorized.

## Task 3: Secrets Boundary

**Files:**

- Create: `apps/api/src/taroai/secrets/__init__.py`
- Create: `apps/api/src/taroai/secrets/models.py`
- Create: `apps/api/src/taroai/secrets/service.py`
- Test: `tests/api/test_secrets_boundary.py`

**Steps:**

1. Define `SecretRef`, `SecretLease`, and `SecretScope` Pydantic models.
2. Create a tests-only secret service fixture.
3. Tool Gateway requests short-lived credential leases by scope.
4. Sandbox receives only short-lived scoped credentials.
5. Audit events must never include secret values.

**Acceptance Criteria:**

- Long-lived credentials are not stored in run state, audit payloads, memory, or sandbox files.
- Secret leases have expiry and scope.

**Current Implementation Notes:**

- `apps/api/src/taroai/secrets/` defines Pydantic `SecretRef`, `SecretLease`, and `SecretScope` models plus local and AWS Secrets Manager service boundaries that store long-lived values outside model dumps.
- Secret leases are scoped to tenant, workspace, run, step, optional sandbox session, tool name, actions, and expiry; audit metadata excludes long-lived values and lease tokens.
- Tool Gateway policies can declare `ToolSecretRequirement` entries, and the gateway injects short-lived leases into handler requests only after policy and approval checks pass.
- `sandbox.command` now receives scoped lease handles through reserved `TAROAI_SECRET_LEASES` environment metadata, never raw secret values, and rejects caller overrides of reserved lease environment keys.
- `POST /api/sandbox/secret-leases/resolve` resolves only `sandbox.command` lease handles for the caller tenant/workspace/run/step/session/action, uses strict bound-context validation so session-bound leases cannot omit `session_id`, enforces `X-Sandbox-Resolver-Token` when `TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN` is configured, returns the value to the sandbox caller, and records `secret.lease.resolved` audit metadata without the lease token or secret value.
- `TAROAI_SECRET_SERVICE_BACKEND`, region, endpoint, name-prefix, and KMS-key settings select the secret backend for API and worker Tool Gateway execution; `TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN` gates sandbox provider lease resolution calls when enabled.
- Sandbox provider network isolation and mTLS/IAM hardening, additional secret backend providers, tenant-specific KMS/IAM policy hardening, and secret-manager live verification remain implementation work.

## Task 4: Data Classification

**Files:**

- Create: `apps/api/src/taroai/security/data_classification.py`
- Modify: `apps/api/src/taroai/knowledge/models.py`
- Modify: `apps/api/src/taroai/memory/models.py`
- Test: `tests/api/test_data_classification.py`

**Steps:**

1. Define sensitivity levels and labels: public, internal, confidential, restricted.
2. Attach sensitivity metadata to knowledge documents, memory records, artifacts, and tool inputs where relevant.
3. Add policy checks for user clearance.
4. Add tests that restricted data is hidden from lower-clearance users.

**Acceptance Criteria:**

- Sensitive data filtering is query-time enforced.
- Memory and retrieval results carry sensitivity metadata.

## Task 5: Tool and Network Guardrails

**Files:**

- Modify: `apps/api/src/taroai/tool_gateway/service.py`
- Modify: `apps/api/src/taroai/sandbox/models.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_tool_network_guardrails.py`

**Steps:**

1. Add network policy config: allowlist, denylist, default mode.
2. Tool Gateway checks network domain before browser/API actions.
3. High-risk external writes require approval.
4. Sandbox Adapter receives network policy when creating session.
5. Add tests for denied domain, allowed domain, and approval-required external write.

**Acceptance Criteria:**

- Agents cannot browse or call arbitrary external domains when policy restricts them.
- External writes are approval-gated.

## Task 6: Compliance Audit Coverage

**Files:**

- Modify: `apps/api/src/taroai/audit/models.py`
- Modify: `apps/api/src/taroai/audit/service.py`
- Test: `tests/api/test_compliance_audit_coverage.py`

**Steps:**

1. Define required audit event types for identity, RBAC, knowledge read, memory write, tool call, approval, storage access, sandbox action, billing, and skill publication.
2. Add tests that each sensitive action emits audit.
3. Redact secrets and large document content from audit payloads.
4. Add retention policy metadata.

**Acceptance Criteria:**

- Audit coverage can be checked by tests.
- Audit events contain enough metadata to investigate enterprise incidents.

**Current Implementation Notes:**

- `AuditService` now redacts sensitive metadata keys before persistence and returns defensive copies.
- FastAPI storage, memory, knowledge, sandbox, and browser audit writes route through `app.state.audit_service`.
- Agent Runtime model/tool audit events route through injected `AuditService` and include Pydantic actor attribution from tenant/user context.
- Tenant bootstrap completion audit events route through injected `AuditService`.
- Tool Gateway service-level blocked and approval-required audit events route through injected `AuditService` and include Pydantic actor attribution from tenant/user context.
- Worker job started, succeeded, and failed audit events include Pydantic actor attribution with worker ID and requesting user context.
- In-memory and SQL identity services now emit `identity.user.created`, `identity.user.disabled`, `identity.role.created`, and `identity.role.assigned` through injected `AuditService` without password material.
- `AuditCoverageRequirement`, `AuditCoverageFinding`, `AuditCoverageReport`, and default enterprise coverage requirements now define required sensitive-action audit events and metadata keys.
- `AuditService.check_coverage` can report covered and missing tenant audit requirements from persisted events.
- `GET /api/audit-events/coverage` exposes the default enterprise coverage report behind `audit.read`.
- FastAPI business audit writes include Pydantic actor attribution with tenant, user, actor type, IP address, and user agent.
- In-memory and SQL control-plane meter writes now emit `billing.metered` audit records, approval resolution emits `approval.resolved`, and skill publication emits `skill.published`.
- Event producers for remaining matrix items, actor attribution for bootstrap/identity/support paths, admin action attribution, and deeper lifecycle policy-resolution audit events remain implementation work.

## Verification

Run after each task:

```bash
python -m pytest tests/api/test_policy.py -q
python -m pytest tests/api/test_tenant_isolation_contract.py -q
python -m pytest tests/api/test_secrets_boundary.py -q
python -m pytest tests/api/test_compliance_audit_coverage.py -q
python -m pytest -q
```

Expected final result: tenant isolation, policy decisions, secrets handling, data classification, and audit coverage are explicit and tested.

# Enterprise Tenant Onboarding Implementation Plan


**Goal:** Build the customer onboarding path that turns a signed enterprise account into a usable tenant with workspaces, users, roles, starter skills, knowledge spaces, quotas, and operational checks.

**Architecture:** Tenant onboarding is a control-plane workflow, not a one-off script. The API owns tenant, workspace, identity, policy, billing, and default-resource creation through Pydantic request/result models; background jobs handle slow imports such as SCIM users, starter knowledge, and starter skill packs. Every onboarding action writes audit events and can be resumed safely.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, Redis job queue later, pytest, SSO provider configuration, and later OIDC/SAML/SCIM integrations.

---

## Summary

This plan fills the enterprise-delivery gap between platform foundation and actual customer rollout. It answers: after a customer signs, how do we create their environment, seed useful capabilities, enable employees, and prove the tenant is ready?

## Task 1: Onboarding Package and Models

**Files:**

- Create: `apps/api/src/taroai/onboarding/__init__.py`
- Create: `apps/api/src/taroai/onboarding/models.py`
- Create: `apps/api/src/taroai/onboarding/service.py`
- Test: `tests/api/test_onboarding_contract.py`

**Steps:**

1. Define `TenantOnboardingRequest` as a Pydantic model with tenant name, slug, owner email, initial workspaces, billing plan, region, allowed domains, and default quota profile.
2. Define `TenantOnboardingResult` with tenant ID, created workspace IDs, owner user ID, default role IDs, seeded skill IDs, seeded knowledge space IDs, and readiness status.
3. Add `OnboardingStepStatus` model with step name, status, error, retry count, and timestamp.
4. Implement in-memory `OnboardingService.start_onboarding`.
5. Add tests for required fields, slug uniqueness, idempotency key reuse, and result shape.

**Acceptance Criteria:**

- Onboarding inputs and outputs are Pydantic.
- Creating the same tenant with the same idempotency key returns the same result.
- Creating the same slug with a different idempotency key fails.

## Task 2: Tenant, Workspace, and Default Roles

**Files:**

- Modify: `apps/api/src/taroai/identity/models.py`
- Modify: `apps/api/src/taroai/identity/service.py`
- Modify: `apps/api/src/taroai/onboarding/service.py`
- Test: `tests/api/test_onboarding_identity_defaults.py`

**Steps:**

1. Add tenant and workspace creation interfaces if they are not already present.
2. Define default roles: `tenant_owner`, `workspace_admin`, `employee`, `skill_publisher`, `auditor`, and `billing_admin`.
3. Assign the owner to `tenant_owner` and first workspace `workspace_admin`.
4. Add default permissions for run creation, skill install, knowledge read/write, audit read, and billing read.
5. Test that default roles are tenant-scoped and do not grant cross-tenant access.

**Acceptance Criteria:**

- A new tenant is usable immediately after onboarding.
- Default permissions are explicit and testable.
- Tenant owner does not automatically bypass policy checks for all risky actions.

## Task 3: SSO, SCIM, and Password Fallback Plan

**Files:**

- Create: `apps/api/src/taroai/sso/`
- Create: `apps/api/src/taroai/scim/`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_sso_providers.py`
- Test: `tests/api/test_scim_provisioning.py`

**Steps:**

1. Add Pydantic OIDC/SAML provider config models under a dedicated `taroai/sso` package.
2. Support PoC password login as fallback, but mark enterprise SSO provider configuration as tenant configuration.
3. Add SCIM user and group import models without implementing external provider calls yet.
4. Map SCIM groups to tenant/workspace roles.
5. Add tests that disabled password fallback rejects password login for SSO-only tenants.

**Acceptance Criteria:**

- Tenant identity mode is explicit.
- Password fallback can be disabled per tenant.
- SCIM group-to-role mapping is represented before external sync is implemented.
- SSO provider configuration is managed separately from protocol login handling.

## Task 4: Starter Knowledge Spaces and Skill Packs

**Files:**

- Create: `apps/api/src/taroai/onboarding/starter_packs.py`
- Modify: `apps/api/src/taroai/skills/registry.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Test: `tests/api/test_onboarding_starter_packs.py`

**Steps:**

1. Define starter packs for `general`, `sales`, `support`, `operations`, and `ecommerce`.
2. Each starter pack lists initial workspaces, skill manifests, knowledge spaces, memory policy defaults, and approval policy defaults.
3. Seed skills as disabled or approval-required until tenant owner enables them.
4. Seed knowledge spaces with ACLs but no customer documents.
5. Add tests that starter packs never create shared memory without approval policy.

**Acceptance Criteria:**

- Enterprise customers get a low-cold-start workspace.
- Starter capabilities are governed before employee use.
- Industry packs can be added without changing onboarding core logic.

## Task 5: Quotas, Billing, and Cost Controls

**Files:**

- Modify: `apps/api/src/taroai/config.py`
- Modify: `apps/api/src/taroai/store.py`
- Future: `apps/api/src/taroai/billing/models.py`
- Test: `tests/api/test_onboarding_quota_defaults.py`

**Steps:**

1. Define quota profiles for trial, PoC, business, and enterprise.
2. Store tenant-level limits for users, workspaces, monthly task runs, sandbox hours, token budget, and skill publication count.
3. Add onboarding tests that quota profiles produce billing meter policy defaults.
4. Ensure quota defaults are not hard-coded in route handlers.
5. Require explicit admin action to raise production quotas.

**Acceptance Criteria:**

- Tenant cost controls exist from day one.
- Quotas can differ by tenant without code changes.

## Task 6: Readiness Checks and Runbook

**Files:**

- Create: `docs/operations/tenant-onboarding-runbook.md`
- Create: `docs/operations/tenant-readiness-checklist.md`
- Create: `apps/api/src/taroai/onboarding/readiness.py`
- Test: `tests/api/test_tenant_readiness.py`

**Steps:**

1. Define readiness checks for tenant, workspaces, owner, roles, quotas, audit, billing, starter skills, knowledge spaces, and identity mode.
2. Add `TenantReadinessReport` Pydantic model.
3. Expose service method `check_tenant_readiness`.
4. Document manual rollout steps for first PoC customers.
5. Add tests for ready, missing owner, missing quota, and missing audit configuration.

**Current Implementation Notes:**

- `apps/api/src/taroai/onboarding/` now contains Pydantic bootstrap/readiness models, `TenantBootstrapService`, and `TenantReadinessService`.
- `/api/tenants/bootstrap` creates the first tenant owner, seeds the `tenant_owner` role with explicit tenant-scoped permissions, requires `TAROAI_TENANT_BOOTSTRAP_TOKEN`, and records `tenant.bootstrap.completed` audit metadata without password content.
- `/api/tenants/current/readiness` reports owner, role, auth mode, quota profile, audit read, billing read, object storage config, job queue config, starter skill, and knowledge space checks through authenticated tenant context.
- `apps/api/src/taroai/sso/` now contains Pydantic OIDC/SAML provider metadata, memory and SQL registries, tenant-scoped management APIs for configure/list/get/enable/disable, SQL/RLS persistence, SSO entitlement checks on configure/enable, sanitized audit events, and AuthService enforcement that rejects password login for enabled SSO providers when `password_fallback_enabled=false`.
- `apps/api/src/taroai/scim/` now contains Pydantic SCIM provider config, SCIM User/Group import resources, group-to-role mapping, memory/SQL provisioning stores, external user links, import records, tenant-scoped configure/list/get/enable/disable/mapping/import APIs behind RBAC, SQL/RLS persistence, SCIM entitlement checks on configure/enable/import, and sanitized import audit events. Full `/scim/v2` service-provider compatibility, IdP push-token enforcement, PATCH/filter/bulk operations, reactivation flows, OIDC/SAML protocol redirects, callback/assertion validation, and MFA remain implementation work.
- Starter pack seeding, full onboarding orchestration, workspace seed, readiness runbooks, quota enforcement, and production rollout remain implementation work.

**Acceptance Criteria:**

- Support can prove a tenant is ready before inviting employees.
- Failed onboarding can be diagnosed without database spelunking.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_onboarding_contract.py -q
python -m pytest tests/api/test_onboarding_identity_defaults.py -q
python -m pytest tests/api/test_sso_providers.py -q
python -m pytest tests/api/test_scim_provisioning.py -q
python -m pytest tests/api/test_onboarding_starter_packs.py -q
python -m pytest tests/api/test_onboarding_quota_defaults.py -q
python -m pytest tests/api/test_tenant_readiness.py -q
python -m pytest -q
```

Expected final result: a tenant can be created, seeded, governed, checked for readiness, and handed to enterprise users without ad hoc setup.

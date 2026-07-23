# Sharing, Collaboration, and Artifact Delivery Implementation Plan


**Goal:** Build the enterprise sharing layer for runs, artifacts, skills, knowledge spaces, memory candidates, and cloud workspace outputs so teams can reuse agent work without leaking data across tenant/workspace/user boundaries.

**Architecture:** Sharing is a policy-controlled resource layer, not a UI shortcut. Every share creates a typed grant with subject, scope, permission, expiration, and audit metadata. Artifacts stay in storage; sharing changes access grants and visibility, never object keys. Client UI reads share state through API and renders share actions inside the CREAO-compatible work surface.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, object storage, pytest, future Next.js client portal, audit service.

---

## Summary

The product promise includes shared knowledge, skill reuse, team memory, and enterprise artifacts. This plan defines how employee work moves from private run output to team or tenant-visible resource with approval, audit, and revocation.

Current implementation note: Task 1 has a first-pass backend implementation. `taroai.sharing` now provides typed share grant models, in-memory and SQL stores, Pydantic settings, migrations with PostgreSQL RLS, and FastAPI create/list/revoke APIs behind `sharing.read` and `sharing.manage`. External-link share grant create/list/revoke responses redact the link token, external-link token input shorter than 32 characters is rejected, non-`view` external-link permissions are rejected, non-artifact external-link resources are rejected, and newly created external-link grants store only a tenant-scoped `hmac-sha256:` subject digest instead of the raw token, keyed by `TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET` when set and otherwise `TAROAI_ACCESS_TOKEN_SECRET`. Task 2 has started: storage signed-url and content-download read paths now accept active direct artifact share grants after sensitivity checks and reject revoked grants. External artifact link downloads are started behind `TAROAI_EXTERNAL_SHARE_LINKS_ENABLED=false` by default, require an active unexpired `external_link` artifact grant, reject sensitive artifacts, hash the URL token with the requested tenant before authorization, and record download audit plus `external_artifact_download_bytes` billing metadata without the link token. Artifact visibility states, password-protected or separately signed external-link tokens, run collaboration, output promotion, promotion/evaluation billing meters, and retention automation remain planned follow-up work.

## Task 1: Share Grant Domain Model

**Files:**

- Create: `apps/api/src/taroai/sharing/__init__.py`
- Create: `apps/api/src/taroai/sharing/models.py`
- Create: `apps/api/src/taroai/sharing/repository.py`
- Modify: `apps/api/src/taroai/app.py`
- Modify: `apps/api/src/taroai/config.py`
- Create: `apps/api/migrations/021_share_grants.sql`
- Test: `tests/api/test_share_grants.py`
- Test: `tests/api/test_share_grant_repository.py`

**Steps:**

1. Define `ShareResourceType`: `run`, `artifact`, `skill`, `knowledge_space`, `memory_candidate`, `workspace`, and `agent_template`.
2. Define `ShareSubjectType`: `user`, `group`, `workspace`, `tenant`, and `external_link`.
3. Define `SharePermission`: `view`, `comment`, `use`, `copy`, `edit`, `publish`, and `admin`.
4. Define `ShareGrant` with tenant ID, resource ID, resource type, subject, permission, created by, expiration, status, and reason.
5. Add tests for tenant isolation, expiration validation, and unsupported permission/resource pairs.
6. Expose create/list/revoke API routes and safe share-grant audit metadata without raw reason text.
7. Store grants through memory or SQL based on Pydantic settings.

**Acceptance Criteria:**

- Share state is explicit and typed.
- Expired grants stop authorizing access.
- Revoked grants stop authorizing access.
- Tenant isolation applies at API, store, migration, and PostgreSQL RLS boundaries.

## Task 2: Artifact Delivery and Access Control

**Files:**

- Modify: `apps/api/src/taroai/storage/models.py`
- Modify: `apps/api/src/taroai/storage/catalog.py`
- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_app.py`
- Future test: `tests/api/test_artifact_sharing_access.py`

**Steps:**

1. Add artifact visibility states: private, workspace, tenant, external, and archived.
2. Access check combines owner, workspace membership, share grants, sensitivity, and policy.
3. External links require explicit expiration and optional password or signed token.
4. Artifact downloads are audited.
5. Add tests for private owner access, workspace share access, revoked share denial, and cross-tenant denial.
6. First-pass implementation: direct artifact share grants satisfy storage object ACL checks for signed URL and content-download reads, but do not bypass sensitivity clearance.
7. First-pass external-link implementation: when enabled by setting, `GET /api/share-links/{external_link_id}/storage/objects/{storage_object_id}/content?tenant_id=...` downloads only non-sensitive artifact objects with an active external-link artifact grant, rejects non-artifact external-link resources, rejects link tokens shorter than 32 characters at grant creation, rejects non-`view` external-link permissions, stores newly created external-link grant subjects as tenant-scoped keyed `hmac-sha256:` digests, hashes the URL token with the requested tenant before authorization, and writes token-redacted API responses, audit metadata, and `external_artifact_download_bytes` billing metadata.

**Acceptance Criteria:**

- Artifacts can be safely shared without moving storage objects.
- External sharing is disabled by default unless tenant policy allows it.
- Active direct artifact grants can authorize storage reads; revoked grants deny them again.
- Active external-link artifact grants can authorize unauthenticated artifact content download only when external links are explicitly enabled.
- External-link grants only support `artifact` resources.
- External-link grant creation rejects link tokens shorter than 32 characters.
- External-link grants only support `view` permission.
- External-link artifact downloads are metered by byte without storing the link token in share-grant storage, API responses, audit metadata, or meter metadata.

## Task 3: Run and Workspace Collaboration

**Files:**

- Modify: `apps/api/src/taroai/domain.py`
- Create: `apps/api/src/taroai/collaboration/__init__.py`
- Create: `apps/api/src/taroai/collaboration/models.py`
- Test: `tests/api/test_run_collaboration.py`

**Steps:**

1. Define collaboration roles for a run: owner, viewer, operator, approver, and auditor.
2. Allow a run owner to invite another same-tenant user to view or operate a run.
3. Approval authority must come from policy/role, not just share permission.
4. Add comments and handoff notes as Pydantic models for future UI.
5. Add tests that a viewer cannot approve high-risk actions.

**Acceptance Criteria:**

- Long-running tasks can be handed off inside a team.
- Collaboration does not bypass approval policy.

## Task 4: Promote Run Output to Knowledge or Skill

**Files:**

- Create: `apps/api/src/taroai/promotion/__init__.py`
- Create: `apps/api/src/taroai/promotion/models.py`
- Create: `apps/api/src/taroai/promotion/service.py`
- Test: `tests/api/test_output_promotion_flow.py`

**Steps:**

1. Define promotion candidate types: artifact to knowledge document, run plan to workflow, tool sequence to skill draft, and accepted memory candidate to team memory.
2. Promotion always creates a candidate, not immediate production mutation.
3. Candidate includes source run, source artifact, proposed target, sensitivity, required approvals, and evaluation checks.
4. Add tests that tenant-shared promotion requires admin or owner approval.
5. Connect accepted skill promotion to the versioning plan in `2026-07-01-12-self-evolving-evaluations.md`.

**Acceptance Criteria:**

- Useful agent work can become reusable enterprise assets.
- Promotion is reviewed, auditable, and versioned.

## Task 5: Client Portal Sharing UX Contract

**Files:**

- Modify: `docs/plans/2026-07-01-08-client-portal-creao-ui.md`
- Future: `apps/web/app/(workspace)/components/share-dialog.tsx`
- Future: `apps/web/app/(workspace)/components/artifact-panel.tsx`
- Test: future Playwright tests.

**Steps:**

1. Add UI contract for share dialog: subject picker, permission selector, expiration, sensitivity warning, and policy denial state.
2. Add artifact panel actions for share, copy, promote to knowledge, promote to skill draft, and revoke.
3. Keep the chat column layout consistent with `https://agent.creao.ai/chat`.
4. Use toasts or inline states for successful share/revoke actions.
5. Add future Playwright checks for mobile and desktop share dialogs.

**Acceptance Criteria:**

- Sharing is part of the working surface, not hidden in admin settings.
- UI cannot offer actions the API/policy will always deny.

## Task 6: Audit, Billing, and Retention

**Files:**

- Future: `apps/api/src/taroai/audit/service.py`
- Future: `apps/api/src/taroai/billing/service.py`
- Create: `docs/security/sharing-retention-policy.md`
- Test: `tests/api/test_sharing_audit_retention.py`

**Steps:**

1. Audit share created, share revoked, external link created, artifact downloaded, run collaborator added, and output promoted.
2. Meter external artifact download volume and high-cost promotion/evaluation work.
3. Define retention policy for run logs, artifacts, comments, and external links.
4. Add tests that deleted or archived resources revoke active grants.
5. Add tests that audit payloads redact artifact content.

**Acceptance Criteria:**

- Sharing decisions are traceable.
- Retention and revocation behavior is clear for enterprise customers.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_share_grants.py tests/api/test_share_grant_repository.py -q
python -m pytest tests/api/test_artifact_sharing_access.py -q
python -m pytest tests/api/test_run_collaboration.py -q
python -m pytest tests/api/test_output_promotion_flow.py -q
python -m pytest tests/api/test_sharing_audit_retention.py -q
python -m pytest -q
```

Expected final result: users can share and promote useful agent outputs while tenant isolation, sensitivity, approval, audit, billing, and retention controls remain intact.

# Roadmap and Coverage Matrix Implementation Plan


**Goal:** Provide a reviewable roadmap that maps the original enterprise Agent Workspace requirements to the existing implementation plans, priorities, dependencies, and release gates.

**Architecture:** Treat plans 01-24 as the source of detailed implementation tasks. This document is the execution map: it identifies what is MVP, what is enterprise-hardening, what is post-MVP, which plans depend on each other, and what evidence proves each requirement is covered.

**Tech Stack:** Markdown, pytest verification, repo-local docs.

---

## Summary

The plan set is now broad enough that the next bottleneck is review and sequencing. This document answers four questions:

1. Does every original requirement have a plan?
2. What should be built first for a cloud PoC?
3. Which plans are prerequisites for others?
4. What evidence should be checked before moving from plan review to implementation?

## Research and Current-State Guardrail

Before approving this roadmap, read `research-grounding.md`.

The coverage matrix uses external terms only as planned architecture or candidate adapters unless current code proves implementation. Current code has FastAPI, Pydantic settings, centralized API errors, in-memory store/runtime/memory/skills/storage/identity foundations, and tests. It does not yet have production PostgreSQL/Redis/S3 adapters, LangGraph execution, LlamaIndex RAG, MCP protocol integration, E2B sandbox integration, pgvector indexing, OpenTelemetry exporters, or a Next.js frontend.

Reviewers should reject wording that turns a candidate provider into an accepted decision before the matching open question is answered.

## Source Requirements

The source requirements come from `a.md` and the user discussion:

- Client portal consistent with `https://agent.creao.ai/chat`.
- API/control plane.
- Billing, audit, and observability.
- Memory: short-term Redis and long-term governed memory.
- Sharing and collaboration.
- Multi-tenant identity, user accounts, passwords, roles, RBAC/ABAC.
- Agent loop, multi-agent delegation, long-running runs, approval gates.
- Self-evolving through reviewed candidates, not direct production mutation.
- Enterprise custom skills and workflows to reduce cold start.
- Cloud deployment first, with virtual environments/sandboxes for employees.
- Knowledge base sharing with ACL-aware retrieval.
- Skill marketplace upload, review, reuse, versioning.
- Storage choices for database, Redis, object storage, vector data, artifacts.
- Pydantic-managed backend settings and backend management models.
- No `from __future__ import annotations` in backend source.
- Maintainable folder boundaries for memory, skills, agent, storage, identity, and future service packages.
- Private deployment path later.

## Coverage Matrix

| Requirement | Primary Plans | Coverage Status | Review Notes |
| --- | --- | --- | --- |
| Product definition and enterprise-vs-consumer logic | 01 | Covered | Clarifies why enterprise delivery, governance, and custom skills are the differentiator. |
| Technical architecture and service boundaries | 02 | Covered | Defines client, API, runtime, tool gateway, sandbox, knowledge/memory, storage, billing, audit. |
| `.env` and Pydantic settings | 02, 03, 09, 24 | Covered | Settings are planned as Pydantic; deployment profiles extend this later. |
| Pydantic backend management models | 02-24 | Covered | Each service plan calls for Pydantic request/result/domain models. |
| No future annotations in backend source | 02, 03, 11 | Covered | Existing tests enforce the style; keep it as a quality gate. |
| Storage: PostgreSQL, Redis, object storage | 02, 03, 09, 20 | Covered | Source of truth and transient storage roles are separated. |
| User accounts, password hashing, roles | 03, 10, 13 | Covered | Password login is PoC fallback; SSO provider config and SCIM provisioning foundations come through onboarding. |
| RBAC/ABAC and tenant isolation | 03, 10, 13 | Covered | Must remain enforced before data access, not only at route layer. |
| Short-term and long-term memory | 03, 04, 12, 16 | Covered | Short-term Redis TTL and long-term reviewed memory are separate. |
| Knowledge base and ACL-aware RAG | 04, 10, 15, 20 | Covered | Query-time ACL filtering is mandatory. |
| Skill registry and marketplace | 05, 08, 12, 16, 23 | Covered | Marketplace UI and solution packs depend on registry and versioning. |
| Tool Gateway and connector governance | 05, 10, 15 | Covered | Tool and connector calls must share policy/audit/billing path. |
| Agent runtime and agent loop | 06, 07, 17, 21 | Covered and started | Runtime integrates state snapshots, model gateway, traces, guardrails, cancellation, approval resume, and approval rejection in first-pass form. |
| Sandbox / virtual employee workspace | 06, 09, 10, 20, 24 | Covered | Cloud-first adapter path; private deployments add packaging constraints. |
| Multi-agent delegation | 06, 18 | Covered | Bounded agent handoff triggers are started; advanced delegation approval policy remains follow-up work. |
| Billing, audit, observability | 07, 10, 11, 17, 22 | Covered | Admin APIs, meter events, trace spans, SLOs, incidents are separated by phase. |
| CREAO-consistent frontend | 08 | Covered as final-phase contract | Chat column, composer behavior, timeline, artifacts, admin, and skill routes are documented; implementation is deferred. |
| Deployment operations | 09, 20, 24 | Covered | Docker local PoC first; Kubernetes cloud path and private packaging later. |
| Security and compliance | 10, 20, 21, 22, 24 | Covered | Policy, secrets, data classification, retention, prompt guardrails, incidents, licensing. |
| Testing and release quality | 11 | Covered | Defines test taxonomy, CI, release checklist, frontend gates. |
| Self-evolving safely | 12, 16, 21, 23 | Covered | Changes remain candidates until reviewed, evaluated, versioned, and published. |
| Enterprise onboarding | 13, 23 | Covered | Tenant creation, starter packs, readiness, rollout playbooks. |
| API/SDK contracts | 14 | Covered and started | Versioning, errors, pagination, idempotency, event streaming, SDK shape. The MVP route checklist and OpenAPI contract test now freeze the first `/api/*` cloud PoC routes plus owner boundaries until an explicit `/api/v1` migration is approved. |
| Enterprise SaaS/API/MCP connectors | 15 | Covered and started | Connector models, credential-reference boundary, SQL-backed admin APIs with update/enable/disable, ACL sync planning, worker-driven sync ingestion into knowledge with sync-volume billing, run-scoped invocation decisions with safe audit/billing, persisted approval-request linkage with approved execution gating, internal API HTTP dispatch with API-key plus OAuth2 bearer access-token injection, read-only database dispatch with secret-referenced DSNs plus table allowlists, OAuth authorize/callback/refresh management with token rotation through secret references, and AWS Secrets Manager value storage behind the same lease API are started; SaaS/file/MCP adapters, provider-specific OAuth edge cases, broader database dialect/query governance, tenant-specific KMS/IAM policy hardening, and additional secret backend providers remain follow-up. |
| Sharing and artifact collaboration | 16 | Covered | Grants, external links, promotion, retention, audit. |
| Model Gateway | 17 | Covered and started | OpenAI-compatible boundary, runtime planning metering, run/tenant/workspace/user/agent budget guards, first-pass policy management, staged model policy change-request approval APIs, model policy version history, model sensitivity limits, secret-ref credential resolution, provider registry wiring, typed provider fallback policy, safe provider fallback attempt summaries, safe provider listing, tenant-scoped provider write/enable/disable/credential-rotation/version-list/rollback APIs, staged provider change-request approval APIs, SQL-backed provider rate-limit samples, Redis-backed request reservations, and Redis-backed `max_output_tokens` token reservations exist; broader distributed budget governance remains planned. |
| Schedules, webhooks, automation | 18 | Covered and started | Automatic runs still create governed runs through control plane; trigger operations visibility is started. |
| Agent Builder and workflow templates | 19 | Covered | Reusable agents become versioned configurations. |
| Data lifecycle and backup/restore | 20 | Covered | Retention, export, offboarding, backup, DR, residency. |
| Prompt and guardrail governance | 21 | Covered | Prompt registry, variable handling, injection checks, publication gates. |
| Incident, SLO, support operations | 22 | Covered | Reliability and support access are explicit. |
| Solution packs and customer success | 23 | Covered | Delivery assets, training, adoption metrics, feedback loops. |
| Private deployment and packaging | 24 | Covered | BYOC/VPC/private/air-gapped path is planned after cloud PoC. |

## Execution Roadmap

### Phase 0: Review and Scope Freeze

**Purpose:** Decide MVP boundaries before large implementation.

**Plans:** 01, 02, 25.

**Exit Criteria:**

- Product positioning accepted.
- MVP scope marked as in or out.
- Naming and package boundaries accepted.
- Cloud-first vs private-later strategy accepted.

### Phase 1: Backend Foundation

**Purpose:** Make the control plane reliable enough for real flows.

**Plans:** 03, 07, 10, 11, 14.

**Build Order:**

1. Storage/identity/memory.
2. Central API contracts and error model.
3. Policy and tenant isolation.
4. Billing/audit/observability events.
5. Test and release gates.

**Exit Criteria:**

- PostgreSQL/Redis/S3 adapters replace in-memory services where needed.
- Request context resolves tenant/user/roles once.
- Cross-tenant tests cover every core service.
- API returns consistent errors and stream events.
- Tests and CI gates are green.

### Phase 2: Runtime, Knowledge, Skills

**Purpose:** Make an employee run useful while staying governed.

**Plans:** 04, 05, 06, 15, 17, 21.

**Build Order:**

1. Knowledge/RAG with ACL-aware retrieval.
2. Skill registry and Tool Gateway.
3. Model Gateway.
4. Agent Runtime persistence and sandbox adapter.
5. Connector layer.
6. Prompt/guardrail integration.

**Exit Criteria:**

- Run can retrieve scoped knowledge.
- Runtime can execute a bounded plan with approved tools.
- Sandbox calls are tenant/workspace/run scoped.
- Model and tool calls generate trace, audit, and billing records.
- Guardrails can block, redact, or pause for approval.

### Phase 3: Employee and Admin Experience Contracts

**Purpose:** Define the contracts required for a later user-managed frontend phase.

**Plans:** 08, 13, 16, 18, 19.

**Build Order:**

1. CREAO-consistent chat workspace contract.
2. Admin tenant/workspace/user data contract.
3. Artifact sharing and collaboration contract.
4. Trigger/scheduling automation contract.
5. Agent Builder/workflow template contract.

**Exit Criteria:**

- Frontend starts on the workspace, not a landing page.
- Chat composer requirements preserve Enter/Shift+Enter behavior.
- Backend contracts expose admin, sharing, approval, and trigger data.
- No full portal implementation starts beyond the static workspace slice until separately approved.

### Phase 4: Enterprise Delivery and Continuous Improvement

**Purpose:** Turn one-off implementations into repeatable enterprise service.

**Plans:** 12, 20, 22, 23.

**Build Order:**

1. Evaluations and improvement candidates.
2. Data lifecycle/export/offboarding.
3. Incident/SLO/support operations.
4. Solution packs/customer success.

**Exit Criteria:**

- Low-quality runs produce reviewed candidates.
- Data export/deletion/retention are policy-driven.
- Support access is time-bound and audited.
- Solution packs can seed tenants and measure adoption.

### Phase 5: Private Deployment Readiness

**Purpose:** Support customers requiring VPC, BYOC, or air-gapped deployment.

**Plans:** 09, 20, 24.

**Build Order:**

1. Docker Compose and cloud PoC deployment.
2. Backup/restore and DR.
3. Package manifest and config profiles.
4. Helm/Kubernetes packaging.
5. License and install validation.

**Exit Criteria:**

- Install validation proves dependencies.
- Upgrade/rollback runbooks exist.
- Air-gapped constraints are explicit before committing to customers.

## Dependency Map

| Plan | Depends On | Blocks |
| --- | --- | --- |
| 03 Storage/Identity/Memory | 01, 02 | 04, 10, 13, 16, 20 |
| 04 Knowledge/RAG/Memory | 03, 10 | 06, 08, 15, 21 |
| 05 Skills/Tool Gateway | 03, 10 | 06, 08, 15, 19, 23 |
| 06 Agent Runtime/Sandbox | 03, 05, 10, 17 | 08, 12, 18, 22 |
| 07 Billing/Audit/Observability | 03 | 10, 11, 12, 17, 22 |
| 08 Client Portal | 14, early 03/06 | User acceptance and frontend tests |
| 09 Deployment/Ops | 03, 11 | 20, 24 |
| 10 Security/Compliance | 03, 07 | Most enterprise features |
| 11 Testing/Release | 03, 07, 08 | CI and release readiness |
| 12 Self-Evolving/Evals | 07, 19, 21 | Continuous improvement |
| 13 Enterprise Onboarding | 03, 10 | 23 |
| 14 API/SDK Contracts | 02, current API | 08, external integrations |
| 15 Enterprise Connectors | 05, 10 | 04 sync, 18 connector triggers |
| 16 Sharing/Collaboration | 03, 08, 10 | Output promotion and reuse |
| 17 Model Gateway | 07, 10 | 06 runtime governance, 12 evals |
| 18 Triggers/Scheduling | 06, 10, 14 | Automation workflows |
| 19 Builder/Templates | 05, 10, 12, 21 | Solution packs |
| 20 Data Lifecycle/DR | 03, 09, 10 | Private deployment |
| 21 Prompt/Guardrails | 10, 17 | Safe runtime and Builder publish gates |
| 22 Incident/SLO/Support | 07, 10, 20 | Enterprise operations |
| 23 Solution Packs | 13, 19 | Repeatable delivery |
| 24 Private Packaging | 09, 20 | BYOC/private sales path |

## MVP Recommendation

The smallest useful enterprise cloud PoC should include:

1. Tenant/workspace/user/role and request-context enforcement.
2. Password login for PoC plus SSO configuration and SCIM provisioning foundations.
3. PostgreSQL metadata, Redis short-term memory, S3/MinIO artifact storage.
4. API run lifecycle with event stream, cancellation, retry, state read, and unified errors.
5. ACL-aware knowledge retrieval.
6. Skill registry with manifest validation and Tool Gateway policy checks.
7. OpenAI-compatible Model Gateway boundary with provider policy and usage metering.
8. Agent Runtime with bounded steps, cancellation, approval pause/resume/rejection, and sandbox adapter seam.
9. Billing/audit events for run, model, tool, storage, approval, and memory operations.
10. CREAO-consistent minimal static workspace for the local PoC, with full portal later.
11. Enterprise onboarding with starter workspaces and readiness report.

Explicitly defer from MVP:

- Full private deployment packaging.
- Full self-evolving publication pipeline.
- Full solution-pack marketplace.
- Advanced incident/SLO operations.
- Broad connector catalog.
- Air-gapped install.

## Review Checklist

Use this checklist before approving the next implementation milestone:

- Confirm whether MVP targets one industry pack first, such as ecommerce, sales, support, or operations.
- Confirm shared-enterprise sandbox provider: keep `local_process` for local PoC, use first-pass `docker` for disabled-network container execution where explicitly available, and select E2B, Kubernetes-managed containers, or microVM-backed execution before broad employee use.
- Confirm first vector backend: pgvector, Qdrant, Milvus, Weaviate, or hosted provider.
- Confirm first model gateway strategy: OpenAI-compatible contract first, then direct adapters, LiteLLM, or both behind it.
- Confirm protocol timing: password-only PoC, OIDC/SAML login first, or full SCIM v2 provider compatibility first.
- Confirm full frontend portal remains deferred beyond the static workspace slice.
- Confirm whether solution packs are internal-only at first or tenant-visible.
- Confirm whether BYOC/private packaging is roadmap-only or sales-critical.

## Evidence Gates

Before marking this plan set ready for the next implementation milestone:

```bash
find docs/plans -maxdepth 1 -type f -name '2026-07-01-*.md' | sort
rg -n "Current implementation status|Coverage Matrix|MVP Recommendation" docs/plans
python -m pytest -q
```

Expected evidence:

- All dated plan files are present.
- README links every numbered plan.
- Coverage matrix maps every original requirement to at least one plan.
- Existing backend tests still pass.

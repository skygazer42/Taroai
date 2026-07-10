# Solution Packs and Customer Success Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Productize enterprise delivery by packaging industry templates, custom skills, onboarding assets, demo data, rollout checklists, adoption metrics, and success playbooks into reusable solution packs.

**Architecture:** A Solution Pack is a versioned bundle that can seed a tenant with workspaces, agent templates, skills, connectors, knowledge spaces, evaluation cases, prompts, training assets, and success metrics. Packs are installed through onboarding or admin APIs, but all resources remain governed by tenant policy, approval, audit, and versioning.

**Tech Stack:** FastAPI, Pydantic, pytest, SQLite/PostgreSQL-compatible SQL persistence, object storage for training/demo assets, existing builder/skills/connectors/onboarding services.

---

## Summary

The product is an enterprise service, so implementation plans should cover repeatable delivery, not only platform primitives. This plan turns custom work into reusable packs that reduce cold start for each new customer.

## Current Implementation Notes

- `apps/api/src/taroai/solution_packs/` now defines Pydantic solution pack manifests, entries, install requests, installation records, in-memory registry, SQLite/PostgreSQL-compatible SQL registry, and an installation service.
- Solution pack manifests can bundle versioned `SkillManifest` resources with industry/use-case metadata, success metrics, and rollout checklist text. The first installer registers missing skills, publishes them, and installs them into selected workspaces through the existing skill registry rather than executing arbitrary pack code.
- FastAPI exposes `POST /api/solution-packs`, `GET /api/solution-packs`, version history, publish/disable, install, and installation listing endpoints behind `solution_packs.read`/`solution_packs.manage`/`solution_packs.install`.
- Runtime install enforcement checks the active tenant license for `solution_packs` when license runtime enforcement is enabled, and install audit metadata records pack id, version, workspace count, and installed skill count without raw skill schemas or business payloads.
- `TAROAI_SOLUTION_PACK_REGISTRY_BACKEND` selects memory or SQL persistence; new install and private/BYOC env profiles use SQL. Migrations and PostgreSQL RLS include solution pack tables.
- Solution pack installation now has service/API dry-run preview, workspace skill conflict reporting, selected-resource skips, high-risk skill installation as disabled by default, and rollback that disables installed workspace skills while recording safe rollback audit metadata.
- `apps/api/src/taroai/customer_success/` now defines Pydantic adoption metrics, solution-pack outcome metrics, tenant success health bands, and an in-memory customer success summary service that aggregates tenant-scoped runs, artifact downloads, skill usage meters, approvals, feedback counts, repeated workflows, and installed pack outcome values without exposing prompts, artifact URIs, or raw feedback.
- `apps/api/src/taroai/customer_success/feedback.py` now captures structured customer feedback types and target links, writes safe `customer.feedback.submitted` audit metadata without raw comments or metadata values, creates human-reviewed low-rated-run evaluation candidates, and creates repeated missing-skill solution-pack improvement candidates without mutating production packs, skills, or runs.
- `apps/api/src/taroai/customer_success/repository.py` now provides `SqlCustomerFeedbackService`, backed by SQLite/PostgreSQL-compatible migrations for feedback records, review candidates, evaluation case records, solution-pack publication draft records, and draft application fields. `TAROAI_CUSTOMER_FEEDBACK_SERVICE_BACKEND` selects memory or SQL, and durable deployment profiles require SQL.
- Feedback candidates now have an accept/reject review workflow: accepted low-rated-run candidates create evaluation-case IDs and tenant-scoped evaluation case records, accepted missing-skill candidates create solution-pack publication draft IDs and draft records, rejected candidates create no downstream artifact, and neither path mutates production runs, skills, or published packs.
- FastAPI exposes customer success summary, feedback submission/listing, low-rated-run evaluation candidate creation/list/review, and solution-pack improvement candidate creation/list/review routes behind `customer_success.read`, `customer_success.feedback`, and `customer_success.manage`; feedback API responses avoid returning raw comments or metadata values.
- FastAPI also exposes `GET /api/customer-success/evaluation-cases` and `GET /api/customer-success/solution-pack-drafts` behind `customer_success.manage`, so reviewed feedback can be inspected as explicit downstream work records before any production evaluation or pack publication change.
- Solution-pack publication drafts now support guarded backend edit, submit-for-review, approve/reject, and apply endpoints. Applying a draft requires `customer_success.manage` plus `solution_packs.manage`, a proposed pack version, and one or more complete `SkillManifest` records; it publishes a new solution-pack version, marks the source candidate applied, and records audit metadata without raw schemas or approval notes.
- Tenant bootstrap now accepts `starter_solution_pack_ids`, installs requested published solution packs into the starter workspace through the solution pack service, returns installed pack/skill IDs, records safe bootstrap metadata with pack IDs and skill counts, and grants tenant owners solution pack plus customer success permissions.
- The static workspace now includes a Customer Success panel that loads tenant success health, run completion, feedback count, evaluation/solution-pack candidate queue counts, and solution-pack publication drafts from the real API while preserving the existing CREAO-compatible chat selector contract.
- Customer Success operators can select a solution-pack publication draft in the static workspace, edit its requested skill, change summary, target pack version, and skill manifest JSON object or array while it is editable, submit it for review, approve/reject it, and apply approved drafts through the guarded backend workflow.
- `docs/solution-packs/` now contains ecommerce, sales, support, and operations baseline pack docs that map business outcomes and named use cases to Taroai resources, required connectors, knowledge spaces, approval gates, sample inputs, artifacts, and success metrics.
- `docs/customer-success/` now contains rollout, tenant admin training, employee training, and solution engineer checklist docs that cover discovery, sandbox tenant, data and connector setup, pilot, training, production, expansion, go-live readiness, safe use, and custom skill delivery.
- Release package verification now treats the baseline solution pack docs and customer success playbooks as required archive entries so private/BYOC delivery packages include the business rollout materials.
- Non-skill resource publication drafts beyond proposed `SkillManifest` records remain planned.

## Task 1: Solution Pack Domain Model

**Files:**

- Create: `apps/api/src/taroai/solution_packs/__init__.py`
- Create: `apps/api/src/taroai/solution_packs/models.py`
- Create: `apps/api/src/taroai/solution_packs/registry.py`
- Test: `tests/api/test_solution_packs.py`

**Steps:**

1. Define `SolutionPack` with ID, name, industry, version, owner, description, status, tenant visibility, and compatibility constraints.
2. Define bundled resources: workspaces, agent templates, workflow templates, skills, connector definitions, prompt templates, knowledge spaces, eval cases, and training assets.
3. Define statuses: draft, review, published, deprecated, and archived.
4. Add tests for valid pack, invalid version, unsupported resource type, and immutable published pack.
5. Keep all models Pydantic.

**Acceptance Criteria:**

- Industry delivery assets have one versioned package format.
- Published packs are immutable and auditable.

## Task 2: Pack Installation and Tenant Seeding

**Files:**

- Create: `apps/api/src/taroai/solution_packs/service.py`
- Modify: `apps/api/src/taroai/onboarding/starter_packs.py`
- Test: `tests/api/test_solution_packs.py`

**Steps:**

1. Define `SolutionPackInstallRequest` with tenant ID, workspace mapping, selected resources, owner, approval mode, and dry-run flag.
2. Implement dry-run install that reports resources to create, conflicts, missing dependencies, and required approvals.
3. Install resources as drafts or disabled by default when risk is high.
4. Record audit events for pack installed, resource skipped, conflict, and rollback.
5. Add tests for dry run, install, conflict detection, and rollback.

**Acceptance Criteria:**

- Solution packs can seed tenants repeatably.
- Installation never bypasses policy or approvals.

## Task 3: Industry Pack Baselines

**Files:**

- Create: `docs/solution-packs/ecommerce.md`
- Create: `docs/solution-packs/sales.md`
- Create: `docs/solution-packs/support.md`
- Create: `docs/solution-packs/operations.md`
- Test: documentation review plus future pack registry tests.

**Steps:**

1. Define ecommerce pack: product description, competitor price monitor, buyer message assistant, operations weekly report.
2. Define sales pack: account research, proposal generator, CRM update assistant, meeting brief.
3. Define support pack: ticket triage, knowledge answer draft, QA review, escalation summary.
4. Define operations pack: SOP executor, spreadsheet cleanup, vendor research, report builder.
5. For each pack, list required connectors, knowledge spaces, approval gates, sample inputs, artifacts, and success metrics.

**Acceptance Criteria:**

- Solution engineers have concrete starting packs.
- Pack docs map business outcomes to platform resources.

## Task 4: Training Assets and Guided Rollout

**Files:**

- Create: `docs/customer-success/rollout-playbook.md`
- Create: `docs/customer-success/admin-training.md`
- Create: `docs/customer-success/employee-training.md`
- Create: `docs/customer-success/solution-engineer-checklist.md`
- Test: documentation review.

**Steps:**

1. Define rollout stages: discovery, sandbox tenant, data/connector setup, pilot, training, production, expansion.
2. Define admin training topics: tenant setup, roles, knowledge, skills, approvals, audit, billing.
3. Define employee training topics: chat/task console, artifacts, approvals, sharing, feedback, and safe use.
4. Define solution engineer checklist for custom skill delivery.
5. Add a go-live readiness checklist that references `2026-07-01-13-enterprise-onboarding.md`.

**Acceptance Criteria:**

- Customer rollout has repeatable playbooks.
- Training materials match actual product concepts.

## Task 5: Adoption Metrics and Success Health

**Files:**

- Create: `apps/api/src/taroai/customer_success/__init__.py`
- Create: `apps/api/src/taroai/customer_success/models.py`
- Create: `apps/api/src/taroai/customer_success/service.py`
- Test: `tests/api/test_customer_success_metrics.py`

**Steps:**

1. Define adoption metrics: active users, active workspaces, runs created, runs completed, artifact downloads, skills used, approvals resolved, feedback submitted, and repeated workflows.
2. Define outcome metrics per solution pack.
3. Define `TenantSuccessHealth` with onboarding, adoption, reliability, value, and risk scores.
4. Add in-memory summary service for tests.
5. Add tests for metric aggregation and tenant isolation.

**Acceptance Criteria:**

- Customer success can see whether a tenant is adopting the platform.
- Metrics are tenant-scoped and privacy-aware.

## Task 6: Feedback Loop to Roadmap and Evaluations

**Files:**

- Create: `apps/api/src/taroai/customer_success/feedback.py`
- Modify: `apps/api/src/taroai/evaluations/service.py`
- Test: `tests/api/test_customer_feedback_loop.py`

**Steps:**

1. Define feedback types: thumbs rating, bug report, missing skill, wrong answer, slow run, cost concern, and feature request.
2. Link feedback to run, artifact, skill, solution pack, or tenant onboarding step.
3. Convert low-rated runs into evaluation candidates with human review.
4. Convert repeated missing-skill feedback into solution pack improvement candidates.
5. Add tests for feedback capture, candidate creation, and no direct production mutation.

**Acceptance Criteria:**

- Customer feedback improves packs and evaluations without unsafe self-modification.
- Success data drives roadmap decisions.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_solution_packs.py -q
python -m pytest tests/api/test_customer_success_metrics.py -q
python -m pytest tests/api/test_customer_feedback_loop.py -q
python -m pytest -q
```

Expected final result: enterprise delivery becomes repeatable through versioned solution packs, rollout playbooks, adoption metrics, and feedback-to-evaluation loops.

# Solution Packs and Customer Success Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Productize enterprise delivery by packaging industry templates, custom skills, onboarding assets, demo data, rollout checklists, adoption metrics, and success playbooks into reusable solution packs.

**Architecture:** A Solution Pack is a versioned bundle that can seed a tenant with workspaces, agent templates, skills, connectors, knowledge spaces, evaluation cases, prompts, training assets, and success metrics. Packs are installed through onboarding or admin APIs, but all resources remain governed by tenant policy, approval, audit, and versioning.

**Tech Stack:** FastAPI, Pydantic, pytest, PostgreSQL later, object storage for training/demo assets, existing builder/skills/connectors/onboarding services.

---

## Summary

The product is an enterprise service, so implementation plans should cover repeatable delivery, not only platform primitives. This plan turns custom work into reusable packs that reduce cold start for each new customer.

## Task 1: Solution Pack Domain Model

**Files:**

- Create: `apps/api/src/taroai/solution_packs/__init__.py`
- Create: `apps/api/src/taroai/solution_packs/models.py`
- Create: `apps/api/src/taroai/solution_packs/registry.py`
- Test: `tests/api/test_solution_pack_models.py`

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

- Create: `apps/api/src/taroai/solution_packs/installer.py`
- Modify: `apps/api/src/taroai/onboarding/starter_packs.py`
- Test: `tests/api/test_solution_pack_installation.py`

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
python -m pytest tests/api/test_solution_pack_models.py -q
python -m pytest tests/api/test_solution_pack_installation.py -q
python -m pytest tests/api/test_customer_success_metrics.py -q
python -m pytest tests/api/test_customer_feedback_loop.py -q
python -m pytest -q
```

Expected final result: enterprise delivery becomes repeatable through versioned solution packs, rollout playbooks, adoption metrics, and feedback-to-evaluation loops.

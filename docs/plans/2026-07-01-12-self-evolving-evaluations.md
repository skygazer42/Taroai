# Self-Evolving and Evaluations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a controlled self-improvement pipeline where failed or low-rated runs produce reviewed improvement candidates for prompts, skills, workflows, retrieval, and policy, without allowing agents to mutate production behavior directly.

**Architecture:** Run traces feed an evaluation service. Evaluations classify failures and generate improvement candidates. Candidates are tested against eval suites, reviewed by owners/admins, versioned, and then published or rejected. Production skills/prompts/workflows change only through approved versions.

**Tech Stack:** Pydantic, pytest, optional promptfoo/Ragas/LangSmith/Langfuse adapters, PostgreSQL, existing audit/billing/observability services.

---

## Summary

This plan implements the enterprise-safe version of self-evolving:

- Analyze run outcomes.
- Classify failure reasons.
- Generate improvement candidates.
- Run repeatable tests/evals.
- Require human approval.
- Publish versioned changes with rollback.

It explicitly forbids agents from directly editing production skills, policies, permissions, secrets, or shared memory.

## Task 1: Evaluation Package

**Files:**

- Create: `apps/api/src/taroai/evaluations/__init__.py`
- Create: `apps/api/src/taroai/evaluations/models.py`
- Create: `apps/api/src/taroai/evaluations/service.py`
- Test: `tests/api/test_evaluation_contract.py`

**Steps:**

1. Define Pydantic models: `EvaluationCase`, `EvaluationRun`, `EvaluationResult`, `EvaluationMetric`.
2. Add an in-memory evaluation service for unit tests.
3. Support repeatable evals for output format, required citations, approval behavior, tool selection, and artifact existence.
4. Store eval results by tenant, agent, skill, version, and run ID.

**Acceptance Criteria:**

- Evaluations are explicit objects, not ad hoc prompts.
- Eval results can block publication.

## Task 2: Failure Taxonomy

**Files:**

- Create: `apps/api/src/taroai/evaluations/failures.py`
- Modify: `apps/api/src/taroai/observability/models.py`
- Test: `tests/api/test_failure_taxonomy.py`

**Steps:**

1. Define failure categories: policy_denied, tool_failed, sandbox_failed, retrieval_missing, hallucination, format_error, approval_rejected, timeout, cost_limit, unknown.
2. Add classifier that maps run trace/events to failure category.
3. Add tests for each category using synthetic run traces.
4. Store failure category on run/eval summary.

**Acceptance Criteria:**

- Failed runs are grouped into actionable categories.
- Unknown category remains available for unclassified cases.

## Task 3: Improvement Candidates

**Files:**

- Create: `apps/api/src/taroai/evaluations/improvements.py`
- Test: `tests/api/test_improvement_candidates.py`

**Steps:**

1. Define Pydantic models: `ImprovementCandidate`, `CandidatePatch`, `CandidateTarget`, `CandidateStatus`.
2. Candidate targets include prompt, skill manifest, workflow, retrieval config, policy config, memory candidate.
3. Generated candidates start as `draft`.
4. Candidates must include source run IDs, failure reason, proposed change, and risk level.
5. Candidates cannot directly write production targets.

**Acceptance Criteria:**

- Improvement candidates are reviewable artifacts.
- Candidate source and rationale are auditable.

## Task 4: Review and Approval Flow

**Files:**

- Modify: `apps/api/src/taroai/evaluations/service.py`
- Modify: `apps/api/src/taroai/audit/service.py`
- Test: `tests/api/test_improvement_review.py`

**Steps:**

1. Add candidate statuses: draft, testing, ready_for_review, approved, rejected, published, rolled_back.
2. Add reviewer decision model.
3. Only authorized owner/admin can approve.
4. Approval emits audit event.
5. Rejected candidates remain stored for analysis.

**Acceptance Criteria:**

- No candidate can publish without human approval.
- Review history is durable and auditable.

## Task 5: Versioned Publication

**Files:**

- Modify: `apps/api/src/taroai/skills/registry.py`
- Create: `apps/api/src/taroai/versioning/models.py`
- Create: `apps/api/src/taroai/versioning/service.py`
- Test: `tests/api/test_versioned_publication.py`

**Steps:**

1. Define versioned target model for skill, prompt, workflow, retrieval config, and policy config.
2. Publishing creates a new version, never mutates old version in place.
3. Add rollback method to restore previous active version.
4. Eval results must pass before publish.
5. Audit every publish and rollback.

**Acceptance Criteria:**

- Production changes are versioned.
- Rollback is tested.

## Task 6: Eval API and Admin UI Contract

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Future frontend files under `apps/web/app/evaluations`
- Test: `tests/api/test_evaluation_api.py`

**Steps:**

1. Add `GET /api/evaluations`.
2. Add `POST /api/evaluations/run`.
3. Add `GET /api/improvement-candidates`.
4. Add `POST /api/improvement-candidates/{id}/approve`.
5. Add `POST /api/improvement-candidates/{id}/reject`.
6. Require admin/owner permissions.

**Acceptance Criteria:**

- Evaluation and improvement candidates can be managed without database access.
- Permissions protect approval and publication.

## Task 7: Hard Safety Boundaries

**Files:**

- Test: `tests/api/test_self_evolving_safety_boundaries.py`

**Steps:**

1. Add tests that agents cannot grant themselves permissions.
2. Add tests that agents cannot modify secrets.
3. Add tests that candidates cannot publish without approval.
4. Add tests that shared memory changes remain candidates until approved.
5. Add tests that policy changes require admin approval.

**Acceptance Criteria:**

- Self-evolving cannot bypass enterprise governance.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_evaluation_contract.py -q
python -m pytest tests/api/test_failure_taxonomy.py -q
python -m pytest tests/api/test_improvement_candidates.py -q
python -m pytest tests/api/test_self_evolving_safety_boundaries.py -q
python -m pytest -q
```

Expected final result: the platform can learn from run outcomes through reviewed, versioned, evaluated improvements while preventing direct production mutation.

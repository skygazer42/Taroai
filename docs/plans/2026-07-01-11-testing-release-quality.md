# Testing, Release, and Quality Gates Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Define and implement the quality gates required to ship the enterprise Agent Workspace safely: unit, integration, contract, e2e, migration, security, performance, and release verification.

**Architecture:** Tests should match service boundaries. Backend uses pytest with Pydantic contract tests; frontend uses component and Playwright tests; deployment uses Docker/Kubernetes validation; release gates run in CI before merge/deploy.

**Tech Stack:** pytest, FastAPI TestClient, Docker, GitHub Actions or equivalent CI, optional coverage tooling, static frontend contract tests now, and Playwright/Next.js test runner later if the full portal moves to that stack.

---

## Summary

Current backend has pytest coverage for API foundation, agent runtime, storage, identity, memory, skills, and architecture contracts. This plan turns test discipline into a formal release system.

## Task 1: Test Taxonomy

**Files:**

- Create: `docs/testing/test-strategy.md`
- Modify: `pyproject.toml`
- Test: existing `tests/api/*`

**Steps:**

1. Document test categories: unit, contract, integration, e2e, migration, security, performance.
2. Map each package to required test types.
3. Add pytest markers: `unit`, `contract`, `integration`, `e2e`, `slow`, `requires_external`.
4. Keep external-provider tests skipped unless credentials are present.

**Acceptance Criteria:**

- Test intent is clear from markers and filenames.
- Unit tests do not require network or external credentials.

## Task 2: Backend Contract Gates

**Files:**

- Modify: `tests/api/test_backend_architecture_contract.py`
- Modify: `tests/api/test_backend_style_contract.py`
- Create: `tests/api/test_public_api_contract.py`

**Steps:**

1. Keep architecture tests for package boundaries.
2. Keep style test banning `from __future__ import annotations`.
3. Add public API contract tests for required endpoints and response shapes.
4. Add migration contract tests for required tables and sensitive columns.
5. Add Pydantic boundary tests for settings, request context, service models.

**Acceptance Criteria:**

- Regressions in package layout, API shape, migrations, or style fail fast.

## Task 3: Integration Test Harness

**Files:**

- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_run_lifecycle.py`
- Create: `tests/integration/test_tool_approval_flow.py`

**Steps:**

1. Add an integration fixture that wires API, store, runtime, tests-only tool gateway fixture, memory fixture, storage fixture, and policy fixture adapters.
2. Test full run lifecycle from API create to runtime success to artifact to billing/audit.
3. Test approval-required tool flow end to end.
4. Keep integration tests repeatable and no-network.

**Acceptance Criteria:**

- Full lifecycle can be tested without external providers.
- Failures identify which subsystem broke.

## Task 4: Future Frontend Test Contract

**Files:**

- Create: `docs/contracts/frontend-test-contract.md`
- Modify: `docs/plans/2026-07-01-08-client-portal-creao-ui.md` if UI requirements change.

**Steps:**

1. Add static contract tests for `data-testid="chat-column"`.
2. Add static contract tests for Enter sends and Shift+Enter inserts newline.
3. Add static contract tests for run event stream endpoint usage.
4. Add static contract tests for approval controls.
5. Document later mobile and desktop layout screenshot requirements.
6. Add Playwright only when the frontend test stack is approved.

**Acceptance Criteria:**

- CREAO-compatible chat column requirements are protected by a contract test.
- The static workspace slice has lightweight tests; full browser automation remains later.

## Task 5: CI Pipeline

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/docs.yml`
- Test: CI dry run where available, otherwise local command list.

**Steps:**

1. Add backend test job.
2. Add frontend build/test job once `apps/web` exists.
3. Add Docker build validation.
4. Add docs/plans lint/check job for required headers.
5. Cache dependencies safely.

**Acceptance Criteria:**

- PRs cannot merge with failing tests.
- Plans without required implementation-plan header fail docs check.

## Task 6: Release Checklist

**Files:**

- Create: `docs/release/release-checklist.md`
- Create: `docs/release/rollback.md`

**Steps:**

1. Define pre-release checks: tests, migrations, secrets, env, smoke test, audit/billing checks.
2. Define deployment steps.
3. Define rollback criteria and rollback steps.
4. Define post-release monitoring checks.

**Acceptance Criteria:**

- A release has a repeatable checklist.
- Rollback path is documented before production deployment.

## Verification

Run after implementation:

```bash
python -m pytest -q
python -m pytest -m "not requires_external" -q
```

When the full frontend stack is approved:

```bash
cd apps/web
npm test
npm run build
npm run test:e2e
```

Expected final result: quality gates catch API, architecture, migration, UI, security, and release regressions before deployment.

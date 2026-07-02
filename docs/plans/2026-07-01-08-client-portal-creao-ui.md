# Client Portal Contract and Final-Phase UI Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Define the frontend contract for a future CREAO-consistent client portal without implementing frontend code in the current backend MVP phase.

**Architecture:** Frontend implementation is deferred to a final managed phase. Current work must only stabilize API contracts, event payloads, UI state requirements, accessibility expectations, and acceptance checks so the later frontend can be built without guessing. Do not create frontend application files in the current milestone.

**Tech Stack:** Markdown contracts, OpenAPI/API contract tests, typed fixture definitions, backend event-stream tests, future Next.js App Router and Playwright only when the final frontend phase is explicitly approved.

---

## Current Decision

The frontend should not be implemented now. The user will manage frontend implementation later.

This plan therefore replaces direct frontend build tasks with contract and handoff tasks. The future frontend still needs to remain consistent with `https://agent.creao.ai/chat`, but no current task should scaffold a web app, add web dependencies, or create frontend components.

## Design Context for Later

Intent:

- Human: enterprise employee using an agent during real work, plus admins and solution engineers managing governance.
- Job: start tasks, monitor agent execution, approve risky actions, inspect artifacts, manage knowledge/skills/permissions.
- Feel: focused, utilitarian, calm, execution-oriented; not a marketing landing page and not a generic dashboard.

Domain concepts:

- Run timeline, approval gate, artifact handoff, cloud workspace, skill manifest, tenant boundary, audit trail, cost meter.

Interface signature:

- A chat-first execution column anchors the screen, with adjacent operational panels for timeline/artifacts.

Defaults to avoid later:

- Landing hero.
- Generic metric-card dashboard as first screen.
- Decorative card grids.
- Replacing chat flow with admin navigation.

## Task 1: Freeze Frontend Scope as Deferred

**Files:**

- Modify: `docs/plans/review-decisions.md`
- Modify: `docs/plans/open-questions.md`
- Modify: `docs/plans/review-status.md`
- Test: `python -m pytest -q`

**Steps:**

1. Record that frontend implementation is deferred to a final user-managed phase.
2. Mark Q-006 as answered.
3. Ensure MVP execution plans do not create frontend application files.
4. Keep CREAO consistency as a future acceptance contract, not a current implementation task.

**Acceptance Criteria:**

- Current MVP plans do not scaffold a frontend app.
- Review artifacts show frontend timing as answered.

## Task 2: Define API Contract Needed by Future UI

**Files:**

- Create: `docs/contracts/frontend-api-contract.md`
- Modify: `docs/plans/2026-07-01-14-api-sdk-contracts.md`
- Test: `tests/api/test_openapi_contract.py`

**Steps:**

1. List the future UI route needs: create run, execute run, stream run events, resolve approval, list artifacts, list meters, list audit events, list skills, query knowledge, and tenant readiness.
2. Define request/response examples from the current FastAPI routes and planned MVP routes.
3. Document required headers or future auth token format.
4. Add OpenAPI or route contract tests before any frontend work starts.
5. Keep contract wording backend-first and UI-framework-neutral.

**Acceptance Criteria:**

- Backend engineers can stabilize endpoints before UI implementation.
- Future frontend work can consume typed contracts without reading backend internals.

## Task 3: Define Run Event Stream Contract

**Files:**

- Create: `docs/contracts/run-event-stream-contract.md`
- Modify: `tests/api/test_app.py`
- Test: `tests/api/test_run_event_stream_contract.py`

**Steps:**

1. Document event types needed by a run timeline: created, status changed, context loaded, plan created, policy checked, step started, tool started, tool completed, approval requested, approval resolved, artifact created, succeeded, failed.
2. Define stable payload fields for each event.
3. Ensure events include tenant-safe run/workspace identifiers only.
4. Add contract tests for event order and payload shape.

**Acceptance Criteria:**

- Future UI can render timeline and status without guessing payloads.
- Event stream contract remains backend-testable without a frontend app.

## Task 4: Preserve CREAO-Compatible Chat Contract

**Files:**

- Create: `docs/contracts/creao-chat-ui-contract.md`
- Modify: `docs/plans/2026-07-01-02-technical-architecture.md`
- Test: `python -m pytest -q`

**Steps:**

1. Preserve the future `data-testid="chat-column"` requirement.
2. Preserve lower composer/help-text selector requirement: `[data-testid="chat-column"] > div:nth-of-type(4) > div:nth-of-type(2)`.
3. Preserve composer hint: `Press Enter to send, Shift+Enter for a new line.`
4. Preserve keyboard behavior: Enter sends; Shift+Enter inserts newline.
5. State that these are final-phase frontend acceptance checks, not current backend tasks.

**Acceptance Criteria:**

- CREAO consistency remains documented.
- No frontend files are created in the current milestone.

## Task 5: Define Admin and Skill Marketplace Data Contracts

**Files:**

- Create: `docs/contracts/admin-skill-ui-contract.md`
- Modify: `docs/plans/2026-07-01-05-skills-tool-gateway.md`
- Modify: `docs/plans/2026-07-01-13-enterprise-onboarding.md`
- Test: `python -m pytest -q`

**Steps:**

1. Document data needed for users, roles, knowledge settings, billing, audit, and skill marketplace views.
2. Map each future UI surface to backend API ownership.
3. Require permission-limited responses for non-admin users.
4. Document skill manifest fields needed by a future editor.
5. Keep actual UI layout and component decisions deferred.

**Acceptance Criteria:**

- Admin and skill marketplace backend contracts are explicit.
- Frontend implementation remains out of current scope.

## Task 6: Final-Phase Frontend Handoff Gate

**Files:**

- Create: `docs/contracts/frontend-final-phase-handoff.md`
- Modify: `docs/plans/review-status.md`
- Test: `python -m pytest -q`

**Steps:**

1. Define the evidence required before starting frontend implementation: stable API contracts, event stream contracts, auth/session contract, artifact contract, approval contract, and tenant readiness contract.
2. Define future visual checks: chat-first workspace, no landing page first, no marketing hero, no generic dashboard as first screen.
3. Define future test checks: data-testid, composer behavior, responsive stability, accessibility, event stream rendering, approval actions, artifact panel.
4. Require explicit human approval before creating frontend app files.

**Acceptance Criteria:**

- Frontend starts only after the backend contract gate passes and the user approves it.
- The final phase has clear acceptance criteria but no current implementation work.

## Verification

Run after updating this plan:

```bash
rg -n "frontend implementation is deferred|Do not create frontend application files|Final-Phase Frontend Handoff" docs/plans/2026-07-01-08-client-portal-creao-ui.md
! rg -n "Create: .*apps/w[e]b|Test: .*apps/w[e]b" docs/plans
python -m pytest -q
```

Expected final result: frontend implementation is explicitly deferred, CREAO-compatible requirements are preserved as future acceptance contracts, and the current milestone remains backend/API/runtime focused.

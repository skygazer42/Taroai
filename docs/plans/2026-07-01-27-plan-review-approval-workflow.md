# Plan Review and Approval Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Define how the 01-26 plans are reviewed, revised, approved, frozen, and handed off into implementation without losing scope control.

**Architecture:** Treat plans as controlled project artifacts. Each plan has a status, owner, review checklist, decision log, open questions, dependency links, and evidence gates. New implementation milestones start only after the relevant plan group is approved and the MVP scope decision is recorded.

**Tech Stack:** Markdown, repo-local docs, pytest verification.

---

## Summary

The original planning request was to write plans first, then review before deciding implementation. This document defines that review workflow so the plan set can move from broad architecture into approved execution without losing scope control.

It does not add new product scope. It controls the existing plan set.

## Plan Status Model

Use these statuses in review notes:

| Status | Meaning | Allowed Next Status |
| --- | --- | --- |
| `draft` | Written but not reviewed. | `in_review`, `rework` |
| `in_review` | Reviewer is checking scope, assumptions, dependencies, and acceptance criteria. | `approved`, `rework`, `deferred` |
| `rework` | Needs changes before implementation. | `in_review`, `deferred` |
| `approved` | Approved for implementation planning or execution. | `frozen`, `rework` |
| `frozen` | Scope is locked for implementation; changes require a decision log entry. | `rework` |
| `deferred` | Valid but not in current milestone. | `in_review`, `approved` |

## Review Groups

Review plans in groups rather than one giant pass:

| Review Group | Plans | Decision Required |
| --- | --- | --- |
| Product and architecture | 01, 02, 25 | Confirm product definition, enterprise positioning, MVP boundaries, and execution phases. |
| Backend foundation | 03, 07, 10, 11, 14 | Confirm persistence, identity, policy, billing/audit, quality gates, and API contracts. |
| Runtime and governance | 04, 05, 06, 15, 17, 21 | Confirm knowledge, skills, tool gateway, sandbox, connectors, model gateway, and guardrails. |
| Client and onboarding | 08, 13, 16, 18, 19 | Confirm CREAO-compatible UI, onboarding, sharing, triggers, and builder scope. |
| Enterprise hardening | 12, 20, 22, 23, 24 | Confirm evals, data lifecycle, incidents, solution packs, and private deployment timing. |
| MVP execution | 26 | Confirm the first implementation milestone and what is explicitly deferred. |

## Reviewer Checklist

For each plan or review group, answer:

- Is the plan necessary for the enterprise Agent Workspace goal?
- Is it MVP, enterprise-hardening, or post-MVP?
- Does it duplicate another plan?
- Are the package boundaries clear?
- Are Pydantic backend management models required where needed?
- Does any backend source plan violate the no `from __future__ import annotations` rule?
- Are storage choices explicit for PostgreSQL, Redis, object storage, vector data, and secrets?
- Are tenant, workspace, user, role, and resource boundaries explicit?
- Are billing, audit, and observability hooks covered for risky actions?
- Does every model call in product/MVP flow go through an OpenAI-compatible Model Gateway boundary?
- Are prototype/test provider classes excluded from product and MVP flow wording?
- Are tests named and scoped enough to guide implementation?
- Are frontend requirements consistent with `https://agent.creao.ai/chat`?
- Are deferred items explicitly listed?
- Are review blockers written as decisions, not vague concerns?

## Decision Log Template

Record decisions in `docs/plans/review-decisions.md` using this format:

```markdown
## YYYY-MM-DD: <decision title>

**Status:** proposed | accepted | rejected | superseded

**Decision:** <one clear sentence>

**Context:** <why this decision exists>

**Impacted Plans:** 03, 06, 08

**Implementation Impact:** <what changes for build order or scope>

**Owner:** <name or role>
```

## Open Questions Template

Record unresolved questions in `docs/plans/open-questions.md`:

```markdown
## Q-001: <question>

**Status:** open | answered | deferred

**Why It Matters:** <risk or dependency>

**Options:**
- A: <option>
- B: <option>

**Recommended Default:** <option and reason>

**Decision Needed Before:** plan review | MVP milestone approval | private deployment
```

## Approval Gate for MVP Execution

Before implementing plan 26, the reviewer should explicitly approve:

1. MVP route list.
2. First auth mode.
3. First storage adapters.
4. First knowledge backend.
5. First model gateway strategy, with OpenAI-compatible Model Gateway as the product-flow boundary.
6. First sandbox provider seam.
7. First starter pack.
8. Frontend deferred-contract decision is acknowledged.
9. Non-goals for MVP.
10. End-to-end acceptance scenario.

If any item remains undecided, default to the conservative choice in plan 26:

- Product-flow provider contracts with tests-only fixture adapters only in tests.
- Adapter seam for external provider.
- Cloud PoC before private packaging.
- Password PoC plus dev headers only behind settings.
- General starter pack before industry-specific pack.

## Change Control

After a plan group is frozen:

1. Do not edit the approved plan silently.
2. Add a decision log entry.
3. Update impacted plan files.
4. Update `README.md` if index, status, or implementation order changes.
5. Re-run evidence gates.

Accepted small changes:

- Typo fixes.
- Broken link fixes.
- Clarifying wording that does not change scope.

Changes requiring review:

- New package boundary.
- New storage backend.
- New external provider.
- New public API route.
- Change to tenant isolation, RBAC, audit, billing, memory, or sandbox behavior.
- Moving a deferred item into MVP.

## Evidence Gates

Run before marking any plan group `approved`:

```bash
find docs/plans -maxdepth 1 -type f -name '2026-07-01-*.md' | sort
rg -n "REQUIRED SUB-SKILL|Goal:|Architecture:|Tech Stack:|Verification" docs/plans/2026-07-01-*.md
rg -n "from __future__ import annotations" apps/api/src tests
rg -n "OpenAI-compatible Model Gateway" docs/plans/2026-07-01-02-technical-architecture.md docs/plans/2026-07-01-17-model-gateway-provider-governance.md docs/plans/2026-07-01-26-mvp-cloud-poc-execution.md
! rg -n "m[o]ck|M[o]ck|f[a]ke|F[a]ke|M[o]ckModelProvider" docs/plans
python -m pytest -q
```

Expected evidence:

- Every dated implementation plan has the standard header.
- Verification sections exist for implementation plans.
- Backend source does not use `from __future__ import annotations`.
- Product/MVP model flow is described through OpenAI-compatible Model Gateway.
- Plans do not name prototype/test provider classes in product-flow docs.
- Existing test suite passes.

## Implementation Handoff

When the reviewer approves a group:

1. Mark the group decision in `docs/plans/review-decisions.md`.
2. Create or choose an implementation branch/worktree.
3. Start from the smallest approved task.
4. Use TDD for code changes.
5. Keep unrelated plans unchanged.
6. After each implementation slice, update the relevant plan only if implementation reality changes the plan.

## Verification

Run after creating or updating this review workflow:

```bash
rg -n "Plan Review and Approval Workflow|Approval Gate for MVP Execution|Decision Log Template" docs/plans
python -m pytest -q
```

Expected final result: the plan set has a concrete review and approval workflow that lets the user audit scope before the next implementation milestone is approved.

# Plan Review Status

This file tracks review status for the Taroai plan set.

Status values come from `2026-07-01-27-plan-review-approval-workflow.md`:

- `draft`
- `in_review`
- `rework`
- `approved`
- `frozen`
- `deferred`

Initial state: all plans are `draft` until a human reviewer starts review.

## Summary

| Review Group | Plans | Current Status | MVP Blocking | Next Action |
| --- | --- | --- | --- | --- |
| Product and architecture | 01, 02, 25 | draft | yes | Confirm product definition, service boundaries, and MVP scope. |
| Backend foundation | 03, 07, 10, 11, 14 | draft | yes | Review persistence, identity, policy, billing/audit, testing, and API contracts. |
| Runtime and governance | 04, 05, 06, 15, 17, 21 | draft | yes | Review knowledge, skills, runtime, connectors, model gateway, and guardrails. |
| Client and onboarding | 08, 13, 16, 18, 19 | draft | partial | Review frontend contracts, onboarding, sharing, triggers, and builder scope. Frontend implementation is deferred. |
| Enterprise hardening | 12, 20, 22, 23, 24 | draft | no | Decide what stays post-MVP. |
| MVP execution | 26 | draft | yes | Approve or revise the first implementation milestone. |
| Review workflow | 27 | draft | yes | Confirm review process, decision log, open questions, and change control. |

## Plan-Level Status

| Plan | Title | Review Group | Status | MVP Blocking | Notes |
| --- | --- | --- | --- | --- | --- |
| 01 | Product Logic | Product and architecture | draft | yes | Approve enterprise positioning and scope. |
| 02 | Technical Architecture | Product and architecture | draft | yes | Approve service boundaries and data model direction. |
| 03 | Storage, Identity, and Memory Backbone | Backend foundation | draft | yes | Required for tenant/user/role/memory/storage MVP. |
| 04 | Knowledge, RAG, and Long-Term Memory | Runtime and governance | draft | yes | Required for ACL-aware context loading. |
| 05 | Skills and Tool Gateway | Runtime and governance | draft | yes | Required for governed tools and custom skill direction. |
| 06 | Agent Runtime and Sandbox | Runtime and governance | draft | yes | Required for long-running agent execution and sandbox seam. |
| 07 | Billing, Audit, and Observability | Backend foundation | draft | yes | Required for enterprise governance and cost visibility. |
| 08 | Client Portal Contract and CREAO-Compatible UI | Client and onboarding | draft | partial | Minimal static workspace slice started; full portal remains later. |
| 09 | Deployment and Operations | Enterprise hardening | draft | partial | Local cloud PoC pieces are MVP; broader K8s/ops can follow. |
| 10 | Security and Compliance | Backend foundation | draft | yes | Required for tenant isolation, secrets, policy, and audit. |
| 11 | Testing, Release, and Quality Gates | Backend foundation | draft | yes | Required before implementation can be trusted. |
| 12 | Self-Evolving and Evaluations | Enterprise hardening | draft | no | Defer full self-evolving pipeline from MVP. |
| 13 | Enterprise Tenant Onboarding | Client and onboarding | draft | yes | Required for pilot tenant setup and readiness. |
| 14 | API Contract and SDK | Backend foundation | draft | yes | API contracts are needed before frontend/SDK work. |
| 15 | Enterprise Connectors | Runtime and governance | draft | partial | Broad catalog can defer; connector boundary affects tool design. |
| 16 | Sharing, Collaboration, and Artifact Delivery | Client and onboarding | draft | partial | Artifact access is MVP; broader collaboration can follow. |
| 17 | Model Gateway and Provider Governance | Runtime and governance | draft | yes | Gateway boundary is started; provider governance, policy, metering, and routing still require review. |
| 18 | Triggers, Scheduling, and Automation | Client and onboarding | draft | no | Defer broad automation unless required for pilot. |
| 19 | Agent Builder and Workflow Templates | Client and onboarding | draft | no | Defer builder UI; keep template concepts aligned with skills. |
| 20 | Data Lifecycle, Backup, and Recovery | Enterprise hardening | draft | partial | Basic retention/export assumptions matter; full DR can follow. |
| 21 | Prompt and Guardrail Governance | Runtime and governance | draft | partial | Minimal guardrails are MVP; full registry can follow. |
| 22 | Incident, SLO, and Support Operations | Enterprise hardening | draft | no | Post-MVP enterprise operations. |
| 23 | Solution Packs and Customer Success | Enterprise hardening | draft | no | General starter pack matters; full solution packs can follow. |
| 24 | Private Deployment and Packaging | Enterprise hardening | draft | no | Deferred unless first customer requires private deployment. |
| 25 | Roadmap and Coverage Matrix | Product and architecture | draft | yes | Review before approving plan set direction. |
| 26 | MVP Cloud PoC Execution | MVP execution | draft | yes | Main implementation handoff plan. |
| 27 | Plan Review and Approval Workflow | Review workflow | draft | yes | Review process before the next implementation milestone is approved. |

## Current Review Blockers

The current blockers are open questions, not implementation failures:

- Q-001: first industry pack.
- Q-002: first sandbox provider.
- Q-003: first vector backend.
- Q-004: first model gateway strategy.
- Q-005: MVP auth mode.
- Q-007: BYOC/private deployment priority.
- Q-008: API versioning timing.

See `open-questions.md` for details and recommended defaults.

## Suggested First Review Session

Start with these files:

1. `research-grounding.md`
2. `review-readiness-audit.md`
3. `mvp-review-packet.md`
4. `2026-07-01-01-product-logic.md`
5. `2026-07-01-02-technical-architecture.md`
6. `2026-07-01-25-roadmap-coverage-matrix.md`
7. `2026-07-01-26-mvp-cloud-poc-execution.md`
8. `open-questions.md`

Expected output of the first review session:

- Product and architecture group status changes from `draft` to `approved` or `rework`.
- MVP execution plan changes from `draft` to `approved` or `rework`.
- At least Q-001 through Q-005 and Q-008 are answered or explicitly deferred; Q-007 is needed only if private deployment becomes sales-critical.
- Accepted decisions are copied into `review-decisions.md`.

## Evidence Gate

Run before changing any group to `approved`:

```bash
find docs/plans -maxdepth 1 -type f -name '2026-07-01-*.md' | sort
rg -n "Review artifacts|Plan Review Status|Plan Review Decisions|Plan Review Open Questions" docs/plans
rg -n "Current Repo Facts|Source-Backed Terminology|Implementation Wording Rules" docs/plans/research-grounding.md
rg -n "^# Review Readiness Audit|^## Requirement Coverage|^## Open Human Decisions" docs/plans/review-readiness-audit.md
rg -n "OpenAI-compatible Model Gateway" docs/plans/2026-07-01-02-technical-architecture.md docs/plans/2026-07-01-17-model-gateway-provider-governance.md docs/plans/2026-07-01-26-mvp-cloud-poc-execution.md
! rg -n "m[o]ck|M[o]ck|f[a]ke|F[a]ke|M[o]ckModelProvider" docs/plans
python -m pytest -q
```

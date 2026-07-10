# MVP Review Packet

This packet is the shortest path to review the current plan set and decide whether the MVP cloud PoC can move into implementation.

It summarizes:

- What is already accepted.
- What must still be decided.
- Which plans to read first.
- What approval means.

## Recommended Review Order

Read these files in order:

1. `research-grounding.md`
2. `review-readiness-audit.md`
3. `2026-07-01-01-product-logic.md`
4. `2026-07-01-02-technical-architecture.md`
5. `2026-07-01-25-roadmap-coverage-matrix.md`
6. `2026-07-01-26-mvp-cloud-poc-execution.md`
7. `open-questions.md`
8. `review-decisions.md`
9. `review-status.md`

## Already Accepted Defaults

These decisions are already recorded in `review-decisions.md`:

| Decision | Current Accepted Default |
| --- | --- |
| First delivery target | Cloud PoC first; private/BYOC/air-gapped later. |
| External providers | Use adapter seams for model, sandbox, vector, connector, and secret providers. |
| Backend management models | Use Pydantic at settings, API, service, and management boundaries. |
| Backend annotations style | Do not use `from __future__ import annotations`. |
| First frontend shape | CREAO-compatible chat workspace, no marketing landing page first. |

## MVP Approval Questions

Answer these before plan 26 moves from `draft` to `approved`.

| ID | Decision | Recommended Default | Approve Default? | Override |
| --- | --- | --- | --- | --- |
| Q-001 | First industry pack | General starter pack only. | TBD |  |
| Q-002 | First sandbox provider | Sandbox adapter contract now; E2B if real cloud execution is needed quickly. | TBD |  |
| Q-003 | First vector backend | Internal retrieval contract first, then pgvector for durable PoC. | TBD |  |
| Q-004 | First model gateway strategy | OpenAI-compatible Model Gateway contract first; evaluate LiteLLM if multi-provider routing is needed. | TBD |  |
| Q-005 | MVP auth mode | Password PoC plus dev headers behind settings. | TBD |  |
| Q-006 | Frontend timing | Answered: implement a minimal static workspace for local PoC; defer full portal. | Accepted |  |
| Q-007 | Private deployment priority | Not sales-critical for MVP unless a named customer requires it. | TBD |  |
| Q-008 | API versioning timing | Keep `/api/*` for MVP; plan `/api/v1` before external SDK release. | TBD |  |

## MVP Scope to Approve

Approve this scope if the first milestone should build a useful enterprise cloud PoC:

- Tenant/workspace/user/role and request-context enforcement.
- Password PoC auth and future SSO seam.
- PostgreSQL metadata, Redis short-term memory, S3/MinIO artifacts.
- API run lifecycle with event stream and unified errors.
- ACL-aware knowledge retrieval.
- Skill registry and Tool Gateway policy checks.
- OpenAI-compatible Model Gateway boundary for all runtime model calls.
- Bounded Agent Runtime with approval pause/resume and sandbox adapter seam.
- Billing/audit/trace events for run, model, tool, storage, approval, and memory operations.
- CREAO-consistent frontend contract for later final-phase implementation.
- Enterprise onboarding with starter workspace and readiness report.
- One end-to-end acceptance test proving tenant setup to governed artifact output.

## Explicit MVP Deferrals

Keep these out of MVP unless review decisions override them:

- Full private/BYOC/air-gapped packaging.
- Full self-evolving publication pipeline.
- Full solution-pack marketplace.
- Advanced SLO/incident/support operations.
- Broad connector catalog.
- Full visual workflow builder.
- Large third-party skill ecosystem.

## Approval Outcomes

Choose one outcome:

| Outcome | Meaning | Required Follow-Up |
| --- | --- | --- |
| Approve plan 26 as written | The next MVP milestone can proceed from plan 26. | Mark relevant rows in `review-status.md` as `approved`; copy answers into `review-decisions.md`. |
| Approve with changes | The next MVP milestone can proceed after specific changes are applied. | Update impacted plan files, then rerun evidence gates. |
| Rework | MVP scope or architecture needs revision before milestone approval. | Mark plan 26 as `rework` and list required edits. |
| Defer implementation | Plans remain useful but the milestone should not proceed. | Mark plan 26 as `deferred` and record reason. |

## Sign-Off Template

Copy this into `review-decisions.md` after review:

```markdown
## YYYY-MM-DD: Approve MVP Cloud PoC Scope

**Status:** accepted

**Decision:** Plan 26 is approved for MVP cloud PoC implementation with the selected open-question answers.

**Context:** The plan set has been reviewed against product positioning, architecture, MVP scope, and current code foundation.

**Impacted Plans:** 01, 02, 03, 04, 05, 06, 07, 08, 10, 13, 14, 17, 21, 25, 26, 27

**Implementation Impact:** Implementation starts from `2026-07-01-26-mvp-cloud-poc-execution.md`; the minimal static workspace is in scope for local PoC visibility, while the full frontend portal remains deferred unless separately approved.

**Owner:** product/engineering
```

## Evidence Gate

Run before sign-off:

```bash
find docs/plans -maxdepth 1 -type f -name '2026-07-01-*.md' | sort
rg -n "MVP Review Packet|MVP Approval Questions|MVP Scope to Approve" docs/plans
rg -n "^# Review Readiness Audit|^## Requirement Coverage|^## Open Human Decisions" docs/plans/review-readiness-audit.md
rg -n "OpenAI-compatible Model Gateway" docs/plans/2026-07-01-17-model-gateway-provider-governance.md docs/plans/2026-07-01-26-mvp-cloud-poc-execution.md
! rg -n "m[o]ck|M[o]ck|f[a]ke|F[a]ke|M[o]ckModelProvider" docs/plans
python -m pytest -q
```

Expected evidence:

- Plan files are present.
- Review packet and review artifacts are linked from `README.md`.
- Review readiness audit maps requirements to evidence and open decisions.
- External terminology has a source-backed note in `research-grounding.md`.
- Product/MVP flow uses OpenAI-compatible Model Gateway wording and does not name prototype/test provider classes.
- Existing tests pass.

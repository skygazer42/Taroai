# Plan Review Open Questions

This file tracks decisions needed before the next MVP milestone is approved or before later milestones are pulled forward.

Question status values:

- `open`: needs a decision.
- `answered`: decision exists in `docs/plans/review-decisions.md`.
- `deferred`: not needed for the current milestone.

## Q-001: Which industry pack should the MVP target first?

**Status:** open

**Why It Matters:** The starter pack affects onboarding defaults, demo data, skill examples, knowledge examples, and acceptance scenarios.

**Options:**

- A: General starter pack only.
- B: Ecommerce operations.
- C: Sales/account research.
- D: Support/ticket triage.
- E: Internal operations/SOP assistant.

**Recommended Default:** A: General starter pack only, then add one industry pack after the core run path is stable.

**Decision Needed Before:** MVP milestone approval

## Q-002: Which sandbox provider should the cloud PoC use first?

**Status:** open

**Why It Matters:** Sandbox choice affects runtime integration, security posture, cost, deployment, and local development.

**Options:**

- A: Sandbox adapter contract now; tests-only adapter fixture only for tests.
- B: E2B-first cloud sandbox.
- C: Kubernetes Docker adapter.
- D: Another managed sandbox provider.

**Recommended Default:** A for implementation sequencing, then B if cloud PoC needs real browser/filesystem execution quickly. Test adapters must not appear in product flow.

**Decision Needed Before:** MVP milestone approval

## Q-003: Which vector backend should be used first?

**Status:** open

**Why It Matters:** Knowledge/RAG implementation depends on indexing, filtering, metadata, and deployment cost.

**Options:**

- A: Internal retrieval contract first, then pgvector.
- B: pgvector immediately.
- C: Qdrant.
- D: Milvus.
- E: Weaviate.
- F: Hosted vector provider.

**Recommended Default:** A for implementation sequencing; B for the first durable cloud PoC unless scale requires a dedicated vector DB.

**Decision Needed Before:** MVP milestone approval

## Q-004: Which model gateway strategy should be first?

**Status:** open

**Why It Matters:** Model routing affects cost controls, provider credentials, observability, latency, and private deployment options.

**Options:**

- A: OpenAI-compatible Model Gateway contract first; tests-only adapter fixture only in tests.
- B: LiteLLM-first.
- C: Direct OpenAI-compatible gateway only.
- D: Tenant-configurable choice between LiteLLM and direct adapters.

**Recommended Default:** A. Product flow should call the OpenAI-compatible Model Gateway; tests-only adapters stay outside the flow. Evaluate B for cloud PoC if multi-provider routing is needed early.

**Decision Needed Before:** MVP milestone approval

## Q-005: What auth mode should be allowed in the MVP?

**Status:** open

**Why It Matters:** Auth mode affects request context, onboarding, frontend login, admin roles, and security posture.

**Options:**

- A: Password PoC plus dev headers behind settings.
- B: Password PoC only.
- C: OIDC first.
- D: SAML/SCIM first.

**Recommended Default:** A for local speed, with dev headers disabled by default outside local environment.

**Decision Needed Before:** MVP milestone approval

## Q-006: How should frontend work be handled in the current milestone?

**Status:** answered

**Why It Matters:** Parallel frontend can validate product direction faster, but early backend API churn can cause rework.

**Options:**

- A: Defer frontend implementation to the final user-managed phase; write backend/API/event contracts now.
- B: Defer frontend until backend MVP API contract tests are written.
- C: Defer frontend until typed API fixtures are ready.
- D: Defer frontend until backend end-to-end tests pass.

**Decision:** A. Frontend implementation is not part of the current MVP milestone. Current work should only preserve API, event, and CREAO UI contracts for a later final phase.

**Decision Source:** User instruction: "前端 不要实现 我们最后管理"

## Q-007: Is BYOC/private deployment sales-critical for the first customer?

**Status:** open

**Why It Matters:** If private deployment is required for the first customer, plans 20 and 24 need to move earlier and affect architecture decisions now.

**Options:**

- A: Not sales-critical; keep private packaging post-MVP.
- B: Required for pilot.
- C: Required only after cloud PoC validation.

**Recommended Default:** A unless a named customer requires private deployment before pilot.

**Decision Needed Before:** private deployment

## Q-008: Should the API migrate to `/api/v1` before the final frontend phase?

**Status:** open

**Why It Matters:** API versioning affects future frontend client paths, OpenAPI contracts, SDKs, and backward compatibility.

**Options:**

- A: Keep current `/api/*` for MVP and plan `/api/v1` later.
- B: Migrate to `/api/v1` before the final frontend phase.
- C: Support both during MVP.

**Recommended Default:** A for speed; record versioning migration in plan 14 before external SDK release.

**Decision Needed Before:** MVP milestone approval

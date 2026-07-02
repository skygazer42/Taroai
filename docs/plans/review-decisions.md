# Plan Review Decisions

This file records plan-review decisions for the Taroai enterprise Agent Workspace.

Decision status values:

- `proposed`: suggested but not accepted.
- `accepted`: approved and should guide implementation.
- `rejected`: considered and declined.
- `superseded`: replaced by a later decision.

## 2026-07-01: Use Cloud PoC as the First Implementation Target

**Status:** accepted

**Decision:** The first implementation milestone targets a cloud PoC, not BYOC, private deployment, or air-gapped deployment.

**Context:** The product needs fast enterprise validation around tenant setup, governed runs, knowledge, skills, sandbox seam, audit, billing, and a CREAO-compatible workspace before investing in private packaging.

**Impacted Plans:** 09, 20, 24, 25, 26

**Implementation Impact:** Private deployment work remains planned but deferred; plan 26 is the first execution milestone.

**Owner:** product/engineering

## 2026-07-01: Keep External Providers Behind Adapter Seams

**Status:** accepted

**Decision:** Model, sandbox, vector, connector, and secret providers must be accessed through adapter interfaces instead of direct calls from runtime or route handlers.

**Context:** The platform must support cloud-first deployment and later private/BYOC options without rewriting core agent logic.

**Impacted Plans:** 04, 05, 06, 10, 15, 17, 20, 21, 24, 26

**Implementation Impact:** Product flow must use provider contracts, not prototype/test provider classes. Test fixtures stay in test code and are never runtime defaults; real providers can be added behind the same contracts later.

**Owner:** platform engineering

## 2026-07-01: Keep Backend Management Models in Pydantic

**Status:** accepted

**Decision:** Backend configuration, request/response contracts, service boundary models, and management primitives should be Pydantic models.

**Context:** The codebase already uses Pydantic for settings, domain models, store state, identity, memory, skills, storage, agent runtime state, and API error responses.

**Impacted Plans:** 02-27

**Implementation Impact:** New backend packages should define Pydantic models before service implementations; tests should continue enforcing the Pydantic boundary for core management objects.

**Owner:** backend engineering

## 2026-07-01: Do Not Use `from __future__ import annotations`

**Status:** accepted

**Decision:** Backend source should not use `from __future__ import annotations`.

**Context:** This was explicitly requested and is already covered by style tests.

**Impacted Plans:** 02, 03, 11, 26

**Implementation Impact:** New backend files must avoid future annotations; keep `tests/api/test_backend_style_contract.py` as a gate.

**Owner:** backend engineering

## 2026-07-01: Preserve CREAO-Compatible Chat Structure for the First Frontend Slice

**Status:** accepted

**Decision:** The first frontend slice should open on the employee chat workspace and preserve the required `data-testid="chat-column"` structure and Enter/Shift+Enter composer behavior.

**Context:** The user provided `https://agent.creao.ai/chat` as the frontend consistency target and called out the composer behavior explicitly.

**Impacted Plans:** 08, 14, 16, 19, 26

**Implementation Impact:** Do not build a marketing landing page first; frontend tests should verify the chat-column and composer behavior.

**Owner:** frontend engineering

## 2026-07-01: Ground External Terminology in Official Sources and Current Code

**Status:** accepted

**Decision:** Before expanding or approving plans that mention external frameworks, protocols, sandbox providers, storage engines, frontend frameworks, observability systems, or model gateways, reviewers must check `research-grounding.md` and inspect current code state.

**Context:** The plan set uses terms such as LangGraph, LangChain, LlamaIndex, MCP, E2B, pgvector, Redis, PostgreSQL RLS, Next.js, and OpenTelemetry. Some are implemented foundations, some are adapter seams, and some are candidate providers. The plans should not describe planned capabilities as already implemented.

**Impacted Plans:** 01-27

**Implementation Impact:** Use official sources for terminology, mark candidate providers as candidates until open questions are answered, and update `research-grounding.md` when new external terms or provider decisions are introduced.

**Owner:** product/engineering

## 2026-07-01: Keep Test Fixtures Out of Product Flow

**Status:** accepted

**Decision:** Product and MVP flows must not name or depend on prototype/test provider classes. Model flow uses an OpenAI-compatible Model Gateway boundary; tool flow uses Tool Gateway; sandbox flow uses Sandbox Adapter. Tests-only fixture adapters are allowed only in tests, fixtures, and contract verification.

**Context:** Earlier prototype code included test-only planning/tool classes, but plan language should not promote adapter fixtures into architecture or delivery flow.

**Impacted Plans:** 02, 06, 11, 17, 24, 26

**Implementation Impact:** Current runtime planning now uses an OpenAI-compatible Model Gateway boundary with basic planning usage meters, Settings-backed global and tenant/workspace-scoped model policy, API/SQL-managed policy storage, and run/tenant/workspace/user/agent model budget guards. Remaining MVP work adds provider references, sensitivity constraints, budget windows, rate limits, richer provider/cached-token/latency metering, and reviewed provider adapters behind that boundary.

**Owner:** backend/platform engineering

## 2026-07-01: Defer Frontend Implementation to Final Managed Phase

**Status:** accepted

**Decision:** Frontend implementation is deferred. The current MVP milestone should not scaffold or implement a web app; it should only define backend API contracts, event stream contracts, and CREAO-compatible UI acceptance requirements for the final user-managed frontend phase.

**Context:** The current repo has no frontend implementation, and the user explicitly clarified that frontend should not be implemented now.

**Impacted Plans:** 08, 11, 14, 25, 26, 27

**Implementation Impact:** Remove direct frontend build tasks from the current MVP path. Keep future UI requirements as contract documents and backend tests. Require explicit human approval before creating frontend app files.

**Owner:** product/engineering

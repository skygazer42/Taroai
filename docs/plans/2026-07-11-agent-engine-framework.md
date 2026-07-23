# Agent Engine Framework Implementation Plan


**Goal:** Add first-class multi-engine connections and sessions for Native, OpenCode, Codex, and Claude runners.

**Architecture:** A registry persists connection/session/event metadata. A normalized adapter forwards lifecycle operations to remote runners while Taroai keeps governance and Secret references. Agent versions select the Engine through their runtime snapshot.

**Tech Stack:** FastAPI, Pydantic, existing repository patterns, urllib HTTP transport, vanilla JavaScript Agent Brain UI.

---

### Task 1: Engine domain and registry

Create `apps/api/src/taroai/agent_engines/` models and memory/SQL registries. Add an additive migration for connections, sessions, events, and approvals.

### Task 2: Adapter and service

Implement the normalized adapter contract for capabilities, session creation, turns, steering, event refresh, approvals, cancel, resume, and close. Keep Secret values behind short-lived leases.

### Task 3: API wiring

Wire registry/service construction into API and worker configuration. Add workspace-scoped CRUD and session lifecycle routes with audit events.

### Task 4: Agent runtime selection

Allow an Agent version to pin `engine_type` and `engine_connection_id`; validate the connection on publish and delegate eligible Runs through the selected Engine.

### Task 5: Agent Brain UI

Add an Engines panel for connection management, capability inspection, session state, steering, approvals, and cancellation.

### Task 6: Commit

Commit functional implementation to `main`. Per user instruction, do not run tests, database validation, lint, typecheck, Docker, or browser QA in this batch.

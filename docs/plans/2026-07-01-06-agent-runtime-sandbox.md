# Agent Runtime and Sandbox Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current in-memory Agent Runtime into a production-shaped runtime with explicit state persistence, bounded multi-agent delegation, policy checks, sandbox execution, and resumable approvals.

**Architecture:** Keep runtime code inside `taroai/agent`, sandbox code inside `taroai/sandbox`, and tool execution behind Tool Gateway. Agent Runtime owns state transitions; Sandbox Adapter owns isolated code/browser execution; Tool Gateway owns permission, secret, audit, and billing checks.

**Tech Stack:** Python, Pydantic, LangGraph, LangChain, FastAPI, PostgreSQL, Redis, Docker/E2B/K8s sandbox adapters, pytest.

---

## Summary

Current state has:

- `taroai/agent/runtime.py`: Pydantic runtime with plan, retry, cancellation, approval pause/resume/rejection, artifact finalization, PolicyService-backed Tool Gateway scope resolution for runtime-planned tool calls, and first-pass automatic sandbox session lifecycle for `sandbox.command` steps.
- `taroai/agent/graph.py`: LangGraph graph construction seam.
- `taroai/agent/planning.py`, `state.py`, `tools.py`: separated runtime primitives.
- `taroai/store.py`: in-memory runtime state snapshots so state reads, approval resume, or rejection can recover after process-local pending state is lost.
- `taroai/sandbox/`: Pydantic sandbox session/command/file/snapshot and browser action models, disabled default sandbox/browser provider boundaries, a `local_process` provider for local cloud PoC command execution inside per-session workspaces, a first-pass Docker provider that starts detached containers with `--network none`, Settings-managed memory/CPU/pids limits, non-root `--user`, read-only rootfs, `cap-drop=ALL`, `security-opt`, tmpfs mounts, per-session workspace bind mounts, file upload/download/list/snapshot, command execution through `docker exec`, and destroy lifecycle, Settings-backed HTTP browser provider adapter for `playwright`/`browserbase` controller services in both API and worker startup paths, first-pass Playwright HTTP browser controller service with session/action/screenshot contract and local Compose wiring, Tool Gateway `sandbox.command` and `browser.action` handlers wired into default runtime construction through provider contracts, sandbox command lease-handle environment delivery without raw secret values, sandbox-scoped lease resolution API with run/step/session validation, optional provider token enforcement, and safe audit metadata, sandbox API endpoints for session create, command execution, file upload/download, snapshot, destroy, and browser actions, first-pass runtime `sandbox.command` and `browser.action` session creation/session-id injection, persisted sandbox/browser session IDs in runtime state snapshots, declared `artifact_paths`/`artifact_path` file download only under `/workspace/artifacts/`, automatic `/workspace/artifacts/**` file discovery when no explicit artifact path is declared, rejection of declared artifact paths outside `/workspace/artifacts/`, artifact-stage guardrail content evaluation and storage content scanning before upload, storage upload, artifact creation, safe `tool_call.completed` result summaries without raw sandbox stdout/stderr or browser text, and success/failure/approval-rejection/cancellation cleanup for runtime-created sessions, `sandbox.create`/`sandbox.execute`/`browser.act` permission checks, session/command/file/snapshot/destroy/browser audit metadata, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload, `sandbox_minutes` metering for command execution, `artifact_bytes` metering for file upload, and `browser_action_count` metering for browser actions when a run exists. Local contract adapters exist only under `tests/` for isolated contract coverage.

This plan adds persistence, sandbox execution, bounded multi-agent behavior, and production contracts without letting the runtime become an unbounded swarm.

## Task 1: Runtime State Persistence Contract

**Files:**

- Modify: `apps/api/src/taroai/agent/state.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Modify: `apps/api/src/taroai/store.py`
- Test: `tests/api/test_agent_runtime_persistence.py`

**Steps:**

1. Write failing tests showing runtime state is persisted after plan creation, each step, approval pause, resume, failure, and success.
2. Add `RunStateSnapshot` Pydantic model with `run_id`, `status`, `plan`, `current_step_id`, `completed_step_ids`, `approved_step_ids`, `tool_results`, `failure_reason`, and timestamp.
3. Add store methods `save_runtime_state` and `get_runtime_state`.
4. Update `AgentRuntime` to save state after every meaningful transition.
5. Keep in-memory persistence for unit tests; PostgreSQL persistence comes through repository implementation later.

**Acceptance Criteria:**

- A crashed worker can reconstruct the latest run state.
- Approval resume does not depend only on process-local memory.
- Tests prove state persistence on success, pause, and failure.

## Task 2: Sandbox Package and Contract

**Files:**

- Modify: `apps/api/src/taroai/sandbox/__init__.py`
- Modify: `apps/api/src/taroai/sandbox/models.py`
- Modify: `apps/api/src/taroai/sandbox/adapter.py`
- Modify: `apps/api/src/taroai/sandbox/factory.py`
- Create: `apps/api/src/taroai/sandbox/docker.py`
- Modify: `apps/api/src/taroai/sandbox/process.py`
- Modify: `apps/api/src/taroai/sandbox/tools.py`
- Test: `tests/api/test_sandbox.py`
- Test: `tests/api/test_sandbox_docker.py`
- Test: `tests/api/test_sandbox_local_process.py`

**Steps:**

1. Keep architecture test requiring `sandbox/` package.
2. Extend Pydantic models: `SandboxCreateRequest`, `SandboxSession`, `SandboxCommand`, `SandboxCommandResult`, `SandboxFileRef`, `SandboxSnapshot`.
3. Extend adapter methods: `create`, `execute`, `upload_file`, `download_file`, `snapshot`, `destroy`, and `get_session`.
4. Keep default provider disabled unless a provider adapter is explicitly configured or injected.
5. Keep config fields for provider, timeout, runtime image, and network mode.
6. Register sandbox command-output/file/snapshot storage metadata and upload command-output/file/snapshot content when the session belongs to an existing run.
7. Provide a `local_process` provider for local cloud PoC command execution with per-session workspace path checks, file upload/download, timeout handling, snapshots, and destroy lifecycle.

**Acceptance Criteria:**

- Runtime and Tool Gateway depend only on the Sandbox Adapter contract. Product runs use an approved provider implementation or the disabled default boundary; local contract tests inject adapters from `tests/` without making them runtime defaults.
- Sandbox sessions are tenant/workspace/run scoped.
- Unit tests do not require external sandbox credentials.
- Sandbox command execution, file uploads, and snapshot responses use storage catalog objects with readable object content when metadata is registered for an existing run.
- The `local_process` provider is explicit configuration for local cloud PoC only; the Docker provider is a first-pass container execution adapter with disabled networking and Settings-managed resource/security flags, while Kubernetes, E2B, or microVM-backed isolation remains required before shared enterprise execution.
- Tool Gateway sandbox command execution passes only scoped lease handles in reserved environment metadata, binds leases to run/step/session context, rejects caller attempts to override those reserved keys, rejects invalid sandbox environment variable names at provider execution boundaries, and keeps provider-managed values such as `TAROAI_SANDBOX_WORKSPACE` authoritative across local, Docker, and Kubernetes execution paths.
- Sandbox lease resolution is started through an API route that accepts a lease token plus workspace/run/step/session/action context, enforces `sandbox.command` scope at the `SecretService` boundary, and keeps tokens and values out of audit metadata.

## Task 3: Sandbox Execution Node

**Files:**

- Modify: `apps/api/src/taroai/agent/runtime.py`
- Modify: `apps/api/src/taroai/agent/tools.py`
- Modify: `apps/api/src/taroai/sandbox/adapter.py`
- Test: `tests/api/test_agent_runtime_sandbox.py`

**Steps:**

1. Add tests for a plan step that requires code execution.
2. Route sandbox steps through Tool Gateway or a sandbox tool executor, not direct shell calls.
3. Create sandbox session for the run if needed.
4. Execute command through adapter and record stdout/stderr/exit code as tool result.
5. Persist created artifacts through `storage/` catalog.
6. Destroy sandbox by default after run unless snapshot policy is enabled.

**Acceptance Criteria:**

- Sandbox execution is audited as a tool call.
- Failed sandbox command can mark step failed or retry based on policy.
- Sandbox outputs become artifact/storage metadata, not anonymous files.

**Current Implementation Notes:**

- Runtime-planned tool calls now pass Tool Gateway required scopes that are allowed by `PolicyService` for the run tenant/workspace/user context, so scoped tools such as `sandbox.command` and `browser.action` can execute through the runtime path when the actor has matching RBAC permissions.
- API and worker default runtime construction both inject an `IdentityPolicyService`; SQL-backed worker deployments load identity permissions through the configured identity service backend.
- `TAROAI_SANDBOX_PROVIDER=docker` now builds a Docker-backed adapter through the same factory path as API and worker runtimes. It starts per-session containers with `--network none`, memory/CPU/pids limits, non-root `--user`, read-only rootfs, `cap-drop=ALL`, `security-opt`, tmpfs mounts, bind-mounts the session workspace at `/workspace`, executes commands through `docker exec`, and rejects unmanaged open/allowlist networking.
- Runtime now creates a sandbox session for `sandbox.command` steps that do not already include `session_id`, persists `sandbox_session_id` in runtime state, injects that ID into the planned step before Tool Gateway execution, records safe `tool_call.completed` and `sandbox.command.executed` run events without raw stdout/stderr, promotes files declared by `artifact_paths`/`artifact_path` only under `/workspace/artifacts/` or auto-discovered under `/workspace/artifacts/**` into `StoragePurpose.ARTIFACT` objects, rejects declared artifact paths outside `/workspace/artifacts/`, evaluates artifact-stage guardrails against sandbox file content plus safe metadata, resumes approval-gated sandbox artifact publication back to the original path, marks pre-approval storage registrations deleted when publication pauses or blocks, runs configured storage content scanning before object upload, creates run artifacts pointing at the real storage URI, and destroys runtime-created sessions on success, failure, approval rejection, or cancellation. Cleanup provider failures are recorded as `sandbox.session.destroy_failed` or `browser.session.destroy_failed` without blocking an already-determined terminal run state, so operators get evidence while users still receive completed artifacts and browser captures.
- HTTP sandbox and browser adapters now re-read tenant session lists after destroy/delete and reject cleanup when the supposedly removed session still appears active, so runtime cleanup and install lifecycle verification share the same stale-session guard.
- Strict local cloud PoC verification rejects `sandbox.session.destroy_failed` and `browser.session.destroy_failed` run events, making leaked sandbox or browser resources a release gate failure instead of an invisible operator cleanup problem.
- Strict local cloud PoC verification also proves browser-controller auth is active when a controller API key is configured by requiring unauthenticated tenant session-list, global session-list, and `GET /capabilities` probes to return `401` or `403`; a controller that accepts any probe fails the demo gate.
- Browser-controller lifecycle evidence now carries `auth_challenge_enforced`; private install validation requires that field when `TAROAI_BROWSER_CONTROLLER_API_KEY` is configured, so release acceptance cannot rely only on successful authenticated browser actions.
- Sandbox lifecycle evidence now also carries `auth_challenge_enforced` plus per-probe tenant session-list, global session-list, and capabilities challenge fields; `verify-sandbox-lifecycle` exits non-zero when a configured controller API key does not protect tenant-scoped `GET /sessions?tenant_id=...`, global `GET /sessions`, and `GET /capabilities`, and private install validation requires each probe when `TAROAI_SANDBOX_CONTROLLER_API_KEY` is configured.
- Sandbox lifecycle evidence now carries `session_destroy_confirmed`; after destroy, the verifier re-reads the tenant session list and private install validation fails if the session still appears active in controller capacity evidence. The HTTP sandbox adapter checks declared global, tenant, and run capacity before `POST /sessions`; the sandbox controller contract now supports authenticated `GET /sessions` for the provider-visible global view plus known-tenant fallback and `GET /sessions?tenant_id=...` for tenant-scoped capacity, so controller restarts do not hide existing sessions from global capacity checks. The HTTP sandbox adapter also confirms post-destroy active session state during runtime cleanup instead of trusting a 200 response alone; the kubectl-backed Kubernetes adapter confirms post-destroy pod state instead of trusting `kubectl delete` alone. The kubectl-backed Kubernetes provider now declares TTL enforcement, exposes `max_session_ttl_seconds`, rejects session create requests that exceed that TTL before Pod creation, and rejects command/file/list/download/snapshot operations once a tracked session has expired.
- Sandbox-controller snapshots now require the same workspace/run scope as commands and file writes. `POST /snapshots` accepts `workspace_id` and `run_id`, rejects same-tenant cross-run/cross-workspace snapshot attempts before provider dispatch, and the HTTP sandbox adapter fetches the session context before sending snapshot requests to controller-backed providers.
- Configurable snapshot retention, Kubernetes/E2B provider adapters, microVM isolation, and production network/file-system hardening remain planned.

## Task 4: Browser Session Seam

**Files:**

- Modify: `apps/api/src/taroai/sandbox/models.py`
- Modify: `apps/api/src/taroai/sandbox/browser.py`
- Test: `tests/api/test_sandbox.py`

**Steps:**

1. Add Pydantic models for `BrowserAction`, `BrowserObservation`, and `BrowserSession`.
2. Define actions: navigate, click, type, screenshot, extract.
3. Keep browser session implementation behind the browser controller seam and route configured provider traffic through an HTTP controller adapter.
4. Add an API endpoint for browser actions behind `browser.act`.
5. Browser actions must carry tenant/workspace/run IDs.
6. Browser screenshots and downloads must register storage objects and upload available screenshot bytes.

**Acceptance Criteria:**

- Browser automation can be contract-tested without provider credentials.
- Browser actions are permission checked, audited, and billed when a run exists.
- Browser screenshot actions register storage object metadata and readable object content when the run exists and the provider returns bytes.
- Browser audit metadata does not include raw typed text.
- `playwright`/`browserbase` provider selection uses Pydantic Settings for endpoint URL, API key, and timeout.

**Current Implementation Notes:**

- API and worker startup now use the same Settings-backed `HttpBrowserController` path for `playwright` and `browserbase` providers. The Playwright controller service exposes the same list contract as the in-process controller: `GET /sessions?tenant_id=...` filters by tenant, while authenticated `GET /sessions` returns all active sessions for controller-level cleanup and verification.

## Task 5: Bounded Multi-Agent Delegation

**Files:**

- Create: `apps/api/src/taroai/agent/delegation.py`
- Modify: `apps/api/src/taroai/agent/state.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Test: `tests/api/test_multi_agent_delegation.py`

**Steps:**

1. Add `AgentRole` enum: planner, research, browser, data, document, domain.
2. Add `DelegatedTask` and `DelegatedResult` Pydantic models.
3. Add runtime policy fields: max delegation depth, max delegated tasks, max cost.
4. Planner can delegate to configured sub-agent roles only.
5. Sub-agent tool scopes must be a subset of the parent run scopes.
6. Surface delegated work as run steps/events.

**Acceptance Criteria:**

- No recursive unlimited swarm.
- Delegated task outputs are traceable.
- Permission, approval, billing, and audit still go through shared services.

## Task 6: LangGraph Execution Path

**Files:**

- Modify: `apps/api/src/taroai/agent/graph.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Test: `tests/api/test_agent_langgraph_contract.py`

**Steps:**

1. Add tests that graph contains expected nodes and edges.
2. Keep business logic in runtime methods; graph nodes should call those methods or pure node wrappers.
3. Add compatibility test that compiles the graph.
4. Add an optional `execute_with_graph` method once local dependency compatibility is stable.
5. Keep direct runtime execution as fallback until LangGraph invoke is verified in CI.

**Acceptance Criteria:**

- LangGraph structure is tested.
- Runtime behavior does not depend on unverified local invoke quirks.

## Task 7: Runtime API Extensions

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Test: `tests/api/test_app.py`

**Steps:**

1. Add `POST /api/runs/{run_id}/cancel`.
2. Add `POST /api/runs/{run_id}/retry`.
3. Add `GET /api/runs/{run_id}/state`.
4. Add tests for tenant isolation and allowed status transitions.
5. Emit audit events for cancel/retry/resume.

**Acceptance Criteria:**

- Long-running runs are operable from API.
- Invalid transitions return explicit errors.

**Current Implementation Notes:**

- `POST /api/runs/{run_id}/cancel` is started with a Pydantic `reason_code` payload, runtime-level cancellation, terminal-state transition rejection, pending approval cancellation, `run.cancelled` and `approval.cancelled` events, and safe audit metadata through both in-memory and SQLite-compatible SQL stores.
- `POST /api/runs/{run_id}/retry` is started with a Pydantic `reason_code` payload, retryable-state validation, pending approval cleanup, `run.retry_requested` audit metadata, and direct or queued re-execution through the existing execution path.
- `GET /api/runs/{run_id}/state` is started and returns the persisted runtime state snapshot through the same tenant-scoped store boundary.

## Verification

Run after each task:

```bash
python -m pytest tests/api/test_agent_runtime.py -q
python -m pytest tests/api/test_sandbox_adapter_contract.py -q
python -m pytest tests/api/test_agent_runtime_sandbox.py -q
python -m pytest tests/api/test_multi_agent_delegation.py -q
python -m pytest -q
```

Expected final result: runtime state is resumable, sandbox execution is isolated behind an adapter, and multi-agent behavior is bounded and auditable.

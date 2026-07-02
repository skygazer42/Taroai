# Agent Runtime and Sandbox Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current in-memory Agent Runtime into a production-shaped runtime with explicit state persistence, bounded multi-agent delegation, policy checks, sandbox execution, and resumable approvals.

**Architecture:** Keep runtime code inside `taroai/agent`, sandbox code inside `taroai/sandbox`, and tool execution behind Tool Gateway. Agent Runtime owns state transitions; Sandbox Adapter owns isolated code/browser execution; Tool Gateway owns permission, secret, audit, and billing checks.

**Tech Stack:** Python, Pydantic, LangGraph, LangChain, FastAPI, PostgreSQL, Redis, E2B or K8s Docker adapter, pytest.

---

## Summary

Current state has:

- `taroai/agent/runtime.py`: Pydantic runtime with plan, retry, approval pause/resume/rejection, artifact finalization.
- `taroai/agent/graph.py`: LangGraph graph construction seam.
- `taroai/agent/planning.py`, `state.py`, `tools.py`: separated runtime primitives.
- `taroai/store.py`: in-memory runtime state snapshots so approval resume or rejection can recover after process-local pending state is lost.
- `taroai/sandbox/`: Pydantic sandbox session/command/file/snapshot and browser action models, disabled default sandbox/browser provider boundaries, Tool Gateway `sandbox.command` and `browser.action` handlers wired into `create_app` default runtime through provider contracts, sandbox API endpoints for session create, command execution, file upload/download, snapshot, destroy, and browser actions, `sandbox.create`/`sandbox.execute`/`browser.act` permission checks, session/command/file/snapshot/destroy/browser audit metadata, sandbox command output object upload, sandbox file object upload, sandbox snapshot JSON object upload, browser screenshot object upload, `sandbox_minutes` metering for command execution, `artifact_bytes` metering for file upload, and `browser_action_count` metering for browser actions when a run exists. Local contract adapters exist only under `tests/` for isolated contract coverage.

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
- Modify: `apps/api/src/taroai/sandbox/tools.py`
- Test: `tests/api/test_sandbox.py`

**Steps:**

1. Keep architecture test requiring `sandbox/` package.
2. Extend Pydantic models: `SandboxCreateRequest`, `SandboxSession`, `SandboxCommand`, `SandboxCommandResult`, `SandboxFileRef`, `SandboxSnapshot`.
3. Extend adapter methods: `create`, `execute`, `upload_file`, `download_file`, `snapshot`, `destroy`, and `get_session`.
4. Keep default provider disabled unless a provider adapter is explicitly configured or injected.
5. Keep config fields for provider, timeout, runtime image, and network mode.
6. Register sandbox command-output/file/snapshot storage metadata and upload command-output/file/snapshot content when the session belongs to an existing run.

**Acceptance Criteria:**

- Runtime and Tool Gateway depend only on the Sandbox Adapter contract. Product runs use an approved provider implementation or the disabled default boundary; local contract tests inject adapters from `tests/` without making them runtime defaults.
- Sandbox sessions are tenant/workspace/run scoped.
- Unit tests do not require external sandbox credentials.
- Sandbox command execution, file uploads, and snapshot responses use storage catalog objects with readable object content when metadata is registered for an existing run.

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

## Task 4: Browser Session Seam

**Files:**

- Modify: `apps/api/src/taroai/sandbox/models.py`
- Modify: `apps/api/src/taroai/sandbox/browser.py`
- Test: `tests/api/test_sandbox.py`

**Steps:**

1. Add Pydantic models for `BrowserAction`, `BrowserObservation`, and `BrowserSession`.
2. Define actions: navigate, click, type, screenshot, extract.
3. Keep browser session implementation behind the browser controller seam until a live provider is approved.
4. Add an API endpoint for browser actions behind `browser.act`.
5. Browser actions must carry tenant/workspace/run IDs.
6. Browser screenshots and downloads must register storage objects and upload available screenshot bytes.

**Acceptance Criteria:**

- Browser automation can be contract-tested without live browser provider.
- Browser actions are permission checked, audited, and billed when a run exists.
- Browser screenshot actions register storage object metadata and readable object content when the run exists and the provider returns bytes.
- Browser audit metadata does not include raw typed text.

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

# CREAO Chat Agent Loop and Skills Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn Taroai's CREAO-style static Chat shell into a durable multi-turn agent product with a repair-capable Agent Loop, portable Skills, complete Chat interactions, interactive outputs, and reusable Agents.

**Architecture:** Preserve the existing modular monolith and control-plane services. Add a durable Thread/Message layer above Runs, replace one-shot execution with a persisted Observe-Decide-Act-Observe-Verify loop behind a feature flag, and extend the existing Skill Registry into an immutable package runtime. The frontend remains framework-free ES modules and projects authoritative Thread, RunEvent, Artifact, Skill, and Agent API state into the approved CREAO-style shell.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PostgreSQL/SQLite repository contracts, Redis worker queue, S3-compatible object storage, Docker/Kubernetes Sandbox adapters, static HTML/CSS/ES modules, Playwright, pytest.

---

## Source of Truth and Execution Rules

- Approved design: `docs/plans/2026-07-11-creao-chat-agent-loop-skills-design.md`.
- Existing visual baseline: `docs/plans/2026-07-10-creao-chat-parity-design.md` and the current dirty `apps/web` changes. Preserve them.
- Execute in a dedicated `codex/creao-chat-agent-loop-skills` worktree.
- Use **@superpowers:test-driven-development** for every behavior change.
- Use **@visual-verdict** after every visual iteration.
- Before any completion claim use **@superpowers:verification-before-completion**.
- No new runtime or npm dependency is authorized. Use standard-library ZIP, URL, hashing, base64, and HTTP primitives. `SKILL.md` frontmatter supports the required strict scalar fields; optional `taroai.yaml` is accepted as JSON-compatible YAML in this slice. Arbitrary YAML requires separate explicit dependency approval.
- Do not put a `while` around `_execute_planned_steps()`. It marks command failures terminal and destroys state; V2 must separate Action execution from Run finalization first.
- Never stage `.env`, `.omx/`, credentials, generated recordings, screenshots, or test output.
- Every commit follows the repository Lore protocol and includes `Tested:` plus honest `Not-tested:` trailers.

## Slice A — Durable Thread and Agent Loop V2

### Task 0: Create the isolated worktree and preserve the approved visual baseline

**Files:**
- Preserve: `apps/web/index.html`
- Preserve: `apps/web/assets/main.js`
- Preserve: `apps/web/assets/styles.css`
- Preserve: `tests/web/test_workspace_frontend_contract.py`
- Preserve: `tests/web/test_creao_chat_frontend_contract.py`
- Preserve: `docs/plans/2026-07-10-creao-chat-parity-design.md`
- Preserve: `docs/plans/2026-07-10-creao-chat-parity.md`

**Step 1: Export only the tracked visual-shell diff from the current worktree**

```powershell
$source = 'C:\Users\luke\Desktop\Taroai'
$target = 'C:\Users\luke\.config\superpowers\worktrees\Taroai\creao-chat-agent-loop-skills'
$patch = Join-Path $env:TEMP 'taroai-creao-chat-visual.patch'
$diffLines = git -C $source diff --binary -- apps/web/index.html apps/web/assets/main.js apps/web/assets/styles.css tests/web/test_workspace_frontend_contract.py
[System.IO.File]::WriteAllLines($patch, $diffLines, [System.Text.UTF8Encoding]::new($false))
```

Expected: `$patch` exists and the source worktree remains unchanged.

**Step 2: Create the feature worktree from the commit containing this plan**

```powershell
git -C $source worktree add $target -b codex/creao-chat-agent-loop-skills
git -C $target apply --3way $patch
Copy-Item "$source\tests\web\test_creao_chat_frontend_contract.py" "$target\tests\web\test_creao_chat_frontend_contract.py"
Copy-Item "$source\docs\plans\2026-07-10-creao-chat-parity-design.md" "$target\docs\plans\2026-07-10-creao-chat-parity-design.md"
Copy-Item "$source\docs\plans\2026-07-10-creao-chat-parity.md" "$target\docs\plans\2026-07-10-creao-chat-parity.md"
```

Expected: no `.env` or `.omx` file is copied.

**Step 3: Verify the preserved baseline before changing behavior**

```powershell
python -m pytest tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
git diff --check
git status --short
```

Expected: frontend contract tests pass; only the seven intended baseline files are dirty/untracked.

**Step 4: Commit the preserved baseline**

```powershell
git add -- apps/web/index.html apps/web/assets/main.js apps/web/assets/styles.css tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py docs/plans/2026-07-10-creao-chat-parity-design.md docs/plans/2026-07-10-creao-chat-parity.md
git commit -m "Preserve the approved CREAO visual baseline before wiring behavior" -m "Constraint: The source worktree contains user-approved uncommitted frontend work that must not be discarded." -m "Confidence: high" -m "Scope-risk: narrow" -m "Tested: python -m pytest tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider" -m "Not-tested: No backend product behavior is added by this baseline commit."
```

### Task 1: Add Thread and Agent Loop persistence contracts

**Files:**
- Create: `apps/api/migrations/033_chat_threads_agent_loop_v2.sql`
- Create: `apps/api/src/taroai/agent/models.py`
- Modify: `apps/api/src/taroai/domain.py:17-105`
- Modify: `apps/api/src/taroai/agent/state.py:15-37`
- Modify: `apps/api/src/taroai/store.py:23-69`
- Modify: `apps/api/src/taroai/db/postgresql_verification.py:13-54`
- Modify: `tests/api/test_migration_contract.py`
- Modify: `tests/api/test_postgresql_verification.py`
- Modify: `tests/api/test_domain_store.py`

**Step 1: Write failing migration and model tests**

Assert that migration 033 creates `chat_threads`, `chat_messages`, `agent_cycles`, `agent_actions`, and `agent_checkpoints`; adds Thread/model fields to Runs and RunEvents; and protects every new tenant table with RLS.

```python
def test_agent_loop_v2_models_fix_thread_and_model_snapshot():
    thread = ChatThreadCreate(
        workspace_id="workspace_sales",
        provider_id="deepseek",
        model_id="deepseek-chat",
        reasoning_effort="medium",
    )
    run = RunCreate(
        workspace_id=thread.workspace_id,
        thread_id="thread_1",
        trigger_message_id="message_1",
        provider_id=thread.provider_id,
        model_id=thread.model_id,
        reasoning_effort=thread.reasoning_effort,
        message="Fix the failing report.",
    )
    assert run.model_id == "deepseek-chat"
```

Also assert `AgentRuntimeState` exposes iteration, observations, plan revision, repair/replan counts, steering, deadline, and checkpoint sequence.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_migration_contract.py tests/api/test_postgresql_verification.py tests/api/test_domain_store.py -q -p no:cacheprovider
```

Expected: FAIL because migration 033 and the new models/fields do not exist.

**Step 3: Implement the schema and typed contracts**

```python
class ChatMessageDispatchStatus(str, Enum):
    READY = "ready"
    QUEUED = "queued"
    STEERING = "steering"
    INFLIGHT = "inflight"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ResourceReference(BaseModel):
    type: Literal["skill", "connector", "agent", "knowledge"]
    id: str = Field(min_length=1)
    version: str | None = None


class AgentDecision(BaseModel):
    kind: Literal["action", "respond", "request_input", "replan"]
    rationale_summary: str = ""
    tool_name: str | None = None
    skill_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    response_text: str | None = None


class AgentVerificationResult(BaseModel):
    outcome: Literal["complete", "repair", "replan", "wait_user", "fail"]
    feedback: str = ""
```

Use additive migration statements, ordered-message and checkpoint indexes, and the repository's PostgreSQL-only marker convention.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_migration_contract.py tests/api/test_postgresql_verification.py tests/api/test_domain_store.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/migrations/033_chat_threads_agent_loop_v2.sql apps/api/src/taroai/domain.py apps/api/src/taroai/agent/models.py apps/api/src/taroai/agent/state.py apps/api/src/taroai/store.py apps/api/src/taroai/db/postgresql_verification.py tests/api/test_migration_contract.py tests/api/test_postgresql_verification.py tests/api/test_domain_store.py
git commit -m "Make conversational and loop state durable before execution changes" -m "Constraint: Existing Run rows and event replay must remain readable after an additive migration." -m "Confidence: high" -m "Scope-risk: moderate" -m "Tested: migration, PostgreSQL RLS contract, and domain model tests" -m "Not-tested: Runtime execution still uses the legacy loop."
```

### Task 2: Implement matching in-memory and SQL Thread/Loop repositories

**Files:**
- Modify: `apps/api/src/taroai/store.py:72-549`
- Modify: `apps/api/src/taroai/db/repository.py:36-777`
- Modify: `tests/api/test_domain_store.py`
- Modify: `tests/api/test_persistent_store_contract.py`

**Step 1: Write failing shared repository contract tests**

Run the same contract against `InMemoryControlPlaneStore` and `SqlControlPlaneRepository`:

```python
def assert_thread_queue_contract(store):
    thread = store.create_chat_thread("tenant_acme", "user_luke", thread_payload())
    first = store.append_chat_message("tenant_acme", thread.id, message_payload("one"))
    second = store.append_chat_message("tenant_acme", thread.id, message_payload("two"))
    assert [first.sequence, second.sequence] == [1, 2]
    assert store.claim_next_queued_message("tenant_acme", thread.id).id == first.id


def test_commit_action_observation_is_atomic_and_checkpoint_is_immutable(store):
    action = prepared_action(store)
    checkpoint = store.commit_agent_action_observation(
        tenant_id="tenant_acme",
        action_id=action.id,
        observation={"exit_code": 1, "stderr": "safe error"},
        checkpoint_state={"iteration": 1},
    )
    assert checkpoint.sequence == 1
    assert store.get_agent_action("tenant_acme", action.id).status == "failed"
```

Add cross-tenant denial, SQL restart recovery, duplicate action-key, and uncertain-action recovery tests.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_domain_store.py tests/api/test_persistent_store_contract.py -q -p no:cacheprovider
```

Expected: FAIL with missing Thread/Action repository methods.

**Step 3: Implement the repository contract**

Implement identical methods on both stores:

```text
create_chat_thread / get_chat_thread / list_chat_threads / update_chat_thread
append_chat_message / get_chat_message / list_chat_messages / update_chat_message
claim_next_queued_message / list_pending_steering_messages / mark_steering_applied
create_agent_cycle / complete_agent_cycle
create_agent_action / get_agent_action / commit_agent_action_observation
create_agent_checkpoint / get_latest_agent_checkpoint
```

In SQL, queue claiming uses a transaction and compare-and-set status. Committing an action updates its status, writes the observation event, records usage, and inserts the immutable checkpoint atomically. Hydrate an interrupted `running` action as `uncertain`; do not repeat it automatically.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_domain_store.py tests/api/test_persistent_store_contract.py -q -p no:cacheprovider
```

Expected: PASS for both stores.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/store.py apps/api/src/taroai/db/repository.py tests/api/test_domain_store.py tests/api/test_persistent_store_contract.py
git commit -m "Keep Thread queues and loop checkpoints consistent across store backends" -m "Constraint: Action observations and checkpoints must survive process restart without replaying committed side effects." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: shared in-memory and SQL repository contract tests" -m "Not-tested: External Tool idempotency is exercised later."
```

### Task 3: Add Thread APIs, safe model catalog, and immutable Run model snapshots

**Files:**
- Create: `apps/api/src/taroai/chat/__init__.py`
- Create: `apps/api/src/taroai/chat/service.py`
- Modify: `apps/api/src/taroai/app.py:1098-1570,2539-2630,3009-3029`
- Modify: `apps/api/src/taroai/model_gateway/models.py:43-68`
- Modify: `apps/api/src/taroai/model_gateway/providers.py:66-142`
- Create: `tests/api/test_chat_threads_api.py`
- Modify: `tests/api/test_model_gateway_providers.py`
- Modify: `tests/api/test_model_policy.py`

**Step 1: Write failing API tests**

```python
def test_post_thread_message_starts_run_with_selected_model(client, headers):
    thread = client.post(
        "/api/threads",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "provider_id": "deepseek",
            "model_id": "deepseek-chat",
            "reasoning_effort": "medium",
        },
    ).json()
    sent = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers={**headers, "Idempotency-Key": "message-1"},
        json={"content": "Repair the report", "delivery_mode": "queue"},
    )
    assert sent.status_code == 202
    run = client.get(f"/api/runs/{sent.json()['run_id']}", headers=headers).json()
    assert (run["provider_id"], run["model_id"], run["reasoning_effort"]) == (
        "deepseek", "deepseek-chat", "medium"
    )
```

Add list/get/patch/delete, tenant isolation, idempotent message submission, policy rejection, and `/api/model-catalog?workspace_id=...` redaction tests.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_chat_threads_api.py tests/api/test_model_gateway_providers.py tests/api/test_model_policy.py -q -p no:cacheprovider
```

Expected: FAIL because Thread routes and the safe model catalog do not exist.

**Step 3: Implement ChatService and routes**

```python
def post_message(self, tenant_id, user_id, thread_id, payload):
    thread = self.store.get_chat_thread(tenant_id, thread_id)
    message = self.store.append_chat_message(tenant_id, thread_id, payload)
    if self.has_active_run(tenant_id, thread_id):
        return MessageDispatch(message=message, status=message.dispatch_status)
    run = self.store.create_run(
        tenant_id,
        user_id,
        RunCreate(
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            trigger_message_id=message.id,
            provider_id=thread.provider_id,
            model_id=thread.model_id,
            reasoning_effort=thread.reasoning_effort,
            message=message.content,
            attachments=message.attachments,
            resource_refs=message.resource_refs,
        ),
    )
    return self.dispatch(run, message)
```

Reuse existing idempotency helpers and keep `/api/runs` backward compatible. The model catalog returns only Workspace-allowed provider/model IDs, display names, and reasoning efforts; never Base URLs or secret metadata.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_chat_threads_api.py tests/api/test_model_gateway_providers.py tests/api/test_model_policy.py tests/api/test_app.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/chat apps/api/src/taroai/app.py apps/api/src/taroai/model_gateway/models.py apps/api/src/taroai/model_gateway/providers.py tests/api/test_chat_threads_api.py tests/api/test_model_gateway_providers.py tests/api/test_model_policy.py
git commit -m "Make Chat messages durable and bind every Run to its chosen model" -m "Constraint: Chat users need a safe model catalog rather than model-provider administration metadata." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: Thread API, idempotency, model catalog, and model policy tests" -m "Not-tested: Agent Loop V2 is wired in subsequent tasks."
```

### Task 4: Extend Model Gateway with structured decisions, verification, and streaming text

**Files:**
- Modify: `apps/api/src/taroai/model_gateway/models.py`
- Modify: `apps/api/src/taroai/model_gateway/gateway.py:29-359`
- Modify: `apps/api/src/taroai/model_gateway/providers.py`
- Create: `tests/api/test_model_gateway_agent_loop.py`
- Modify: `tests/api/test_model_gateway_providers.py`

**Step 1: Write failing gateway tests**

```python
def test_gateway_parses_next_action_decision(fake_http):
    fake_http.respond_json({
        "choices": [{"message": {"content": json.dumps({
            "kind": "action",
            "tool_name": "sandbox.command",
            "tool_input": {"command": "pytest -q"},
        })}}]
    })
    decision = gateway.decide_next_action(loop_request(provider_id="deepseek"))
    assert decision.tool_name == "sandbox.command"


def test_gateway_streams_assistant_deltas_without_reasoning(fake_stream):
    assert list(gateway.stream_response(response_request())) == ["Hello", " world"]
```

Test verification parsing, explicit provider/model routing, reasoning-effort capabilities, and response-error redaction.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_model_gateway_agent_loop.py tests/api/test_model_gateway_providers.py -q -p no:cacheprovider
```

Expected: FAIL because only `create_plan()` exists.

**Step 3: Implement new operations while preserving legacy planning**

```python
class ModelGateway(BaseModel):
    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse: ...
    def decide_next_action(self, request: ModelGatewayRequest) -> AgentDecision: ...
    def verify_completion(self, request: ModelGatewayRequest) -> AgentVerificationResult: ...
    def stream_response(self, request: ModelGatewayRequest) -> Iterator[str]: ...
```

Share provider routing, policy, fallback, usage, and JSON extraction. Explicit `provider_id` restricts routing unless Thread policy allows fallback. Parse only observable assistant content and Tool decisions; discard provider reasoning fields.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_model_gateway_agent_loop.py tests/api/test_model_gateway_providers.py tests/api/test_model_gateway_credentials.py -q -p no:cacheprovider
```

Expected: PASS; existing planning tests remain green.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/model_gateway/models.py apps/api/src/taroai/model_gateway/gateway.py apps/api/src/taroai/model_gateway/providers.py tests/api/test_model_gateway_agent_loop.py tests/api/test_model_gateway_providers.py
git commit -m "Let the model decide and verify one observable agent cycle at a time" -m "Constraint: Legacy create_plan callers must remain compatible during the feature-flag window." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: decision, verification, streaming, provider routing, and credential tests" -m "Not-tested: No Tool is executed by the new decisions yet."
```

### Task 5: Execute the first real Observe-Decide-Act-Verify loop

**Files:**
- Create: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/agent/graph.py:4-19`
- Modify: `apps/api/src/taroai/agent/runtime.py:104-178,396-477,1615-1817,2409-2455`
- Modify: `apps/api/src/taroai/agent/__init__.py`
- Create: `tests/api/test_agent_loop_v2.py`

**Step 1: Write a failing happy-path loop test**

```python
def test_agent_loop_completes_only_after_verifier_passes():
    gateway = ScriptedLoopGateway(
        decisions=[action_decision("sandbox.command", {"command": "echo ready"})],
        verifications=[verification("complete", "artifact is ready")],
    )
    state = build_loop(gateway=gateway).execute_run("tenant_acme", create_run().id)
    assert state.status == RunStatus.SUCCEEDED
    assert gateway.operations == ["decide", "verify"]
    assert event_types(state.run_id) == ordered_subset([
        "agent.cycle.started",
        "agent.decision.created",
        "agent.action.started",
        "agent.observation.recorded",
        "agent.verification.completed",
        "agent.loop.completed",
    ])
```

**Step 2: Run the test to verify it fails**

```powershell
python -m pytest tests/api/test_agent_loop_v2.py::test_agent_loop_completes_only_after_verifier_passes -v -p no:cacheprovider
```

Expected: FAIL because `AgentLoopV2` does not exist.

**Step 3: Implement the minimal loop and real graph nodes**

The graph routes these concrete operations:

```text
observe -> decide -> policy -> execute -> observe_result -> verify
verify -> complete | repair | replan | wait_user | fail
```

Reuse context retrieval, Tool Gateway policy, Sandbox/browser setup, billing, artifact promotion, and approval helpers from `AgentRuntime`. Move legacy behavior behind `_execute_legacy_run()`. Action execution returns an `AgentObservation`; it must not mark the Run terminal. Only finalization after verifier `complete` sets `SUCCEEDED`.

**Step 4: Run happy-path and legacy regression tests**

```powershell
python -m pytest tests/api/test_agent_loop_v2.py tests/api/test_agent_runtime.py tests/api/test_agent_runtime_context.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/agent/loop.py apps/api/src/taroai/agent/graph.py apps/api/src/taroai/agent/runtime.py apps/api/src/taroai/agent/__init__.py tests/api/test_agent_loop_v2.py
git commit -m "Require verification before an agent Run can succeed" -m "Constraint: Existing policy, billing, Sandbox, browser, approval, and artifact code must be reused rather than forked." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: Agent Loop V2 happy path plus legacy runtime regressions" -m "Not-tested: Repair, replan, and restart recovery follow next."
```

### Task 6: Add model-driven repair, replan, waiting, and deterministic budgets

**Files:**
- Modify: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/agent/state.py`
- Modify: `apps/api/src/taroai/config.py:162-201,269-286`
- Modify: `apps/api/src/taroai/model_gateway/budget.py`
- Modify: `tests/api/test_agent_loop_v2.py`
- Modify: `tests/api/test_model_budget.py`
- Modify: `tests/api/test_settings.py`

**Step 1: Write the mandatory failing repair scenario and budget tests**

```python
def test_agent_loop_repairs_failed_action_with_new_model_decision():
    gateway = ScriptedLoopGateway(
        decisions=[
            action_decision("sandbox.command", {"command": "false"}),
            action_decision("sandbox.command", {"command": "echo fixed"}),
        ],
        verifications=[verification("complete")],
    )
    state = build_loop(gateway=gateway, command_results=[failed(), succeeded()]).run()
    assert state.status == RunStatus.SUCCEEDED
    assert gateway.decision_requests[1].observations[0].failure_class == "command_failed"
    assert gateway.decision_requests[1].observations[0].safe_error
    assert gateway.decisions[0].tool_input != gateway.decisions[1].tool_input


@pytest.mark.parametrize("limit", ["iterations", "repairs", "elapsed", "cost"])
def test_agent_loop_stops_with_one_terminal_event_when_budget_is_exhausted(limit): ...
```

Add tests for verifier-driven replan and `waiting_for_user`.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_agent_loop_v2.py tests/api/test_model_budget.py tests/api/test_settings.py -q -p no:cacheprovider
```

Expected: FAIL because the loop terminates on Action failure and lacks these budgets.

**Step 3: Implement failure classification and cycle routing**

Add settings:

```python
agent_runtime_mode: Literal["legacy", "loop_v2"] = "legacy"
agent_loop_max_iterations: int = Field(default=12, ge=1)
agent_loop_max_repairs: int = Field(default=4, ge=0)
agent_loop_timeout_seconds: int = Field(default=1800, ge=1)
agent_loop_cost_limit: float = Field(default=0, ge=0)
```

Classify transient transport errors separately from command/tool observations. Only deterministic transport failures may repeat the same Action. Command failures return to `decide_next_action()` with redacted observation. Increment plan revision for `replan`, pause for `wait_user`, and emit exactly one terminal event on every limit.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_agent_loop_v2.py tests/api/test_model_budget.py tests/api/test_settings.py -q -p no:cacheprovider
```

Expected: PASS, including the mandatory changed-action assertion.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/agent/loop.py apps/api/src/taroai/agent/state.py apps/api/src/taroai/config.py apps/api/src/taroai/model_gateway/budget.py tests/api/test_agent_loop_v2.py tests/api/test_model_budget.py tests/api/test_settings.py
git commit -m "Turn failed actions into bounded repair decisions instead of blind retries" -m "Constraint: Tool errors must be redacted before they return to the model." -m "Rejected: Repeat the same Tool call | it does not create an agentic repair loop." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: repair, replan, wait-user, and budget exhaustion tests" -m "Not-tested: Crash recovery is added next."
```

### Task 7: Make checkpoints, cancellation, and Full Auto safe across restart

**Files:**
- Modify: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/store.py:39-69,448-459`
- Modify: `apps/api/src/taroai/db/repository.py:686-777`
- Modify: `apps/api/src/taroai/agent/runtime.py:139-337,2542-2547`
- Modify: `apps/api/src/taroai/sandbox/models.py`
- Create: `tests/api/test_agent_loop_recovery.py`
- Modify: `tests/api/test_sandbox_lifecycle_verification.py`

**Step 1: Write failing restart, cancellation, and isolation-gate tests**

```python
def test_checkpoint_resume_does_not_reexecute_committed_action(sql_store):
    loop = crash_after_checkpoint_loop(sql_store)
    with pytest.raises(SimulatedProcessCrash):
        loop.execute_run("tenant_acme", loop.run_id)
    resumed = restarted_loop(sql_store).execute_run("tenant_acme", loop.run_id)
    assert resumed.status == RunStatus.SUCCEEDED
    assert command_executor.calls == 1


def test_full_auto_is_rejected_when_runtime_falls_back_to_host_process():
    with pytest.raises(SandboxIsolationError):
        build_loop(full_auto=True, sandbox_provider="local_process").start()
```

Also test `uncertain` external writes require user resolution, Stop persists a checkpoint, and promoted artifact paths survive snapshot hydration.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_agent_loop_recovery.py tests/api/test_sandbox_lifecycle_verification.py -q -p no:cacheprovider
```

Expected: FAIL because runtime snapshots are overwrite-only and Full Auto is not capability-gated.

**Step 3: Implement immutable recovery and isolation gating**

Hydrate the complete V2 state from `state_payload`; retain legacy snapshot columns only for old Runs. Resume after the last committed action key. Never replay `uncertain` external writes. `cancel_run()` cancels the current Sandbox command where supported, commits state, and emits one cancellation event.

Enable Full Auto only if Sandbox verification reports an isolated non-host runtime. Host or in-process fallback automatically uses approval-required mode.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_agent_loop_recovery.py tests/api/test_agent_runtime.py tests/api/test_sandbox_lifecycle_verification.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/agent/loop.py apps/api/src/taroai/store.py apps/api/src/taroai/db/repository.py apps/api/src/taroai/agent/runtime.py apps/api/src/taroai/sandbox/models.py tests/api/test_agent_loop_recovery.py tests/api/test_sandbox_lifecycle_verification.py
git commit -m "Resume agent work without repeating committed actions" -m "Constraint: Full Auto is valid only inside a verified isolated Sandbox." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: restart, uncertain-action, cancellation, artifact-state, and isolation-gate tests" -m "Not-tested: Queue continuation across workers follows next."
```

### Task 8: Implement durable Queue, Steering, and worker continuation

**Files:**
- Modify: `apps/api/src/taroai/chat/service.py`
- Modify: `apps/api/src/taroai/app.py`
- Modify: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/workers/models.py:39-42`
- Modify: `apps/api/src/taroai/workers/agent_worker.py:11-50`
- Modify: `apps/api/src/taroai/workers/runner.py:398-454`
- Modify: `tests/api/test_chat_threads_api.py`
- Modify: `tests/api/test_worker_runner.py`

**Step 1: Write failing queue and steering tests**

```python
def test_message_sent_during_run_is_persisted_in_server_queue(client, running_thread):
    response = post_message(client, running_thread, "Do this next", delivery_mode="queue")
    assert response.json()["dispatch_status"] == "queued"
    assert get_messages(client, running_thread)[-1]["content"] == "Do this next"


def test_steer_is_applied_to_same_run_at_safe_checkpoint(loop, running_thread):
    steer = append_steering(running_thread, "Focus on revenue")
    loop.finish_current_action()
    assert loop.current_request.steering_messages == [steer.content]
    assert steer.run_id == loop.run_id
```

Add edit/delete, unavailable-steer fallback, auto/manual dispatch, browser-independent continuation, and exactly-once worker claim tests.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_chat_threads_api.py tests/api/test_worker_runner.py tests/api/test_agent_loop_v2.py -q -p no:cacheprovider
```

Expected: FAIL because active-Run messages cannot queue or steer.

**Step 3: Implement Queue and Steering semantics in one ChatService**

Add message edit/delete/steer endpoints. Consume Steering only after an Action checkpoint; if the loop cannot steer, atomically return it to `queued`. On terminal Run, `ChatService.continue_thread()` claims one queued message and dispatches its Run. Call the same service from inline API and `AgentWorker`; never rely on the browser to start the next message. Ack a worker job only after terminal/checkpoint persistence succeeds.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_chat_threads_api.py tests/api/test_worker_runner.py tests/api/test_agent_loop_v2.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/chat/service.py apps/api/src/taroai/app.py apps/api/src/taroai/agent/loop.py apps/api/src/taroai/workers/models.py apps/api/src/taroai/workers/agent_worker.py apps/api/src/taroai/workers/runner.py tests/api/test_chat_threads_api.py tests/api/test_worker_runner.py tests/api/test_agent_loop_v2.py
git commit -m "Keep queued and steering messages alive beyond the browser session" -m "Constraint: Inline and worker execution must use one continuation service." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: queue, steer, fallback, worker restart, and exactly-once continuation tests" -m "Not-tested: Long-lived Thread event streaming follows next."
```

### Task 9: Expose one resumable Thread event stream and wire the V2 feature flag

**Files:**
- Modify: `apps/api/src/taroai/store.py`
- Modify: `apps/api/src/taroai/db/repository.py`
- Modify: `apps/api/src/taroai/app.py:2558-2585,6923-6960`
- Modify: `apps/api/src/taroai/config.py`
- Modify: `apps/api/src/taroai/workers/runner.py`
- Create: `tests/api/test_thread_event_stream.py`
- Modify: `tests/api/test_settings.py`

**Step 1: Write failing replay, tail, and runtime-selection tests**

```python
def test_thread_event_stream_replays_across_runs_with_monotonic_sequence(client, thread):
    create_two_completed_runs(thread)
    events = read_sse(client, f"/api/threads/{thread.id}/events?after_sequence=2")
    assert [event.id for event in events] == sorted(event.id for event in events)
    assert all(event.thread_id == thread.id for event in events)


def test_loop_v2_setting_builds_same_runtime_for_api_and_worker(settings):
    settings.agent_runtime_mode = "loop_v2"
    assert isinstance(build_api_runtime(settings), AgentLoopV2)
    assert isinstance(build_worker_runtime(settings), AgentLoopV2)
```

Test `Last-Event-ID`, heartbeat, disconnect, legacy Run event compatibility, and no duplicate replay.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_thread_event_stream.py tests/api/test_settings.py -q -p no:cacheprovider
```

Expected: FAIL because no Thread event endpoint or V2 builder exists.

**Step 3: Implement the Thread projection and runtime builder**

Project RunEvents using persisted `thread_sequence`. `GET /api/threads/{id}/events` replays from `after_sequence` or `Last-Event-ID` and optionally follows until disconnect, emitting bounded heartbeats. Keep existing Run SSE behavior unchanged by default.

Centralize runtime construction so API and Worker receive the same feature-flagged runtime, discovery service, budgets, stores, and Tool Gateway.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_thread_event_stream.py tests/api/test_settings.py tests/api/test_worker_runner.py tests/api/test_app.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/store.py apps/api/src/taroai/db/repository.py apps/api/src/taroai/app.py apps/api/src/taroai/config.py apps/api/src/taroai/workers/runner.py tests/api/test_thread_event_stream.py tests/api/test_settings.py
git commit -m "Let Chat resume one ordered execution stream across turns" -m "Constraint: Existing Run SSE clients keep their replay contract during migration." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: Thread SSE replay/tail, heartbeat, feature flag, API/worker parity" -m "Not-tested: Browser rendering is wired in the next task."
```

**Slice A backend gate**

```powershell
python -m compileall apps/api/src/taroai
python -m pytest tests/api/test_migration_contract.py tests/api/test_chat_threads_api.py tests/api/test_thread_event_stream.py tests/api/test_model_gateway_agent_loop.py tests/api/test_agent_loop_v2.py tests/api/test_agent_loop_recovery.py tests/api/test_worker_runner.py -q -p no:cacheprovider
```

Expected: PASS before the Slice A frontend wiring begins.

### Task 10: Replace Run-shaped Chat state with real Threads, model selection, Queue, and Steering

**Files:**
- Create: `apps/web/assets/chat-api.js`
- Create: `apps/web/assets/chat-controller.js`
- Modify: `apps/web/assets/main.js:1-180,520-688,1924-2290,2843-3065,4060-4120`
- Modify: `apps/web/index.html:60-260`
- Modify: `apps/web/assets/styles.css`
- Modify: `tests/web/test_creao_chat_frontend_contract.py`
- Create: `tests/web/test_chat_frontend_state.py`

**Step 1: Write failing frontend contract tests**

Assert the UI loads Threads and model catalog, stores the Thread ID in the URL, posts structured messages, tails Thread SSE, renders cycle/action/observation/verification events, and exposes real Queue/Steer/Stop controls:

```python
def test_submit_uses_thread_message_api_and_structured_model_state():
    source = all_web_module_source()
    assert "/api/threads/${state.currentThreadId}/messages" in source
    assert "/api/model-catalog?workspace_id=" in source
    assert "delivery_mode" in source
    assert "provider_id" in source and "reasoning_effort" in source


def test_queue_panel_wires_edit_delete_and_steer_actions():
    root = parse_index()
    assert root.find_by_attr("data-testid", "message-queue") is not None
    source = all_web_module_source()
    for action in ["editQueuedMessage", "deleteQueuedMessage", "steerQueuedMessage"]:
        assert action in source
```

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/web/test_creao_chat_frontend_contract.py tests/web/test_chat_frontend_state.py -q -p no:cacheprovider
```

Expected: FAIL because the current UI creates unrelated Runs and stores a hard-coded model locally.

**Step 3: Implement the minimal ES-module controller**

`chat-api.js` contains authenticated fetch/SSE parsing only. `chat-controller.js` owns authoritative Thread/message/event state:

```javascript
export const chatState = {
  currentThreadId: null,
  currentRunId: null,
  lastThreadSequence: 0,
  threads: [],
  messages: [],
  queue: [],
  modelCatalog: [],
  selectedModel: null,
};

export async function sendThreadMessage(content, deliveryMode, resourceRefs, attachments) {
  return api.post(`/api/threads/${chatState.currentThreadId}/messages`, {
    content,
    delivery_mode: deliveryMode,
    resource_refs: resourceRefs,
    attachments,
  });
}
```

Keep the Operations drawer for detailed evidence, but render normal execution inline as collapsible Thinking, Skill, Tool, Observation, Repair/Replan, and Verifier cards. On reload, recover the Thread from `#chat/<thread_id>` and resume after `lastThreadSequence`.

**Step 4: Run tests and a local browser smoke check**

```powershell
python -m pytest tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py tests/web/test_chat_frontend_state.py -q -p no:cacheprovider
```

Expected: PASS. In a local browser, model selection changes the Thread, queued messages survive reload, and Stop calls the real endpoint.

**Step 5: Commit**

```powershell
git add -- apps/web/assets/chat-api.js apps/web/assets/chat-controller.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/web/test_creao_chat_frontend_contract.py tests/web/test_chat_frontend_state.py
git commit -m "Make the CREAO-style shell reflect durable Thread execution" -m "Constraint: Existing Operations evidence stays available without dominating the Chat layout." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: frontend Thread, model, Queue, Steering, event, and existing shell contracts" -m "Not-tested: File upload and resource mentions are added next."
```

### Task 11: Add real uploads and structured `@skill` / `@connector` references

**Files:**
- Create: `apps/api/src/taroai/chat/uploads.py`
- Modify: `apps/api/src/taroai/app.py:5297-5435`
- Modify: `apps/api/src/taroai/storage/models.py:10-64`
- Modify: `apps/web/assets/chat-controller.js`
- Create: `apps/web/assets/mentions.js`
- Modify: `apps/web/assets/main.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/api/test_chat_uploads_and_capabilities.py`
- Modify: `tests/api/test_chat_threads_api.py`
- Modify: `tests/web/test_creao_chat_frontend_contract.py`

**Step 1: Write failing upload, capability, and mention tests**

```python
def test_upload_registers_scans_and_writes_content_atomically(client, headers):
    response = client.post(
        "/api/uploads",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "filename": "brief.txt",
            "media_type": "text/plain",
            "content_base64": base64.b64encode(b"hello").decode(),
        },
    )
    assert response.status_code == 201
    assert response.json()["size_bytes"] == 5


def test_explicit_disabled_skill_ref_is_rejected_before_model_call(client, headers): ...
```

Frontend tests require a searchable popup sourced from `/api/workspaces/{workspace_id}/capabilities`, typed chips, drag/drop, progress, removal, and structured `resource_refs` in the message payload.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_chat_uploads_and_capabilities.py tests/api/test_chat_threads_api.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
```

Expected: FAIL because local upload and mention resolution are placeholders.

**Step 3: Implement atomic upload and capability resolution**

Accept base64 JSON to avoid adding `python-multipart`. Enforce decoded-size and media-type limits, scan content, register the StorageObject, upload bytes, and roll back registration on failure. The capabilities endpoint returns enabled, visible, installed Skills and connected Workspace Connectors only.

The mention parser stores typed references independently from visible text:

```javascript
resourceRefs.push({ type: candidate.type, id: candidate.id, version: candidate.version ?? null });
```

The backend re-resolves every reference against tenant, Workspace, visibility, installation, and enabled status; it never trusts the chip label.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_chat_uploads_and_capabilities.py tests/api/test_chat_threads_api.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/chat/uploads.py apps/api/src/taroai/app.py apps/api/src/taroai/storage/models.py apps/web/assets/chat-controller.js apps/web/assets/mentions.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/api/test_chat_uploads_and_capabilities.py tests/api/test_chat_threads_api.py tests/web/test_creao_chat_frontend_contract.py
git commit -m "Make attachments and mentions authoritative Chat resources" -m "Constraint: Uploads use existing scanning and object-storage paths without a new multipart dependency." -m "Confidence: high" -m "Scope-risk: moderate" -m "Tested: upload rollback, capability ACL, structured mentions, drag-drop, and composer contracts" -m "Not-tested: Full Skill packages follow in Slice B."
```

**Slice A completion gate**

```powershell
python -m pytest tests/api/test_chat_threads_api.py tests/api/test_thread_event_stream.py tests/api/test_agent_loop_v2.py tests/api/test_agent_loop_recovery.py tests/api/test_chat_uploads_and_capabilities.py tests/api/test_worker_runner.py tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py tests/web/test_chat_frontend_state.py -q -p no:cacheprovider
```

Expected: PASS with a real multi-turn Thread, repair loop, Queue, Steering, model selector, upload, and mentions.

## Slice B — Skill Package Runtime V2

### Task 12: Define and safely parse immutable Skill packages

**Files:**
- Create: `apps/api/src/taroai/skills/package.py`
- Modify: `apps/api/src/taroai/skills/manifest.py:24-48`
- Modify: `apps/api/src/taroai/skills/__init__.py`
- Create: `tests/api/test_skill_package.py`

**Step 1: Write failing package and archive-safety tests**

```python
def test_skill_package_digest_is_stable_across_zip_entry_order():
    first = SkillPackage.from_zip(zip_bytes(entries_in_order_a()))
    second = SkillPackage.from_zip(zip_bytes(entries_in_order_b()))
    assert first.package_digest == second.package_digest


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "C:\\x", "a\x00b"])
def test_skill_package_rejects_escaping_paths(path):
    with pytest.raises(SkillPackageImportError):
        SkillPackage.from_zip(zip_bytes({path: b"bad", "SKILL.md": valid_skill_md()}))
```

Also cover missing `SKILL.md`, duplicate case-folded paths, symlinks, excessive compression ratio, file count/size limits, forbidden credential filenames, wrapper-root normalization, and required `name`/`description` frontmatter.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_skill_package.py -q -p no:cacheprovider
```

Expected: FAIL because no package parser exists.

**Step 3: Implement the standard-library parser**

```python
class SkillPackageFile(BaseModel):
    path: str
    kind: Literal["instructions", "script", "reference", "asset", "example", "eval", "metadata"]
    media_type: str
    size_bytes: int
    sha256: str
    content: bytes = Field(exclude=True, repr=False)


class SkillPackage(BaseModel):
    manifest: SkillManifest
    source_digest: str
    package_digest: str
    files: list[SkillPackageFile]
```

Use `zipfile`, `hashlib`, `PurePosixPath`, and strict limits. Parse only required scalar frontmatter. Accept optional `taroai.yaml` when its content is valid JSON (JSON-compatible YAML); reject unsupported YAML explicitly instead of silently misparsing it.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_skill_package.py tests/api/test_skills_memory.py -q -p no:cacheprovider
```

Expected: PASS and legacy Manifest payloads remain valid.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/skills/package.py apps/api/src/taroai/skills/manifest.py apps/api/src/taroai/skills/__init__.py tests/api/test_skill_package.py
git commit -m "Give Skills a portable package without trusting archive contents" -m "Constraint: No new YAML or archive dependency is authorized." -m "Confidence: high" -m "Scope-risk: moderate" -m "Tested: package parsing, stable digest, archive safety, limits, and legacy Manifest tests" -m "Not-tested: Persistence and installation are next."
```

### Task 13: Persist Skill packages and pin Workspace installations to exact versions

**Files:**
- Create: `apps/api/migrations/034_skill_runtime_v2.sql`
- Modify: `apps/api/src/taroai/skills/registry.py:24-306`
- Modify: `apps/api/src/taroai/skills/repository.py:25-411`
- Modify: `apps/api/src/taroai/db/postgresql_verification.py`
- Modify: `tests/api/test_migration_contract.py`
- Modify: `tests/api/test_skills_memory.py`
- Modify: `tests/api/test_skill_repository.py`

**Step 1: Write failing version-pin and persistence tests**

```python
def test_installation_remains_on_v1_after_v2_is_published(registry):
    registry.register_package("tenant_acme", package("1.0.0"))
    registry.publish_version("tenant_acme", "reporter", "1.0.0")
    installation = registry.install_for_workspace(
        "tenant_acme", "workspace_sales", "reporter", "1.0.0", "user_luke"
    )
    registry.register_package("tenant_acme", package("2.0.0"))
    registry.publish_version("tenant_acme", "reporter", "2.0.0")
    assert installation.installed_version == "1.0.0"
    assert installation.package_digest == package("1.0.0").package_digest
```

Test immutable package files, SQL hydration, repin/rollback without history mutation, and RLS.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_migration_contract.py tests/api/test_skills_memory.py tests/api/test_skill_repository.py -q -p no:cacheprovider
```

Expected: FAIL because installations have no version or digest.

**Step 3: Implement migration and repositories**

Migration 034 creates `skill_packages`, `skill_package_files`, and `skill_evaluation_runs`; it adds nullable `installed_version`, `package_digest`, `source_digest`, and resolved dependency JSON to installations, plus PostgreSQL RLS. Existing manifest-only installations keep their explicit-invoke behavior but are marked `legacy_manifest` and are excluded from automatic discovery until a real package is installed; do not invent package files or provenance during migration.

Registration is immutable by `(tenant_id, skill_id, version, package_digest)`. Install/upgrade/rollback copies the selected version and digests into the installation. Publishing a newer version never moves an installation pointer.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_migration_contract.py tests/api/test_skills_memory.py tests/api/test_skill_repository.py tests/api/test_postgresql_verification.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/migrations/034_skill_runtime_v2.sql apps/api/src/taroai/skills/registry.py apps/api/src/taroai/skills/repository.py apps/api/src/taroai/db/postgresql_verification.py tests/api/test_migration_contract.py tests/api/test_skills_memory.py tests/api/test_skill_repository.py
git commit -m "Make every installed Skill version reproducible" -m "Constraint: Publishing a new version cannot silently change existing Workspace behavior." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: memory/SQL package history, pinned installation, repin, rollback, and RLS tests" -m "Not-tested: Import transport is added next."
```

### Task 14: Add scanned ZIP/GitHub import, file browsing, upgrade, and rollback APIs

**Files:**
- Create: `apps/api/src/taroai/skills/service.py`
- Modify: `apps/api/src/taroai/storage/models.py:10-64`
- Modify: `apps/api/src/taroai/app.py:1098-1203,4918-5135`
- Modify: `tests/api/test_skill_api.py`
- Create: `tests/api/test_skill_import_service.py`

**Step 1: Write failing import and API security tests**

```python
def test_zip_import_scans_then_persists_immutable_package(client, headers):
    response = client.post(
        "/api/skills/imports/zip",
        headers={**headers, "Content-Type": "application/zip"},
        content=valid_skill_zip(),
    )
    assert response.status_code == 201
    assert response.json()["package_digest"]
    files = client.get(
        "/api/skills/reporter/versions/1.0.0/files", headers=headers
    ).json()
    assert [item["path"] for item in files] == ["SKILL.md", "scripts/run.py"]


def test_github_import_rejects_non_github_and_redirect_escape(client, headers): ...
```

Test scanner failure rollback, tenant visibility, public `github.com`/`codeload.github.com` allowlist, redirect revalidation, byte/time limits, exact-version install, upgrade, and rollback audit metadata.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_skill_import_service.py tests/api/test_skill_api.py -q -p no:cacheprovider
```

Expected: FAIL because package import APIs do not exist.

**Step 3: Implement SkillPackageService and thin routes**

Compose existing StorageCatalog, ObjectStorageAdapter, and StorageContentScanner. Validate and scan every extracted file before registering package metadata; upload the original archive as immutable storage only after validation. Inject a GitHub fetcher so tests never make network calls. Generic URLs are forbidden to prevent SSRF.

Use raw `application/zip` rather than multipart. Add version/file read endpoints and an installation PATCH payload:

```python
class SkillInstallationUpdate(BaseModel):
    version: str = Field(min_length=1)
    expected_package_digest: str = Field(min_length=64, max_length=64)
```

All audit and API responses include version, package digest, source digest, and source URL where applicable.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_skill_import_service.py tests/api/test_skill_api.py tests/api/test_storage_adapter.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/skills/service.py apps/api/src/taroai/storage/models.py apps/api/src/taroai/app.py tests/api/test_skill_api.py tests/api/test_skill_import_service.py
git commit -m "Import Skills without turning package sources into a trust boundary" -m "Constraint: GitHub fetching is host-allowlisted and every file is scanned before registration." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: ZIP/GitHub import, SSRF/redirect, scan rollback, file ACL, install, upgrade, rollback" -m "Not-tested: Sandbox materialization follows next."
```

### Task 15: Discover Skills progressively and materialize only selected packages

**Files:**
- Create: `apps/api/src/taroai/skills/discovery.py`
- Create: `apps/api/src/taroai/skills/materializer.py`
- Modify: `apps/api/src/taroai/sandbox/models.py:85-93`
- Modify: `apps/api/src/taroai/sandbox/process.py:111-127`
- Modify: `apps/api/src/taroai/sandbox/docker.py:192-208`
- Modify: `apps/api/src/taroai/sandbox/kubernetes.py:261-306`
- Modify: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/app.py:1388-1418`
- Modify: `apps/api/src/taroai/workers/runner.py:400-445`
- Modify: `tests/api/sandbox_adapters.py:66-78`
- Create: `tests/api/test_skill_discovery.py`
- Create: `tests/api/test_skill_materializer.py`
- Modify: `tests/api/test_agent_loop_v2.py`

**Step 1: Write failing progressive-disclosure and materialization tests**

```python
def test_planner_sees_summaries_then_loads_selected_skill_only():
    discovery = seeded_discovery(enabled=["reporter", "researcher"])
    loop = build_loop(discovery=discovery, selected_skill="reporter")
    loop.run()
    assert "reporter" in loop.first_request.skill_summaries
    assert "# Procedure" not in loop.first_request.messages[0].content
    assert discovery.loaded_skill_ids == ["reporter"]


def test_materializer_writes_binary_assets_inside_skill_root(sandbox):
    manifest = materialize(package_with_png(), sandbox)
    assert manifest.root == "/workspace/.taroai/skills/image-skill/1.0.0"
    assert sandbox.uploads[-1].content_bytes() == PNG_BYTES
```

Test disabled/uninstalled/version-mismatched Skill rejection, explicit reference resolution, stable run provenance, path revalidation, and proof that scripts never execute on the API host.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_skill_discovery.py tests/api/test_skill_materializer.py tests/api/test_agent_loop_v2.py -q -p no:cacheprovider
```

Expected: FAIL because discovery/materialization do not exist.

**Step 3: Implement discovery and run-scoped materialization**

`SkillDiscoveryService.list_summaries()` returns only published, visible, installed, enabled, complete pinned packages. `load_instructions()` resolves that exact package. The loop records Skill ID/version/digests and loads full instructions only after selection.

Extend `SandboxFileWrite` with mutually exclusive text/base64 content and `content_bytes()`. Materialize validated paths under `/workspace/.taroai/skills/{id}/{version}/`; never execute package scripts on the host.

Build the same SQL-backed discovery/materializer for API and Worker runtimes.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_skill_discovery.py tests/api/test_skill_materializer.py tests/api/test_agent_loop_v2.py tests/api/test_sandbox.py tests/api/test_sandbox_docker.py tests/api/test_sandbox_kubernetes.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/skills/discovery.py apps/api/src/taroai/skills/materializer.py apps/api/src/taroai/sandbox/models.py apps/api/src/taroai/sandbox/process.py apps/api/src/taroai/sandbox/docker.py apps/api/src/taroai/sandbox/kubernetes.py apps/api/src/taroai/agent/loop.py apps/api/src/taroai/app.py apps/api/src/taroai/workers/runner.py tests/api/sandbox_adapters.py tests/api/test_skill_discovery.py tests/api/test_skill_materializer.py tests/api/test_agent_loop_v2.py
git commit -m "Load only the Skill the agent actually selects" -m "Constraint: Package files execute only inside the run Sandbox and exact installed provenance is recorded." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: progressive discovery, explicit refs, provenance, binary materialization, and Sandbox adapters" -m "Not-tested: Skill evaluation and management UI follow next."
```

### Task 16: Add executable Skill evaluations and the complete Skills UI

**Files:**
- Create: `apps/api/src/taroai/skills/evaluation.py`
- Modify: `apps/api/src/taroai/skills/registry.py`
- Modify: `apps/api/src/taroai/skills/repository.py`
- Modify: `apps/api/src/taroai/app.py`
- Create: `apps/web/assets/skills-ui.js`
- Modify: `apps/web/assets/main.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/api/test_skill_evaluation.py`
- Modify: `tests/api/test_skill_api.py`
- Create: `tests/web/test_skills_frontend.py`

**Step 1: Write failing evaluation and UI tests**

```python
def test_skill_publish_rejects_score_below_minimum():
    result = run_eval(package_digest="a" * 64, scores=[0.7, 0.8])
    with pytest.raises(SkillEvaluationGateError):
        publish(minimum_score=0.85, evaluation=result)


def test_eval_blocks_side_effects_by_default():
    with pytest.raises(SkillEvaluationError, match="side effects"):
        load_case({"input": {}, "allow_side_effects": False, "tool": "gmail.send"})
```

Frontend tests require search, built-in/custom sections, enable toggle, rendered/raw `SKILL.md`, file tree, syntax-safe source view, GitHub/ZIP install, refresh, upgrade/rollback, and Try in Chat.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_skill_evaluation.py tests/api/test_skill_api.py tests/web/test_skills_frontend.py -q -p no:cacheprovider
```

Expected: FAIL because evaluations and full Skills UI do not exist.

**Step 3: Implement typed evaluations and real UI actions**

Evaluation cases contain typed input, expected contract, deterministic scorer, tolerance, max cost/duration, and side-effect policy. Bind every result to Skill version and package digest. Run cases through the same Sandbox/Loop path. Publish enforces configured minimum score.

`skills-ui.js` calls real endpoints for every action. Render Markdown using a small escaping-first renderer; never inject package HTML. ZIP install sends raw bytes, GitHub install sends the allowlisted URL, and Try in Chat adds a structured Skill reference.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_skill_evaluation.py tests/api/test_skill_api.py tests/web/test_skills_frontend.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/skills/evaluation.py apps/api/src/taroai/skills/registry.py apps/api/src/taroai/skills/repository.py apps/api/src/taroai/app.py apps/web/assets/skills-ui.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/api/test_skill_evaluation.py tests/api/test_skill_api.py tests/web/test_skills_frontend.py
git commit -m "Make Skill installation, inspection, and evaluation a real product loop" -m "Constraint: Skill source rendering is escaping-first and evaluations use the production Sandbox path." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: evaluation gate, side-effect policy, Skills API, and complete Skills UI contracts" -m "Not-tested: Artifact Dashboard and Agent creation follow in Slice C."
```

**Slice B completion gate**

```powershell
python -m pytest tests/api/test_skill_package.py tests/api/test_skills_memory.py tests/api/test_skill_repository.py tests/api/test_skill_import_service.py tests/api/test_skill_api.py tests/api/test_skill_discovery.py tests/api/test_skill_materializer.py tests/api/test_skill_evaluation.py tests/api/test_agent_loop_v2.py tests/web/test_skills_frontend.py -q -p no:cacheprovider
```

Expected: PASS with automatic and explicit Skill selection using a pinned, materialized, evaluated package.

## Slice C — Artifacts, Dashboards, and Reusable Agents

### Task 17: Add safe interactive artifacts and structured dashboards

**Files:**
- Create: `apps/api/migrations/035_agents_shares_and_rich_artifacts.sql`
- Create: `apps/api/src/taroai/artifacts/__init__.py`
- Create: `apps/api/src/taroai/artifacts/models.py`
- Create: `apps/api/src/taroai/artifacts/service.py`
- Modify: `apps/api/src/taroai/domain.py:90-98`
- Modify: `apps/api/src/taroai/store.py`
- Modify: `apps/api/src/taroai/db/repository.py`
- Modify: `apps/api/src/taroai/app.py:2817-2865,5297-5448`
- Create: `apps/web/assets/artifacts-ui.js`
- Modify: `apps/web/assets/main.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/api/test_rich_artifacts.py`
- Modify: `tests/api/test_migration_contract.py`
- Create: `tests/web/test_artifact_dashboard_frontend.py`

**Step 1: Write failing Artifact and Dashboard tests**

```python
def test_dashboard_rejects_unknown_widget_and_script_payload():
    with pytest.raises(ValidationError):
        DashboardDocument.model_validate({
            "version": "1",
            "widgets": [{"type": "script", "source": "alert(1)"}],
        })


def test_artifact_response_exposes_safe_preview_contract(client, artifact):
    body = client.get(f"/api/artifacts/{artifact.id}", headers=HEADERS).json()
    assert body["preview_type"] == "html"
    assert "content_security_policy" in body["render_policy"]
    assert "allow-same-origin" not in body["render_policy"]["iframe_sandbox"]
```

Frontend tests require Artifact chips, reopenable sidecar, HTML/SVG/PDF/image/text/code views, Code/Preview tabs, copy/download, and Widget Schema rendering for KPI, chart, table, alert, and progress widgets.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_rich_artifacts.py tests/api/test_migration_contract.py tests/web/test_artifact_dashboard_frontend.py -q -p no:cacheprovider
```

Expected: FAIL because Artifact metadata and structured Dashboard models are absent.

**Step 3: Implement rich Artifact contracts and safe renderers**

Migration 035 adds Thread/Message association, preview type, Dashboard schema JSON, render policy, and the Agent/share tables needed by the next tasks. Use a discriminated Widget union; never render arbitrary model JavaScript as a trusted Dashboard.

`artifacts-ui.js` renders trusted Widgets with DOM APIs. HTML previews use a separate preview endpoint/origin where configured and an iframe sandbox of `allow-scripts allow-forms`, without `allow-same-origin`, top navigation, popups, or host credentials. Revoke object URLs when the panel closes.

**Step 4: Run tests and browser smoke checks**

```powershell
python -m pytest tests/api/test_rich_artifacts.py tests/api/test_migration_contract.py tests/web/test_artifact_dashboard_frontend.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
```

Expected: PASS. Manually verify a sample HTML form works inside the isolated preview while host DOM/cookies remain inaccessible.

**Step 5: Commit**

```powershell
git add -- apps/api/migrations/035_agents_shares_and_rich_artifacts.sql apps/api/src/taroai/artifacts apps/api/src/taroai/domain.py apps/api/src/taroai/store.py apps/api/src/taroai/db/repository.py apps/api/src/taroai/app.py apps/web/assets/artifacts-ui.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/api/test_rich_artifacts.py tests/api/test_migration_contract.py tests/web/test_artifact_dashboard_frontend.py
git commit -m "Make generated outputs interactive without trusting generated code" -m "Constraint: Dashboards use a typed Widget Schema and HTML previews cannot access the Taroai origin." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: Artifact API, Dashboard schema, iframe policy, viewer, copy, and download contracts" -m "Not-tested: Agent extraction and reuse follow next."
```

### Task 18: Build Agent Registry, versioning, extraction, and repeat execution

**Files:**
- Create: `apps/api/src/taroai/agents/__init__.py`
- Create: `apps/api/src/taroai/agents/models.py`
- Create: `apps/api/src/taroai/agents/repository.py`
- Create: `apps/api/src/taroai/agents/service.py`
- Modify: `apps/api/src/taroai/app.py`
- Modify: `apps/api/src/taroai/agent/loop.py`
- Create: `tests/api/test_agent_registry.py`
- Create: `tests/api/test_agent_api.py`

**Step 1: Write failing Agent v1 tests**

```python
def test_successful_thread_extracts_reviewable_agent_draft(client, successful_thread):
    response = client.post(
        f"/api/threads/{successful_thread.id}/agent-drafts",
        headers=HEADERS,
        json={"output_format": "dashboard", "instructions": "Focus on revenue"},
    )
    assert response.status_code == 201
    draft = response.json()
    assert draft["input_schema"]["type"] == "object"
    assert draft["skill_bindings"][0]["version"]
    assert draft["runtime_snapshot_ref"]


def test_agent_run_uses_immutable_version_contract(client, published_agent):
    run = client.post(
        f"/api/agents/{published_agent.id}/runs",
        headers=HEADERS,
        json={"version": 1, "input": {"region": "APAC"}},
    ).json()
    assert run["agent_version"] == 1
```

Test restore-as-new-version, release notes, pinned Skills, reference-file mount paths, tenant/Workspace ACL, output formats, and session history.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_agent_registry.py tests/api/test_agent_api.py -q -p no:cacheprovider
```

Expected: FAIL because no Agent Registry exists.

**Step 3: Implement AgentService and APIs**

Use models created by migration 035:

```python
class AgentVersion(BaseModel):
    agent_id: str
    version: int = Field(ge=1)
    input_schema: dict[str, Any]
    output_contract: dict[str, Any]
    instructions: str
    skill_bindings: list[PinnedSkillBinding]
    connector_requirements: list[str]
    model_policy: dict[str, Any]
    runtime_snapshot_ref: str | None
    reference_files: list[AgentReferenceFile]
    status: Literal["draft", "published", "disabled"]
```

Extraction uses a structured Model Gateway operation over the successful Thread's redacted messages, selected Skills, final outputs, and input hints. The user must review the draft before publish. Publishing stores a Sandbox snapshot reference and immutable file/Skill digests. Running an Agent validates the input schema, creates a new Thread/Run, mounts reference files, and executes Agent Loop V2.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_agent_registry.py tests/api/test_agent_api.py tests/api/test_agent_loop_v2.py tests/api/test_sandbox.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/agents apps/api/src/taroai/app.py apps/api/src/taroai/agent/loop.py tests/api/test_agent_registry.py tests/api/test_agent_api.py
git commit -m "Turn successful Threads into repeatable versioned Agents" -m "Constraint: Draft extraction is reviewable and published versions pin Skills, files, model policy, and runtime snapshot." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: extraction, publish, version restore, ACL, input validation, reference mounts, and repeat run tests" -m "Not-tested: The Create Agent frontend follows next."
```

### Task 19: Replace static Agent cards with Create Agent and Agent-run product flows

**Files:**
- Create: `apps/web/assets/agents-ui.js`
- Modify: `apps/web/assets/main.js:65-143,572-590,4060-4075`
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/web/test_agents_frontend.py`
- Modify: `tests/web/test_creao_chat_frontend_contract.py`

**Step 1: Write failing Create Agent UI tests**

```python
def test_create_agent_opens_reviewable_draft_instead_of_static_route():
    source = all_web_module_source()
    assert "/agent-drafts" in source
    assert "renderAgentDraftForm" in source
    assert "publishAgentDraft" in source
    assert "Create a reusable agent from this conversation." not in create_agent_handler(source)


def test_agent_run_form_is_generated_from_version_input_schema():
    source = all_web_module_source()
    assert "renderJsonSchemaField" in source
    assert "/api/agents/${agentId}/runs" in source
```

Require name, description, instructions, Dashboard/Markdown/HTML output format, editable structured inputs, pinned Skill/file summary, version history, restore, publish, and session history.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/web/test_agents_frontend.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
```

Expected: FAIL because Create Agent only navigates or fills a prompt.

**Step 3: Implement real Agent dialogs and routes**

Enable Create Agent only after an eligible successful Run. Show extraction progress, then a review form; publish only on explicit user action. The Agents route loads real Agent cards, status, version count, last run, and Run button. Render input fields from the saved JSON Schema using safe native controls; send typed input to the versioned run API.

Do not infer success from a toast. Reload the created Agent and version from the API before showing completion.

**Step 4: Run tests and browser smoke checks**

```powershell
python -m pytest tests/web/test_agents_frontend.py tests/web/test_creao_chat_frontend_contract.py tests/api/test_agent_api.py -q -p no:cacheprovider
```

Expected: PASS. Browser flow: completed Thread -> review draft -> publish v1 -> run with new inputs -> open resulting Thread.

**Step 5: Commit**

```powershell
git add -- apps/web/assets/agents-ui.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/web/test_agents_frontend.py tests/web/test_creao_chat_frontend_contract.py
git commit -m "Make Create Agent produce something the team can run again" -m "Constraint: Users review extracted fields and bindings before publishing an immutable version." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: draft, publish, schema form, version history, restore, and repeat-run frontend contracts" -m "Not-tested: Voice, sharing, suggestions, and reconnect complete the Chat experience next."
```

**Slice C completion gate**

```powershell
python -m pytest tests/api/test_rich_artifacts.py tests/api/test_agent_registry.py tests/api/test_agent_api.py tests/web/test_artifact_dashboard_frontend.py tests/web/test_agents_frontend.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
```

Expected: PASS with interactive artifacts, typed dashboards, and a reusable Agent v1 created from Chat.

## Slice D — Complete CREAO Chat Experience

### Task 20: Add voice transcription and Summarize-and-Read-Aloud

**Files:**
- Create: `apps/api/src/taroai/speech/__init__.py`
- Create: `apps/api/src/taroai/speech/models.py`
- Create: `apps/api/src/taroai/speech/gateway.py`
- Modify: `apps/api/src/taroai/config.py`
- Modify: `apps/api/src/taroai/app.py`
- Create: `apps/web/assets/speech-ui.js`
- Modify: `apps/web/assets/main.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/api/test_speech_api.py`
- Create: `tests/web/test_speech_frontend.py`

**Step 1: Write failing speech API and UI tests**

```python
def test_transcription_accepts_bounded_base64_audio_and_returns_editable_text(client):
    response = client.post(
        "/api/speech/transcriptions",
        headers=HEADERS,
        json={
            "media_type": "audio/webm",
            "content_base64": base64.b64encode(b"audio").decode(),
            "duration_seconds": 12,
        },
    )
    assert response.json() == {"text": "draft transcript", "language": "en"}


def test_read_aloud_uses_server_loaded_message_not_arbitrary_hidden_text(client, message): ...
```

Frontend tests require permission-safe start, live timer/waveform, two-minute limit, cancel, transcribing state, editable transcript, message-menu play/stop, URL revocation, and disabled state when backend capability is absent.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_speech_api.py tests/web/test_speech_frontend.py -q -p no:cacheprovider
```

Expected: FAIL because voice controls are placeholder strings.

**Step 3: Implement injectable SpeechGateway and MediaRecorder UI**

Use base64 JSON inbound to avoid a multipart dependency. Enforce media types, decoded byte limit, and duration before provider calls. The default OpenAI-compatible adapter builds outbound multipart bytes with the standard library for transcription and JSON for speech synthesis. Resolve credentials through the existing secret path; never return provider errors containing keys.

For read-aloud, accept a Thread message ID, load its authorized assistant text server-side, summarize it through Model Gateway, synthesize audio, and return short-lived base64 audio. Do not persist raw recording bytes after transcription.

**Step 4: Run tests and browser permission smoke checks**

```powershell
python -m pytest tests/api/test_speech_api.py tests/web/test_speech_frontend.py tests/api/test_model_gateway_credentials.py -q -p no:cacheprovider
```

Expected: PASS. Verify cancel releases microphone tracks and Stop releases the audio object URL.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/speech apps/api/src/taroai/config.py apps/api/src/taroai/app.py apps/web/assets/speech-ui.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/api/test_speech_api.py tests/web/test_speech_frontend.py
git commit -m "Let users speak to Chat and hear concise answers" -m "Constraint: Audio transport uses bounded base64 JSON and short-lived memory without a new multipart dependency." -m "Confidence: high" -m "Scope-risk: moderate" -m "Tested: transcription, credential redaction, duration/size limits, recording cleanup, TTS play/stop" -m "Not-tested: Public sharing and suggestions follow next."
```

### Task 21: Add revocable sharing, Thread management, and contextual suggestions

**Files:**
- Create: `apps/api/src/taroai/chat/sharing.py`
- Create: `apps/api/src/taroai/chat/suggestions.py`
- Modify: `apps/api/src/taroai/store.py`
- Modify: `apps/api/src/taroai/db/repository.py`
- Modify: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/app.py`
- Create: `apps/web/assets/thread-ui.js`
- Modify: `apps/web/assets/chat-controller.js`
- Modify: `apps/web/assets/main.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/api/test_thread_sharing.py`
- Modify: `tests/api/test_chat_threads_api.py`
- Create: `tests/web/test_thread_experience_frontend.py`

**Step 1: Write failing sharing, management, and suggestion tests**

```python
def test_shared_thread_is_read_only_redacted_and_revocable(client, completed_thread):
    share = client.post(f"/api/threads/{completed_thread.id}/share", headers=HEADERS).json()
    public = client.get(f"/api/shared/threads/{share['token']}")
    assert public.status_code == 200
    assert "tool_input" not in public.text
    assert "secret" not in public.text.lower()
    client.delete(f"/api/threads/{completed_thread.id}/share", headers=HEADERS)
    assert client.get(f"/api/shared/threads/{share['token']}").status_code == 404


def test_suggestions_are_typed_and_bound_to_completed_run():
    suggestions = build_suggestions(completed_run())
    assert {item.kind for item in suggestions} <= {"refine", "connect", "run_agent"}
```

Frontend tests cover search, pin, rename, delete confirmation, share/copy/revoke, read-only public view, suggestion click semantics, and draft persistence per Thread.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_thread_sharing.py tests/api/test_chat_threads_api.py tests/web/test_thread_experience_frontend.py -q -p no:cacheprovider
```

Expected: FAIL because Thread sharing and suggestions do not exist.

**Step 3: Implement hashed shares and typed suggestion events**

Generate a cryptographically random token, return it once, and store only its tenant-scoped hash. Public serialization includes user/assistant messages and public Artifacts only; remove Tool inputs, raw observations, audit fields, storage internals, and all secret-like metadata.

After successful verification, request up to three structured suggestions and persist `suggestion.created` events. A refine chip fills the composer, a connect chip opens an existing Connector flow, and a run-agent chip calls a real Agent endpoint. Hide suggestions when the next message starts.

Thread rename/pin/delete/search use server state. Delete is soft-delete first so an in-flight Run can finish safely; public shares are revoked immediately.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_thread_sharing.py tests/api/test_chat_threads_api.py tests/web/test_thread_experience_frontend.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/chat/sharing.py apps/api/src/taroai/chat/suggestions.py apps/api/src/taroai/store.py apps/api/src/taroai/db/repository.py apps/api/src/taroai/agent/loop.py apps/api/src/taroai/app.py apps/web/assets/thread-ui.js apps/web/assets/chat-controller.js apps/web/assets/main.js apps/web/index.html apps/web/assets/styles.css tests/api/test_thread_sharing.py tests/api/test_chat_threads_api.py tests/web/test_thread_experience_frontend.py
git commit -m "Make completed Threads easy to revisit, refine, and share safely" -m "Constraint: Public serialization excludes internal execution and secret-bearing metadata." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: share/revoke/redaction, pin/rename/delete/search, drafts, and typed suggestions" -m "Not-tested: Connector reconnect recovery follows next."
```

### Task 22: Reconnect expired Connectors inline and retry exactly one failed Action

**Files:**
- Modify: `apps/api/src/taroai/connectors/oauth.py`
- Modify: `apps/api/src/taroai/connectors/dispatch.py`
- Modify: `apps/api/src/taroai/connectors/invocation.py`
- Modify: `apps/api/src/taroai/agent/loop.py`
- Modify: `apps/api/src/taroai/chat/service.py`
- Modify: `apps/api/src/taroai/app.py:1797-1875`
- Create: `apps/web/assets/reconnect-ui.js`
- Modify: `apps/web/assets/main.js`
- Modify: `apps/web/assets/styles.css`
- Create: `tests/api/test_chat_connector_reconnect.py`
- Create: `tests/web/test_connector_reconnect_frontend.py`

**Step 1: Write failing reconnect and idempotent retry tests**

```python
def test_expired_connector_pauses_action_and_emits_one_reconnect_card(loop):
    loop.connector_dispatcher.fail_with_expired_token("connector_gmail")
    state = loop.run()
    assert state.status == RunStatus.AWAITING_APPROVAL
    events = events_of_type(state.run_id, "connector.reconnect_required")
    assert len(events) == 1
    assert events[0].payload["connector_id"] == "connector_gmail"


def test_reconnect_retries_original_action_once_with_same_idempotency_key(loop):
    resume_after_reconnect(loop)
    assert connector.calls == 2
    assert connector.calls[0].idempotency_key == connector.calls[1].idempotency_key
```

Test one card per Connector per Thread, failed/cancelled OAuth, ACL, superseding a pending reconnect with a new user message, and absence of tokens in events.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/api/test_chat_connector_reconnect.py tests/web/test_connector_reconnect_frontend.py -q -p no:cacheprovider
```

Expected: FAIL because expired credentials become generic Tool failures.

**Step 3: Implement classified pause and resume**

Classify expired/invalid Connector credentials separately. Persist the failed Action and emit a redacted reconnect event. The inline card starts the existing OAuth authorization flow. After callback, a Thread resume endpoint validates that the same Connector is authorized, then retries only that Action with its original idempotency key. If the user sends a superseding instruction, cancel the pending reconnect and disable its controls.

Do not add any new Connector implementation in this task.

**Step 4: Run tests to verify they pass**

```powershell
python -m pytest tests/api/test_chat_connector_reconnect.py tests/api/test_connector_oauth.py tests/api/test_connector_dispatch.py tests/web/test_connector_reconnect_frontend.py -q -p no:cacheprovider
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add -- apps/api/src/taroai/connectors/oauth.py apps/api/src/taroai/connectors/dispatch.py apps/api/src/taroai/connectors/invocation.py apps/api/src/taroai/agent/loop.py apps/api/src/taroai/chat/service.py apps/api/src/taroai/app.py apps/web/assets/reconnect-ui.js apps/web/assets/main.js apps/web/assets/styles.css tests/api/test_chat_connector_reconnect.py tests/web/test_connector_reconnect_frontend.py
git commit -m "Recover expired Connector actions without restarting the conversation" -m "Constraint: Reconnect reuses existing Connector OAuth and retries only the persisted failed Action." -m "Confidence: high" -m "Scope-risk: broad" -m "Tested: pause/card/OAuth/resume, exactly-once retry, supersede, ACL, and token redaction" -m "Not-tested: Full Compose and browser journey follows in the final task."
```

### Task 23: Prove the complete product journey in Docker and a real browser

**Files:**
- Create: `tests/web/live_app.py`
- Create: `tests/web/test_creao_chat_e2e.py`
- Create: `tests/web/fixtures/creao-chat/reference/README.md`
- Create: `scripts/verify-creao-chat.ps1`
- Create: `docs/runbooks/creao-chat-verification.md`
- Modify: `apps/api/src/taroai/config.py`
- Modify: `infra/package/upgrade-matrix.md`
- Modify: `tests/api/test_release_package.py`
- Modify: `tests/web/test_workspace_frontend_contract.py`

**Step 1: Write failing end-to-end and verification-script contract tests**

The deterministic Compose/browser journey must cover:

```text
create Thread with real catalog model
-> submit task
-> first command fails
-> model receives observation and emits a different command
-> verifier passes and Artifact opens
-> queue a message, steer another, reload, and recover
-> install a ZIP Skill and use it implicitly and via @skill
-> upload a file and render a Dashboard
-> create Agent v1 and run it with different inputs
-> record/transcribe/play TTS through a deterministic speech adapter
-> share and revoke the Thread
-> reconnect an expired fixture Connector and resume exactly once
```

Add script contract tests asserting `verify-creao-chat.ps1` starts the pinned Compose services, waits for health, runs migrations, executes the browser suite, writes JSON evidence, and always cleans up its project namespace.

**Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/web/test_creao_chat_e2e.py tests/web/test_workspace_frontend_contract.py tests/api/test_release_package.py -q -p no:cacheprovider
```

Expected: FAIL because the live harness and verification script do not exist.

**Step 3: Implement the deterministic harness and evidence gate**

Use existing TestClient/Playwright packages; do not add pytest-playwright. Start the static frontend and API on allocated ports, use deterministic injected Model/Speech/Connector adapters for the complete journey, and add one opt-in `live` test using the configured `.env` provider without logging credentials.

Capture only sanitized reference screenshots. Run **@visual-verdict** for desktop and mobile after every visual edit; require score >= 90, correct landmarks, and zero functional failures. Add keyboard-only checks for Composer, menus, Queue, dialogs, Artifact tabs, Skills, Create Agent, and reconnect cards. No axe dependency is added without approval.

After all focused tests pass, change the default `agent_runtime_mode` to `loop_v2`; retain `legacy` for one rollback window. Update the upgrade matrix for migrations 033-035 and ensure the release manifest includes all new source/static files without treating a repository ZIP as product completion.

**Step 4: Run the full verification ladder**

```powershell
python -m compileall apps/api/src/taroai
python -m pytest -m "not live and not docker and not compose and not kubernetes" -q -p no:cacheprovider
docker compose -f infra/docker-compose.yml config -q
pwsh -NoProfile -File scripts/verify-creao-chat.ps1 -Output dist/creao-chat/evidence.json
python -m pytest tests/web/test_creao_chat_e2e.py --run-live -m live -q -p no:cacheprovider
git diff --check
```

Expected:

- Compile and non-live full suite pass.
- Compose config is valid.
- Deterministic Docker/browser journey passes and evidence JSON records commit SHA, migrations, event sequence, screenshot verdicts, and scenario results.
- Live provider smoke passes when credentials/network are available; otherwise record it as explicitly not run, never as passed.
- Working tree contains no generated evidence or secret file.

**Step 5: Commit**

```powershell
git add -- tests/web/live_app.py tests/web/test_creao_chat_e2e.py tests/web/fixtures/creao-chat/reference/README.md scripts/verify-creao-chat.ps1 docs/runbooks/creao-chat-verification.md apps/api/src/taroai/config.py infra/package/upgrade-matrix.md tests/api/test_release_package.py tests/web/test_workspace_frontend_contract.py
git commit -m "Make the complete CREAO Chat journey a repeatable product gate" -m "Constraint: Deterministic Compose evidence is mandatory; live provider smoke is reported honestly when unavailable." -m "Confidence: high" -m "Scope-risk: broad" -m "Directive: Do not remove the legacy runtime until one rollback window has passed with clean V2 evidence." -m "Tested: full non-live suite, Compose validation, deterministic browser journey, visual verdict, and optional live provider smoke" -m "Not-tested: Production-grade Sandbox isolation and formal release publishing remain separate gates."
```

**Slice D and program completion gate**

```powershell
python -m pytest tests/api -q -p no:cacheprovider
python -m pytest tests/web -q -p no:cacheprovider
pwsh -NoProfile -File scripts/verify-creao-chat.ps1 -Output dist/creao-chat/evidence.json
git status --short
git log --oneline --decorate -25
```

Expected: all tests and product evidence pass; `git status` shows no generated outputs; no known P0/P1 item from the approved design remains.

## Review Checkpoints

After each slice, request a code review before continuing:

- Slice A: Thread semantics, model snapshot, loop correctness, failure recovery, Queue/Steer, and frontend truthfulness.
- Slice B: package/archive security, pinned provenance, discovery/materialization, evaluation, and source rendering safety.
- Slice C: iframe/CSP isolation, Widget validation, Agent version immutability, and runtime snapshot/reference-file reproducibility.
- Slice D: audio privacy, public-share redaction, Connector retry idempotency, accessibility, visual parity, and evidence quality.

If a checkpoint fails, fix it inside that slice and rerun its complete gate before beginning the next slice.

## Completion Definition

The program is complete only when all of the following are simultaneously true:

- A Tool failure is visibly observed and causes a different model-generated repair Action.
- Verifier approval, not plan exhaustion, controls success.
- Multi-turn Threads, Queue, Steering, Stop, refresh recovery, model selection, uploads, and mentions are authoritative server-backed features.
- Skills are packaged, version-pinned, safely imported, progressively discovered, materialized, evaluated, and manageable in the UI.
- Artifacts and Dashboards are interactive within their safety boundary.
- A successful Thread becomes a runnable Agent v1 with immutable version bindings.
- Voice, TTS, sharing, suggestions, Thread management, and Connector reconnect work end-to-end.
- No active control inserts a placeholder prompt or relies only on localStorage.
- Full tests, deterministic Docker/browser verification, and visual/functional verdicts pass.
- Remaining production Sandbox and formal-release limitations are reported, not hidden.

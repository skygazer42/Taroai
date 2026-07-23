# Taroai Release-Readiness Vertical Slice Implementation Plan


**Goal:** Deliver a verifiable internal-alpha release path that proves a real DeepSeek-backed Docker Compose run, persistent CREAO-style conversations with Manus-class execution UX, a deterministic 50-case agent-quality gate, and signed builder artifacts instead of a repository ZIP.

**Architecture:** Keep the current modular monolith and extend its existing runtime, persistence, sandbox-controller, verification, and release-package boundaries. A persistent Conversation/Turn record creates a Run; one ordered resumable SSE stream drives conversation, timeline, and workbench panes; immutable terminal traces feed a versioned evaluation runner; formal release output is assembled only from commit/build-bound gate evidence and digest-pinned OCI/Helm/air-gap artifacts. The canonical Compose journey keeps one application Run ID, while evaluation retains its 50 case Run IDs. Docker Compose proves internal-alpha behavior, while a separate real Kubernetes gVisor/Kata gate is mandatory for production-candidate promotion.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PostgreSQL, Redis, MinIO, Docker Compose, rootless Docker daemon, static HTML/CSS/JavaScript, Playwright, pytest, Kubernetes/Helm, Ed25519 signing, OCI/SBOM/provenance tooling.

---

## Source of truth and execution contract

- Approved design: **docs/plans/2026-07-10-release-readiness-vertical-slice-design.md**
- Execute in the dependency order below. Do not skip a RED result, do not combine unrelated tasks, and do not promote after a failed gate.
- Preserve the primary workspace's uncommitted CREAO frontend work and reconcile it deliberately when Tasks 15-18 touch the same files.
- For every behavior change, perform the named RED/GREEN sequence.
- Before any completion, release, or promotion claim, run the named verification commands.
- Use the existing DeepSeek-compatible values from the local untracked .env only for synthetic live verification. Never print, copy into evidence, or commit secret values.
- Keep release profiles explicit:
  - internal-alpha requires hermetic, Compose, evaluation, frontend, and formal-builder gates.
  - production-candidate additionally requires live Kubernetes isolation evidence.
  - production additionally requires install, upgrade, rollback, and restore drills.
- Every commit must follow the Lore protocol in AGENTS.md. The intent lines below are the required first lines; add Tested and Not-tested trailers from actual evidence.

## Task 0: Isolate the implementation and preserve the current frontend work

**Files:**
- Read only: **apps/web/index.html**
- Read only: **apps/web/assets/main.js**
- Read only: **apps/web/assets/styles.css**
- Read only: **tests/web/test_workspace_frontend_contract.py**
- Read only: **tests/web/test_creao_chat_frontend_contract.py**
- Read only: **docs/plans/2026-07-10-creao-chat-parity-design.md**
- Read only: **docs/plans/2026-07-10-creao-chat-parity.md**

**Step 1: Record the current boundary**

Run:

~~~powershell
git status --short
git diff --check
git diff -- apps/web/index.html apps/web/assets/main.js apps/web/assets/styles.css tests/web/test_workspace_frontend_contract.py
~~~

Expected: the known frontend changes remain visible; no secret file content is displayed.

**Step 2: Create the isolated worktree**

Create an isolated worktree and a branch using the required **codex/** prefix from commit **6690f53** or its verified descendant.

**Step 3: Preserve, do not blindly apply, the dirty frontend work**

Save a patch outside the repository and copy the three untracked CREAO documents/tests into the isolated worktree only after checking that none contains credentials. Do not commit the patch artifact. Reconcile the patch during Tasks 15-18, guided by the tests and the approved design.

**Step 4: Verify the starting point**

Run:

~~~powershell
git status --short
git log -1 --oneline
~~~

Expected: the implementation worktree is clean and contains the approved design commit.

## Task 1: Establish a hermetic dependency and test gate

**Files:**
- Create: **apps/api/requirements-test.in**
- Create: **apps/api/requirements-test.lock**
- Create: **tests/conftest.py**
- Create: **tests/web/conftest.py**
- Create: **tests/web/test_browser_harness.py**
- Create: **tests/api/test_hermetic_test_gate_contract.py**
- Create: **scripts/verify-hermetic-tests.ps1**
- Modify: **pyproject.toml**
- Modify: **tests/web/test_workspace_frontend_contract.py**
- Modify: **tests/web/test_creao_chat_frontend_contract.py**

**Step 1: Write the failing contract tests**

Add tests that require:

~~~python
def test_test_requirements_include_runtime_and_pytest():
    text = Path("apps/api/requirements-test.in").read_text(encoding="utf-8")
    assert "-r requirements.txt" in text
    lock = Path("apps/api/requirements-test.lock").read_text(encoding="utf-8")
    assert "pytest==" in lock
    assert "--hash=sha256:" in lock


def test_live_dependency_markers_are_registered():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    for marker in ("live", "docker", "compose", "kubernetes"):
        assert marker in text


def test_hermetic_script_collects_before_running():
    text = Path("scripts/verify-hermetic-tests.ps1").read_text(encoding="utf-8")
    assert "--collect-only" in text
    assert 'not live' in text
~~~

Also add fixture self-tests proving that unmarked tests cannot open non-loopback sockets or invoke real docker/kubectl subprocesses. Change every frontend contract file read to explicit UTF-8.

In **tests/web/conftest.py**, define a session-scoped Playwright/Chromium process and per-test isolated browser context/page without any Compose dependency. Add a harness self-test that loads a data URL, then closes the context without leaked browser processes. Do not define **live_app** until Task 15.

**Step 2: Run the tests and observe RED**

Run:

~~~powershell
python -m pytest tests/api/test_hermetic_test_gate_contract.py tests/web/test_browser_harness.py tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
~~~

Expected: failure because the test manifest, marker declarations, fixture, and verification script do not yet exist.

**Step 3: Implement the minimum hermetic gate**

- Make **requirements-test.in** include the runtime manifest plus only test-only packages actually imported by the suite. Resolve it only for the pinned Ubuntu/Python 3.12 CI platform into a fully transitive, version-pinned **requirements-test.lock** with hashes and a recorded generator version; installation must use **--require-hashes**. A range-constrained requirements.txt is input, not release evidence.
- Register the four markers in **pyproject.toml**.
- In **tests/conftest.py**, add a **--run-live** option. Skip tests marked live, docker, compose, or kubernetes unless it is present; block non-loopback sockets and real docker/kubectl subprocesses for every unmarked test.
- In **verify-hermetic-tests.ps1**, verify Python and lock-generator versions, create a fresh virtual environment, install **requirements-test.lock** with **--require-hashes**, install the Chromium revision pinned by the locked Playwright package, record that revision, run collection, then run the non-live suite. Validate PowerShell syntax and LF/shell compatibility for every release script.
- Do not weaken existing application dependencies or hide collection errors.

**Step 4: Run GREEN and the clean-environment proof**

Run:

~~~powershell
python -m pytest tests/api/test_hermetic_test_gate_contract.py tests/web/test_browser_harness.py tests/web/test_workspace_frontend_contract.py tests/web/test_creao_chat_frontend_contract.py -q -p no:cacheprovider
pwsh -NoProfile -File scripts/verify-hermetic-tests.ps1
~~~

Expected: zero collection errors and zero failures under **not live and not docker and not compose and not kubernetes**.

**Step 5: Commit**

Intent line:

~~~text
Make test results reproducible outside workstation state
~~~

## Task 2: Remove runtime secrets from versioned release inputs

**Files:**
- Modify: **.gitignore**
- Modify: **.env.example**
- Modify: **infra/package/README.md**
- Modify: **tests/api/test_secrets_boundary.py**
- Create: **scripts/verify-repository-secret-history.sh**
- Remove from Git index only: **.env**

**Step 1: Write the failing boundary test**

~~~python
def test_repository_does_not_track_runtime_env():
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        capture_output=True,
        text=True,
    )
    assert tracked.returncode != 0
    assert ".env" in Path(".gitignore").read_text(encoding="utf-8").splitlines()
~~~

Add assertions that release documentation requires immediate rotation of every historical key, that **.env.example** contains names/placeholders only, and that the redacting history-scan wrapper is a required builder/CI input.

**Step 2: Run RED**

Run:

~~~powershell
python -m pytest tests/api/test_secrets_boundary.py -q -p no:cacheprovider
~~~

Expected: failure because .env is currently tracked.

**Step 3: Implement the boundary**

Run **git rm --cached -- .env** so the local file remains available, add it to **.gitignore**, retain only placeholders in **.env.example**, and document that untracking does not clean Git history. Rotate every historical key before formal release. Add a pinned, non-secret-printing history scan whose machine-readable verdict is consumed by CI/builder. A historical finding may be allowlisted only by secret fingerprint after rotation is independently recorded, with owner, reason, and date; new or unrotated findings fail. Never display matched values.

**Step 4: Run GREEN and inspect the staged scope**

Run:

~~~powershell
python -m pytest tests/api/test_secrets_boundary.py -q -p no:cacheprovider
git status --short
git diff --cached --name-status
~~~

Expected: the index records .env removal; the working copy remains locally available and ignored.

**Step 5: Commit**

Intent line:

~~~text
Keep development secrets outside versioned release inputs
~~~

## Task 3: Make sandbox path contracts platform-independent

**Files:**
- Modify: **apps/api/src/taroai/sandbox/docker.py**
- Modify: **apps/api/src/taroai/sandbox/kubernetes.py**
- Modify: **tests/api/test_sandbox_docker.py**
- Modify: **tests/api/test_sandbox_kubernetes.py**

**Step 1: Add Windows-host regression tests**

Add:

In **test_sandbox_docker.py**, lock the existing recording-runner regression by locating the mount argument after Docker's **--volume** flag and using the adapter's known workspace root; never recover a Windows host path with **split(":", 1)**. In **test_sandbox_kubernetes.py**, assert that nested display paths and command working directories remain POSIX paths even when the test host is Windows.

**Step 2: Run RED**

Run:

~~~powershell
python -m pytest tests/api/test_sandbox_docker.py tests/api/test_sandbox_kubernetes.py -q -p no:cacheprovider
~~~

Expected: at least the new Windows-path regression fails.

**Step 3: Implement the minimum fix**

Use **PurePosixPath** inside the existing private display-path logic for container/Kubernetes paths and keep host paths as **Path** objects until the Docker CLI boundary. Fix the recording test to inspect structured CLI arguments. Do not add public helpers solely to satisfy the tests.

**Step 4: Run GREEN**

Run the same test command. Expected: both sandbox suites pass on Windows and remain host-independent.

**Step 5: Commit**

Intent line:

~~~text
Keep sandbox contracts hermetic across developer platforms
~~~

## Task 4: Route strict Compose runs through the sandbox controller

**Files:**
- Modify: **apps/api/src/taroai/config.py**
- Modify: **apps/api/src/taroai/sandbox/factory.py**
- Modify: **apps/api/src/taroai/sandbox/http.py**
- Modify: **.env.example**
- Modify: **infra/docker-compose.yml**
- Modify: **tests/api/test_settings.py**
- Modify: **tests/api/test_deployment_config_profiles.py**
- Modify: **tests/api/test_sandbox_http_provider.py**
- Create: **tests/api/test_compose_sandbox_controller_contract.py**

**Step 1: Write the failing provider contract**

~~~python
def test_strict_profile_routes_api_through_docker_controller():
    settings = Settings(
        sandbox_provider="docker_controller",
        sandbox_controller_base_url="http://sandbox-controller:8002",
        sandbox_controller_api_key="dev-only",
    )
    adapter = build_sandbox_adapter(settings)
    assert isinstance(adapter, HttpSandboxAdapter)
    assert adapter.provider == "docker"


def test_production_rejects_docker_controller():
    payload = complete_valid_production_settings()
    payload.update(
        environment="production",
        deployment_mode="private",
        sandbox_provider="docker_controller",
    )
    with pytest.raises(ValueError, match="docker_controller.*local"):
        Settings(**payload)
~~~

Add a Compose text assertion requiring the API default to select the controller-backed provider, and add the production rejection to **test_deployment_config_profiles.py** so it cannot pass first for an unrelated missing production setting.

**Step 2: Run RED**

Run:

~~~powershell
python -m pytest tests/api/test_settings.py tests/api/test_sandbox_http_provider.py tests/api/test_compose_sandbox_controller_contract.py -q -p no:cacheprovider
python -m pytest tests/api/test_deployment_config_profiles.py -q -p no:cacheprovider
~~~

Expected: the new provider is not accepted or routed.

**Step 3: Implement the controller-backed local provider**

- Add **docker_controller** as an explicit local verification provider.
- Map it to **HttpSandboxAdapter(provider="docker")** so controller capabilities and session provider context match.
- Keep direct **docker** for narrow adapter tests only.
- Do not add **docker_controller** to enterprise/production sandbox providers.
- Set the strict Compose API to use **docker_controller** and the controller service to use **docker**.

**Step 4: Run GREEN and validate Compose**

~~~powershell
python -m pytest tests/api/test_settings.py tests/api/test_sandbox_http_provider.py tests/api/test_compose_sandbox_controller_contract.py -q -p no:cacheprovider
docker compose -f infra/docker-compose.yml config -q
~~~

Expected: all tests and Compose validation pass.

**Step 5: Commit**

Intent line:

~~~text
Prove Compose execution crosses the sandbox controller boundary
~~~

## Task 5: Remove host-daemon authority from strict Compose

**Files:**
- Modify: **infra/docker-compose.yml**
- Modify: **apps/api/Dockerfile.sandbox**
- Modify: **apps/api/src/taroai/config.py**
- Modify: **apps/api/src/taroai/sandbox/docker.py**
- Modify: **apps/api/src/taroai/sandbox/controller_service.py**
- Modify: **tests/api/test_compose_sandbox_controller_contract.py**
- Modify: **tests/api/test_sandbox_docker.py**
- Modify: **tests/api/test_sandbox_controller_service.py**

**Step 1: Write the failing isolation contracts**

~~~python
def test_controller_never_mounts_raw_docker_socket():
    compose = load_compose()
    mounts = compose["services"]["sandbox-controller"].get("volumes", [])
    assert all("/var/run/docker.sock" not in str(item) for item in mounts)


def test_isolated_rootless_daemon_and_controller_have_limits():
    compose = load_compose()
    assert "sandbox-daemon" in compose["services"]
    controller = compose["services"]["sandbox-controller"]
    assert controller["read_only"] is True
    assert controller["user"] != "0"
    assert controller["environment"]["DOCKER_HOST"].startswith("tcp://sandbox-daemon:")
~~~

Also assert that the rootless daemon is not published to a host port and that no controller-local path is bind-mounted into a remote daemon. Lock one supported topology: a co-located disposable rootless daemon with per-session Docker named volumes and archive/copy API transfer. Assert a dedicated internal engine network: only **sandbox-controller** and **sandbox-daemon** may join it; API/worker/browser services cannot resolve or reach the daemon API. The daemon has no host PID/network namespace, host socket, host path, or host device.

If Docker Desktop requires the outer rootless-DinD service to be privileged, the evidence must record that fact and label the local gate **functional sandbox beta with no isolation claim**. It may never satisfy the Kubernetes production gate.

**Step 2: Run RED**

Run:

~~~powershell
python -m pytest tests/api/test_compose_sandbox_controller_contract.py -q -p no:cacheprovider
~~~

Expected: failure because the raw Docker socket is mounted.

**Step 3: Implement the isolated daemon**

- Add an internal-only rootless Docker daemon service dedicated to this Compose project.
- Point the controller at its Docker API through **DOCKER_HOST**.
- Add a Docker named-volume workspace transport to **DockerSandboxAdapter**: create one volume per session, upload/download through Docker's archive/copy API, mount it only at /workspace, and delete it with the session. Do not rely on a controller host path or colon-delimited bind mount.
- Run the controller as a fixed non-root UID with read-only root, dropped capabilities, no-new-privileges, tmpfs, healthcheck, and resource limits.
- Put API/controller traffic on a separate control network and controller/daemon traffic on a private internal engine network. The daemon must have no host port, host socket, host namespace, or host device.
- Do not describe this as the production isolation boundary.

**Step 4: Run GREEN and smoke the daemon**

~~~bash
export TAROAI_SANDBOX_CONTROLLER_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
cleanup() { docker compose -f infra/docker-compose.yml down -v --remove-orphans >/dev/null 2>&1 || true; unset TAROAI_SANDBOX_CONTROLLER_API_KEY; }
trap cleanup EXIT
python -m pytest tests/api/test_compose_sandbox_controller_contract.py -q -p no:cacheprovider
docker compose -f infra/docker-compose.yml config -q
docker compose -f infra/docker-compose.yml up -d --wait --wait-timeout 180 sandbox-daemon sandbox-controller
docker compose -f infra/docker-compose.yml ps
bash scripts/verify-sandbox-lifecycle.sh --base-url http://127.0.0.1:8002 --api-key "$TAROAI_SANDBOX_CONTROLLER_API_KEY"
~~~

Expected: both services become healthy; a real named-volume create/upload/exec/download/destroy/post-destroy lifecycle passes; API/worker cannot reach the daemon endpoint; no host Docker socket/path is mounted. If the outer service is privileged, evidence explicitly carries the no-isolation-claim label.

**Step 5: Commit**

Intent line:

~~~text
Remove host-root authority from local sandbox verification
~~~

## Task 6: Make the real DeepSeek Compose gate fail closed

**Files:**
- Modify: **apps/api/src/taroai/deployment/models.py**
- Create: **apps/api/src/taroai/deployment/build_context.py**
- Create: **infra/package/build-context.schema.json**
- Create: **scripts/build-release-context.sh**
- Modify: **scripts/verify-hermetic-tests.ps1**
- Modify: **scripts/verify-compose-strict-e2e.sh**
- Modify: **apps/api/src/taroai/deployment/local_cloud_poc_verification.py**
- Modify: **apps/api/src/taroai/deployment/local_cloud_poc_demo_gate.py**
- Modify: **apps/api/src/taroai/deployment/install_validation.py**
- Create: **infra/package/strict-compose-evidence.schema.json**
- Modify: **tests/api/test_local_cloud_poc_demo_gate.py**
- Modify: **tests/api/test_local_cloud_poc_verification.py**
- Create: **tests/api/test_release_build_context.py**

**Step 1: Write failing strict-gate tests**

Add behavior tests that feed the demo gate valid evidence and then independently mutate it. Each of these must fail: forged success boolean, model response absent, Compose functional-sandbox-beta contract absent, cross-Run artifact, missing checksum, cleanup failure, post-destroy access success, install evidence mismatch, and invalid evidence signature/hash. The local contract requires controller authentication, tenant/Run scope, resource limits, lifecycle cleanup, no raw host socket, and an explicit **no_isolation_claim**. Add a failure-path test proving the script atomically writes schema-valid redacted failure evidence before returning nonzero.

Add a shared **BuildContext** contract containing mode, git SHA, optional immutable source tag, profile, build ID, and optional CI run ID. Readiness mode permits an explicit local build ID; release mode requires source tag and CI run ID. Every gate CLI accepts one **--build-context** JSON path (or the same tested standard environment variables), embeds its hash, and refuses inconsistent context.

Extend the hermetic verifier with **-BuildContext** and **-Output** so its signed/checksummed test evidence participates in the same binding.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_local_cloud_poc_demo_gate.py tests/api/test_local_cloud_poc_verification.py tests/api/test_release_build_context.py -q -p no:cacheprovider
~~~

Expected: strict mode still permits incomplete evidence.

**Step 3: Implement fail-closed verification**

- Require real PostgreSQL, Redis, MinIO, browser controller, sandbox controller, and non-empty model response evidence.
- Define this evidence level as **compose_functional_sandbox_beta**. It proves governed routing and lifecycle behavior but explicitly does not satisfy runtime/network/filesystem production isolation. Only Task 22 may produce **kubernetes_production_isolation** evidence.
- Make DeepSeek the first configured strict provider while preserving the generic OpenAI-compatible provider contract.
- Define one canonical application Run: the browser UI creates it and exposes its run_id; API/runtime/sandbox/artifact verifiers consume that ID rather than creating a second Run. Require that ID across planning, tool/sandbox execution, file creation, artifact promotion/download, cleanup, and post-destroy denial.
- Execute one canonical artifact Run here; the versioned 50-case suite is not available until Task 13. Its real Compose results will later be joined to this canonical evidence under the same BuildContext.
- Redact prompt/key/header values before atomically writing checksummed JSON evidence on both success and failure.
- Remove warning-only fallback from strict mode.
- Generate BuildContext once and embed its hash in Compose evidence; never regenerate context independently inside a downstream gate.

**Step 4: Run tests, then the real gate**

~~~bash
python -m pytest tests/api/test_local_cloud_poc_demo_gate.py tests/api/test_local_cloud_poc_verification.py tests/api/test_release_build_context.py -q -p no:cacheprovider
docker compose -f infra/docker-compose.yml config -q
bash scripts/build-release-context.sh --mode readiness --git-sha "$(git rev-parse HEAD)" --profile internal-alpha --build-id "local-$(date -u +%Y%m%dT%H%M%SZ)" --output dist/build-context.json
pwsh -NoProfile -File scripts/verify-hermetic-tests.ps1 -BuildContext dist/build-context.json -Output dist/hermetic/evidence.json
bash scripts/verify-compose-strict-e2e.sh --build-context dist/build-context.json --output dist/compose/canonical-evidence.json
~~~

Expected: tests pass and the live command exits zero only with real same-run evidence. If provider/network failure occurs, retain redacted evidence and keep the gate failed.

**Step 5: Commit**

Intent line:

~~~text
Make strict E2E fail closed on sandbox governance
~~~

## Task 7: Add durable Conversation and Turn domain records

**Files:**
- Create: **apps/api/migrations/033_conversations.sql**
- Modify: **apps/api/src/taroai/domain.py**
- Modify: **tests/api/test_migration_contract.py**
- Create: **tests/api/test_conversation_domain.py**

**Step 1: Write failing model and migration tests**

~~~python
def test_turn_sequence_and_status_are_explicit():
    turn = Turn(
        id="turn-1",
        tenant_id="tenant-1",
        conversation_id="conversation-1",
        sequence=1,
        role=TurnRole.USER,
        status=TurnStatus.ACCEPTED,
        content="Create a report",
    )
    assert turn.sequence == 1


def test_conversation_migration_is_append_only_and_tenant_scoped():
    assert previous_migration_number("033_conversations.sql") == 32
    sql = migration_text("033_conversations.sql")
    assert "conversations" in sql
    assert "turns" in sql
    assert "run_dispatch_outbox" in sql
    assert "tenant_id" in sql
    assert "UNIQUE" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
~~~

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_conversation_domain.py tests/api/test_migration_contract.py -q -p no:cacheprovider
~~~

Expected: models and migration are absent.

**Step 3: Implement the minimal schema**

- Before editing, re-check that 032 is still the highest migration; if another branch has advanced it, renumber 033-035 together before implementation.
- Add Conversation, ConversationCreate, Turn, TurnCreate, TurnRole, and TurnStatus models.
- Persist workspace, title, timestamps, role, content, attachment references, idempotency key, and strictly increasing sequence.
- Add nullable conversation_id, input_turn_id, and requested_model references to Runs without editing migration 001. Add source_run_id to assistant Turns.
- Add a transactional run_dispatch_outbox table with run_id unique, payload hash, attempt/lease state, and timestamps. Use tenant-inclusive composite unique keys/foreign keys and indexes. Enable and force PostgreSQL RLS for all new tenant-owned tables using the existing tenant-policy pattern.
- Test a SQLite upgrade and the real PostgreSQL migration path.

**Step 4: Run GREEN**

Run the same tests. Expected: domain and migration contracts pass.

**Step 5: Commit**

Intent line:

~~~text
Preserve multi-turn work as durable tenant-scoped conversations
~~~

## Task 8: Expose tenant-safe Conversation and Turn repositories and APIs

**Files:**
- Modify: **apps/api/src/taroai/store.py**
- Modify: **apps/api/src/taroai/db/repository.py**
- Modify: **apps/api/src/taroai/app.py**
- Create: **tests/api/test_conversation_api.py**
- Modify: **tests/api/test_db_repository.py**

**Step 1: Write failing API/repository tests**

Add:

~~~python
def test_turns_persist_in_strict_sequence(client):
    conversation = create_conversation(client)
    first = append_turn(client, conversation["id"], "one")
    second = append_turn(client, conversation["id"], "two")
    assert [first["sequence"], second["sequence"]] == [1, 2]


def test_duplicate_turn_idempotency_key_returns_same_turn(client):
    first = append_turn(client, key="same-key")
    second = append_turn(client, key="same-key")
    assert second["id"] == first["id"]


def test_cross_tenant_conversation_is_denied(client):
    assert get_as_other_tenant(client).status_code in {403, 404}


def test_ten_concurrent_appends_receive_unique_contiguous_sequences(sql_repository):
    turns = append_concurrently(sql_repository, count=10)
    assert sorted(turn.sequence for turn in turns) == list(range(1, 11))
~~~

Include a SQL repository restart test proving records survive process-local store recreation.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_conversation_api.py tests/api/test_db_repository.py -q -p no:cacheprovider
~~~

Expected: repository methods and endpoints do not exist.

**Step 3: Implement the minimal APIs**

Implement create/list/get Conversation and append/list Turn operations in both stores and expose:

- POST /api/conversations
- GET /api/conversations
- GET /api/conversations/{conversation_id}
- POST /api/conversations/{conversation_id}/turns
- GET /api/conversations/{conversation_id}/turns

Use the existing tenant/user authorization and idempotency patterns. The public append endpoint accepts USER Turns only; assistant Turns are internal runtime writes. In PostgreSQL, lock the Conversation row before allocating the next sequence and retain a tenant/conversation/sequence unique constraint as the race backstop. Use an equivalent per-Conversation lock in the memory store.

**Step 4: Run GREEN**

Run the same tests. Expected: memory and SQL behavior match.

**Step 5: Commit**

Intent line:

~~~text
Expose durable tenant-scoped conversations through one API
~~~

## Task 9: Bind Runs, selected models, and attachments to Turns

**Files:**
- Modify: **apps/api/src/taroai/domain.py**
- Modify: **apps/api/src/taroai/store.py**
- Modify: **apps/api/src/taroai/db/repository.py**
- Modify: **apps/api/src/taroai/app.py**
- Modify: **apps/api/src/taroai/agent/runtime.py**
- Modify: **apps/api/src/taroai/workers/runner.py**
- Create: **apps/api/src/taroai/workers/run_dispatch_outbox.py**
- Modify: **apps/api/src/taroai/workers/agent_worker.py**
- Modify: **tests/api/test_conversation_api.py**
- Modify: **tests/api/test_agent_runtime_context.py**
- Create: **tests/api/test_run_dispatch_outbox.py**

**Step 1: Write failing linkage tests**

~~~python
def test_submitting_user_turn_creates_linked_run(client):
    result = submit_turn(
        client,
        selected_model="deepseek-chat",
        attachments=["artifact-1"],
    )
    run = get_run(client, result["run_id"])
    assert run["conversation_id"] == result["conversation_id"]
    assert run["input_turn_id"] == result["turn_id"]
    assert run["requested_model"] == "deepseek-chat"


def test_runtime_receives_selected_model_and_attachment_refs():
    context = captured_runtime_context()
    assert context.requested_model == "deepseek-chat"
    assert context.attachments == ["artifact-1"]


def test_duplicate_submission_creates_one_turn_one_run_and_one_queue_job(client):
    first = submit_turn(client, idempotency_key="turn-submit-1")
    second = submit_turn(client, idempotency_key="turn-submit-1")
    assert second == first
    assert queued_job_count(first["run_id"]) == 1


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "timed_out"])
def test_terminal_run_creates_exactly_one_assistant_turn(client, status):
    run = terminate_run_twice_for_reconciliation(client, status=status)
    turns = assistant_turns_for_source_run(client, run["id"])
    assert len(turns) == 1
~~~

Add crash-window tests:

- transaction commits Turn+Run+outbox, then the API crashes before Redis enqueue; dispatcher later publishes exactly one logical job
- dispatcher publishes, then crashes before acknowledging the outbox row; a replay republishes, but worker idempotency executes the Run once

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_conversation_api.py tests/api/test_agent_runtime_context.py tests/api/test_run_dispatch_outbox.py -q -p no:cacheprovider
~~~

Expected: Run does not carry the linkage.

**Step 3: Implement the linkage**

In one SQL transaction, persist the user Turn, Run, and unique dispatch-outbox row. A leased dispatcher publishes to Redis at least once and marks the outbox row delivered; the worker deduplicates by run/job ID before execution. Use **runs.input_turn_id** for the user Turn and **turns.source_run_id** for the runtime-authored assistant Turn; enforce one assistant Turn per tenant/source Run/assistant role with a unique constraint. Copy selected model and validated attachment references into immutable Run context. On succeeded, failed, cancelled, and timed_out terminal states, append/finalize exactly one assistant Turn. Cancellation/timeout use role assistant, explicit cancelled/timed_out status, and safe user-facing text without raw exception or secret content. HTTP idempotency returns the same Turn/Run, while the outbox closes the database-to-Redis crash window. Do not infer selected model from browser local storage at the backend.

**Step 4: Run GREEN**

Run the same tests. Expected: one user Turn, one linked Run, and one terminal assistant Turn.

**Step 5: Commit**

Intent line:

~~~text
Bind every execution to the visible turn and selected model
~~~

## Task 10: Define the immutable 50-case evaluation dataset

**Files:**
- Create: **apps/api/src/taroai/evaluations/__init__.py**
- Create: **apps/api/src/taroai/evaluations/models.py**
- Create: **apps/api/src/taroai/evaluations/dataset.py**
- Create: **infra/evals/schema/evaluation-case.schema.json**
- Create: **infra/evals/agent-quality/v1/manifest.json**
- Create: **infra/evals/agent-quality/v1/cases.jsonl**
- Create: **tests/api/test_evaluation_contract.py**

**Step 1: Write the failing dataset contract**

~~~python
def test_v1_dataset_contains_exactly_50_unique_valid_cases():
    dataset = load_dataset("infra/evals/agent-quality/v1")
    assert len(dataset.cases) == 50
    assert len({case.id for case in dataset.cases}) == 50
    assert category_counts(dataset) == {
        "file_sandbox": 10,
        "browser": 10,
        "multi_tool": 10,
        "approval_policy": 10,
        "failure_recovery": 10,
    }
    assert sum(case.critical for case in dataset.cases) >= 13
    safety_cases = [case for case in dataset.cases if "safety" in case.scorers]
    approval_cases = [case for case in dataset.cases if case.expected.requires_approval]
    assert safety_cases
    assert approval_cases
    assert all(case.critical for case in safety_cases + approval_cases)
    assert dataset.manifest.sha256 == dataset.content_sha256()
~~~

Validate every JSONL record against the checked-in schema and require bounded step, retry, latency, and cost budgets. The schema must distinguish **expected_retry** and **expected_human_intervention** so designed recovery/approval behavior is not counted as unexpected failure.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_evaluation_contract.py -q -p no:cacheprovider
~~~

Expected: evaluation package and dataset are absent.

**Step 3: Implement the data contract**

Add EvaluationCase, DatasetManifest, AgentSnapshot, CaseBudget, ExpectedOutcome, and scorer-reference models. **ExpectedOutcome.requires_approval** and the registered safety scorer, not optional tags, determine which cases must be critical. Populate 10 approved synthetic cases in each category. Use tags inside those categories only for reporting on structured output, artifacts, citation/RAG, safety/privacy, retries, and human approval. Do not include customer data or secrets.

**Step 4: Run GREEN**

Run the same test. Expected: exactly 50 schema-valid, hash-locked cases.

**Step 5: Commit**

Intent line:

~~~text
Make agent quality claims reproducible against immutable data
~~~

## Task 11: Implement deterministic, explainable scorers

**Files:**
- Create: **apps/api/src/taroai/evaluations/scorers.py**
- Create: **tests/api/test_evaluation_scorers.py**

**Step 1: Write failing scorer tests**

Cover exact, contains, JSON structure/schema, citation, tool selection, approval, artifact, and safety scoring:

~~~python
def test_unknown_scorer_fails_closed():
    with pytest.raises(UnknownScorerError):
        score("not-registered", actual={}, expected={})


def test_score_includes_machine_value_and_human_reason():
    result = score("tool_selection", actual={"tools": ["search"]}, expected={"tools": ["search"]})
    assert result.passed is True
    assert result.value == 1.0
    assert result.reason
~~~

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_evaluation_scorers.py -q -p no:cacheprovider
~~~

Expected: scorer registry is absent.

**Step 3: Implement the minimum scorer registry**

Define a Scorer protocol and immutable Score result. Keep every blocking v1 scorer deterministic and reviewable. An optional LLM judge may emit advisory metadata later, but cannot determine the release verdict.

**Step 4: Run GREEN**

Run the same test. Expected: every registered scorer passes positive/negative fixtures and unknown scorers fail closed.

**Step 5: Commit**

Intent line:

~~~text
Keep release scoring deterministic and reviewable
~~~

## Task 12: Run all cases and aggregate reliability, cost, and latency

**Files:**
- Create: **apps/api/src/taroai/evaluations/runner.py**
- Create: **apps/api/src/taroai/evaluations/metrics.py**
- Create: **tests/api/test_evaluation_runner.py**
- Create: **tests/api/test_evaluation_metrics.py**

**Step 1: Write failing runner and metric tests**

~~~python
def test_runner_executes_all_50_cases_without_aborting_on_one_failure():
    result = EvaluationRunner(executor=RecordingExecutor(fail_case="browser-03")).run(dataset)
    assert len(result.case_results) == 50
    assert result.case_results_by_id["browser-03"].passed is False


def test_metrics_match_known_50_case_fixture():
    metrics = aggregate_metrics(known_results())
    assert metrics.task_success_rate == pytest.approx(0.90)
    assert metrics.critical_pass_rate == 1.0
    assert metrics.p95_latency_ms == expected_p95()
~~~

Require per-case output summary, tool calls/failures, retries, cost, latency, human intervention, scorer results, and terminal trace checksum.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_evaluation_runner.py tests/api/test_evaluation_metrics.py -q -p no:cacheprovider
~~~

Expected: runner and aggregator are absent.

**Step 3: Implement deterministic execution**

Inject a CaseExecutor instead of constructing the runtime internally. Execute cases in manifest order with fixed timeouts and budgets. Capture one case failure as data, but fail the suite only after every case result is persisted. Aggregate overall, category, agent-version, cost, p50/p95 latency, tool failure, retry, intervention, and unknown-failure metrics. Define tool failure as failed tool invocations divided by all tool invocations; define retry/intervention rates as cases with **unexpected** retry/intervention divided by eligible cases, excluding cases whose schema explicitly expects that behavior. Lock each numerator and denominator with fixtures.

**Step 4: Run GREEN**

Run the same tests. Expected: stable result ordering and exact fixture metrics.

**Step 5: Commit**

Intent line:

~~~text
Turn representative agent runs into repeatable release evidence
~~~

## Task 13: Persist evaluation results and enforce the regression gate

**Files:**
- Create: **apps/api/migrations/034_agent_evaluation_results.sql**
- Create: **apps/api/src/taroai/evaluations/repository.py**
- Create: **apps/api/src/taroai/evaluations/service.py**
- Create: **apps/api/src/taroai/evaluations/gate.py**
- Create: **apps/api/src/taroai/evaluations/evidence.py**
- Create: **apps/api/src/taroai/evaluations/cli.py**
- Create: **infra/evals/agent-quality/gate.json**
- Create: **infra/evals/agent-quality/baselines/v1.json**
- Create: **scripts/run-agent-eval-gate.sh**
- Create: **scripts/verify-internal-alpha-runtime.sh**
- Modify: **scripts/verify-compose-strict-e2e.sh**
- Modify: **apps/api/src/taroai/deployment/local_cloud_poc_demo_gate.py**
- Modify: **apps/api/src/taroai/app.py**
- Modify: **tests/api/test_migration_contract.py**
- Create: **tests/api/test_evaluation_gate.py**
- Create: **tests/api/test_evaluation_api.py**
- Create: **tests/api/test_evaluation_evidence.py**
- Create: **tests/api/test_internal_alpha_runtime_orchestrator_contract.py**

**Step 1: Write failing gate/API tests**

Require:

~~~python
def test_gate_thresholds_fail_closed():
    assert verdict(critical_pass_rate=0.99).passed is False
    assert verdict(task_success_rate=0.89).passed is False
    assert verdict(tool_failure_rate=0.011).passed is False
    assert verdict(retry_rate=0.051).passed is False
    assert verdict(human_intervention_rate=0.051).passed is False
    assert verdict(cost_regression=0.101).passed is False
    assert verdict(p95_latency_regression=0.101).passed is False


def test_baseline_or_dataset_hash_mismatch_blocks_release():
    assert evaluate_gate(result_with_wrong_hash()).passed is False
~~~

Add admin-permission tests for POST /api/evaluations/run and GET /api/evaluations/{id}. Evidence tests must reject raw keys, authorization headers, and unredacted prompt secrets.

Add migration assertions that 034 follows 033, is tenant-scoped, stores dataset/result hashes, enforces immutable result identity with a unique constraint, and defines the required lookup indexes/foreign keys.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_evaluation_gate.py tests/api/test_evaluation_api.py tests/api/test_evaluation_evidence.py tests/api/test_migration_contract.py tests/api/test_internal_alpha_runtime_orchestrator_contract.py -q -p no:cacheprovider
~~~

Expected: persistence, API, and gate do not exist.

**Step 3: Implement persistence and thresholds**

Persist dataset/version/hash, agent/model/prompt/tool/policy snapshot, the shared BuildContext plus hash, runner/scorer version, per-case checksums, metrics, verdict, and result hash. Enforce:

- critical cases: 100 percent
- task success: at least 90 percent and no more than 2 percentage-point regression
- tool failure: at most 1 percent
- unexpected retry and human intervention: at most 5 percent
- cost and p95 latency regression: at most 10 percent

Make gate and evidence writes immutable and tenant/admin scoped. The CLI must run the selected versioned dataset through an injected **compose** CaseExecutor backed by the real model, PostgreSQL, Redis, MinIO, browser, and sandbox services; atomically write hash-locked evidence; verify it after writing; and return nonzero when the gate verdict fails. Extend the strict Compose verifier to join this 50-case evidence with Task 6's canonical artifact evidence under the same BuildContext without mutating either source packet.

Add one runtime orchestrator that owns the entire service lifecycle. It creates a unique Compose project and temporary controller key, exports the key without printing it, runs **up --wait**, canonical verification in reuse/no-teardown mode, the 50-case evaluator, and verify-only aggregation, then always captures redacted diagnostics and runs **down -v --remove-orphans** in a trap/finally block. Test startup failure, mid-suite failure, healthy reuse, fixed output paths, and cleanup.

**Step 4: Run GREEN**

Run the same tests, then:

~~~bash
bash scripts/verify-internal-alpha-runtime.sh --build-context dist/build-context.json --dataset infra/evals/agent-quality/v1 --output-dir dist
~~~

Expected: pass/fail fixtures, migration, authorization, redaction, CLI exit code, and evidence self-verification all pass.

**Step 5: Commit**

Intent line:

~~~text
Prevent measurable agent regressions from shipping
~~~

## Task 14: Replace snapshot SSE with an ordered resumable stream

**Files:**
- Create: **apps/api/src/taroai/run_events.py**
- Create: **infra/events/run-event.v1.schema.json**
- Modify: **apps/api/src/taroai/agent/runtime.py**
- Modify: **apps/api/src/taroai/store.py**
- Modify: **apps/api/src/taroai/db/repository.py**
- Modify: **apps/api/src/taroai/app.py**
- Create: **tests/api/test_run_event_stream.py**
- Create: **tests/api/test_run_event_stream_live.py**

**Step 1: Write failing stream semantics**

~~~python
def test_stream_waits_for_new_events():
    stream = open_stream(run_id)
    publish_event(run_id, sequence=1)
    assert next_event(stream).sequence == 1


def test_last_event_id_replays_only_the_gap():
    publish_sequences(run_id, [1, 2, 3])
    assert stream_sequences(run_id, last_event_id="1") == [2, 3]


def test_terminal_event_closes_stream():
    for event_type in ("run.succeeded", "run.failed", "run.cancelled", "run.timed_out"):
        assert stream_closes_after(new_run(), event_type=event_type)
~~~

Also test strictly increasing IDs, heartbeat without cursor advance, cross-tenant denial, duplicate event suppression, **Last-Event-ID** precedence over **after_sequence**, and recovery across API restart. Use two independent repository/app instances so the test proves worker/API cross-process notification rather than an in-process condition.

Add a **live + compose** test that runs API, worker, PostgreSQL, and Redis as separate processes, disconnects/restarts the API while the worker publishes, then proves replay plus notification yields each sequence exactly once. Set a deliberately long database reconciliation interval and assert the event arrives before it, proving Redis notification—not polling—woke the API.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_run_event_stream.py -q -p no:cacheprovider
python -m pytest tests/api/test_run_event_stream_live.py --run-live -m "live and compose" -q -p no:cacheprovider
~~~

Expected: current endpoint emits a finite snapshot and closes early.

**Step 3: Implement the stream service**

Define a versioned **run-event.v1** JSON schema for turn delta/completion, planning, tool, sandbox, browser, artifact, approval, cancellation, error, and terminal events. Validate/normalize producers at the append/persist boundary, including existing runtime event names; document a compatibility mapping and preserve old readers during the migration. Inject the Redis notification publisher into the worker composition root in **workers/runner.py** as well as the API-side store; PostgreSQL remains the replay source, with a bounded database reconciliation interval when a notification is lost. Parse **Last-Event-ID** first and use **after_sequence** only when the header is absent. Replay persisted gaps, emit heartbeat comments, and close on every terminal RunStatus or configured idle/error timeout. Do not hold a database transaction open while waiting. Scope every cursor and notification by tenant plus Run.

**Step 4: Run GREEN**

Run the same test. Expected: reconnect has no missing/duplicate event and terminal events close cleanly.

**Step 5: Commit**

Intent line:

~~~text
Make run progress ordered and recoverable across interruptions
~~~

## Task 15: Restore and deepen persistent conversations in the CREAO shell

**Files:**
- Modify: **apps/web/index.html**
- Modify: **apps/web/assets/main.js**
- Modify: **tests/web/conftest.py**
- Modify: **tests/web/test_browser_harness.py**
- Create: **tests/web/test_conversation_frontend.py**
- Reconcile: **tests/web/test_creao_chat_frontend_contract.py**

**Step 1: TDD the real live-app harness**

Add a failing **live + compose** harness test that requests **live_app**, verifies API/Web health, creates a unique tenant/workspace fixture, and confirms session teardown removes its Compose project/resources. Run:

~~~powershell
python -m pytest tests/web/test_browser_harness.py --run-live -m "live and compose" -q -p no:cacheprovider
~~~

Expected: RED because **live_app** is not defined. Implement it in **tests/web/conftest.py** with session startup, bounded health waits, per-test data isolation, diagnostics on failure, and guaranteed teardown; then rerun to GREEN.

**Step 2: Reconcile the preserved CREAO patch**

Review the saved Task 0 patch against current files. Keep approved CREAO typography, palette, navigation, and useful interaction work; discard duplicate Run-shaped mock state and status-template assistant replies.

**Step 3: Write failing browser-backed tests**

Create these as **live + compose** tests:

~~~python
def test_reload_restores_selected_thread(page, live_app):
    conversation_id = create_two_turn_conversation(page)
    page.reload()
    expect(page).to_have_url(re.compile(conversation_id))
    expect(page.locator("[data-turn]")).to_have_count(4)


def test_second_prompt_reuses_conversation_id(page, live_app):
    ids = submit_two_prompts(page)
    assert ids.first_conversation == ids.second_conversation
~~~

Also test direct deep links and that selected model plus attachment IDs are present in the real API request.

**Step 4: Run RED**

~~~powershell
python -m pytest tests/web/test_conversation_frontend.py --run-live -m "live and compose" -q -p no:cacheprovider
~~~

Expected: history is Run-shaped and refresh does not restore durable Turns.

**Step 5: Implement the conversation client**

Use the page/context fixtures from Task 1 and the now-tested live_app fixture; do not assume pytest-playwright is installed. Record locked Playwright/Chromium versions and fail if they drift. Persist the selected Conversation ID in the URL, load Conversation/Turns from the API on boot, list history by Conversation, reuse the ID for later prompts, and render terminal assistant content from the persisted assistant Turn. Keep state changes idempotent on reload.

**Step 6: Run GREEN**

Run the same test. Expected: deep link, refresh, multi-turn continuation, model, and attachment flow pass.

**Step 7: Commit**

Intent line:

~~~text
Let users resume the same agent context after reload
~~~

## Task 16: Consume the SSE stream incrementally with recovery

**Files:**
- Create: **apps/web/assets/sse.js**
- Modify: **apps/web/assets/main.js**
- Create: **tests/web/test_sse_parser.py**
- Create: **tests/web/test_streaming_frontend.py**

**Step 1: Write failing streaming tests**

~~~python
def test_incremental_events_render_before_completion(page, live_app):
    submit_slow_run(page)
    expect(page.locator("[data-event-sequence='1']")).to_be_visible()
    expect(page.locator("[data-run-terminal]")).not_to_be_visible()


def test_reconnect_has_no_duplicate_rows(page, live_app):
    interrupt_stream_once(page)
    expect(unique_event_sequences(page)).to_equal(rendered_event_sequences(page))


def test_cancel_aborts_active_stream(page, live_app):
    cancel_run(page)
    expect(page.locator("[data-stream-state]")).to_have_attribute("data-stream-state", "closed")
~~~

In the parser suite, load **sse.js** in a local Playwright data-page without API/Compose and feed arbitrary byte chunks covering CRLF, multiple data lines, heartbeat comments, two events in one chunk, and UTF-8 code points split across chunks. Add live browser tests proving new chat, logout, Conversation switch, Run switch, cancellation, and terminal state abort the previous stream. Reject a cursor from another tenant/Run.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/web/test_sse_parser.py -q -p no:cacheprovider
python -m pytest tests/web/test_streaming_frontend.py --run-live -m "live and compose" -q -p no:cacheprovider
~~~

Expected: current code waits for a full response and/or relies on 1.5 second polling.

**Step 3: Implement incremental fetch streaming**

Implement a small independently testable SSE parser in **sse.js** using a streaming **TextDecoder**. Use bearer-compatible **fetch** plus **ReadableStream**, **AbortController**, persisted tenant/Run-scoped last event sequence, exponential backoff with jitter, and sequence-based de-duplication. Update all panes from the versioned event contract. Remove polling as the primary progress path; retain only a bounded terminal-state reconciliation fallback.

**Step 4: Run GREEN**

Run the same test. Expected: progress appears before completion and one forced reconnect produces no gap or duplicate.

**Step 5: Commit**

Intent line:

~~~text
Show live agent progress without gaps or duplicate evidence
~~~

## Task 17: Implement the CREAO-styled Manus three-pane workbench

**Files:**
- Modify: **apps/web/index.html**
- Modify: **apps/web/assets/styles.css**
- Modify: **apps/web/assets/main.js**
- Create: **tests/web/test_three_pane_e2e.py**

**Step 1: Write failing responsive interaction tests**

Require:

~~~python
def test_desktop_shows_all_three_panes(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    for pane in ("conversation", "timeline", "workbench"):
        expect(page.locator(f"[data-pane='{pane}']")).to_be_visible()


def test_keyboard_resizes_splitters(page):
    splitter = page.locator("[role='separator']").first
    before = splitter.get_attribute("aria-valuenow")
    splitter.press("ArrowRight")
    assert splitter.get_attribute("aria-valuenow") != before


def test_mobile_can_reach_every_pane(page):
    page.set_viewport_size({"width": 320, "height": 720})
    tabs = page.get_by_role("tab")
    expect(tabs).to_have_count(3)
    for index in range(3):
        tabs.nth(index).click()
        expect(page.locator("[role='tabpanel']:visible")).to_have_count(1)
~~~

Also assert each tab owns its tabpanel, arrow keys move focus/selection, closed drawers are inert and cannot receive focus, each desktop pane has an independent scroll container, and no viewport has horizontal document overflow.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/web/test_three_pane_e2e.py --run-live -m "live and compose" -q -p no:cacheprovider
~~~

Expected: the workbench is an overlay and not a persistent accessible pane.

**Step 3: Implement the layout**

- At desktop widths, show Conversation, Agent Timeline, and Browser/Terminal/Artifact panes simultaneously with independent scroll regions.
- Use ARIA separators with keyboard resizing and stored user sizes.
- On narrow screens, expose all panes through accessible tabs/drawer; never hide execution evidence permanently.
- Preserve the approved CREAO visual baseline and existing data hooks needed by backend/runtime tests.

**Step 4: Run GREEN**

Run the same tests at 320, 720, 1280, and 1440 widths.

**Step 5: Commit**

Intent line:

~~~text
Keep conversation reasoning and execution evidence visible together
~~~

## Task 18: Make approvals durable, contextual, and idempotent

**Files:**
- Create: **apps/api/migrations/035_approval_context.sql**
- Modify: **apps/api/src/taroai/domain.py**
- Modify: **apps/api/src/taroai/store.py**
- Modify: **apps/api/src/taroai/db/repository.py**
- Modify: **apps/api/src/taroai/agent/runtime.py**
- Modify: **apps/api/src/taroai/app.py**
- Modify: **apps/web/index.html**
- Modify: **apps/web/assets/main.js**
- Modify: **apps/web/assets/styles.css**
- Create: **tests/api/test_approval_context.py**
- Modify: **tests/api/test_db_repository.py**
- Create: **tests/web/test_approval_e2e.py**

**Step 1: Write failing backend and browser tests**

Require persisted tool, redacted arguments/diff, scope, risk, estimated cost, expiry, idempotency key, decision actor, and timestamp.

~~~python
def test_duplicate_approval_decision_resolves_once(client):
    first = decide(client, key="decision-1", decision="approve")
    second = decide(client, key="decision-1", decision="approve")
    assert second["id"] == first["id"]


def test_high_risk_action_requires_confirmation(page, live_app):
    open_high_risk_approval(page)
    page.get_by_role("button", name="Approve").click()
    expect(page.get_by_role("dialog", name="Confirm high-risk action")).to_be_visible()
~~~

Also test expired approval denial and a failed resolution that can be safely retried with the same key.

Add SQL restart and concurrency tests. Two simultaneous approve/reject requests must produce one terminal decision and one conflict/replay response. A high-risk approval request without a valid server-issued confirmation token bound to approval ID, context hash, actor, and expiry must be rejected even if the browser is bypassed.

Lock the HTTP protocol:

- POST /api/approvals/{approval_id}/confirmation-challenges returns a short-lived one-time token only after actor authorization and exact context-hash confirmation
- the server stores only token hash/nonce, actor, context hash, issued/expiry/consumed timestamps
- POST /api/approvals/{approval_id}/decision carries the token and idempotency key; the decision CAS atomically consumes the nonce

Test token replay, cross-actor use, context mutation after issuance, expiry, refresh after issuance, and two concurrent decision requests.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_approval_context.py -q -p no:cacheprovider
python -m pytest tests/web/test_approval_e2e.py --run-live -m "live and compose" -q -p no:cacheprovider
~~~

Expected: context and idempotent resolution are incomplete.

**Step 3: Implement the approval boundary**

Persist the context through both memory and SQL repositories before suspending a Run. Redact secrets server-side. Implement the confirmation-challenge endpoint and store only a keyed token hash/nonce; derive/sign tokens from a dedicated secret-manager key, never the browser. Bind approval ID, immutable context hash, actor, and expiry; atomically consume the nonce in the same decision CAS so UI bypass/replay cannot approve. Use one idempotency key per decision and one atomic transition shared by approve, reject, and expiry. Show busy, success, expiry, conflict, error, and retry states in the timeline/workbench.

**Step 4: Run GREEN**

Run the same tests. Expected: double click/refresh cannot execute twice, and failed network resolution can reconcile safely.

**Step 5: Commit**

Intent line:

~~~text
Make consequential actions understandable and safely decidable
~~~

## Task 19: Add a release-grade real-browser quality gate

**Files:**
- Create: **tests/web/test_manus_workspace_e2e.py**
- Create: **tests/web/fixtures/visual-workspace-run-event-v1.json**
- Create: **tests/web/snapshots/.gitkeep**
- Create after design approval: **tests/web/visual-baselines/workspace-320.png**
- Create after design approval: **tests/web/visual-baselines/workspace-720.png**
- Create after design approval: **tests/web/visual-baselines/workspace-1280.png**
- Create after design approval: **tests/web/visual-baselines/workspace-1440.png**
- Create: **tests/web/visual-baselines/manifest.json**
- Create: **infra/testing/web-release.lock.json**
- Create after explicit dependency approval: **apps/web/vendor/axe/axe.min.js**
- Create after explicit dependency approval: **apps/web/vendor/axe/LICENSE**
- Create: **scripts/verify-web-release.ps1**
- Create: **scripts/update-web-visual-baselines.ps1**
- Modify: **apps/api/src/taroai/deployment/local_cloud_poc_verification.py**
- Create: **tests/api/test_web_release_gate_contract.py**

**Step 1: Write the failing gate contract**

**External dependency gate:** Task 19 cannot reach GREEN until the user explicitly approves vendoring a vetted axe-core release. Record the approved version, license, and SHA-256 in **web-release.lock.json** before implementation.

Assert that the script starts the real API/Web stack and covers:

- create/resume Conversation
- incremental streaming and reconnect
- sandbox/tool action
- approval and one idempotent decision
- artifact preview/download
- cancellation feedback
- 320, 720, 1280, and 1440 screenshots
- keyboard-only flow
- automated WCAG 2.2 AA audit
- zero unexpected browser console/network errors
- approved pixel baselines with at most 0.5 percent differing pixels
- CREAO visual-verdict score at least 90 as a secondary design review

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_web_release_gate_contract.py -q -p no:cacheprovider
~~~

Expected: the release browser script and gate are absent.

**Step 3: Implement the gate using existing Playwright**

Run the gate in the Playwright Python browser image already used by the project, but pin it by immutable image digest in **web-release.lock.json** together with OS, Playwright/Chromium revision, viewport, DPR 1, zh-CN locale, Asia/Shanghai timezone, light color scheme, reduced-motion setting, and font inventory hash. After explicit approval, lock the vetted axe-core version, local file hash, and license.

Separate the real DeepSeek functional journey from deterministic screenshots. Visual baselines replay the checked-in redacted run-event.v1 fixture under a fixed clock/seed and stable fixture content; the manifest records masked selectors for unavoidable IDs/timestamps and fails if an unapproved dynamic selector appears. Commit baseline images plus a manifest containing environment/fixture/mask hashes, image SHA-256, approver, and approval timestamp. The update script must run only in the locked container and require an explicit approver argument. Fail above 0.5 percent pixel difference; treat visual-verdict score 90 as a secondary composition/style check. Collect at least 5 warm-up runs plus 20 measured real-journey runs for timing p95. Save screenshots, accessibility JSON, sample timings, console/network logs, pixel-diff results, visual-verdict JSON, and the exact BuildContext hash under one redacted evidence directory; mismatched context fails closed.

**Step 4: Run the full browser gate**

~~~powershell
pwsh -NoProfile -File scripts/verify-web-release.ps1 -BuildContext dist/build-context.json -Output dist/web/evidence.json
~~~

Expected: desktop/mobile end-to-end tests pass, cancel feedback is within 500 ms, first execution event p95 is at most 1.5 seconds under the local profile, no critical/serious accessibility findings remain, and visual verdict is at least 90.

**Step 5: Commit**

Intent line:

~~~text
Block releases that only look complete
~~~

## Task 20: Build signed OCI, Helm, and air-gap release artifacts

**Files:**
- Modify: **apps/api/src/taroai/deployment/release_package.py**
- Modify: **apps/api/src/taroai/deployment/package_manifest.py**
- Modify: **apps/api/src/taroai/deployment/transfer_evidence.py**
- Modify: **apps/api/Dockerfile**
- Modify: **apps/api/Dockerfile.browser**
- Modify: **apps/api/Dockerfile.sandbox**
- Modify: **apps/web/Dockerfile**
- Modify: **infra/helm/taroai/Chart.yaml**
- Modify: **infra/helm/taroai/values.yaml**
- Create: **infra/package/release-policy.json**
- Create: **infra/package/toolchain.lock.json**
- Create: **scripts/build-formal-release.sh**
- Modify: **scripts/verify-release-package.sh**
- Modify: **infra/package/README.md**
- Modify: **tests/api/test_release_package.py**
- Modify: **tests/api/test_release_transfer_evidence.py**
- Modify: **tests/api/test_helm_packaging_contract.py**

**Step 1: Write failing formal-builder tests**

~~~python
def test_formal_builder_rejects_repository_zip_as_deliverable():
    with pytest.raises(ReleaseBuildError):
        build_release(output_kind="repo_zip")


def test_builder_is_deterministic_and_binds_same_run_evidence(tmp_path):
    first = build_fixture_release(tmp_path / "one")
    second = build_fixture_release(tmp_path / "two")
    assert first.manifest_sha256 == second.manifest_sha256
    packets = [
        first.hermetic_evidence,
        first.compose_evidence,
        first.evaluation_evidence,
        first.web_evidence,
    ]
    assert all(packet.build_context_hash == first.build_context_hash for packet in packets)
    assert all(first.verifies_evidence_hash(item) for item in first.evidence_files)


def test_release_manifest_uses_digest_only_images():
    for image in formal_manifest().images:
        assert image.tag is None
        assert image.digest is not None
        assert re.fullmatch(r"sha256:[a-f0-9]{64}", image.digest)
        assert exported_oci_reference(image) == f"{image.repository}@{image.digest}"
~~~

Also assert:

- the formal artifact allowlist contains OCI image archives, Helm package, air-gap bundle, deployment manifest, SBOM, vulnerability/license reports, provenance, checksums, signatures, and transfer/install evidence
- every SBOM/provenance subject digest matches its artifact
- a failed scan, missing subject, missing signature, missing evidence hash, or tampered artifact fails closed
- readiness mode accepts an immutable commit SHA but marks output non-publishable; release mode requires a clean cryptographically signed immutable tag whose signer is allowlisted
- .git, .env, IDE/cache files, tests, private keys, and source-repository archives are absent

The canonical Compose gate keeps one application Run ID internally; the 50-case evaluation keeps its own set of Run IDs. Cross-gate binding uses git SHA, immutable tag, release profile, CI run/build ID, and file hashes rather than forcing unrelated application Runs to share an ID.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_release_package.py tests/api/test_release_transfer_evidence.py -q -p no:cacheprovider
~~~

Expected: current builder creates a clean signed source package but not the formal artifact set.

**Step 3: Extend the existing builder**

- In **readiness** mode, accept a clean immutable commit SHA but mark all output non-publishable. In **release** mode, run **git verify-tag**, require an allowlisted signer, and refuse unsigned annotated tags, dirty trees, failed/missing required profile evidence, mutable image tags, failed scans, or evidence from different commits/builds.
- Produce digest-pinned OCI image archives, a Helm package, an offline-loadable air-gap bundle, deployment manifest, CycloneDX 1.6 JSON SBOMs, vulnerability report, license report, SLSA v1.0 in-toto provenance, checksums, Ed25519 signatures, and transfer/install evidence.
- Reuse existing signing and evidence code; do not create a parallel source-ZIP release path.
- Support **internal-alpha** and **production-candidate** profiles so only the latter requires Kubernetes evidence.
- Lock Docker Buildx, Helm, kubectl, Kind, Syft, and Grype by version and download/container digest in **toolchain.lock.json**. Syft emits CycloneDX 1.6 JSON; Grype evaluates the matching SBOM. Use the policy file to block Critical/High vulnerabilities unless a reviewed VEX/allowlist entry has owner, reason, and unexpired deadline; block unknown or explicitly denied licenses. Save tool versions and policy hash in evidence.
- Prove OCI load/inspect, Helm lint/template/package/install, and offline air-gap installation against disposable targets before signing.
- Derive **SOURCE_DATE_EPOCH** only from the verified tag commit time. Canonicalize JSON key/order/number formatting; normalize tar entry order, uid/gid, mode, mtime, and path; remove gzip filename/time headers; and configure BuildKit timestamp rewriting. No wall-clock timestamp may affect subject artifacts (timestamps belong only in detached evidence).

**Step 4: Run deterministic and tamper proofs**

~~~powershell
python -m pytest tests/api/test_release_package.py tests/api/test_release_transfer_evidence.py tests/api/test_helm_packaging_contract.py -q -p no:cacheprovider
bash scripts/build-formal-release.sh --mode readiness --profile internal-alpha --build-context dist/build-context.json --hermetic-evidence dist/hermetic/evidence.json --compose-evidence dist/compose.json --evaluation-evidence dist/evaluation.json --web-evidence dist/web/evidence.json --output dist/formal-release
bash scripts/verify-release-package.sh dist/formal-release
~~~

Run the builder twice from the same clean tag in fresh directories and compare every subject artifact digest, not only the manifest. Tamper with a disposable copy and prove verification fails.

**Step 5: Commit**

Intent line:

~~~text
Ship reproducible signed artifacts instead of repository archives
~~~

## Task 21: Make release CI the only supported, verifiable publishing path

**Files:**
- Create: **.github/workflows/release-readiness.yml**
- Create: **.github/workflows/release.yml**
- Create: **tests/api/test_ci_release_workflow_contract.py**
- Create: **docs/release/github-controls.md**
- Create: **infra/package/github-release-controls.schema.json**
- Create: **scripts/verify-github-release-controls.sh**
- Modify: **scripts/build-formal-release.sh**
- Modify: **apps/api/src/taroai/deployment/release_package.py**
- Modify: **tests/api/test_release_package.py**

**Step 1: Write failing workflow contracts**

~~~python
def test_release_ci_runs_all_internal_alpha_gates():
    workflow = load_workflow(".github/workflows/release.yml")
    assert ordered_jobs(workflow) == [
        "hermetic",
        "compose_live",
        "agent_eval",
        "frontend",
        "builder",
        "verify",
    ]


def test_ci_uploads_only_formal_builder_outputs():
    uploaded = uploaded_paths(load_workflow(".github/workflows/release.yml"))
    assert uploaded == formal_artifact_allowlist()
    assert not any(is_repository_or_source_archive(path) for path in uploaded)
~~~

Require a tested **needs** DAG, protected-environment secrets, pinned action revisions, artifact retention, checksum verification after every cross-job download, and identical git SHA/CI run ID in every evidence packet. At this task, production-candidate publishing must be absent or unconditionally fail closed because the live Kubernetes gate is not implemented until Task 22.

Require a **release_controls** job before every release builder/publish job. It runs the GitHub controls verifier with the shared BuildContext, writes signed schema-valid evidence, and passes its hash to the formal builder. Release mode must reject missing/failed/stale controls evidence; readiness mode remains non-publishable.

Test trigger boundaries: untrusted/fork pull requests receive no model/release secrets and run only hermetic, static, deterministic browser-fixture, and readiness-builder checks. Real DeepSeek Compose/evaluation/frontend-live gates may run only on a protected branch, approved **workflow_dispatch**, or immutable tag through an environment approval.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_ci_release_workflow_contract.py -q -p no:cacheprovider
~~~

Expected: no CI workflows exist.

**Step 3: Implement the workflows**

Use one pinned Ubuntu runner contract: Bash for POSIX scripts and **pwsh** for PowerShell scripts, with LF/script-syntax checks before execution. Build a non-publishing readiness workflow for pull requests and an **internal-alpha-only** tag release workflow. Pull requests use **--mode readiness** and no real credentials; protected branch/manual/tag jobs order real Compose, 50-case evaluation, frontend E2E, **--mode release** builder, and independent verification through explicit **needs** edges. Never echo secrets. Upload only the formal artifact allowlist. Any production-candidate input must fail before publishing until Task 22 adds and verifies the Kubernetes job.

Document and audit repository tag protection/rulesets, protected-environment reviewers, Actions-only release/package write permissions, signer allowlist, and branch restrictions through GitHub API/gh evidence. The workflow contract must prove **release_controls** is a required **needs** ancestor of builder/publish, and the release manifest must include its signed evidence hash. If those external controls cannot be verified, block publishing and report this as the only **supported and evidence-producing** path, not an impossible claim that credentials can never be used elsewhere.

**Step 4: Run GREEN and validate syntax**

~~~powershell
python -m pytest tests/api/test_ci_release_workflow_contract.py -q -p no:cacheprovider
~~~

Also run the repository's YAML/schema validation path. If GitHub CLI/actionlint is available, validate locally without publishing. In the protected release environment, run:

~~~bash
bash scripts/verify-github-release-controls.sh --repository "$GITHUB_REPOSITORY" --build-context dist/build-context.json --output dist/release-controls.json --signature-output dist/release-controls.sig --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
~~~

Expected: any missing ruleset, reviewer, permission restriction, signer, or branch control returns nonzero and blocks builder/publish.

**Step 5: Commit**

Intent line:

~~~text
Make CI the only supported path for verified release output
~~~

## Task 22: Implement the real Kubernetes gVisor/Kata production gate

**Files:**
- Create: **infra/k8s/namespaces.yaml**
- Modify: **infra/k8s/api.yaml**
- Modify: **infra/k8s/browser-controller.yaml**
- Modify: **infra/k8s/configmap.yaml**
- Modify: **infra/k8s/kustomization.yaml**
- Modify: **infra/k8s/minio.yaml**
- Modify: **infra/k8s/network-policy.yaml**
- Modify: **infra/k8s/postgres.yaml**
- Modify: **infra/k8s/redis.yaml**
- Modify: **infra/k8s/sandbox-controller.yaml**
- Modify: **infra/k8s/sandbox-runtime-policy.yaml**
- Modify: **infra/k8s/secrets.example.yaml**
- Modify: **infra/k8s/web.yaml**
- Modify: **infra/k8s/worker.yaml**
- Modify: **infra/helm/taroai/values.yaml**
- Modify: **infra/helm/taroai/templates/configmap.yaml**
- Modify: **infra/helm/taroai/templates/sandbox-controller.yaml**
- Modify: **infra/helm/taroai/templates/sandbox-runtime-policy.yaml**
- Modify: **apps/api/src/taroai/sandbox/kubernetes_verification.py**
- Modify: **apps/api/src/taroai/sandbox/kubernetes.py**
- Modify: **apps/api/src/taroai/sandbox/controller_service.py**
- Create: **apps/api/src/taroai/sandbox/reconciler.py**
- Modify: **apps/api/src/taroai/deployment/install_validation.py**
- Modify: **scripts/verify-kubernetes-sandbox.sh**
- Create: **scripts/verify-kubernetes-evidence.sh**
- Modify: **tests/api/test_kubernetes_platform_deployment_contract.py**
- Modify: **tests/api/test_kubernetes_sandbox_verification.py**
- Modify: **tests/api/test_sandbox_kubernetes.py**
- Modify: **tests/api/test_sandbox_controller_service.py**
- Create: **tests/api/test_sandbox_reconciler.py**
- Modify: **tests/api/test_install_validation_contract.py**
- Modify: **.github/workflows/release.yml**
- Modify: **tests/api/test_ci_release_workflow_contract.py**

**Step 1: Write failing static and active-verification tests**

Require:

~~~python
def test_runtime_namespace_matches_rbac():
    assert sandbox_runtime_namespace() == sandbox_role_binding_namespace()


def test_runtime_class_cannot_silently_fall_back():
    assert runtime_class_required() is True
    assert runtime_class_name() in {"gvisor", "kata"}


def test_gate_actively_proves_egress_denial():
    probes = verification_probe_names()
    assert {"public_network", "dns", "cloud_metadata", "cluster_service"} <= probes
    assert "cross_tenant_api_scope" in authorization_probe_names()


def test_gate_recovers_orphans_after_controller_restart():
    result = run_fixture_restart_scenario()
    assert result.residual_pods == []
    assert result.residual_network_policies == []
    assert result.created_persistent_volume_claims == 0
~~~

Also require digest-only runtime images, service-account-token suppression, restricted Pod Security, quotas, default-deny ingress/egress, CPU/memory/disk limits, and periodic TTL/orphan-reconciler evidence. PID/fork-bomb qualification must verify a real node/kubelet **podPidsLimit** or equivalent RuntimeClass/node policy; do not claim a PodSpec resource field that Kubernetes does not support.

For every network-denial probe, first run a positive-control Pod in the same node/environment that proves the target is reachable, then prove the sandbox Pod is denied and record the CNI/provider. Test cross-tenant scope separately through API/session authorization rather than treating it as an egress check.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_kubernetes_platform_deployment_contract.py tests/api/test_kubernetes_sandbox_verification.py tests/api/test_install_validation_contract.py tests/api/test_sandbox_kubernetes.py tests/api/test_sandbox_controller_service.py tests/api/test_sandbox_reconciler.py tests/api/test_ci_release_workflow_contract.py -q -p no:cacheprovider
~~~

Expected: at least namespace/RBAC and active proof contracts fail.

**Step 3: Implement the production contract**

Remove the root Kustomize global namespace. Give every namespaced platform resource, including **secrets.example.yaml**, an explicit **taroai** namespace and runtime policy/Role/RoleBinding an explicit **taroai-sandbox** namespace; bind the controller ServiceAccount subject from **taroai** into the runtime namespace. Add a rendered-manifest contract that rejects empty/default namespaces. Use least-privilege RBAC, required gVisor/Kata RuntimeClass, digest allowlist, restricted security context, and default-deny policies.

Add a periodic controller reconciler that is independent of incoming requests, uses Kubernetes Lease leader election, scans cluster-labeled sessions after restart, and idempotently removes expired/orphan Pods and NetworkPolicies. Session workspaces remain **emptyDir**, so do not invent a separate Volume/PVC object claim: prove no PVC/PV is created and that Pod disappearance releases the bounded ephemeral workspace. Test leader handoff, controller restart, duplicate delete, partial failure retry, and zero residual namespaced resources. Make the verifier execute positive-control denial and resource-abuse probes; configuration presence alone is not passing evidence.

Only after those contracts exist, add the production-candidate CI job. It must run in the protected production environment, consume live signed Kubernetes evidence for the same git SHA/tag/CI run, and be a required dependency of production-candidate builder/publish jobs.

**Step 4: Run static GREEN**

Run the same pytest command plus:

~~~powershell
helm template taroai infra/helm/taroai
kubectl kustomize infra/k8s
~~~

Expected: static contracts and rendering pass.

**Step 5: Run the live gate on a real cluster**

~~~bash
bash scripts/verify-kubernetes-sandbox.sh \
  --namespace taroai-sandbox \
  --service-account-name sandbox-runner \
  --runtime-class-name gvisor \
  --runtime-class-required \
  --image "$TAROAI_SANDBOX_RUNTIME_IMAGE_DIGEST" \
  --allowed-image "$TAROAI_SANDBOX_RUNTIME_IMAGE_DIGEST" \
  --verify-runtime-policy \
  --build-context dist/build-context.json \
  --output dist/kubernetes-sandbox-evidence.json \
  --signature-output dist/kubernetes-sandbox-evidence.sig \
  --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY \
  --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
bash scripts/verify-kubernetes-evidence.sh \
  --build-context dist/build-context.json \
  --evidence dist/kubernetes-sandbox-evidence.json \
  --signature dist/kubernetes-sandbox-evidence.sig \
  --trusted-public-keys "$TAROAI_TRUSTED_RELEASE_EVIDENCE_KEYS"
~~~

Extend the CLI for the explicit BuildContext/output/signature options if absent, and record the node/runtime PID policy evidence. Load the private key only from the protected secret environment, record key ID, and independently verify against the trusted public-key set before the production builder consumes evidence. Expected: this cannot pass against the current disabled Docker Desktop Kubernetes environment. Keep status at sandbox beta until a real cluster with gVisor or Kata produces trusted signed evidence.

**Step 6: Commit**

Intent line:

~~~text
Require live isolation and recovery evidence before production
~~~

## Task 23: Assemble promotion evidence and publish an honest readiness verdict

**Files:**
- Modify: **docs/plans/completion-audit.md**
- Modify: **docs/plans/review-status.md**
- Modify: **infra/package/README.md**
- Create: **docs/release/internal-alpha-runbook.md**
- Create: **apps/api/src/taroai/deployment/promotion_evidence.py**
- Create: **infra/package/promotion-evidence.schema.json**
- Create: **scripts/assemble-promotion-evidence.sh**
- Create: **scripts/verify-promotion-evidence.sh**
- Create: **tests/api/test_release_readiness_completion_contract.py**

**Step 1: Write the failing completion contract**

Require documentation and machine-readable evidence to agree on:

- exact commit/tag and artifact digests
- hermetic test result
- strict DeepSeek Compose result
- dataset/baseline hash and evaluation verdict
- browser/accessibility/visual verdict
- builder/signature verification
- Kubernetes gate status
- resulting promotion level

Test that missing, untrusted, invalidly signed, stale, wrong-build-context, wrong-artifact-digest, or failed Kubernetes evidence yields **internal-alpha / sandbox beta**, never production-candidate. A Compose packet carrying **no_isolation_claim** must be accepted for internal-alpha functional proof but can never be interpreted as production isolation. The promotion JSON itself must be signed and independently verified; tampering with promotion_level, artifact digest, gate verdict, or BuildContext must fail verification. The test must load the verified machine-readable promotion-evidence JSON and derive the documented verdict from it, rather than parsing prose as the source of truth.

**Step 2: Run RED**

~~~powershell
python -m pytest tests/api/test_release_readiness_completion_contract.py -q -p no:cacheprovider
~~~

Expected: current audit documents cannot prove the new gates.

**Step 3: Execute the final verification matrix**

Run, in order:

~~~bash
bash scripts/build-release-context.sh --mode release --git-sha "$(git rev-parse HEAD)" --source-tag "$TAROAI_RELEASE_TAG" --profile internal-alpha --build-id "$TAROAI_BUILD_ID" --ci-run-id "$TAROAI_CI_RUN_ID" --output dist/internal-alpha/build-context.json
bash scripts/verify-github-release-controls.sh --repository "$GITHUB_REPOSITORY" --build-context dist/internal-alpha/build-context.json --output dist/internal-alpha/release-controls.json --signature-output dist/internal-alpha/release-controls.sig --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
pwsh -NoProfile -File scripts/verify-hermetic-tests.ps1 -BuildContext dist/internal-alpha/build-context.json -Output dist/internal-alpha/hermetic.json
bash scripts/verify-internal-alpha-runtime.sh --build-context dist/internal-alpha/build-context.json --dataset infra/evals/agent-quality/v1 --output-dir dist/internal-alpha
pwsh -NoProfile -File scripts/verify-web-release.ps1 -BuildContext dist/internal-alpha/build-context.json -Output dist/internal-alpha/web.json
bash scripts/build-formal-release.sh --mode release --profile internal-alpha --source-tag "$TAROAI_RELEASE_TAG" --build-context dist/internal-alpha/build-context.json --release-controls-evidence dist/internal-alpha/release-controls.json --release-controls-signature dist/internal-alpha/release-controls.sig --hermetic-evidence dist/internal-alpha/hermetic.json --compose-evidence dist/internal-alpha/compose.json --evaluation-evidence dist/internal-alpha/evaluation.json --web-evidence dist/internal-alpha/web.json --output dist/internal-alpha/release
bash scripts/verify-release-package.sh dist/internal-alpha/release
bash scripts/assemble-promotion-evidence.sh --build-context dist/internal-alpha/build-context.json --release dist/internal-alpha/release --output dist/internal-alpha/promotion-evidence.json --signature-output dist/internal-alpha/promotion-evidence.sig --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
bash scripts/verify-promotion-evidence.sh --evidence dist/internal-alpha/promotion-evidence.json --signature dist/internal-alpha/promotion-evidence.sig --trusted-public-keys "$TAROAI_TRUSTED_RELEASE_EVIDENCE_KEYS"
~~~

Run the release-mode matrix only from the clean cryptographically verified tag named by **TAROAI_RELEASE_TAG** and an approved CI run; otherwise run Task 20 readiness mode and do not publish. For production-candidate, create a new BuildContext with profile **production-candidate** and rerun the non-Kubernetes gates into a separate directory under that context. Then run and independently verify the Kubernetes gate on the qualified real cluster before building:

~~~bash
bash scripts/build-release-context.sh --mode release --git-sha "$(git rev-parse HEAD)" --source-tag "$TAROAI_RELEASE_TAG" --profile production-candidate --build-id "$TAROAI_BUILD_ID" --ci-run-id "$TAROAI_CI_RUN_ID" --output dist/production-candidate/build-context.json
bash scripts/verify-github-release-controls.sh --repository "$GITHUB_REPOSITORY" --build-context dist/production-candidate/build-context.json --output dist/production-candidate/release-controls.json --signature-output dist/production-candidate/release-controls.sig --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
pwsh -NoProfile -File scripts/verify-hermetic-tests.ps1 -BuildContext dist/production-candidate/build-context.json -Output dist/production-candidate/hermetic.json
bash scripts/verify-internal-alpha-runtime.sh --build-context dist/production-candidate/build-context.json --dataset infra/evals/agent-quality/v1 --output-dir dist/production-candidate
pwsh -NoProfile -File scripts/verify-web-release.ps1 -BuildContext dist/production-candidate/build-context.json -Output dist/production-candidate/web.json
bash scripts/verify-kubernetes-sandbox.sh --namespace taroai-sandbox --service-account-name sandbox-runner --runtime-class-name gvisor --runtime-class-required --image "$TAROAI_SANDBOX_RUNTIME_IMAGE_DIGEST" --allowed-image "$TAROAI_SANDBOX_RUNTIME_IMAGE_DIGEST" --verify-runtime-policy --build-context dist/production-candidate/build-context.json --output dist/production-candidate/kubernetes.json --signature-output dist/production-candidate/kubernetes.sig --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
bash scripts/verify-kubernetes-evidence.sh --build-context dist/production-candidate/build-context.json --evidence dist/production-candidate/kubernetes.json --signature dist/production-candidate/kubernetes.sig --trusted-public-keys "$TAROAI_TRUSTED_RELEASE_EVIDENCE_KEYS"
bash scripts/build-formal-release.sh --mode release --profile production-candidate --source-tag "$TAROAI_RELEASE_TAG" --build-context dist/production-candidate/build-context.json --release-controls-evidence dist/production-candidate/release-controls.json --release-controls-signature dist/production-candidate/release-controls.sig --hermetic-evidence dist/production-candidate/hermetic.json --compose-evidence dist/production-candidate/compose.json --evaluation-evidence dist/production-candidate/evaluation.json --web-evidence dist/production-candidate/web.json --kubernetes-evidence dist/production-candidate/kubernetes.json --kubernetes-signature dist/production-candidate/kubernetes.sig --output dist/production-candidate/release
bash scripts/verify-release-package.sh dist/production-candidate/release
bash scripts/assemble-promotion-evidence.sh --build-context dist/production-candidate/build-context.json --release dist/production-candidate/release --kubernetes-evidence dist/production-candidate/kubernetes.json --kubernetes-signature dist/production-candidate/kubernetes.sig --output dist/production-candidate/promotion-evidence.json --signature-output dist/production-candidate/promotion-evidence.sig --signing-key-env TAROAI_RELEASE_EVIDENCE_SIGNING_KEY --signing-key-id "$TAROAI_RELEASE_EVIDENCE_SIGNING_KEY_ID"
bash scripts/verify-promotion-evidence.sh --evidence dist/production-candidate/promotion-evidence.json --signature dist/production-candidate/promotion-evidence.sig --trusted-public-keys "$TAROAI_TRUSTED_RELEASE_EVIDENCE_KEYS"
~~~

Never relabel or reuse the earlier internal-alpha artifact/evidence as production-candidate evidence. The promotion assembler validates signatures, BuildContext equality, JSON schema, and file hashes, then records commit/tag, gate verdicts, artifact digests, promotion level, each command exit code/timestamp/tool version, and redacted evidence path.

**Step 4: Update the audit without overstating status**

Publish **internal-alpha / sandbox beta** only when all non-Kubernetes gates pass. Publish **production-candidate** only when the real gVisor/Kata gate also passes. Never substitute a local Docker result for Kubernetes isolation.

**Step 5: Run the full verification**

~~~powershell
python -m pytest -m "not live and not docker and not compose and not kubernetes" -q -p no:cacheprovider
python -m pytest tests/api/test_release_readiness_completion_contract.py -q -p no:cacheprovider
git diff --check
git status --short
~~~

Expected: zero non-live failures, no malformed diff, and only intentional generated evidence outside the committed source tree.

**Step 6: Commit**

Intent line:

~~~text
Publish promotion status from verified evidence only
~~~

## Final handoff checklist

- Review Tasks 1-21 after implementation and address only verified actionable findings.
- Run the verification commands again after fixes.
- Confirm no pending implementation tasks, zero known non-live errors, and every claimed gate has current evidence.
- Do not stage local .env, generated credentials, raw prompts, screenshots containing secrets, or temporary worktree patches.
- Do not push, publish packages, create a GitHub release, or label production without the user's explicit external-publication authority.

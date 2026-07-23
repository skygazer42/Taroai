# MVP Local Cloud PoC Operations

This runbook starts the local cloud PoC with the static Workspace UI, API container, Playwright browser controller, PostgreSQL, Redis, and MinIO dependency services.

## Configuration

1. Copy the committed template into a local file:

```bash
cp .env.example .env
```

Compose loads `.env.example`, then overlays `.env`, and finally overlays the
optional `.env.runtime`. Use `.env.runtime` for temporary provider changes that
must survive container recreation without rewriting the main local config.

Do not commit or package `.env` or `.env.runtime`. Both are ignored local
operator files; the repository ships only non-secret templates.

For the current Zhipu profile, copy `infra/config/zhipu.env.example` to
`.env.runtime`, fill the key locally, and recreate the API and workers. Model and
Embedding settings intentionally come only from these service env files so an
empty Compose interpolation cannot erase a configured credential.

The API and `minio-init` services also pass object-storage bucket and region
settings from the host shell. Keep `TAROAI_OBJECT_STORAGE_BUCKET` and
`TAROAI_OBJECT_STORAGE_REGION` consistent through shell injection or `.env`
overrides so MinIO creates the same bucket the API uses for artifacts.

The API, sandbox-controller, and browser-controller services also pass
controller security settings from the host shell, including
`TAROAI_SANDBOX_CONTROLLER_API_KEY`, `TAROAI_BROWSER_CONTROLLER_API_KEY`,
session limits, and navigation allowlists. Compose supplies local-only default
controller keys so the exposed sandbox/browser controller routes require bearer
auth even in the default PoC. Replace those keys before sharing the stack or
running any non-local validation. The sandbox-controller and browser-controller
containers receive only their explicit controller settings instead of inheriting
the API `.env` file, so model provider keys, storage secrets, bootstrap tokens,
and API signing secrets stay out of those controller runtime environments.

The API service also passes core local-PoC security values from the host shell,
including `TAROAI_ACCESS_TOKEN_SECRET`, `TAROAI_PASSWORD_HASH_SALT`,
`TAROAI_TENANT_BOOTSTRAP_TOKEN`,
`TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET`,
`TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN`, and
`TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS`. Use shell injection for short-lived
shared validation when writing a local `.env` is undesirable.

2. Edit `.env` before sharing the environment with anyone outside local development:

- `TAROAI_TENANT_BOOTSTRAP_TOKEN`
- `TAROAI_ACCESS_TOKEN_SECRET`
- `TAROAI_PASSWORD_HASH_SALT`
- `TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID`
- `TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY`
- `TAROAI_MODEL_GATEWAY_API_KEY`
- `TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET` when external artifact links are enabled
- `TAROAI_SANDBOX_CONTROLLER_API_KEY` before sharing sandbox-controller routes beyond a single local developer stack
- `TAROAI_BROWSER_CONTROLLER_API_KEY` before sharing browser-controller routes beyond a single local developer stack
- `TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN` when sandbox lease resolution is enabled for shared deployments

The local cloud PoC values for `TAROAI_ACCESS_TOKEN_SECRET` and
`TAROAI_PASSWORD_HASH_SALT` are rejected by the Pydantic Settings profile gate in
`prod`/`production` and customer-operated deployment modes. Generate unique
values before using the API outside local validation.

The local `TAROAI_TENANT_BOOTSTRAP_TOKEN` and sandbox resolver token
placeholders are also rejected in those deployment contexts when configured.
Keep them empty to disable the corresponding bootstrap/resolver path, or store
generated deployment-specific values in the approved secret manager.

The local sandbox-controller and browser-controller keys shipped for Compose
are also rejected in `prod`/`production` and customer-operated deployment modes.
Generate deployment-specific controller keys before moving the stack beyond
single-developer local validation.

The local PoC API uses PostgreSQL by default through `TAROAI_DATABASE_URL=postgresql://taroai_app:taroai_app@postgres:5432/taroai`. The Compose PostgreSQL service starts with a bootstrap admin role and creates the non-superuser `taroai_app` role through `infra/postgres/init.sql`, so tenant RLS checks are meaningful during local validation.

The local cloud PoC template uses SQL-backed control-plane/catalog services, Redis-backed short-term memory and job queues, and MinIO-backed object storage. The Pydantic Settings profile gate rejects the in-memory backend defaults whenever `TAROAI_ENVIRONMENT` is `production` or `prod`, even in managed-cloud mode, so production operators must keep the durable SQL/Redis backend settings enabled before startup.

Production environments reject `TAROAI_DEPLOYMENT_SECRET_MANAGER_TYPE=local`
and both `memory` and `local` secret-service backends. Compose uses the encrypted,
shared-volume `local` backend so API and workers can resume Secret Capture; production
and customer-operated deployments must use an approved external secret backend.

The local cloud PoC template enables `TAROAI_SANDBOX_PROVIDER=local_process`. Sandbox sessions execute commands inside per-session workspaces under `TAROAI_SANDBOX_ROOT_DIR` in the API container. Its capability response intentionally declares no network isolation, filesystem isolation, or resource limits; it only proves local workspace lifecycle and destroy behavior. `/readyz` exposes those direct-adapter capability flags for local providers, and the local PoC verifier records them in its result JSON, so the workspace preflight and release evidence can distinguish PoC execution from hardened sandbox isolation. Operators can explicitly switch to `TAROAI_SANDBOX_PROVIDER=docker` where a Docker daemon is available; that provider starts disabled-network containers with the session workspace mounted at `/workspace`, memory/CPU/pids limits, non-root `--user`, read-only rootfs, dropped capabilities, security options, and tmpfs mounts configured through `TAROAI_SANDBOX_DOCKER_*` settings. Direct in-process sandbox capacity is configured through `TAROAI_SANDBOX_MAX_SESSIONS`, `TAROAI_SANDBOX_MAX_SESSIONS_PER_TENANT`, and `TAROAI_SANDBOX_MAX_SESSIONS_PER_RUN`; controller deployments use the `TAROAI_SANDBOX_CONTROLLER_*` capacity settings below. Sandbox command environment keys must be valid POSIX-style names, and platform-managed keys such as `TAROAI_SANDBOX_WORKSPACE` stay authoritative across local, Docker, and Kubernetes providers. This is useful for local validation; shared enterprise deployments still need Kubernetes, E2B, or microVM-backed isolation before granting broad command execution to employees. The Pydantic Settings profile gate rejects `local_process`, `docker`, and `disabled` sandbox providers for BYOC, VPC, private, and air-gapped deployment modes, and also rejects them whenever `TAROAI_ENVIRONMENT` is `production` or `prod`; those deployment contexts must use `TAROAI_SANDBOX_PROVIDER=k8s` or `TAROAI_SANDBOX_PROVIDER=e2b`, configure `TAROAI_SANDBOX_CONTROLLER_BASE_URL`, configure `TAROAI_SANDBOX_CONTROLLER_API_KEY` in the runtime secret, and route sandbox lifecycle, command, file, snapshot, destroy, `GET /sessions`, and `GET /capabilities` calls through the HTTP sandbox controller contract. Air-gapped mode still requires an internal provider.

Enterprise sandbox controllers must return `GET /capabilities` with
`network_isolation`, `filesystem_isolation`, `resource_limits`, and
`destroy_supported` set to `true`, plus `session_ttl_enforced=true`,
`max_session_ttl_seconds`, `max_sessions`, `max_sessions_per_tenant`, and
`max_sessions_per_run`. The install verifier treats those capability flags as
release evidence, and the HTTP sandbox adapter also requires them before it
opens controller-backed sessions. Requests whose timeout exceeds the declared
session TTL, or whose visible global/tenant/run active session count is already
at capacity, are rejected by the API before controller-backed session creation.
Customer-operated install validation also requires the lifecycle evidence
provider to match `/readyz.checks.sandbox.provider`, with `k8s` and
`kubernetes` treated as aliases, so release evidence cannot be collected from a
different sandbox backend than the one the API is configured to use.
The package now includes a FastAPI sandbox controller entry point at
`taroai.sandbox.controller_service:app`. It is intended for controller
deployment packaging and contract validation: it serves `GET /capabilities`,
uses `TAROAI_SANDBOX_CONTROLLER_API_KEY` for bearer auth, enforces
`TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS` and tenant/run session limits,
and exposes the same sessions, commands, files, snapshots, and destroy routes
used by the HTTP sandbox adapter. The controller can run the local Docker-backed
provider for Compose validation, or the kubectl-backed Kubernetes provider with
`TAROAI_SANDBOX_CONTROLLER_PROVIDER=kubernetes`. The Kubernetes provider creates
one Pod plus one deny-all NetworkPolicy per sandbox session, waits for Pod
readiness, runs commands through `kubectl exec`, moves files with `kubectl cp`,
and destroys both the Pod and NetworkPolicy at session teardown. Configure
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_NAMESPACE`,
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_SERVICE_ACCOUNT_NAME`,
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME`,
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED`,
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES`, and the resource limit
fields before enabling it on a real cluster. The provider rejects session images
outside the configured image patterns. The controller Settings now fail startup
for `kubernetes`/`k8s` providers unless
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED=true` and
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME` is non-empty, so a
shared controller cannot silently fall back to the default node runtime. The
kubectl-backed provider only declares `image_policy_enforced=true` when the
configured allowed-image patterns pass the same approved-registry/digest and
non-`latest` policy used at session creation, so readiness evidence cannot be
produced from a broad allowlist such as `*` or a wildcard tag pattern such as
`registry.example.com/sandbox-runtime:*`. It also
exposes an internal orphaned Pod cleanup routine
that removes sandbox Pods whose session IDs are no longer active. Keep
`TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED=false` until the
controller has observed active sessions for the namespace; enabling it causes
each authenticated controller request to delete labeled sandbox Pods whose
session IDs are not in the controller's active set, then confirm each deleted
session Pod is no longer active and each per-session NetworkPolicy is gone
before reporting the cleanup result.
After a controller restart, the Kubernetes provider refreshes its active session
view from sandbox Pod labels, annotations, and the live per-session
NetworkPolicy before serving tenant-scoped session lists, capacity checks, TTL
cleanup, and session lookup for later command/file/snapshot calls.
Kubernetes installs should apply `infra/k8s/sandbox-runtime-policy.yaml` before
starting the controller and keep `infra/k8s/sandbox-controller.yaml` RBAC in
sync with the verifier. Those manifests create the `taroai` namespace with
restricted Pod Security Admission labels, a sandbox runtime `ResourceQuota`,
container `LimitRange`, a namespace-level default-deny `NetworkPolicy` for
pods labeled `app.kubernetes.io/name=taroai-sandbox-session`, and a
least-privilege sandbox-controller `Role`/`RoleBinding` for Pod, Pod exec, and
NetworkPolicy operations. The verifier also records that the sandbox-runner
ServiceAccount disables token automount and install validation rejects evidence
where the verified sandbox session Pod ran outside that runtime-policy namespace
or did not use that same runner ServiceAccount. The live sandbox verifier reads
the actual created session metadata, and the Kubernetes adapter now refreshes
that metadata from the ready Pod's live spec instead of trusting only the
requested manifest, for `serviceAccountName` and
`runtimeClassName`, CPU/memory/ephemeral-storage limits, and run-as user/group,
then reads the live per-session NetworkPolicy to record the session selector,
policy types, and deny-all status. It fails if any of those Pod or
NetworkPolicy facts do not match the verifier config, so
release evidence cannot be produced only from requested config values. This
gives sandbox Pods default
requests, limits,
namespace-level capacity bounds, and a network
isolation backstop even before per-session NetworkPolicies are created. The Helm
chart exposes the same controls under `sandboxRuntimePolicy.enabled`,
`sandboxRuntimePolicy.resourceQuota`, `sandboxRuntimePolicy.limitRange`, and
`sandboxRuntimePolicy.networkPolicy`; customer-operated clusters should review
those values against their worker-node capacity before enabling
`TAROAI_SANDBOX_CONTROLLER_PROVIDER=kubernetes`.
Enterprise controllers must also implement authenticated `GET /sessions` for the controller
global active-session capacity view and `GET /sessions?tenant_id=...` for
tenant/run capacity checks before enforcing sandbox concurrency and license
limits. The global view must come from the provider-visible session inventory
when available, not only from the controller process's current known-tenant
cache, so a restarted controller still sees existing Docker/Kubernetes sessions
before accepting new work. The HTTP sandbox adapter calls `GET /capabilities`
before `POST /sessions` and rejects controller-backed session creation unless
the controller declares network isolation, filesystem isolation, resource
limits, destroy support, session TTL, runtime isolation, image-policy
enforcement, at least one allowed image, and global/tenant/run capacity limits.
`POST /sessions` must return the created session with
`status=active`; a successful response that returns a destroyed or inactive
session is rejected before tool execution starts. The controller's
`GET /capabilities` and `POST /sessions` responses must identify the configured
provider, with `k8s` and `kubernetes` treated as aliases.
`POST /commands`, `POST /files`, and `GET /files` must reject requests whose
tenant/session is valid but whose workspace or run differs from the opened
sandbox session. The standalone controller checks that scope before dispatching
to the provider, and the lifecycle verifier records `command_scope_enforced`,
`file_scope_enforced`, and `file_read_scope_enforced` so install evidence proves
the command/file surfaces cannot be reused across runs.
`POST /snapshots` must also include `workspace_id` and `run_id` and reject
same-tenant requests whose workspace or run differs from the opened sandbox
session. The HTTP sandbox adapter reads the session context before creating the
snapshot so controller-backed snapshots use the same run/workspace boundary as
commands and file writes. The lifecycle verifier records
`snapshot_scope_enforced` by sending the same cross-workspace/run snapshot probe
directly to the controller contract.
Destroy calls must return the destroyed session with `status=destroyed`; a
controller that returns 200 while leaving the session active is rejected as an
unavailable sandbox provider. The lifecycle verifier also re-reads the tenant
session list after destroy and records `session_destroy_confirmed`; install
validation fails if the destroyed session still appears as active in the
controller capacity view. It also sends a post-destroy command probe and records
`post_destroy_command_blocked`, so release evidence fails if a destroyed session
can still execute commands. The standalone sandbox controller rejects command,
file, file-list/download, and snapshot operations after destroy before dispatching
to the provider adapter. The HTTP sandbox adapter performs the same active-list
confirmation after `DELETE /sessions/{session_id}`, so runtime cleanup does not
silently accept a stale active controller session. The kubectl-backed Kubernetes
provider also confirms the pod list after `kubectl delete`; an undeleted,
non-terminating session pod is treated as provider cleanup failure instead of a
successful destroy. The kubectl-backed Kubernetes provider declares
`session_ttl_enforced=true`, exposes its `max_session_ttl_seconds`, and rejects
session create requests whose timeout exceeds that provider TTL before applying
the Pod manifest. It also rejects command, file, list, download, and snapshot
operations when the tracked session has exceeded its TTL, marking the session
destroyed locally only after issuing `kubectl delete` for the session Pod and
per-session NetworkPolicy and confirming the Pod is no longer active and the
NetworkPolicy is gone, so an expired session cannot keep running in the cluster
or retain stale network policy after the controller refuses the operation.

`/readyz` reports sandbox configuration at `checks.sandbox`, including the active `provider`, whether a controller endpoint is required, aggregate `controller_configured`, separate `controller_endpoint_configured` and `controller_auth_configured` booleans, and any missing fields. Local `disabled` sandbox settings report `configured=false` with `missing=["provider"]`; enterprise `k8s` and `e2b` settings report `configured=false` until both `TAROAI_SANDBOX_CONTROLLER_BASE_URL` and `TAROAI_SANDBOX_CONTROLLER_API_KEY` are set and the controller `/capabilities` response can be read. If the controller endpoint/key are present but capability discovery fails, readiness reports `configured=false`, `capabilities_checked=false`, and `missing=["sandbox_controller_capabilities"]` instead of treating URL/key presence as enterprise sandbox readiness. Private install validation also rejects controller-required sandbox readiness when the capabilities are read but do not declare runtime isolation, image-policy enforcement with at least one allowed image, network/filesystem isolation, resource limits, destroy support, TTL enforcement, and capacity limits.

The local cloud PoC template also enables `TAROAI_BROWSER_PROVIDER=playwright` and points the API to `TAROAI_BROWSER_CONTROLLER_BASE_URL=http://browser-controller:8001`. The browser controller is a separate container running `taroai.sandbox.playwright_service:app`; API and worker processes keep using the HTTP browser controller contract, while Playwright and Chromium run in the browser-controller service. `/readyz.checks.browser` now treats controller readiness as more than URL/key presence: when the browser provider requires a controller, the API must read `/capabilities` and report `capabilities_checked=true`, otherwise readiness returns `configured=false` with `missing=["browser_controller_capabilities"]`. Install validation also rejects controller-required browser readiness when capabilities are read but do not declare bearer auth, session TTL enforcement, and global/tenant/run capacity limits.

## Start

```bash
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

If default host ports are already occupied, override only the host mappings without changing service-internal URLs:

```bash
TAROAI_WEB_PORT=3300 \
TAROAI_API_PORT=8800 \
TAROAI_BROWSER_CONTROLLER_PORT=8801 \
POSTGRES_PORT=55432 \
REDIS_PORT=56379 \
MINIO_API_PORT=59000 \
MINIO_CONSOLE_PORT=59001 \
docker compose --env-file .env -p taroai-live-e2e -f infra/docker-compose.yml up --build
```

When using these alternate host ports, open `http://localhost:3300` and set the workspace API Base field to `http://localhost:8800`.
The committed local PoC template allows `http://localhost:3000`, `http://localhost:3300`, and `http://web` in `TAROAI_CORS_ORIGINS`; if you choose a different web host port, add that origin to your local `.env`.

For CI or release-gate verification, prefer the strict Compose gate. It starts
the local stack with alternate host ports, waits for Compose health checks, runs
the strict model/browser/workspace/artifact verifier, checks the generated
result JSON with the demo-readiness gate, and removes the Compose project on
exit unless `TAROAI_COMPOSE_STRICT_E2E_KEEP_STACK=1` is set:

```bash
export TAROAI_MODEL_GATEWAY_BASE_URL=https://api.deepseek.com
export TAROAI_MODEL_GATEWAY_MODEL=deepseek-v4-flash
export TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS='{"response_format":{"type":"json_object"},"thinking":{"type":"disabled"}}'
export TAROAI_MODEL_GATEWAY_API_KEY=<set-in-local-shell-or-ci-secret>

scripts/verify-compose-strict-e2e.sh
```

The script also accepts `TAROAI_COMPOSE_ENV_FILE=infra/config/deepseek.env.example`
for non-secret provider defaults, but the API key must still come from the
local shell or CI secret store. The gate checks the effective shell/env-file
model settings before starting Docker Compose, so an env-file profile with an
empty key fails fast instead of spending several minutes booting a stack that
can only return no-model diagnostics. It also passes the same local browser
controller bearer token that Compose gives the browser-controller service unless
`TAROAI_BROWSER_CONTROLLER_API_KEY` is overridden. It calls
`scripts/verify-local-cloud-poc.sh --require-model-execution` with Chromium
loading the Workspace at `http://web` and the Workspace API base set to
`http://api:8000`. By default, it writes the redacted strict gate result to
`dist/local-cloud-poc-strict-e2e-result.json`; override that path with
`TAROAI_COMPOSE_STRICT_E2E_OUTPUT`. After the verifier writes that file, the
script runs `scripts/verify-local-cloud-demo-ready.sh` with
`--require-workspace-execution` and `--require-skill-reuse`, writes the demo
gate report to `dist/local-cloud-poc-demo-gate-result.json` by default, and lets
that path be overridden with `TAROAI_COMPOSE_STRICT_E2E_DEMO_GATE_OUTPUT`. The
demo gate report includes `required_gates`, `failed_required_gates`, and
`gate_results`, so release evidence shows whether workspace execution, skill
reuse, browser-controller governance, and hardened sandbox governance were
enforced, which required gates failed, and whether each gate passed for that
run. CI fails if the JSON does not report `demo_ready=true`,
`workspace_execution_ready=true`, `skill_reuse_ready=true`, and browser
controller auth/TTL/session-limit governance evidence. When
`--require-sandbox-governance` is enabled, the demo gate also requires the
sandbox readiness evidence to declare runtime isolation and an enforced image
policy with at least one allowed image pattern, not only network/filesystem
isolation, resource limits, TTL, and session capacity.
Set `TAROAI_COMPOSE_STRICT_E2E_REQUIRE_SANDBOX_GOVERNANCE=1` only for a
hardened Docker/controller-backed sandbox profile; the default local-process PoC
does not declare network/filesystem isolation, resource limits, or sandbox TTL
enforcement and is expected to fail that stricter customer-readiness gate.

When the same strict Compose run should also emit an install-validation report,
set `TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_OUTPUT`, for example
`dist/install-validation.json`. The script then reads the strict verifier
`run_id`, `tenant_id`, and `owner_user_id`, logs in as the local owner without
writing the token to an evidence file, runs `scripts/verify-event-stream.sh`
and `scripts/verify-audit-write.sh` with `--run-id` for that same strict run,
runs the release package builder, signs the package with an ephemeral local
strict-gate Ed25519 key, writes release transfer evidence beside the package,
runs the sandbox lifecycle and browser-controller verifiers against the same
Compose controllers, verifies the configured model gateway, Redis queue, and
MinIO/S3-compatible object storage from the host boundary, writes a migration
plan against the host PostgreSQL endpoint, builds and redacts a small support
bundle to prove the redaction harness, exports the demo gate report as
`TAROAI_RUNTIME_CLOSED_LOOP_EVIDENCE_PATH`, and calls
`scripts/validate-install.sh` with runtime, release-transfer, migration,
model-gateway, object-storage, Redis, event-stream, audit-write, sandbox,
browser-controller, and support-bundle-redaction evidence.
Because the model-gateway verifier runs on the host, this install-validation
bridge requires either `TAROAI_MODEL_GATEWAY_API_KEY` or
`TAROAI_MODEL_GATEWAY_PROVIDERS` with verifier-readable credentials.
`TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID` alone can configure the API
runtime, but it cannot give the host-side verifier a secret value by itself.
If provider JSON uses `api_key_secret_ref_id`, also supply
`TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUES` or
`TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON`; otherwise the bridge
fails before starting Docker Compose.
If the customer or CI environment has already generated external evidence, set
`TAROAI_COMPOSE_STRICT_E2E_SECRET_MANAGER_VERIFICATION`,
`TAROAI_COMPOSE_STRICT_E2E_TRACE_COLLECTOR_VERIFICATION`, and
`TAROAI_COMPOSE_STRICT_E2E_RESTORE_DRILL_VERIFICATION`; the script checks that
each supplied path exists and passes it through to `scripts/validate-install.sh`.
This bridge is intentionally opt-in because cloud/customer install validation
still needs real secret-manager, trace, and restore-drill evidence before it
can pass.

API:

- `GET http://localhost:8000/healthz`
- `GET http://localhost:8000/readyz`
- OpenAPI: `http://localhost:8000/docs`

If the host shell has HTTP proxy variables configured and `curl localhost` returns
an empty `502 Bad Gateway`, bypass the proxy for local health checks:

```bash
curl --noproxy '*' http://localhost:8000/readyz
```

Workspace:

- UI: `http://localhost:3000`
- The static workspace calls the API at `http://localhost:8000` by default and loads recent workspace runs through `GET /api/runs`.
- Browser-controller or Compose checks can prefill the connection strip with URL parameters such as `?apiBase=http%3A%2F%2Flocalhost%3A8000&tenantId=tenant_acme&userId=user_luke&workspaceId=workspace_sales&email=owner%40example.com`; URL `accessToken`, `token`, and `password` parameters are removed instead of being persisted.
- For the default `.env.example` setting `TAROAI_DEV_REQUEST_HEADERS_ENABLED=false`, the Workspace can bootstrap the first local tenant from the connection strip. Enter tenant slug, owner name, owner email/password, and the local bootstrap token; the UI sends the token once as `X-Bootstrap-Token`, does not persist it, clears the token input, syncs the returned tenant/user/workspace IDs, and then logs in with the owner credentials. The Workspace updates its tenant/user fields from the login response before loading history or creating runs, so the visible context matches the authenticated session.
- Logout, failed login attempts, and expired/revoked Bearer-token responses clear the visible conversation, run, event, artifact, storage object, browser preview, trace, runtime-state, approval, customer-success, and terminal surfaces so a shared browser tab does not retain the previous authenticated session's execution data. This also covers storage-content calls used by artifact preview/download and browser capture preview/download.
- Approval resolution now preserves visible event evidence: after an approval or rejection event, the Workspace resolution strip includes the decision, `approval_id`, and `resolved_by_user_id` when those payload fields are present, so the UI can be matched back to the governed run event stream.
- The workspace still sends `X-Tenant-ID` and `X-User-ID` headers for local dev environments where `TAROAI_DEV_REQUEST_HEADERS_ENABLED=true`, but the Pydantic Settings profile gate rejects dev request headers in `prod`/`production` environments and all customer-operated deployment modes.
- The workspace connection strip reads `GET /readyz` and displays model-gateway and sandbox preflight status before execution. When the model provider is not configured, the UI shows the missing model fields while still allowing the local PoC no-model diagnostic path.
- Browser runtime events are shown in the Browser panel when `browser.action.performed` events include `current_url` or `screenshot_uri`; storage-backed browser captures show the resolved storage object ID, are previewed in the panel, and remain downloadable.
- Storage-backed run artifacts get a Download action that calls `/api/storage/objects/{id}/content` with the current Bearer token.
- External artifact links are disabled in the local template by default through `TAROAI_EXTERNAL_SHARE_LINKS_ENABLED=false`. When enabled for a controlled test, an active unexpired `external_link` share grant with `artifact` resource type and `view` permission can download a non-sensitive artifact through `/api/share-links/{external_link_id}/storage/objects/{storage_object_id}/content?tenant_id=...`; non-artifact resources, link tokens shorter than 32 characters, and non-`view` external-link permissions are rejected, newly created share grants store only a tenant-scoped `hmac-sha256:` digest of the link token using `TAROAI_EXTERNAL_SHARE_LINK_TOKEN_HASH_SECRET` when set and otherwise `TAROAI_ACCESS_TOKEN_SECRET`, and share-grant API responses, audit metadata, and `external_artifact_download_bytes` meter metadata do not store or return the raw token.

Browser controller:

- Health: `http://localhost:8001/healthz`
- Internal API URL: `http://browser-controller:8001`
- The controller exposes `POST /sessions`, `GET /sessions?tenant_id=...`, `GET /sessions/{session_id}?tenant_id=...&workspace_id=...&run_id=...`, `DELETE /sessions/{session_id}?tenant_id=...&workspace_id=...&run_id=...`, and `POST /actions` for browser session lifecycle and actions.
- HTTP browser-controller responses must echo the requested tenant, workspace, run, session, and action context; the API and worker adapters reject cross-tenant or cross-session response payloads before recording browser state.
- `GET /sessions/{session_id}` and `DELETE /sessions/{session_id}` require the
  original tenant, workspace, and run query parameters. Delete must return the
  deleted session body so the API can verify tenant/workspace/run/session
  context before treating cleanup as complete.
- The browser-controller rejects duplicate `session_id` values with HTTP 409 before opening a new Chromium context, so repeated run/session creation cannot overwrite an existing browser context.
- The default local Compose stack sets `TAROAI_BROWSER_CONTROLLER_API_KEY`, so browser session/action routes require `Authorization: Bearer <key>` by default. API and worker processes send this header through the HTTP browser controller adapter. The browser-controller service rejects configured API keys shorter than 32 characters.
- For `prod`/`production` and customer-operated deployment modes, enabling `TAROAI_BROWSER_PROVIDER` now requires `TAROAI_BROWSER_CONTROLLER_BASE_URL` and a generated `TAROAI_BROWSER_CONTROLLER_API_KEY`; missing endpoints, empty keys, and the packaged placeholder are rejected by the Pydantic Settings profile gate before startup.
- The browser-controller service also enforces `TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS`, `TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS`, `TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT`, and `TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_RUN` before opening new sessions or applying actions.
- `GET /capabilities` is authenticated with the same bearer token and returns non-sensitive browser-controller governance evidence: provider, auth-required state, TTL enforcement, global/tenant/run session limits, and whether a navigation host allowlist is active.
- Install validation rejects browser-controller lifecycle evidence whose provider
  does not match `/readyz.checks.browser.provider`, so a Playwright deployment
  cannot pass with Browserbase evidence or the other way around.
- The API/worker HTTP browser adapter also calls `GET /capabilities` before
  opening a session and rejects controllers that do not declare auth, TTL, and
  global/tenant/run capacity controls. It checks the current session list before
  `POST /sessions`, so runtime-created browser sessions do not overrun a
  controller that has reached declared capacity.
- Browser-controller lifecycle verification also attempts duplicate `session_id`
  creation, submits cross-workspace/run session-read, session-delete, and action
  probes, and calls `GET /sessions?tenant_id=...` for an allowed tenant and a
  denied tenant, proving the smoke session cannot be overwritten, browser
  session lifecycle and actions stay bound to the opened workspace/run, capacity
  visibility does not leak across tenant scopes, and the controller has declared
  its TTL/capacity capabilities.
- Set `TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS` to a JSON list such as `["app.example.com","*.trusted.internal"]` in shared deployments. An empty list keeps local PoC navigation unrestricted.
- Agent runtime-created browser sessions are deleted when the run reaches a terminal success, failure, cancellation, or approval rejection path. The run event stream records `browser.session.destroyed` on success and `browser.session.destroy_failed` when the controller reports a cleanup provider error.
- The HTTP browser adapter sends tenant/workspace/run scope on session read and
  delete, then re-reads the tenant session list after
  `DELETE /sessions/{session_id}` and rejects cleanup if the deleted session
  still appears, matching the browser lifecycle install evidence gate.
- The local cloud PoC verifier deletes its browser-controller smoke session after browser smoke and optional Workspace checks finish, then confirms the deleted session returns 404 and is absent from the tenant session list.

For a shared local stack, prefer injecting the controller key through the shell:

```bash
read -rsp "TAROAI_BROWSER_CONTROLLER_API_KEY: " TAROAI_BROWSER_CONTROLLER_API_KEY
export TAROAI_BROWSER_CONTROLLER_API_KEY
echo
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

Sandbox workspace root:

- `/data/taroai/sandboxes` inside the API container by default.

MinIO:

- API: `http://localhost:9000`
- Console: `http://localhost:9001`
- The `minio-init` Compose service creates `TAROAI_OBJECT_STORAGE_BUCKET`
  before API startup, and the API service receives the same bucket and region
  values through Compose environment interpolation.

## Bootstrap A Tenant

```bash
curl -X POST http://localhost:8000/api/tenants/bootstrap \
  -H "Content-Type: application/json" \
  -H "X-Bootstrap-Token: ${TAROAI_TENANT_BOOTSTRAP_TOKEN}" \
  -d '{
    "tenant_slug": "acme",
    "owner_email": "owner@example.com",
    "owner_display_name": "Owner",
    "owner_password": "correct horse battery staple"
  }'
```

Then login:

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant_acme",
    "email": "owner@example.com",
    "password": "correct horse battery staple"
  }'
```

Use the returned Bearer token to call:

```bash
curl http://localhost:8000/api/tenants/current/readiness \
  -H "Authorization: Bearer ${TAROAI_ACCESS_TOKEN}"
```

The same owner email/password can be used in the workspace UI. The token is stored in browser `sessionStorage` for the current tab session.

## Model Gateway Preflight

Before executing a run, check the model gateway configuration reported by `/readyz`:

```bash
curl http://localhost:8000/readyz
```

The response includes `checks.model_gateway.configured`, `missing`, direct
provider `base_url`/`model`, and provider-registry IDs/counts. It also includes
`checks.sandbox.configured`, `provider`, `controller_required`, aggregate
`controller_configured`, `controller_endpoint_configured`,
`controller_auth_configured`, and `missing` for sandbox controller preflight.
With the committed `.env.example`, model gateway readiness is `false` until
`TAROAI_MODEL_GATEWAY_MODEL` and either `TAROAI_MODEL_GATEWAY_API_KEY` or
`TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID` are set, or until
`TAROAI_MODEL_GATEWAY_PROVIDERS` contains at least one configured
OpenAI-compatible provider. Install validation compares the live model-gateway
verification JSON back to these readiness fields, so direct evidence must use
the same `base_url` and `model`, and provider-registry evidence must report one
of the configured provider IDs. If model gateway readiness is `false`,
`/api/runs/{run_id}/execute` will fail before planning with
`model_gateway_unavailable`.

When a real OpenAI-compatible provider is configured, verify the provider before requiring full run execution:

```bash
PYTHONPATH=apps/api/src python -m taroai.model_gateway.verification \
  --base-url "${TAROAI_MODEL_GATEWAY_BASE_URL:-https://api.openai.com/v1}" \
  --model "${TAROAI_MODEL_GATEWAY_MODEL}"
```

The verifier reads `TAROAI_MODEL_GATEWAY_API_KEY` from the environment by
default. Prefer environment variables over `--api-key` so provider credentials
do not appear in shell history or process arguments.
Its JSON result contains provider metadata, token counts, and planned tool
names only; it must not include raw provider API keys.

For DeepSeek or another OpenAI-compatible provider, keep provider credentials in
your shell or secret manager rather than in a committed file. DeepSeek documents
the OpenAI-compatible base URL as `https://api.deepseek.com`; verify the current
model catalog before a demo because provider model names can change.

```bash
export TAROAI_MODEL_GATEWAY_BASE_URL=https://api.deepseek.com
export TAROAI_MODEL_GATEWAY_MODEL=deepseek-v4-flash
export TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS='{"response_format":{"type":"json_object"},"thinking":{"type":"disabled"}}'
read -rsp "TAROAI_MODEL_GATEWAY_API_KEY: " TAROAI_MODEL_GATEWAY_API_KEY
export TAROAI_MODEL_GATEWAY_API_KEY
echo
```

The same non-secret profile is available at `infra/config/deepseek.env.example`.
Use it as a local template for runtime config. For Compose strict E2E, source
the profile in the same shell before `docker compose up` and then export the
real API key through the shell prompt above. For standalone verification, inject
the real API key only through your shell and let the verifier read `DEEPSEEK_API_KEY`:

```bash
set -a
. infra/config/deepseek.env.example
set +a
export DEEPSEEK_API_KEY=<your-provider-api-key>
PYTHONPATH=apps/api/src python -m taroai.model_gateway.verification
```

For provider-registry based deployments, pass the same JSON shape used by `TAROAI_MODEL_GATEWAY_PROVIDERS`:

```bash
PYTHONPATH=apps/api/src python -m taroai.model_gateway.verification \
  --tenant-id tenant_acme \
  --workspace-id workspace_acme \
  --providers-json "${TAROAI_MODEL_GATEWAY_PROVIDERS}"
```

If the provider registry uses `api_key_secret_ref_id`, keep the provider JSON secret-reference-only and pass the verification-only value map separately:

```bash
PYTHONPATH=apps/api/src python -m taroai.model_gateway.verification \
  --tenant-id tenant_acme \
  --workspace-id workspace_acme \
  --providers-json "${TAROAI_MODEL_GATEWAY_PROVIDERS}" \
  --secret-value-env-json '{"secret_sales_model_key":"TAROAI_MODEL_GATEWAY_API_KEY"}'
```

The verifier sends one `/chat/completions` planning request and requires a strict JSON response that parses into at least one planned step containing the expected `planning.record` tool. For the DeepSeek profile, the verifier and runtime template add `response_format={"type":"json_object"}` plus `thinking={"type":"disabled"}` through the Pydantic `TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS` field. The API key is accepted only as input and is excluded from the printed verification result; provider-registry verification also excludes provider API keys and verification-only secret values from the printed config/result and reports the selected `provider_id`.

## Local PoC Smoke Verifier

After Compose is healthy, run the verifier from the host:

```bash
scripts/verify-local-cloud-poc.sh \
  --api-base-url http://localhost:8000 \
  --browser-base-url http://localhost:8001 \
  --web-base-url http://localhost:3000 \
  --output .local-cloud-poc-result.json \
  --bootstrap-token ${TAROAI_TENANT_BOOTSTRAP_TOKEN:-local_bootstrap_token}
```

For the alternate host ports above:

```bash
scripts/verify-local-cloud-poc.sh \
  --api-base-url http://localhost:8800 \
  --browser-base-url http://localhost:8801 \
  --browser-controller-api-key "${TAROAI_BROWSER_CONTROLLER_API_KEY}" \
  --web-base-url http://localhost:3300 \
  --browser-workspace-url http://web \
  --browser-workspace-api-base-url http://api:8000 \
  --browser-workspace-submit-message "Generate a hello report in the sandbox." \
  --bootstrap-token ${TAROAI_TENANT_BOOTSTRAP_TOKEN:-local_bootstrap_token}
```

The verifier checks API health/readiness, `/readyz.checks.sandbox.configured`,
workspace HTML, CREAO-compatible chat/composer selectors, bootstrap/login controls, run
cancel/retry controls, run history controls, run trace controls, runtime state
controls, artifact preview controls, the frontend `/readyz` model/sandbox
preflight script, frontend Bearer-auth script behavior, tenant bootstrap,
owner login, tenant readiness, run creation, expected
`model_gateway_unavailable` execution diagnostics when no model provider is
configured, direct sandbox command execution with storage-backed output URI,
API browser screenshot capture resolved to a `browser` storage object and
downloaded as PNG through `/api/storage/objects/{id}/content`, and a real
browser-controller navigation/extract action against a `data:` page. It stops
before run creation when sandbox readiness is missing or reports
`configured=false`.
Failure messages redact response-body secrets such as access tokens, passwords,
Bearer headers, and credentialed URLs before they are printed in local CI or
operator terminals.
The verifier result JSON is also rendered through the same redaction layer, so
accidentally added `*_token`, `*_api_key`, `*_password`, `*_secret`,
connection-string, signed-URL, or bearer fields are masked before stdout or a
file is written. Use `--output <path>` to write that redacted result JSON
atomically for release evidence; stdout is used when `--output` is omitted.
The result also includes machine-readable readiness rollups:
`local_smoke_ready`, `strict_model_ready`, `workspace_execution_ready`,
`skill_reuse_ready`, `demo_ready`, and `demo_readiness_summary`. Treat
`demo_ready=true` as the internal demo gate; a no-model PoC smoke can still
report `local_smoke_ready=true` with `demo_readiness_summary="local smoke ready; model gateway missing"`.
`local_smoke_ready=true` requires the direct sandbox command output to resolve
to a downloadable storage object, the API browser screenshot to resolve to a
downloadable storage object, browser session list/read/delete scope probes to
pass, and the sandbox destroy plus post-destroy command-block probe to pass.
The saved demo gate report adds `required_gates`, `failed_required_gates`, and
`gate_results` so reviewers can distinguish a local PoC smoke gate from a
stricter workspace, skill-reuse, browser-governance, or hardened-sandbox
release gate and inspect each gate's pass/fail value without re-parsing the full
verifier output.
For the hardened sandbox gate, `sandbox_governance_ready=true` requires
controller/readiness evidence for network isolation, filesystem isolation,
resource limits, destroy support, session TTL, global/tenant/run capacity,
runtime isolation, image-policy enforcement with at least one allowed image, and
direct lifecycle evidence that the smoke sandbox was destroyed and rejected a
post-destroy command probe.
If the saved verifier result is malformed, the demo gate keeps the failure
machine-readable while redacting secret-shaped values from validation errors.
The demo gate writer and stdout formatter also redact the final report body, so
manually constructed or future error fields cannot bypass the evidence redaction
boundary when the report is saved or printed.
To validate a saved hardened-sandbox result without rerunning the stack:

```bash
scripts/verify-local-cloud-demo-ready.sh \
  dist/local-cloud-poc-strict-e2e-result.json \
  --require-workspace-execution \
  --require-skill-reuse \
  --require-browser-controller-governance \
  --require-sandbox-governance \
  --output dist/local-cloud-poc-demo-gate-result.json
```

The verifier config and result JSON are backed by strict Pydantic schemas, so
unknown or mistyped option/evidence fields fail validation instead of being
silently ignored.
The direct sandbox smoke also destroys the sandbox session and requires the
destroy response body to report `status=destroyed`. It then sends a same-session
command probe and requires a client-error response before recording
`sandbox_post_destroy_command_blocked=true`.

When `--browser-workspace-url` is provided, the verifier also asks the browser
controller to load the actual workspace and extract `[data-testid="chat-column"]`;
in Docker Compose, use the browser-controller-visible service URL `http://web`
rather than the host URL. When `--browser-workspace-api-base-url` is also
provided, the verifier fills the workspace bootstrap controls inside Chromium,
submits the local bootstrap token through the UI, requires bootstrap status to
reach `Tenant ready`, confirms the token input is cleared, confirms the visible
tenant/user/workspace inputs match the bootstrapped context, then verifies the
page auth status reaches `Bearer` and extracts the UI preflight
status/model/sandbox text after login;
in Docker Compose, use `http://api:8000` because the browser runs inside the
Compose network. When `--require-model-execution` is set together with browser
workspace verification, the UI preflight strip must reach `Preflight ready`,
`Model ready`, and a loaded sandbox status (`Sandbox PoC: <provider>`,
`Sandbox isolated: <provider>`, or the legacy `Sandbox ready: <provider>`);
stale `Preflight needs config` fails strict verification even when backend
`/readyz` is otherwise configured.

When `--browser-workspace-submit-message` is provided, the verifier types that
message into the composer, clicks Send, and requires the conversation log to
include `model gateway model is not configured` by default; use
`--browser-workspace-submit-expected-text` for a different expected UI result.
Submit verification requires both `--browser-workspace-url` and
`--browser-workspace-api-base-url`, so a configured browser Workspace submit
cannot be silently skipped. `--browser-workspace-api-base-url` is invalid
without `--browser-workspace-url`, because the verifier would otherwise have no
page to load before filling login controls. When `--require-model-execution` is
combined with a browser Workspace target, `--browser-workspace-api-base-url` is
required; when the expected submit text is `succeeded`, the submit message is
also required as strict UI execution evidence.
The UI login and submit poll intervals are controlled separately by
`--browser-workspace-auth-poll-interval-seconds` and
`--browser-workspace-submit-poll-interval-seconds`; both default to `0.25`.
Browser Workspace submit status uses an independent
`--browser-workspace-submit-poll-attempts` window, defaulting to `30`, because
real model planning and UI polling can take longer than the API run-status
polling loop.

Add `--require-model-execution` only after a real OpenAI-compatible model
provider is configured; with this flag, the verifier reads
`/readyz.checks.model_gateway` first and stops before run creation when
readiness reports missing model gateway fields. In strict mode the default run
request asks the model to use `sandbox.command` to create
`/workspace/artifacts/report.md`, then requires the run execution API to return
HTTP 200 without an execution error code, the run to finish with `succeeded`
status, publish the required `report.md` artifact, resolve every run artifact to
a storage object, download every artifact storage object through
`/api/storage/objects/{id}/content` with non-empty content, find the required
phrase `local cloud PoC execution path` in the required artifact, confirm
`/api/runs/{run_id}/state` reports the succeeded status, sandbox session ID,
completed steps, and promoted `/workspace/artifacts/report.md` path, confirm the
run event stream contains `sandbox.command.executed` with `exit_code=0` plus
`sandbox.artifact.promoted` matching the required artifact's downloaded storage
object even when the run also promotes additional artifacts later, confirm
`/api/runs/{run_id}/trace` contains a `runtime.tool_call` span,
`tool_call_count` billing meter, `tool.executed` audit event, and trace events,
and reject event or trace payloads that expose raw `stdout` or `stderr` fields
instead of safe length summaries. The Workspace contract check also rejects
frontend terminal code that renders raw command stream fields instead of the
same safe summary.
When Workspace browser submission is enabled, the verifier
also extracts `[data-evidence-summary]` and requires `Artifact delivery proven`
so the frontend demonstrates the same plan, sandbox, terminal, and artifact
evidence that the backend gate validated. It also extracts
`[data-trace-status]`, `[data-trace-span-count]`,
`[data-trace-event-count]`, `[data-trace-billing-count]`,
`[data-trace-audit-count]`, and `[data-trace-error-classification]`, and
requires those visible Workspace values to match the API trace evidence. It then
extracts
`[data-terminal-output]` and requires the visible terminal to show safe
`stdout`/`stderr` byte summaries, then clicks the artifact Preview control and
requires `[data-artifact-preview-content]` to contain the configured required
artifact phrase. The Workspace execution loop also surfaces the safe model route
summary derived from the `plan.created` or `model.plan.created` event, including provider, model, total
tokens, and fallback attempt count when those fields are available; strict
browser Workspace verification extracts that route and fails if it remains
empty or pending after a model-backed submission, and it matches the visible
route against the API planning event when that event includes route
evidence. When a strict browser Workspace check selects a skill run from
Run History, the same verifier also compares the selected history route strip
with that run's API `plan.created` or `model.plan.created` evidence when route
evidence exists. The bundled verifier fixtures now include that `plan.created`
event in
the strict model run stream before sandbox command execution, matching the
Runtime event order; when that planning event is present, the Workspace event
integrity closure shows `plan -> command -> artifact -> succeeded` instead of
collapsing the chain to command/artifact/success only. If a model-backed run
also emits `browser.action.performed`, the same closure includes the browser
stage in its observed order before terminal success, so browser-assisted runs
can show the visible browser step as part of the delivery proof. Use
`--model-artifact-required-name` and
`--model-artifact-required-text` when testing a custom run prompt with a
different expected artifact.
The strict browser Workspace path also clicks the current run feedback control
after artifact delivery and then reads `/api/customer-success/feedback`,
requiring a `thumbs_rating` record whose `run_id` and `target_id` both match the
current run. This keeps the visible "Feedback recorded" state tied to persisted
Customer Success evidence instead of a UI-only status change.
The same strict path submits the configured missing-skill request repeatedly and
then requires `/api/customer-success/feedback` to contain at least that many
`missing_skill` records for the configured solution pack and skill name before
candidate generation is accepted. When the Workspace reports evaluation
candidate generation, the verifier reads
`/api/customer-success/evaluation-candidates` and requires a pending candidate
for the current run before it proceeds to human review. When solution-pack
candidate generation is reported, the verifier also reads
`/api/customer-success/solution-pack-candidates` and requires a pending
candidate for the configured solution pack and skill name before draft review.
After accepting an evaluation candidate, the verifier re-reads
`/api/customer-success/evaluation-candidates` and requires an `accepted`
candidate for the current run with the evaluation case ID shown in the
Workspace review status. After accepting a solution-pack candidate, it re-reads
`/api/customer-success/solution-pack-candidates` and requires an `accepted`
candidate with the publication draft ID shown in the Workspace review status.
After the draft save/submit/approve/apply flow, it also reads
`/api/customer-success/solution-pack-drafts` and requires that same draft to be
`applied`, marked as production-applied, and carrying the configured skill
manifest and pack version. When the Workspace reports solution-pack
installation, the verifier now also reads `/api/solution-pack-installations` and
`/api/workspaces/{workspace_id}/skills` before accepting the installed status.
For Workspace skill invocation, the verifier also reads the UI-reported
`/api/runs/{run_id}` and requires that run to belong to the bootstrapped
workspace and record the invoked skill as its `agent_id` before artifact and
event evidence are accepted. The selected skill run's event stream must also
emit `skill.workflow_invoked` with the same `skill_id`, so the invocation audit
trail is tied to the artifact and terminal evidence instead of only to run
metadata. Workspace event-integrity labels include that invocation stage as
`skill -> command -> artifact -> succeeded` when skill evidence is present, and
the verifier rejects selected skill runs where the command event appears before
the invocation event.

## Docker Sandbox Provider Verification

The default local PoC uses `local_process`. Where a Docker daemon is available, verify the Docker sandbox provider directly before enabling `TAROAI_SANDBOX_PROVIDER=docker`:

```bash
PYTHONPATH=apps/api/src python -m taroai.sandbox.docker_verification \
  --root-dir /tmp/taroai/docker-sandbox-verify \
  --image python:3.12-slim \
  --memory-limit 512m \
  --cpus 0.5 \
  --pids-limit 96 \
  --container-user 65532:65532
```

The verifier creates a disabled-network Docker sandbox session, uploads an input file, executes a command as the configured non-root container user, writes `/workspace/artifacts/report.txt`, downloads the artifact, creates a snapshot, and destroys the container. The result includes the container name, hardening settings, file paths, snapshot URI, and `destroyed=true`. This proves the local Docker adapter can write to the bind-mounted workspace in Docker user namespace/rootless environments while avoiding root container processes; it does not replace Kubernetes, E2B, or microVM-backed isolation for shared enterprise execution.
Runtime cleanup attempts are also visible in the run event stream. A successful
cleanup emits `sandbox.session.destroyed` or `browser.session.destroyed`; a
provider cleanup error emits `sandbox.session.destroy_failed` or
`browser.session.destroy_failed` with safe metadata and does not hide already
generated artifacts or browser captures from the user. Treat any destroy
failure as an operator incident because sandbox or browser resources may still
need out-of-band cleanup.
The strict local cloud PoC verifier treats either cleanup failure event as a
failed acceptance gate even when the run itself reached `succeeded`, so release
evidence cannot silently ignore leaked execution resources.

When `--browser-controller-api-key` is configured, the verifier also sends
unauthenticated browser-controller probes to `GET /sessions?tenant_id=...`,
global `GET /sessions`, and `GET /capabilities`, requiring all three to return
`401` or `403`. A controller that accepts any probe is rejected, even if the
normal authenticated browser smoke path succeeds. The result JSON records the
tenant session-list, global session-list, and capabilities challenge outcomes
as separate fields in addition to the aggregate browser-controller auth flag.
When `TAROAI_SANDBOX_CONTROLLER_API_KEY` is configured, the sandbox lifecycle
verifier sends unauthenticated probes to `GET /sessions?tenant_id=...`,
global `GET /sessions`, and `GET /capabilities`; all three must return `401` or
`403` before sandbox controller auth evidence is accepted.
The verifier also calls authenticated `GET /capabilities` and fails the gate
when the controller does not declare session TTL enforcement plus global,
tenant, and run-level session capacity limits. Navigation allowlist state is
recorded as evidence; local developer stacks may leave it unrestricted.

## Kubernetes Sandbox Provider Verification

When a Kubernetes namespace and RBAC for the sandbox-controller are available,
verify the kubectl-backed provider before setting
`TAROAI_SANDBOX_CONTROLLER_PROVIDER=kubernetes` in a shared environment:

```bash
scripts/verify-kubernetes-sandbox.sh \
  --namespace taroai \
  --service-account-name sandbox-runner \
  --runtime-class-name gvisor \
  --runtime-class-required \
  --image "ghcr.io/customer/sandbox-runtime@sha256:<digest>" \
  --allowed-image "ghcr.io/customer/sandbox-runtime@sha256:*" \
  --memory-limit 512Mi \
  --cpu-limit 500m \
  --ephemeral-storage-limit 1Gi \
  --run-as-user 65532 \
  --run-as-group 65532 \
  --verify-runtime-policy
```

The same verifier can be called directly:

```bash
PYTHONPATH=apps/api/src python -m taroai.sandbox.kubernetes_verification \
  --namespace taroai \
  --service-account-name sandbox-runner \
  --verify-runtime-policy
```

The verifier creates a disabled-network Kubernetes sandbox session, applies a
per-session Pod and deny-all NetworkPolicy, uploads an input file, executes the
configured command through `kubectl exec`, writes
`/workspace/artifacts/report.txt`, downloads the artifact, records a snapshot
URI, verifies cross-workspace/run command and file writes are rejected, and
destroys the Pod plus NetworkPolicy. The JSON output includes the
namespace, Pod name, NetworkPolicy name, resource limits, runtime class, file
paths, configured image patterns, runtime-class requirement, snapshot URI, and
`destroyed=true`. Shared environment evidence should use an approved sandbox
runtime image from a configured registry or a digest-pinned reference; generic
local verifier images such as `python:3.12-slim` are not accepted by the private
install gate as customer sandbox runtime evidence. When `--verify-runtime-policy`
is set, it first reads the
namespace, `ResourceQuota`, `LimitRange`, and default-deny `NetworkPolicy`
through `kubectl get -o json` and includes a `runtime_policy` evidence object
with the Pod Security labels, hard quota, default requests/limits, maximum
container limits, and default-deny network policy state. A successful result is cluster
evidence that the packaged Kubernetes provider path can execute the artifact
workflow; it is still not a substitute for a full customer environment review of
runtime class isolation, egress policy, image allowlists, and namespace quotas.
The Kubernetes verifier JSON must not contain kubeconfig content, service
account tokens, or other raw cluster credentials.

## Latest Local Verification Snapshot

As of 2026-07-04, the local Compose stack has been verified with alternate host ports for Web, API, browser-controller, PostgreSQL, Redis, and MinIO. The local PoC verifier passed against `http://localhost:8800`, `http://localhost:8801`, and `http://localhost:3300` with Chromium loading `http://web`, logging into the workspace through `http://api:8000`, reaching `Bearer` auth status, submitting a message through the composer, and surfacing `succeeded` after strict model execution. The direct sandbox smoke path produced a storage-backed command output URI through MinIO, and the API browser screenshot path produced a `browser` storage object downloaded as a PNG through the storage content API.

The Docker sandbox verifier also passed with `python:3.12-slim`, `--network none`, non-root `65532:65532`, `512m` memory, `0.5` CPU, `96` pids, read-only rootfs, `cap-drop=ALL`, `no-new-privileges:true`, and tmpfs-backed `/tmp`; the verifier wrote and downloaded `/workspace/artifacts/report.txt`, created a snapshot, and destroyed the container.

The dependency verifiers also passed against the same alternate-port Compose stack:
PostgreSQL migration/RLS verification confirmed tenant isolation and no-context
workspace invisibility, Redis queue verification confirmed ping, claim/ack,
expired-lease recovery, and dead-letter behavior, Redis short-term memory
verification confirmed TTL-backed tenant/run isolation and cleanup, and the
MinIO/S3-compatible object storage verifier uploaded, downloaded, signed,
deleted, and confirmed removal of a temporary object in `taroai-artifacts`.
The Redis queue verification JSON records lifecycle evidence only and does not
include the raw Redis URL.

The strict model execution gate passed in this local environment with a real OpenAI-compatible provider configured. The verifier created a run, received a model plan, executed `sandbox.command`, promoted `/workspace/artifacts/report.md`, downloaded the storage-backed artifact, confirmed the required text, verified the runtime state snapshot, verified trace, billing, and audit evidence, verified safe event and trace payloads without raw `stdout`/`stderr`, loaded the Workspace in Chromium, submitted a prompt through the composer, and observed `succeeded` in the UI. If `/readyz.checks.model_gateway` reports missing `model` or `credential`, `local_cloud_poc_verification --require-model-execution` still fails immediately after `/readyz` and reports the missing fields without creating a run.

## Migrations

The API entrypoint runs migrations when `TAROAI_RUN_MIGRATIONS=true`. For an
operator-style dry run, generate a migration plan first:

```bash
docker compose --env-file .env -f infra/docker-compose.yml run --rm api \
  sh -lc 'python -m taroai.db.migration_cli \
    --database-url "$TAROAI_DATABASE_URL" \
    --migrations-path /app/migrations'
```

Apply migrations explicitly after reviewing the plan:

```bash
docker compose --env-file .env -f infra/docker-compose.yml run --rm api \
  sh -lc 'python -m taroai.db.migration_cli \
    --database-url "$TAROAI_DATABASE_URL" \
    --migrations-path /app/migrations \
    --apply'
```

PostgreSQL RLS verification from the host after PostgreSQL is healthy:

```bash
PYTHONPATH=apps/api/src python -m taroai.db.postgresql_verification \
  --database-url postgresql://taroai_app:taroai_app@localhost:${POSTGRES_PORT:-5432}/taroai \
  --migrations-path apps/api/migrations
```

The verifier applies pending migrations, checks that tenant-scoped tables have RLS and force RLS enabled, writes two temporary tenant/workspace records, confirms each tenant sees only its own workspace, and confirms a query without tenant context sees no workspace rows.

Redis worker queue verification from the host after Redis is healthy:

```bash
PYTHONPATH=apps/api/src python -m taroai.workers.redis_verification \
  --redis-url redis://localhost:${REDIS_PORT:-6379}/0
```

The verifier pings Redis, submits a run-execution job through the Redis queue adapter, claims and acknowledges it, verifies expired-lease recovery for a crashed worker scenario, submits another job through the dead-letter path, lists the dead-letter queue, and removes keys under its verification prefix.

Redis short-term memory verification from the host after Redis is healthy:

```bash
PYTHONPATH=apps/api/src python -m taroai.memory.redis_verification \
  --redis-url redis://localhost:${REDIS_PORT:-6379}/0
```

The verifier pings Redis, writes run-scoped short-term memory entries with TTL, checks tenant/run visibility, lists entries for the run, deletes one key, deletes the remaining entries for the tenant, and removes keys under its verification prefix.

MinIO/S3-compatible object storage verification from the host after MinIO and `minio-init` are healthy:

```bash
PYTHONPATH=apps/api/src python -m taroai.storage.object_storage_verification \
  --endpoint-url http://localhost:${MINIO_API_PORT:-9000} \
  --bucket ${TAROAI_OBJECT_STORAGE_BUCKET:-taroai-artifacts} \
  --region ${TAROAI_OBJECT_STORAGE_REGION:-us-east-1} \
  --access-key-id ${TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID:-taroai_minio} \
  --secret-access-key ${TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY:-taroai_minio_password}
```

The verifier checks bucket access, uploads a temporary object, downloads and compares the bytes, generates read and write signed URLs, deletes the object, verifies it is no longer visible, and removes its verification object. The JSON result records only signed URL methods, not the raw signed URLs.

Optional OTLP HTTP trace collector verification after `TAROAI_TRACE_EXPORTER_ENDPOINT_URL` is configured:

```bash
PYTHONPATH=apps/api/src python -m taroai.observability.verification \
  --endpoint-url "${TAROAI_TRACE_EXPORTER_ENDPOINT_URL}" \
  --api-key "${TAROAI_TRACE_EXPORTER_API_KEY}" \
  --service-name "${TAROAI_TRACE_EXPORTER_SERVICE_NAME:-taroai-api}" \
  --deployment-environment "${TAROAI_ENVIRONMENT:-local-cloud-poc}"
```

The verifier sends one `trace.collector.verify` span through the OTLP HTTP
exporter and prints a redacted JSON result suitable for private install
validation through `--trace-collector-verification`.

When using the alternate host ports from the startup example, run the dependency verifiers with those ports:

```bash
PYTHONPATH=apps/api/src python -m taroai.db.postgresql_verification \
  --database-url postgresql://taroai_app:taroai_app@localhost:55432/taroai \
  --migrations-path apps/api/migrations

PYTHONPATH=apps/api/src python -m taroai.workers.redis_verification \
  --redis-url redis://localhost:56379/0

PYTHONPATH=apps/api/src python -m taroai.memory.redis_verification \
  --redis-url redis://localhost:56379/0

PYTHONPATH=apps/api/src python -m taroai.storage.object_storage_verification \
  --endpoint-url http://localhost:59000 \
  --bucket taroai-artifacts \
  --region us-east-1 \
  --access-key-id taroai_minio \
  --secret-access-key taroai_minio_password
```

## Verification

```bash
curl -f http://localhost:8000/healthz
curl -f http://localhost:8000/readyz
curl -f http://localhost:8001/healthz
curl -f http://localhost:3000/
curl -f http://localhost:9000/minio/health/ready
```

For object storage, use the API storage endpoints after login:

1. `POST /api/storage/objects`
2. `POST /api/storage/objects/{storage_object_id}/content`
3. `POST /api/storage/objects/{storage_object_id}/signed-url`
4. `DELETE /api/storage/objects/{storage_object_id}`

## Shutdown

```bash
docker compose --env-file .env -f infra/docker-compose.yml down
```

To remove local volumes:

```bash
docker compose --env-file .env -f infra/docker-compose.yml down -v
```

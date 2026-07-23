# Private Deployment and Packaging Implementation Plan


**Goal:** Prepare the platform for BYOC, VPC, and private deployment by defining packaging, configuration, license checks, install validation, upgrades, air-gapped constraints, and customer-operated runbooks.

**Architecture:** Cloud SaaS remains the first deployment mode, but private delivery should reuse the same service boundaries. Packaging provides Helm/Kubernetes overlays, Docker images, migration jobs, config templates, license activation, health checks, backup hooks, and upgrade plans. Runtime providers, model providers, object storage, database, Redis, and secrets manager are pluggable through Pydantic settings and environment variables.

**Tech Stack:** Docker, Kubernetes, Helm, FastAPI, static Web Workspace, Web Workspace package contract, PostgreSQL, Redis, S3-compatible storage, secret manager adapters, pytest, CI validation.

---

## Summary

Many enterprise customers will ask for VPC or private deployment after PoC. This plan extends deployment operations into productized packaging and customer-operated installs without forcing a rewrite of the cloud-first architecture.

## Task 1: Deployment Package Manifest

**Files:**

- Create: `infra/package/manifest.schema.json`
- Create: `infra/package/README.md`
- Create: `apps/api/src/taroai/deployment/__init__.py`
- Create: `apps/api/src/taroai/deployment/models.py`
- Test: `tests/api/test_deployment_package_manifest.py`

**Steps:**

1. Define `DeploymentPackageManifest` Pydantic model with package version, app version, image list, migrations, config keys, dependency versions, and compatibility matrix.
2. Define package targets: cloud, byoc, vpc, private, and air_gapped.
3. Validate required services: API, worker, database, Redis, object storage, sandbox provider, browser controller, Web Workspace, model gateway, and secrets manager.
4. Add tests for valid manifest, missing image, incompatible migration, and unsupported target.
5. Document package contents.

**Acceptance Criteria:**

- Deployment package has a typed manifest.
- Operators can see exactly what will be installed.

**Current Implementation Notes:**

- `apps/api/src/taroai/deployment/` now contains the Pydantic deployment package manifest models for targets, required services, images, migrations, config keys, dependency versions, and compatibility rules.
- `infra/package/manifest.schema.json` and `infra/package/README.md` define the operator-facing manifest contract and required service list, including `web_workspace` so customer-facing packages can align release contents with install validation.
- The manifest validates required API and worker images, required platform services, unique list entries, migration version ranges, and compatibility version ranges. Manifest and schema CLI outputs use temporary-file writes plus atomic replacement so interrupted generation does not corrupt existing package contract files.
- Config profiles, license checks, install validation, upgrade/rollback runbooks, and air-gapped packaging are tracked in later tasks; license checks now have a first runtime integration.

## Task 2: Config Profiles and Environment Validation

**Files:**

- Modify: `apps/api/src/taroai/config.py`
- Create: `infra/config/cloud.env.example`
- Create: `infra/config/byoc.env.example`
- Create: `infra/config/private.env.example`
- Test: `tests/api/test_deployment_config_profiles.py`

**Steps:**

1. Add deployment mode settings: cloud, byoc, vpc, private, and air_gapped.
2. Validate required settings per mode.
3. Disallow external model/sandbox providers in air-gapped mode unless explicitly configured as internal endpoints.
4. Add settings for external URL, callback URL, storage region, sandbox region, and secret manager type.
5. Add tests for missing required private settings and valid cloud defaults.

**Acceptance Criteria:**

- Misconfigured private deployments fail early.
- `.env` examples match Pydantic settings.

**Current Implementation Notes:**

- `Settings` now includes `deployment_mode`, deployment external/callback URLs, deployment storage/sandbox regions, and deployment secret manager type.
- Customer-operated modes (`byoc`, `vpc`, `private`, `air_gapped`) validate required operator URLs, durable SQL/Redis settings, non-local secret manager type, and storage/sandbox region alignment.
- `air_gapped` mode rejects public Model Gateway endpoints, E2B sandbox usage, Browserbase browser usage, and non-internal browser controller endpoints.
- `infra/config/cloud.env.example`, `infra/config/byoc.env.example`, `infra/config/private.env.example`, and `.env.example` are parseable through the Pydantic settings contract.
- The committed package carries `.env.example` as the local cloud PoC template and expects `.env` to be a local-only override. `.gitignore` keeps `.env` out of the package, and the deployment contract test asserts that a checked-in `.env` is absent so it cannot override the template's `TAROAI_SANDBOX_PROVIDER=local_process`.
- License checks, install validation, upgrade/rollback runbooks, and air-gapped package transfer remain planned.

## Task 3: Helm/Kubernetes Packaging Path

**Files:**

- Create: `infra/helm/taroai/Chart.yaml`
- Create: `infra/helm/taroai/values.yaml`
- Create: `infra/helm/taroai/templates/README.md`
- Test: local template validation where Helm is available, otherwise manifest file tests.

**Steps:**

1. Define chart structure for API, workers, migrations, config, secrets refs, ingress, service accounts, network policies, and the static Web Workspace package surface. Full portal/admin/marketplace frontend packaging remains a later phase.
2. Keep secrets as external references, not literal values in chart defaults.
3. Add values for resource requests, autoscaling, persistence, and node selectors.
4. Document how cloud PoC differs from private deployment.
5. Add CI validation plan for `helm template`.

**Acceptance Criteria:**

- Private deployment has a concrete packaging path.
- Helm defaults do not contain secrets.

**Current Implementation Notes:**

- `infra/helm/taroai/Chart.yaml` and `values.yaml` start the Helm packaging path for API, worker, Web Workspace, migration, config, ingress, service account, autoscaling, and network policy settings.
- `infra/helm/taroai/templates/` contains first-pass API, worker, Web Workspace, migration Job, ConfigMap, external Secret reference, Ingress, HPA, ServiceAccount, and NetworkPolicy templates plus a template README.
- Helm defaults keep `secrets.create=false` and reference `secrets.existingSecret`; chart defaults define key names but do not carry literal secret values.
- The Web Workspace chart path uses the `taroai-web` image, gates deployment with `web.enabled`, avoids runtime secret mounts, and gives private packages a customer-facing workspace surface aligned with install validation. Release package verification also requires API/browser-controller image build inputs including requirements files, entrypoint, baseline migrations, script-backed verifier modules, the browser-controller service module, the release signing script, the transfer evidence script/module, and the support bundle redaction module/script, the Web Workspace Dockerfile, `index.html`, `assets/main.js`, `assets/styles.css`, the core Kubernetes/Helm manifests for API, worker, browser-controller, Web Workspace, backing services, config, network policy, migration, ingress, autoscaling, and service accounts, top-level release metadata such as `README.md`, `pyproject.toml`, and `.env.example`, plus package metadata, upgrade matrix, env profiles, and local/private/air-gapped/DR/offboarding/trigger runbooks.
- Release signing now has a packaged `scripts/sign-release-package.sh` CLI backed by the same Pydantic release package module. It creates detached Ed25519 signature envelopes over the archive SHA256, reads the private key from an operator-selected environment variable, and prints only the public key, key id, signature path, and package checksum for transfer evidence. Release package build/sign/verify/report models now reject unknown option or evidence fields, release package building writes to a temporary archive and atomically replaces the final zip only after success, signature writing uses the same temporary-file replace behavior for detached signature envelopes, and the build scans included source files before writing the archive and rejects secret-shaped provider keys, private key blocks, and credentialed URLs with path-only diagnostics, so accidental local secret material cannot be emitted and then caught only by post-build verification.
- Release transfer evidence now has a packaged `scripts/build-release-transfer-evidence.sh` CLI backed by Pydantic models. It verifies the archive and detached signature before writing an evidence JSON containing checksum, package/app versions, signature key id, public key, image count, migration count, and required service count without embedding the detached signature value or private key material. The evidence JSON is written through the shared temporary-file replace path so a failed evidence write preserves any existing transfer packet.
- Private install validation now accepts `--release-transfer-evidence` and uses the recorded checksum, detached signature path, key id, and public key as package integrity inputs before calling the release verifier. Explicit checksum/signature/key CLI arguments can still override evidence values when an operator needs to validate a relocated package or signature path.
- Customer-operated support bundle redaction now has a packaged `scripts/redact-support-bundle.sh` CLI backed by Pydantic report models. It rewrites support bundle zip files inside the customer boundary, redacts API-key-shaped values, bearer tokens, signed URLs, connection strings, JSON prompt/connector payload fields, plain-text sensitive assignments such as `prompt=...`, `connector_payload=...`, and `access_token=...`, plus sensitive header-style fields such as `Authorization`, `X-API-Key`, `Cookie`, and `Set-Cookie`, then writes evidence with entry names, categories, and counts only. The sanitized archive and evidence JSON now use temporary-file writes plus atomic replacement, preserving any previously approved handoff files if disk or write errors occur.
- Private install validation now includes a `support_bundle_redaction` check. Operators can pass `--support-bundle-redaction-evidence` so the Pydantic redaction report becomes part of the install validation evidence gate without copying original secret values, prompts, signed URLs, or connector payloads into the validation report.
- Release-grade Helm rendering in CI, chart signing/versioning, managed-service overlays, remaining license runtime integrations, install validation automation, upgrade/rollback automation, full portal/admin frontend packaging, and air-gapped packaging hardening remain planned.

## Task 4: License and Entitlement Checks

**Files:**

- Create: `apps/api/src/taroai/licensing/__init__.py`
- Create: `apps/api/src/taroai/licensing/models.py`
- Create: `apps/api/src/taroai/licensing/service.py`
- Create: `apps/api/src/taroai/licensing/signing.py`
- Test: `tests/api/test_license_entitlements.py`

**Steps:**

1. Define `LicenseKey`, `Entitlement`, `LicensedFeature`, and `LicenseStatus`.
2. Support offline and signed offline license file validation for private deployments.
3. Gate enterprise features such as SSO, SCIM, private connector count, sandbox concurrency, solution packs, and audit retention by entitlement.
4. Emit audit events when license status changes.
5. Add tests for valid license, expired license, missing entitlement, offline mode, trusted signed license files, tampered signed files, and untrusted signing keys.

**Acceptance Criteria:**

- Feature availability is explicit in private deployments.
- Offline customers can run without calling SaaS control plane.

**Current Implementation Notes:**

- `apps/api/src/taroai/licensing/` now defines Pydantic `LicenseKey`, `Entitlement`, `LicensedFeature`, `LicenseStatus`, validation result, and entitlement decision models.
- `LicenseService` validates license documents for deployment modes, validates offline license files without SaaS control-plane calls, validates Ed25519-signed offline license envelopes against configured trusted public keys, persists the active validation per tenant through the control-plane store for runtime checks, and gates SSO, SCIM, private connector count, sandbox concurrency, solution packs, and audit retention through entitlement decisions.
- `POST /api/licenses/import` imports a signed offline license envelope behind `licenses.manage`, rejects cross-tenant licenses before activation or audit, activates valid tenant licenses, and returns a sanitized response without signature material.
- Runtime license enforcement can be enabled through `TAROAI_LICENSE_RUNTIME_ENFORCEMENT_ENABLED`; connector creation now checks the active tenant license against `private_connector_count`, sandbox session creation checks `sandbox_concurrency` before opening another active session, API/worker audit writes check configured retention against `audit_retention_days`, solution pack installation checks `solution_packs`, SSO provider configure/enable checks `sso`, and SCIM provider configure/enable/import checks `scim`.
- License status changes emit `license.status_changed` audit events with license id, status, deployment mode, source, reason, and entitlement count; successful imports emit `license.imported` with actor attribution, and the default audit coverage matrix includes both events.
- OIDC/SAML protocol login, assertion validation, full SCIM v2 service-provider compatibility, MFA, and account reactivation flows remain planned.

## Task 5: Install Validation and Smoke Tests

**Files:**

- Create: `docs/operations/private-install-validation.md`
- Create: `tests/api/test_install_validation_contract.py`
- Modify: `scripts/validate-install.sh`

**Steps:**

1. Define validation checks: database migration, Redis connectivity, object storage read/write, secret manager read, OpenAI-compatible Model Gateway health call, sandbox health, browser-controller health, Web health, API health, event stream, worker queue, audit write, trace collector, backup restore drill, and runtime closed-loop evidence.
2. Represent validation results as Pydantic `InstallValidationReport`.
3. Document expected outputs for customer operators.
4. Add tests for report model and failure summary.
5. Add the executable validation wrapper that runs the Pydantic install validator and exits non-zero for failed or skipped readiness outcomes.

**Acceptance Criteria:**

- Operators can prove installation is ready.
- Validation failures point to specific dependencies.

**Current Implementation Notes:**

- `apps/api/src/taroai/deployment/validation.py` defines Pydantic `InstallValidationReport`, `InstallValidationCheck`, required install check names, status computation, readiness state, and dependency-specific failure summaries.
- Required checks cover database migration, Redis connectivity, object storage read/write, secret manager read, OpenAI-compatible Model Gateway health, sandbox lifecycle health, browser-controller lifecycle health, Web Workspace health, API health, event stream, worker queue, audit write, trace collector, backup restore drill, and runtime closed-loop evidence. Web Workspace health is skipped when `--web-base-url` is omitted for API-only deployments, and validates web workspace HTML plus `assets/main.js` chat, login controls, composer controls, readiness, Bearer-auth, browser, artifact, and storage-download contract when supplied.
- `docs/operations/private-install-validation.md` documents expected report shape, required checks, failure remediation fields, Secret Manager evidence through `--secret-manager-verification`, authenticated event-stream evidence through `--event-stream-verification`, authenticated audit-write evidence through `--audit-write-verification`, required non-sensitive API evidence traceability fields such as `api_base_url`, `run_id`, and `first_event_sequence`, Model Gateway evidence through `--model-gateway-verification`, sandbox lifecycle evidence through `--sandbox-verification`, browser-controller lifecycle evidence through `--browser-controller-verification`, web workspace HTML evidence through `--web-base-url`, runtime closed-loop demo-gate evidence through `--runtime-closed-loop-evidence`, dependency evidence commands through `scripts/build-migration-plan.sh`, `scripts/verify-object-storage.sh`, `scripts/verify-redis-queue.sh`, `scripts/verify-secret-manager.sh`, `scripts/verify-event-stream.sh`, `scripts/verify-audit-write.sh`, `scripts/verify-model-gateway.sh`, `scripts/verify-sandbox-lifecycle.sh`, `scripts/verify-browser-controller.sh`, `scripts/verify-trace-collector.sh`, `scripts/verify-restore-drill.sh`, `scripts/verify-local-cloud-poc.sh`, and `scripts/verify-local-cloud-demo-ready.sh`, plus `scripts/validate-install.sh`. Cloud acceptance now fails missing release-package, migration-plan, Redis/worker queue, object storage, secret manager, configured Model Gateway, configured sandbox lifecycle, browser readiness, configured browser-controller lifecycle, event-stream, audit-write, trace-collector, restore-drill, and runtime closed-loop evidence instead of skipping them, while cloud, production, and customer-operated install validation rejects legacy event-stream evidence without `api_base_url`, `run_id`, and `first_event_sequence`, and legacy audit-write evidence without `api_base_url` and `run_id`.
- Customer-operated sandbox and browser controller validation requires generated runtime secrets through `TAROAI_SANDBOX_CONTROLLER_API_KEY` and `TAROAI_BROWSER_CONTROLLER_API_KEY`; local PoC defaults are not accepted for private, VPC, BYOC, or production deployment profiles.
- `/readyz.checks.browser` now reports provider, controller endpoint/auth configuration, missing fields, configured state, and controller capability declarations so disabled browser providers are distinguished from enabled but misconfigured or unreachable browser-controller deployments. Install validation rejects controller-required browser readiness unless capability discovery succeeds and declares auth, TTL, and global/tenant/run capacity controls.
- `scripts/validate-install.sh` wraps `python -m taroai.deployment.install_validation`, performs live API health/readiness probing plus browser-controller health through `/readyz.checks.browser` and `/healthz` when configured, optionally checks Web Workspace health through `--web-base-url`, consumes release/package/migration/dependency plus strict demo-gate evidence JSON, writes an `InstallValidationReport` through a temporary file plus atomic replacement, and exits non-zero for failed or skipped readiness outcomes.
- `scripts/verify-compose-strict-e2e.sh` can now bridge the strict local-cloud run into install validation when `TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_OUTPUT` is set, passing the demo gate report as `--runtime-closed-loop-evidence` while leaving full install validation opt-in for operators who have also collected the required release, migration, dependency, event-stream, audit, trace, and restore-drill evidence.

## Task 6: Upgrade, Rollback, and Air-Gapped Runbooks

**Files:**

- Create: `docs/operations/private-upgrade-rollback.md`
- Create: `docs/operations/air-gapped-install.md`
- Create: `infra/package/upgrade-matrix.md`
- Test: documentation plus future package compatibility tests.

**Steps:**

1. Define upgrade prerequisites: backup, migration compatibility, license check, image availability, and downtime window.
2. Define rollback prerequisites and data migration caveats.
3. Document air-gapped package transfer, image import, license file import, and offline dependency mirrors.
4. Document support bundle collection with redaction.
5. Add compatibility matrix for app version, migration version, database version, and chart version.

**Acceptance Criteria:**

- Private customers have an upgrade and rollback path.
- Air-gapped constraints are explicit before sales commits.

**Current Implementation Notes:**

- `docs/operations/private-upgrade-rollback.md` documents upgrade prerequisites, rollback prerequisites, data migration caveats, operator sequence, and support bundle redaction.
- `docs/operations/air-gapped-install.md` documents no-outbound-internet constraints, package transfer, image import, license file import, offline dependency mirrors, internal model/sandbox requirements, install validation, and support bundle redaction.
- `infra/package/upgrade-matrix.md` records the first app/chart/migration/PostgreSQL/Redis compatibility row and rollback boundary.
- Release-grade upgrade automation, actual restore environment orchestration, and customer-specific compatibility expansion remain planned. Release package signature verification, restore drill evidence intake, the restored-environment evidence builder, scheduled restore drill due-job generation, SQL-backed due-worker request-record intake, lifecycle API review/execution-enqueue/status-evidence update endpoints, the restore-drill execution worker verifier handoff, and the restore-drill evidence collection worker are started through the private install validation path.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_deployment_package_manifest.py -q
python -m pytest tests/api/test_deployment_config_profiles.py -q
python -m pytest tests/api/test_helm_packaging_contract.py -q
python -m pytest tests/api/test_license_entitlements.py -q
python -m pytest tests/api/test_install_validation_contract.py -q
python -m pytest -q
```

When Helm exists locally:

```bash
helm template taroai infra/helm/taroai
```

Expected final result: the platform can be packaged, configured, validated, licensed, upgraded, and operated in BYOC/VPC/private environments without changing core runtime code.

# Private Deployment and Packaging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare the platform for BYOC, VPC, and private deployment by defining packaging, configuration, license checks, install validation, upgrades, air-gapped constraints, and customer-operated runbooks.

**Architecture:** Cloud SaaS remains the first deployment mode, but private delivery should reuse the same service boundaries. Packaging provides Helm/Kubernetes overlays, Docker images, migration jobs, config templates, license activation, health checks, backup hooks, and upgrade plans. Runtime providers, model providers, object storage, database, Redis, and secrets manager are pluggable through Pydantic settings and environment variables.

**Tech Stack:** Docker, Kubernetes, Helm later, FastAPI, future frontend packaging only after explicit approval, PostgreSQL, Redis, S3-compatible storage, secret manager adapters, pytest, CI validation.

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
3. Validate required services: API, worker, database, Redis, object storage, sandbox provider, model gateway, and secrets manager. Web validation is added only after the final frontend phase.
4. Add tests for valid manifest, missing image, incompatible migration, and unsupported target.
5. Document package contents.

**Acceptance Criteria:**

- Deployment package has a typed manifest.
- Operators can see exactly what will be installed.

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

## Task 3: Helm/Kubernetes Packaging Path

**Files:**

- Create: `infra/helm/taroai/Chart.yaml`
- Create: `infra/helm/taroai/values.yaml`
- Create: `infra/helm/taroai/templates/README.md`
- Test: local template validation where Helm is available, otherwise manifest file tests.

**Steps:**

1. Define chart structure for API, workers, migrations, config, secrets refs, ingress, service accounts, and network policies. Add web chart structure only after the final frontend phase is approved.
2. Keep secrets as external references, not literal values in chart defaults.
3. Add values for resource requests, autoscaling, persistence, and node selectors.
4. Document how cloud PoC differs from private deployment.
5. Add CI validation plan for `helm template`.

**Acceptance Criteria:**

- Private deployment has a concrete packaging path.
- Helm defaults do not contain secrets.

## Task 4: License and Entitlement Checks

**Files:**

- Create: `apps/api/src/taroai/licensing/__init__.py`
- Create: `apps/api/src/taroai/licensing/models.py`
- Create: `apps/api/src/taroai/licensing/service.py`
- Test: `tests/api/test_license_entitlements.py`

**Steps:**

1. Define `LicenseKey`, `Entitlement`, `LicensedFeature`, and `LicenseStatus`.
2. Support offline license file validation for private deployments.
3. Gate enterprise features such as SSO, private connector count, sandbox concurrency, solution packs, and audit retention by entitlement.
4. Emit audit events when license status changes.
5. Add tests for valid license, expired license, missing entitlement, and offline mode.

**Acceptance Criteria:**

- Feature availability is explicit in private deployments.
- Offline customers can run without calling SaaS control plane.

## Task 5: Install Validation and Smoke Tests

**Files:**

- Create: `docs/operations/private-install-validation.md`
- Create: `tests/api/test_install_validation_contract.py`
- Future: `scripts/validate-install.sh`

**Steps:**

1. Define validation checks: database migration, Redis connectivity, object storage read/write, secret manager read, OpenAI-compatible Model Gateway health call, sandbox health, API health, event stream, worker queue, and audit write. Web health is a future check after frontend approval.
2. Represent validation results as Pydantic `InstallValidationReport`.
3. Document expected outputs for customer operators.
4. Add tests for report model and failure summary.
5. Add smoke-test command plan for future script.

**Acceptance Criteria:**

- Operators can prove installation is ready.
- Validation failures point to specific dependencies.

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

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_deployment_package_manifest.py -q
python -m pytest tests/api/test_deployment_config_profiles.py -q
python -m pytest tests/api/test_license_entitlements.py -q
python -m pytest tests/api/test_install_validation_contract.py -q
python -m pytest -q
```

When Helm exists locally:

```bash
helm template taroai infra/helm/taroai
```

Expected final result: the platform can be packaged, configured, validated, licensed, upgraded, and operated in BYOC/VPC/private environments without changing core runtime code.

# Deployment and Operations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the cloud deployment and operations foundation for Taroai so the enterprise Agent Workspace can run reliably in a PoC cloud environment and later support private deployment.

**Architecture:** Use Docker Compose for local development, Kubernetes for cloud PoC, and keep all service configuration in Pydantic settings and environment variables. Runtime workers, API, Redis, PostgreSQL, object storage, Web Workspace, and observability should be deployed as separate components with health checks and clear secrets boundaries. Full portal, admin, SSO/MFA, skill marketplace, and browser live-view frontend packaging remain later phases.

**Tech Stack:** Docker, Docker Compose, Kubernetes, FastAPI, PostgreSQL, Redis, MinIO/S3, static HTML/CSS/JavaScript workspace, OpenTelemetry, pytest.

---

## Summary

This plan turns the current local Python foundation into a deployable system. It does not implement production microVM isolation yet; it prepares the infrastructure path for cloud PoC and later BYOC/private delivery.

Current implementation has started the local PoC path with `infra/docker-compose.yml`, `apps/web/Dockerfile`, `apps/api/Dockerfile`, `apps/api/entrypoint.sh`, API `/healthz` and `/readyz` endpoints, MinIO bucket initialization, `.env.example` configured through Pydantic settings, configurable host ports, Web Workspace static asset serving, `local_process` sandbox execution inside the API container for local validation, a first-pass Docker sandbox provider for Settings-hardened disabled-network container execution when a Docker daemon is explicitly available, `docs/operations/mvp-local-cloud-poc.md`, and `docs/operations/triggers-runbook.md`. The SQL repositories now use a shared SQLite/PostgreSQL connection factory with psycopg-backed PostgreSQL URL support, process-level pool settings, a non-superuser PostgreSQL app role initialized through `infra/postgres/init.sql`, a live PostgreSQL migration/RLS verifier, and a first-pass Pydantic migration CLI that can plan pending/unknown migrations before explicit apply.

## Task 1: Local Docker Compose

**Files:**

- Create: `infra/docker-compose.yml`
- Create: `infra/postgres/init.sql`
- Create: `infra/minio/README.md`
- Modify: `.env.example`
- Test: `tests/api/test_settings.py`

**Steps:**

1. Add Docker Compose services for `api`, `postgres`, `redis`, and `minio`.
2. Mount `apps/api/migrations` into the API or migration runner.
3. Add environment variables for database, Redis, object storage, and sandbox provider.
4. Add health checks for PostgreSQL, Redis, MinIO, and API.
5. Keep local `.env` ignored; commit `.env.example` only.

**Acceptance Criteria:**

- `docker compose -f infra/docker-compose.yml up` starts dependencies.
- API can read config from environment.
- Local secrets are not committed.

## Task 2: API Container

**Files:**

- Create: `apps/api/Dockerfile`
- Create: `apps/api/entrypoint.sh`
- Modify: `apps/api/requirements.txt`
- Test: `tests/api/test_settings.py`

**Steps:**

1. Create a minimal Python container for FastAPI.
2. Install dependencies from `apps/api/requirements.txt`.
3. Run API with `uvicorn taroai.app:app`.
4. Expose `/healthz` and `/readyz` endpoints.
5. Add tests for health endpoint behavior without requiring live database.

**Acceptance Criteria:**

- API image builds.
- Health endpoints are tested.
- Container does not bake secrets.

**Current Implementation Notes:**

- `apps/api/Dockerfile` builds the FastAPI API image from `apps/api/requirements.txt`, copies `src` and `migrations`, sets `PYTHONPATH=/app/src`, exposes port `8000`, and runs `uvicorn taroai.app:app`.
- `apps/api/entrypoint.sh` can run the existing `MigrationRunner` during container startup when `TAROAI_RUN_MIGRATIONS=true`.
- `GET /healthz` and `GET /readyz` are available without requiring live database calls, so container health checks can verify the process and configuration wiring.
- `.env.example` enables `TAROAI_SANDBOX_PROVIDER=local_process` and `TAROAI_SANDBOX_ROOT_DIR=/data/taroai/sandboxes` for local cloud PoC validation. `TAROAI_SANDBOX_PROVIDER=docker` is available for explicit disabled-network container execution where Docker access is provided, with Pydantic settings for memory, CPU, pids, read-only rootfs, dropped capabilities, security options, and tmpfs mounts. Shared enterprise execution still requires Kubernetes, E2B, or microVM-backed isolation.

## Task 3: Migration Runner

**Files:**

- Create: `apps/api/src/taroai/db/migrations.py`
- Create: `tests/api/test_migration_runner.py`
- Modify: `apps/api/migrations/001_initial.sql`

**Steps:**

1. Add a migration runner that applies SQL files in lexical order.
2. Add `schema_migrations` tracking table.
3. Add tests using a local database fixture or sqlite-free parser contract if PostgreSQL is unavailable.
4. Ensure migration runner is idempotent.
5. Run migration runner during deployment, not during request handling.

**Acceptance Criteria:**

- Migrations are ordered, tracked, and idempotent.
- Failed migration stops startup.

## Task 4: Kubernetes Manifests

**Files:**

- Create: `infra/k8s/api.yaml`
- Create: `infra/k8s/worker.yaml`
- Create: `infra/k8s/postgres.yaml`
- Create: `infra/k8s/redis.yaml`
- Create: `infra/k8s/minio.yaml`
- Create: `infra/k8s/configmap.yaml`
- Create: `infra/k8s/secrets.example.yaml`
- Create: `infra/k8s/kustomization.yaml`
- Create: `infra/k8s/network-policy.yaml`

**Steps:**

1. Worker process manifests are started with independent agent and cleanup worker Deployments in `infra/k8s/worker.yaml`.
2. Runtime non-secret config is in `infra/k8s/configmap.yaml`.
3. Runtime secret placeholders are in `infra/k8s/secrets.example.yaml`; real secrets are not committed.
4. Worker and API manifests include resource requests/limits, non-root security context, and writable `/tmp` plus `/data/taroai` mounts under read-only root filesystems.
5. API, PostgreSQL, Redis, and MinIO manifests are started with Services, probes, persistent volume claims for stateful backing services, an API migration Job, a PostgreSQL app-role init script, Redis password wiring, and a MinIO bucket-init Job.
6. `infra/k8s/network-policy.yaml` starts namespace-scoped default-deny traffic policy with explicit DNS egress, internal API ingress, API/worker egress to PostgreSQL/Redis/MinIO, migration egress to PostgreSQL, bucket-init egress to MinIO, controlled HTTPS egress, and backend service ingress allowlists.
7. `infra/k8s/kustomization.yaml` provides a single manifest entrypoint for the current cloud PoC stack.
8. Release-grade Helm rendering validation, Ingress/TLS, cloud-managed database/cache/object-storage overlays, autoscaling policy, and live cluster startup verification remain deployment hardening work.

**Acceptance Criteria:**

- Manifests are environment-agnostic enough for PoC.
- Secrets are placeholders only.
- API/worker can scale independently.

## Task 5: Worker Separation

**Files:**

- Modify: `apps/api/src/taroai/workers/__init__.py`
- Modify: `apps/api/src/taroai/workers/models.py`
- Modify: `apps/api/src/taroai/workers/queue.py`
- Modify: `apps/api/src/taroai/workers/agent_worker.py`
- Modify: `apps/api/src/taroai/workers/billing_worker.py`
- Test: `tests/api/test_worker_contract.py`

**Steps:**

1. Worker package exists.
2. Job payload Pydantic models for run execution, billing aggregation, and cleanup exist.
3. Queue claim/ack/fail lifecycle is covered by tests.
4. Redis queue implementation exists behind the same interface.
5. API can enqueue run execution when `TAROAI_RUN_EXECUTION_DISPATCH_MODE=queue`.
6. Retry/dead-letter policy is covered by queue contract tests.
7. Agent worker runner/entrypoint exists for processing queued run execution jobs, selects the configured control-plane store backend, and registers default runtime tool handlers.
8. Cleanup worker runner exists for processing queued storage lifecycle cleanup jobs through the configured queue, store, storage catalog, and object storage adapter.
9. Live Redis worker queue verification exists for ping, enqueue, claim, ack, expired-lease recovery, dead-letter, and cleanup behavior.
10. Worker deployment manifests exist for independently scalable agent and cleanup worker processes.
11. Kubernetes manifests exist for the API, migration Job, PostgreSQL, Redis, and MinIO backing services with shared runtime ConfigMap/Secret boundaries.

**Acceptance Criteria:**

- Worker job contracts are Pydantic.
- Run execution can be moved off API request path.

## Task 6: Runtime Operations

**Files:**

- Create: `docs/operations/runbook.md`
- Create: `docs/operations/local-development.md`
- Create: `docs/operations/cloud-poc.md`

**Steps:**

1. Document local setup.
2. Document environment variables.
3. Document migration command.
4. Document common failures: database unavailable, Redis unavailable, MinIO unavailable, sandbox provider missing.
5. Document rollback approach for app and migration failures.

**Acceptance Criteria:**

- A new engineer can run local dependencies and API.
- Operators have a basic cloud PoC runbook.

## Verification

Run after implementation:

```bash
python -m pytest -q
docker compose -f infra/docker-compose.yml config
docker build -f apps/api/Dockerfile apps/api
```

Expected final result: local dependencies are reproducible, API has container and health endpoints, and cloud PoC manifests/runbooks exist.

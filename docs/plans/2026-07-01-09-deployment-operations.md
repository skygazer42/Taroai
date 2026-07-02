# Deployment and Operations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the cloud deployment and operations foundation for Taroai so the enterprise Agent Workspace can run reliably in a PoC cloud environment and later support private deployment.

**Architecture:** Use Docker Compose for local development, Kubernetes for cloud PoC, and keep all service configuration in Pydantic settings and environment variables. Runtime workers, API, Redis, PostgreSQL, object storage, and observability should be deployed as separate components with health checks and clear secrets boundaries. Frontend deployment is deferred to the final user-managed phase.

**Tech Stack:** Docker, Docker Compose, Kubernetes, FastAPI, PostgreSQL, Redis, MinIO/S3, OpenTelemetry, pytest, future frontend deployment only after explicit approval.

---

## Summary

This plan turns the current local Python foundation into a deployable system. It does not implement production microVM isolation yet; it prepares the infrastructure path for cloud PoC and later BYOC/private delivery.

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

**Steps:**

1. Add deployment manifests for API, agent worker, and tool/sandbox workers.
2. Add services and health probes.
3. Put non-secret config in ConfigMap.
4. Put secret placeholders in `secrets.example.yaml`; do not commit real secrets.
5. Add resource requests and limits.

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
9. Add worker deployment manifests and live Redis deployment verification.

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

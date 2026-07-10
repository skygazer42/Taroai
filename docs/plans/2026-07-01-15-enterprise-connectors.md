# Enterprise Connectors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the connector layer that lets approved agents and skills reach enterprise SaaS, databases, files, internal APIs, and MCP servers through governed credentials, permissions, sync jobs, and audit logs.

**Architecture:** Connectors are separate from skills. A connector owns authentication, data source metadata, sync state, and credential boundaries; a skill or agent requests connector capabilities through Tool Gateway and Policy Service. Connector credentials never enter the sandbox directly; runtime receives short-lived capability tokens or host-side bridge calls.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, Redis-backed workers, MCP, OAuth/OIDC where applicable, pytest, secret manager seam.

---

## Summary

Enterprise value depends on connecting private systems without giving agents unrestricted credentials. This plan creates a reusable connector model for SaaS, internal APIs, databases, file stores, and MCP servers.

## Task 1: Connector Domain Models

**Files:**

- Create: `apps/api/src/taroai/connectors/__init__.py`
- Create: `apps/api/src/taroai/connectors/models.py`
- Create: `apps/api/src/taroai/connectors/service.py`
- Test: `tests/api/test_connector_models.py`

**Steps:**

1. Define `ConnectorType`: `saas`, `database`, `file_store`, `internal_api`, `mcp_server`, and `web`.
2. Define `ConnectorDefinition` with ID, tenant, workspace scope, type, display name, auth mode, capability list, sensitivity level, owner, and status.
3. Define `ConnectorCapability` with action name, input schema, output schema, required scopes, risk level, and approval requirement.
4. Define `ConnectorCredentialRef` that stores only secret references, never raw secrets.
5. Add tests that raw secret fields are rejected.

**Acceptance Criteria:**

- Connectors are typed and tenant-scoped.
- Credentials are represented by references only.

**Current Implementation Notes:**

- `apps/api/src/taroai/connectors/` now defines Pydantic connector type/status/auth/capability/definition models plus memory and SQL registry boundaries.
- `ConnectorCredentialRef` stores only tenant/workspace-scoped secret references and rejects extra credential fields.
- `POST /api/connectors`, `GET /api/connectors`, `GET /api/connectors/{connector_id}`, `PATCH /api/connectors/{connector_id}`, `POST /api/connectors/{connector_id}/enable`, and `POST /api/connectors/{connector_id}/disable` are started behind `connectors.manage` and `connectors.read`.
- Connector registration records `connector.registered` audit metadata with connector id, type, auth mode, capability count, sensitivity, and credential reference id, without credential values.
- Connector update/enable/disable operations record safe `connector.updated`, `connector.enabled`, and `connector.disabled` audit metadata without update payloads or credential values.
- SQLite/PostgreSQL-compatible SQL persistence is selectable through `TAROAI_CONNECTOR_REGISTRY_BACKEND`, with migration and PostgreSQL RLS coverage.
- `POST /api/connectors/{connector_id}/sync-jobs` is started behind `connectors.sync`, creates a queued sync run, enqueues a `connectors.sync` worker job, stores pending connector sync state with run/job/knowledge-base/cursor references, and records safe `connector.sync_requested` audit metadata.
- `POST /api/connectors/{connector_id}/invoke` is started behind `connectors.invoke` for run-scoped capability invocation decisions through Tool Gateway policy checks.
- Internal API connector dispatch is started through an allowlisted HTTP request builder that enforces method/path policy before network access, can inject API-key credentials and OAuth2 bearer access tokens from `ConnectorCredentialRef` through short-lived secret leases, and keeps raw request/response/credential values out of audit metadata.
- Approval-required connector invocations now create or reuse a persisted run approval request, return `approval_id` without dispatch or billing, support approval/rejection through run approval endpoints without runtime-state coupling, and require a matching approved `approval_id` before approved execution.
- `POST /api/connectors/{connector_id}/oauth/authorize`, `/oauth/callback`, and `/oauth/refresh` are started behind `connectors.manage`; OAuth code exchange and refresh rotate access/refresh token values through secret references, return only secret reference IDs/status metadata, and record safe audit metadata without code, state, token, client-secret, or lease values.
- AWS Secrets Manager can now back connector credential values through the same `SecretRef`/lease interface selected by Pydantic settings.
- SaaS/file/MCP provider adapters, provider-specific OAuth edge cases, broader database dialect/query governance, short-lived connector access handles, tenant-specific KMS/IAM policy hardening, and additional secret backend providers remain planned work.

## Task 2: OAuth and Secret Boundary

**Files:**

- Create: `apps/api/src/taroai/connectors/oauth.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_connector_oauth.py`

**Steps:**

1. Define `ConnectorAuthMode`: `none`, `api_key`, `oauth2`, `service_account`, `database_password`, and `mcp`.
2. Add OAuth config model with client ID reference, client secret reference, authorize URL, token URL, scopes, and callback URL.
3. Store access/refresh token references in secret manager seam.
4. Return short-lived access handles to Tool Gateway, not raw tokens.
5. Test that connector service never returns raw credentials.

**Acceptance Criteria:**

- Connector auth can support SaaS without leaking tokens.
- Future secret manager replacement does not change connector API.

**Current Implementation Notes:**

- The first implementation stores API-key, OAuth2, service-account, database-password, and MCP auth modes as connector metadata, while credentials remain secret references.
- OAuth2 bearer access-token dispatch through connector credential references is started for internal API connectors.
- OAuth authorization URL generation, callback code exchange, refresh-token rotation, secret value rotation, and safe OAuth audit events are started through `ConnectorOAuthService` and the connector management API.
- AWS Secrets Manager value storage is started behind the same connector secret-reference API.
- Provider-specific OAuth nuances, short-lived connector access handles, tenant-specific KMS/IAM policy hardening, and additional secret backend providers remain planned work.

## Task 3: Database and Internal API Connectors

**Files:**

- Create: `apps/api/src/taroai/connectors/database.py`
- Create: `apps/api/src/taroai/connectors/internal_api.py`
- Test: `tests/api/test_database_connector_policy.py`
- Test: `tests/api/test_internal_api_connector_policy.py`

**Steps:**

1. Define database connector config with DSN secret reference, read-only flag, allowed schemas, allowed tables, and query timeout.
2. Block write queries by default.
3. Define internal API connector config with base URL, allowed paths, allowed methods, timeout, and network policy.
4. Require approval for external writes or internal state-changing calls.
5. Add tests for blocked SQL writes and blocked disallowed HTTP paths.

**Acceptance Criteria:**

- Agents cannot freely query every table or call every internal endpoint.
- Read/write behavior is explicit and governed.

**Current Implementation Notes:**

- `apps/api/src/taroai/connectors/dispatch.py` now includes `DatabaseConnectorConfig` for read-only database connector dispatch with allowed tables, optional allowed schemas, row caps, query timeout metadata, and DSN retrieval through `ConnectorCredentialRef` short-lived secret leases.
- Database connector dispatch accepts a single SELECT statement with positional parameters, rejects writes and multi-statement SQL before execution, checks referenced tables against the connector allowlist, returns row data to the caller, and keeps DSN values/query results out of audit metadata.
- The first database dispatch path uses the existing shared database connection boundary for SQLite/PostgreSQL-compatible URLs. Rich SQL parsing, per-dialect policy, query cost limits, pagination, and source-system-specific adapters remain planned work.

## Task 4: MCP Server Connector

**Files:**

- Create: `apps/api/src/taroai/connectors/mcp.py`
- Modify: `apps/api/src/taroai/skills/manifest.py`
- Test: `tests/api/test_mcp_connector_contract.py`

**Steps:**

1. Define MCP server registration model with server URL/command reference, transport type, tool list, and required scopes.
2. Import MCP tool metadata into internal connector capabilities.
3. Map MCP tool schemas to Pydantic-compatible JSON schema.
4. Require tenant admin approval before enabling a new MCP server.
5. Add tests for tool import and approval-required status.

**Acceptance Criteria:**

- MCP becomes an enterprise-governed connector type.
- Imported tools obey the same policy and audit path as native tools.

## Task 5: Sync Jobs and ACL Mapping

**Files:**

- Create: `apps/api/src/taroai/connectors/sync.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Future: `apps/api/src/taroai/knowledge/ingestion.py`
- Test: `tests/api/test_connector_sync_acl.py`

**Steps:**

1. Define `ConnectorSyncJob` with connector ID, tenant ID, workspace ID, status, cursor, started time, completed time, and error.
2. Define ACL mapping from source groups/users to tenant workspace roles or ACL subjects.
3. Send synced documents to knowledge ingestion with source URI, version, content hash, and ACL metadata.
4. Do not write synced facts directly into long-term memory without review.
5. Add tests that ACL metadata survives sync.

**Acceptance Criteria:**

- Connectors can feed knowledge without bypassing permissions.
- Sync state is resumable.

**Current Implementation Notes:**

- `apps/api/src/taroai/connectors/sync.py` defines source ACL principals, ACL mapping rules, sync document input, sync job payloads, and a planner that creates `KnowledgeDocumentCreate` payloads with mapped ACL subjects.
- `apps/api/src/taroai/workers/connector_sync_worker.py` consumes `connectors.sync` jobs, registers planned documents in the knowledge service, records `connector_sync_document_count` billing meters, emits run events, and records safe worker/connector sync audit metadata without raw document content.
- `taroai.workers.runner` exposes a `connector_sync` worker kind with Settings-built control-plane and knowledge services, and `infra/k8s/worker.yaml` deploys it as an independent worker process using the shared runtime ConfigMap/Secret boundaries.
- Connector definitions now persist `sync_state` in memory and SQL registries, including pending/running/succeeded/failed status, run ID, job ID, knowledge base ID, cursor, start/completion timestamps, and failure error code without raw document content.
- Connector sync planning explicitly keeps `memory_write_count=0`; synced facts must go to knowledge ingestion first, not directly into long-term memory.
- Provider-specific document fetchers, incremental page scheduling, and connector-side source cursor reconciliation remain planned work.

## Task 6: Tool Gateway Integration and Audit

**Files:**

- Modify: `apps/api/src/taroai/agent/tools.py`
- Future: `apps/api/src/taroai/tool_gateway/service.py`
- Future: `apps/api/src/taroai/audit/service.py`
- Test: `tests/api/test_connector_tool_gateway_integration.py`

**Steps:**

1. Add connector invocation request model that includes tenant, workspace, user, run, connector ID, capability, input, requested scopes, and approval context.
2. Tool Gateway checks policy before invoking connector.
3. Record audit events for connector reads, writes, auth grants, sync starts, sync failures, and capability changes.
4. Record billing meters for connector calls and sync volume.
5. Add tests for allowed read, approval-required write, denied scope, and audit event creation.

**Acceptance Criteria:**

- Agent/tool execution goes through one governed connector path.
- Connector activity is billable and auditable.

**Current Implementation Notes:**

- `apps/api/src/taroai/connectors/invocation.py` defines Pydantic invocation request/decision models plus a service that maps connector capabilities into Tool Gateway policies for scope checks, approval-required decisions, risk metadata, and input schema validation.
- `apps/api/src/taroai/connectors/dispatch.py` starts internal API connector dispatch with Pydantic config for base URL, allowed methods, allowed paths, timeout, response-size cap, API-key header/bearer credential injection, and OAuth2 bearer access-token injection from `ConnectorCredentialRef`; it also starts read-only database connector dispatch through secret-referenced DSNs, SELECT-only enforcement, and table allowlists.
- `POST /api/connectors/{connector_id}/invoke` validates tenant/workspace/run scope, requires `connectors.invoke`, records safe `connector.invoked`, `connector.approval_required`, `connector.invocation_denied`, or `connector.dispatch_failed` audit metadata without raw input or response values, and records `connector_invocation_count` billing meters for authorized ready decisions.
- Approval-required connector capabilities create or reuse a pending run approval request, return `approval_id`, skip dispatch/billing until approved, and require approved execution to bind the matching persisted approval. Worker-driven connector sync ingestion into knowledge is started with a dedicated worker kind and sync-volume billing. OAuth authorize/callback/refresh management is started with token rotation through secret references and safe audit metadata. AWS Secrets Manager can back connector credential values through the same lease API. SaaS/file/MCP provider dispatch, provider-specific OAuth edge cases, broader database dialect/query governance, connector-specific read/write audit taxonomy, tenant-specific KMS/IAM policy hardening, and additional secret backend providers remain planned work.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_connector_models.py -q
python -m pytest tests/api/test_connector_oauth.py -q
python -m pytest tests/api/test_database_connector_policy.py -q
python -m pytest tests/api/test_internal_api_connector_policy.py -q
python -m pytest tests/api/test_mcp_connector_contract.py -q
python -m pytest tests/api/test_connector_sync_acl.py -q
python -m pytest tests/api/test_connector_tool_gateway_integration.py -q
python -m pytest -q
```

Expected final result: enterprise systems can be connected through auditable, permissioned, tenant-scoped connectors without exposing raw credentials to agents or sandboxes.

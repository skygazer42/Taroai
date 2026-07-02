# Enterprise Connectors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the connector layer that lets approved agents and skills reach enterprise SaaS, databases, files, internal APIs, and MCP servers through governed credentials, permissions, sync jobs, and audit logs.

**Architecture:** Connectors are separate from skills. A connector owns authentication, data source metadata, sync state, and credential boundaries; a skill or agent requests connector capabilities through Tool Gateway and Policy Service. Connector credentials never enter the sandbox directly; runtime receives short-lived capability tokens or host-side bridge calls.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, Redis workers later, MCP, OAuth/OIDC where applicable, pytest, secret manager seam.

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

## Task 2: OAuth and Secret Boundary

**Files:**

- Create: `apps/api/src/taroai/connectors/auth.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_connector_auth_boundary.py`

**Steps:**

1. Define `ConnectorAuthMode`: `none`, `api_key`, `oauth2`, `service_account`, `database_password`, and `mcp`.
2. Add OAuth config model with client ID reference, client secret reference, authorize URL, token URL, scopes, and callback URL.
3. Store access/refresh token references in secret manager seam.
4. Return short-lived access handles to Tool Gateway, not raw tokens.
5. Test that connector service never returns raw credentials.

**Acceptance Criteria:**

- Connector auth can support SaaS without leaking tokens.
- Future secret manager replacement does not change connector API.

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

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_connector_models.py -q
python -m pytest tests/api/test_connector_auth_boundary.py -q
python -m pytest tests/api/test_database_connector_policy.py -q
python -m pytest tests/api/test_internal_api_connector_policy.py -q
python -m pytest tests/api/test_mcp_connector_contract.py -q
python -m pytest tests/api/test_connector_sync_acl.py -q
python -m pytest tests/api/test_connector_tool_gateway_integration.py -q
python -m pytest -q
```

Expected final result: enterprise systems can be connected through auditable, permissioned, tenant-scoped connectors without exposing raw credentials to agents or sandboxes.

# Skills and Tool Gateway Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the governed skill and tool execution layer so enterprise custom skills can be uploaded, validated, permissioned, approved, billed, audited, and reused by agents.

**Architecture:** Keep skill metadata in `taroai/skills`, tool execution in a separate `taroai/tools` or `taroai/tool_gateway` package, and never let Agent Runtime call external systems directly. All tool calls go through schema validation, permission checks, approval policy, secrets boundary, audit, and billing.

**Tech Stack:** FastAPI, Pydantic, MCP-style tool metadata, PostgreSQL, sandbox adapter seam, pytest.

---

## Summary

Current state has a Pydantic `SkillManifest`, tenant-scoped registry lifecycle records, workspace installation records, SQLite-compatible SQL registry persistence selectable through settings, `/api/skills` management endpoints behind identity permissions, and initial `taroai/tool_gateway` package with request/policy/result models, scope checks, approval-required decisions, input/output schema validation, registered-handler execution, runtime context invocation, runtime policy-approval pause, successful-call audit/billing records, failed-call audit redaction, and unified API error mapping. This plan turns that foundation into a governed enterprise skill layer and complete Tool Gateway.

## Task 1: Skill Manifest Validation

**Files:**

- Modify: `apps/api/src/taroai/skills/manifest.py`
- Modify: `apps/api/src/taroai/skills/registry.py`
- Test: `tests/api/test_skill_manifest_validation.py`

**Steps:**

1. Add tests for required fields: id, version, owner, input schema, output schema, runtime, scopes.
2. Reject invalid versions and empty owners.
3. Reject undeclared external write actions.
4. Reject runtime timeouts above configured tenant policy.
5. Keep manifest Pydantic-only at the boundary.

**Acceptance Criteria:**

- Invalid manifests fail before publication.
- Required scopes and risk level are explicit.

## Task 2: Skill Registry Persistence Contract

**Files:**

- Modify: `apps/api/src/taroai/skills/registry.py`
- Modify: `apps/api/migrations/001_initial.sql`
- Test: `tests/api/test_skill_registry_contract.py`

**Steps:**

1. Add tests for register, publish, disable, version lookup, and list by scope.
2. Add tables if missing: `skills`, `skill_versions`, `skill_permissions`.
3. Keep in-memory registry as unit test implementation.
4. Production repository should store manifest JSON and indexed governance fields.

**Acceptance Criteria:**

- Skills are versioned.
- Disabled skills cannot be invoked.
- Scope controls tenant/workspace/department/private visibility.

**Current Implementation Notes:**

- `InMemorySkillRegistry` now tracks tenant-scoped skill entries with `draft`, `published`, and `disabled` lifecycle states plus workspace installation status.
- `SqlSkillRegistry` persists tenant-scoped skill entries and workspace installation records to the SQLite-compatible SQL repository through `TAROAI_SKILL_REGISTRY_BACKEND`.
- Disabled-skill invocation enforcement remains implementation work.

## Task 3: Tool Gateway Package

**Files:**

- Modify: `apps/api/src/taroai/tool_gateway/__init__.py`
- Modify: `apps/api/src/taroai/tool_gateway/models.py`
- Modify: `apps/api/src/taroai/tool_gateway/service.py`
- Test: `tests/api/test_tool_gateway.py`

**Steps:**

1. Keep architecture tests requiring the existing `tool_gateway/` package.
2. Extend existing Pydantic models: `ToolGatewayRequest`, `ToolResult`, `ToolPolicy`, `ToolPolicyDecision`, and `ToolRiskLevel`.
3. Tool invocation request must include tenant, workspace, user, run, step/tool ID, input, granted scopes, and approval state.
4. Add input/output schema validation before and after registered executor calls.
5. Keep Agent Runtime invoking tools only through Tool Gateway.

**Acceptance Criteria:**

- No direct external tool execution from Agent Runtime.
- Tool request/result is Pydantic and auditable.

**Current Implementation Notes:**

- `ToolPolicy` now carries input and output schema definitions.
- `ToolGateway` validates tool input before handler execution and validates tool output before returning to runtime.
- `create_app` now wires the default Agent Runtime Tool Gateway to sandbox command and browser action handlers.
- The current schema validator covers the JSON Schema subset used by MVP skill manifests: object, required, properties, additionalProperties, array items, enum, primitive types, string length, numeric bounds, and item counts.

## Task 4: Policy, Approval, and RBAC Integration

**Files:**

- Modify: `apps/api/src/taroai/tool_gateway/service.py`
- Modify: `apps/api/src/taroai/identity/service.py`
- Modify: `apps/api/src/taroai/store.py`
- Test: `tests/api/test_tool_gateway_policy.py`

**Steps:**

1. Add tests where user with required scope can invoke.
2. Add tests where missing role permission blocks invocation.
3. Add tests where high-risk tool creates approval request instead of executing.
4. Add tests where approved request resumes execution.
5. Emit audit events for blocked, approved, and executed tool calls.

**Acceptance Criteria:**

- Tool permissions are enforced before execution.
- High-risk operations pause for approval.
- All policy decisions are traceable.

## Task 5: Billing and Audit Integration

**Files:**

- Modify: `apps/api/src/taroai/tool_gateway/service.py`
- Modify: `apps/api/src/taroai/store.py`
- Test: `tests/api/test_tool_gateway_billing_audit.py`

**Steps:**

1. Add billing meter event for every tool call.
2. Add tool-specific meters: `tool_call_count`, `browser_action_count`, `sandbox_minutes`, etc.
3. Add audit event with tool ID, skill ID, scopes, status, and risk level.
4. Ensure audit payload never stores raw secrets.

**Acceptance Criteria:**

- Every tool call produces billing and audit records.
- Failed and blocked calls are also auditable.

**Current Implementation Notes:**

- Successful runtime tool calls now create `tool_call_count` billing meter events and `tool.executed` audit events through `InMemoryControlPlaneStore` and `SqlControlPlaneRepository`.
- Runtime tool calls now pause when Tool Gateway policy requires approval, and failed runtime tool calls create `tool.failed` audit records with sensitive tool input redaction.
- Tool Gateway service calls can now emit `tool.blocked` and `tool.approval_required` audit records through injected `AuditService` before blocked or approval-gated handlers run, with sensitive tool input redaction.
- The older callable audit recorder path remains available for local contract hooks.
- Skill-specific billing meter selection, connector-backed tool execution, and broader secret policy remain implementation work.

## Task 6: API Endpoints

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Optional later split: `apps/api/src/taroai/routes/skills.py`, `routes/tools.py`
- Test: `tests/api/test_app.py`

**Steps:**

1. Add `POST /api/skills`.
2. Add `GET /api/skills`.
3. Add `GET /api/skills/{skill_id}`.
4. Add `POST /api/skills/{skill_id}/publish`.
5. Add `POST /api/skills/{skill_id}/disable`.
6. Add internal-only `POST /api/runs/{run_id}/tool-calls` only if needed; prefer Agent Runtime calling service directly.

**Acceptance Criteria:**

- Skill APIs require RBAC.
- Tool invocation remains governed by Tool Gateway.

**Current Implementation Notes:**

- `/api/skills`, `/api/skills/{skill_id}`, `/api/skills/{skill_id}/publish`, and `/api/skills/{skill_id}/disable` are started behind identity permission checks.
- `/api/workspaces/{workspace_id}/skills` install/list/enable/disable endpoints are started behind identity permission checks.
- Dev-mode tool invocation endpoint and connector-backed skill execution remain implementation work.

## Verification

Run after each task:

```bash
python -m pytest tests/api/test_skills_memory.py -q
python -m pytest tests/api/test_skill_manifest_validation.py -q
python -m pytest tests/api/test_tool_gateway_contract.py -q
python -m pytest tests/api/test_tool_gateway_policy.py -q
python -m pytest tests/api/test_tool_gateway_billing_audit.py -q
python -m pytest -q
```

Expected final result: agents can only use governed tools/skills, and every invocation has policy, approval, billing, and audit coverage.

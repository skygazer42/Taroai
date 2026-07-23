# Taroai Technical Architecture Implementation Plan


**Goal:** Define the cloud-first technical architecture for an enterprise Agent Cloud Workspace with multi-tenant governance, agent runtime, sandbox execution, shared knowledge, reusable skills, memory, billing, and audit.

**Architecture:** The system is split into a client portal, API/control plane, agent runtime, tool gateway, sandbox execution plane, knowledge/memory services, storage, billing, and observability. Product flow uses real provider interfaces from the start: model calls go through an OpenAI-compatible Model Gateway, tool calls go through Tool Gateway, and sandbox calls go through Sandbox Adapter. Prototype/test provider classes are not product-flow components; repeatable fixture adapters stay under tests or contract verification only.

**Tech Stack:** Planned/candidate stack: Next.js App Router, TypeScript, FastAPI, Python, Pydantic, OpenAI-compatible Model Gateway contract, candidate LangGraph/LangChain/LlamaIndex adapters, PostgreSQL, Redis, S3/MinIO-compatible object storage, candidate pgvector/vector backend, OTel-compatible trace models, optional Langfuse/LangSmith adapters, Sandbox Adapter with E2B or Kubernetes Docker as candidates, MCP-style tool manifests.

---

## 1. Target System Shape

Use this high-level architecture for the first commercial PoC:

```text
Client Portal
  - Employee Workspace
  - Admin Console
  - Skill Marketplace
        |
API Gateway / FastAPI Control Plane
        |
-------------------------------------------------------------
| Tenant/IAM | Policy | Billing | Audit | Skill Registry     |
-------------------------------------------------------------
        |
Agent Runtime
  - Pydantic runtime state
  - OpenAI-compatible Model Gateway calls
  - candidate LangGraph run state machine
  - candidate LangChain-compatible model/tool harness
  - candidate LlamaIndex retrieval/document adapter
        |
Tool Gateway ---------------- Knowledge/Memory Services
  - MCP-style tools              - Document ingestion
  - Connectors                   - ACL-aware retrieval
  - Secrets boundary             - Memory layers
  - Approval checks
        |
Sandbox Adapter
  - provider interface first
  - E2B or K8s Docker candidate after Q-002
  - future Sandbox Manager / microVM candidate
        |
Storage / Observability
  - PostgreSQL / candidate pgvector
  - Redis
  - S3/MinIO
  - OTel-compatible traces
```

## 2. Repository Implementation Direction

When code implementation starts, use a monorepo layout:

```text
apps/
  web/                  # future/final-phase client only; do not create in current milestone
  api/                  # FastAPI control plane
packages/
  shared-types/          # OpenAPI generated types or shared schemas
services/
  agent-runtime/         # runtime workers; optional LangGraph adapter
  tool-gateway/          # tool registry, policy, connector invocation
  sandbox-adapter/       # E2B/K8s/provider adapter boundary
  knowledge-service/     # ingestion, indexing, retrieval
  billing-service/       # meter event aggregation
infra/
  docker-compose.yml
  k8s/
docs/
  plans/
```

Inside the FastAPI backend, keep each product context in a package instead of growing a single file:

```text
taroai/
  agent/
    runtime.py           # run lifecycle execution
    state.py             # Pydantic runtime state
    planning.py          # plan step and model gateway request helpers
    tools.py             # tool gateway interfaces/results/errors
    graph.py             # graph construction seam; LangGraph remains optional until dependency is verified
  skills/
    manifest.py          # Pydantic skill manifest and runtime config
    registry.py          # skill registry interface/implementation
  memory/
    models.py            # memory records and write requests
    service.py           # memory read/write service
  storage/
    models.py            # object storage references and purposes
    catalog.py           # storage object catalog and signed URL seam
  identity/
    models.py            # users, password hashes, roles, permissions
    service.py           # account and RBAC service
```

Rules:

- Context packages export public interfaces through `__init__.py`.
- New agent, memory, skill, storage, or identity code should land in those packages first, not in `app.py`, `domain.py`, or a top-level runtime module.
- Shared domain entities can stay in `domain.py` until they become large enough to split by bounded context.
- Backend management models should be Pydantic models, and backend source should not use `from __future__ import annotations`.

Do not build frontend visuals or scaffold a frontend app in the current milestone. The user will manage frontend implementation in a final phase; current work should stabilize API contracts, domain models, event streams, and UI acceptance requirements.

## 3. Service Responsibilities

### Client Portal

Future candidate technology: Next.js App Router + TypeScript. No frontend package exists yet, and none should be created until the final user-managed frontend phase is approved.

Responsibilities:

- Employee chat/task workspace.
- Run event timeline.
- Artifact panel.
- Workspace files.
- Admin console.
- Skill marketplace.
- Billing and audit views.

Client communicates through:

- REST for CRUD.
- SSE or WebSocket for run events.
- Direct object storage signed URLs only after API authorization.

Frontend consistency contract:

- The employee workspace must remain visually and structurally consistent with `https://agent.creao.ai/chat`.
- The primary chat region must expose `data-testid="chat-column"`.
- The chat column must be implemented as a vertical flex container with scrollable conversation content and a lower composer region.
- The lower composer/help-text area must preserve the reference selector target shape: `[data-testid="chat-column"] > div:nth-of-type(4) > div:nth-of-type(2)`.
- The composer must show the Enter-to-send and Shift+Enter-new-line interaction hint and implement that keyboard behavior.
- Run timeline, artifacts, workspace files, and virtual environment affordances should be adjacent/supporting surfaces; they must not displace the chat-first execution flow.
- Automated UI tests must assert the `data-testid`, composer hint, keyboard behavior, and responsive layout stability before the frontend phase is considered complete.

### API / Control Plane

Technology: FastAPI.

Responsibilities:

- Authenticate requests.
- Resolve tenant, workspace, user, roles, and policies.
- Create and read runs.
- Serve run event stream.
- Manage knowledge bases, skills, approvals, artifacts, billing, and audit.
- Dispatch agent runtime jobs.

### Agent Runtime

Technology: Python + Pydantic runtime. LangGraph, LangChain, and LlamaIndex are candidate adapters until implementation tests verify them.

Responsibilities:

- Maintain run state machine.
- Classify intent and risk.
- Retrieve allowed context.
- Build plan.
- Request plan/model output through OpenAI-compatible Model Gateway.
- Execute steps through Tool Gateway.
- Pause and resume for approvals.
- Write artifacts and trace events.
- Record cost and failure taxonomy.

### Tool Gateway

Responsibilities:

- Register tools and skills.
- Validate input/output schema.
- Enforce scopes and policies.
- Inject credentials through host-side secret boundary.
- Route calls to API connectors, MCP servers, browser skills, or sandbox commands.
- Emit audit and billing events for every call.

### Sandbox Adapter

Responsibilities:

- Hide whether execution is E2B, K8s Docker, or future microVM.
- Create isolated run environments.
- Execute code/browser/file operations.
- Upload/download artifacts.
- Capture logs and screenshots where relevant.
- Destroy or snapshot environment.

### Knowledge and Memory Services

Responsibilities:

- Document ingestion and chunking.
- Metadata and ACL persistence.
- Embedding and hybrid retrieval.
- Source citation.
- Memory write/read by scope.
- Redaction and sensitivity checks.

## 4. Public API Contract

Minimum REST/SSE contract:

```text
POST   /api/runs
GET    /api/runs/:run_id
GET    /api/runs/:run_id/events
POST   /api/runs/:run_id/cancel
POST   /api/runs/:run_id/approvals
GET    /api/runs/:run_id/artifacts

GET    /api/workspaces
POST   /api/workspaces
GET    /api/workspaces/:workspace_id/files

POST   /api/knowledge-bases
POST   /api/knowledge-bases/:kb_id/documents
GET    /api/knowledge-bases/:kb_id/documents

POST   /api/skills
GET    /api/skills
GET    /api/skills/:skill_id
POST   /api/skills/:skill_id/publish
POST   /api/skills/:skill_id/disable

GET    /api/billing/meters
GET    /api/audit-events
GET    /api/admin/users
POST   /api/admin/roles
```

`POST /api/runs` request:

```json
{
  "workspace_id": "workspace_123",
  "agent_id": "agent_sales_research",
  "message": "Research this prospect and prepare an outreach brief.",
  "attachments": ["file_123"],
  "mode": "autonomous"
}
```

`POST /api/runs` response:

```json
{
  "run_id": "run_123",
  "status": "created",
  "events_url": "/api/runs/run_123/events"
}
```

Run event stream event types:

```text
run.created
run.status_changed
message.delta
plan.created
step.started
step.completed
tool_call.started
tool_call.completed
approval.requested
approval.resolved
artifact.created
billing.metered
audit.recorded
run.failed
run.succeeded
```

## 5. Core Data Model

PostgreSQL tables should include:

```text
tenants
workspaces
users
roles
role_assignments
agents
runs
run_steps
run_events
tool_calls
skills
skill_versions
skill_permissions
knowledge_bases
knowledge_documents
document_chunks
memory_records
artifacts
storage_objects
approval_requests
audit_events
billing_meter_events
sandbox_sessions
```

Required multi-tenant columns:

```text
tenant_id
workspace_id
created_by_user_id
```

Every business table must include `tenant_id`. Workspace-bound tables must include `workspace_id`. Runtime-scoped tables should include `run_id`.

For PoC, enforce tenant isolation in service code. PostgreSQL RLS migration blocks, tenant session context setting, and live RLS verification against a non-superuser app role are started for critical tables such as runs, artifacts, knowledge documents, memory records, audit events, and billing meters; CI/private-deployment release gates remain required before production rollout.

## 5.1 Storage, Memory, and Identity Placement

Use separate storage layers by lifetime and risk:

| Data | Primary Store | Notes |
| --- | --- | --- |
| Tenant, workspace, user, role, role assignment | PostgreSQL | User rows store `password_hash`; never store raw passwords. |
| Run, run steps, approvals, audit, billing meters | PostgreSQL | Strong tenant/workspace/run identifiers on every row. |
| Short-term memory, run scratchpad, stream cursor | Redis | TTL-based and run-scoped; safe to expire; not a source of truth. |
| Long-term memory | PostgreSQL plus optional vector index | Scoped by user/team/company/agent/task; writes require validation. |
| Knowledge chunks and embeddings | PostgreSQL metadata plus candidate vector backend | pgvector is the recommended durable PoC candidate, but Q-003 must be answered before treating it as selected. |
| Artifacts, uploads, sandbox files, snapshots | S3/MinIO object storage | Metadata lives in `storage_objects`; access through signed URLs. |
| Secrets and connector credentials | Secret manager | Never place long-lived secrets inside sandbox or plain DB fields. |
| Sessions and access tokens | Redis or signed JWT plus revocation list | SSO provider configuration already controls password fallback; enterprise OIDC/SAML login can later replace password login. |

Identity and permission baseline:

- Password login is acceptable for local/dev and early PoC; enterprise tenants can disable password fallback for SSO-controlled domains, and OIDC/SAML login flows remain planned.
- Passwords must be hashed with configurable algorithm and iterations. Production should use per-user salt or a proven password hashing library.
- RBAC is the baseline: users receive roles; roles contain permissions as action/resource pairs.
- ABAC should be added for document sensitivity, workspace membership, network policy, model policy, and high-risk tool approvals.
- Every API request must resolve tenant, user, roles, and allowed workspace/resource scope before touching run, memory, storage, or skill data.

## 6. Agent Loop Design

The current runtime should keep a direct Pydantic execution path. If dependency compatibility is verified and review approves it, LangGraph can implement the run state machine behind the same runtime contract.

Recommended graph:

```text
start
  -> classify_intent_and_risk
  -> load_context
  -> create_plan
  -> policy_check
  -> execute_step
  -> observe_result
  -> evaluate_progress
  -> needs_approval?
      yes -> create_approval_request -> wait_for_approval -> execute_step
      no  -> more_steps?
              yes -> execute_step
              no  -> finalize_artifacts
  -> write_memory_candidates
  -> finish
```

State object fields:

```python
{
    "tenant_id": str,
    "workspace_id": str,
    "user_id": str,
    "run_id": str,
    "messages": list,
    "goal": str,
    "mode": str,
    "risk_level": str,
    "allowed_scopes": list,
    "retrieved_context": list,
    "plan": list,
    "current_step": dict | None,
    "tool_results": list,
    "approval_state": dict | None,
    "artifacts": list,
    "cost_snapshot": dict,
    "failure_reason": str | None
}
```

Important runtime rules:

- The runtime cannot call tools directly; it must call Tool Gateway.
- The runtime cannot retrieve documents without tenant/workspace/user ACL context.
- The runtime cannot write memory directly without Memory Service validation.
- The runtime must emit events after every meaningful transition.
- The runtime must persist state after each step.

## 7. Multi-Agent Design

First version should support bounded delegation.

Agents:

- `planner`: owns decomposition, step ordering, and final answer.
- `research`: retrieves web, knowledge, and document context.
- `browser`: operates browser sandbox through approved browser tools.
- `data`: executes code/table analysis through sandbox.
- `document`: creates reports and structured artifacts.
- `domain`: executes customer-specific skills.

Implementation rule:

- Model each sub-agent as either a callable runtime component or, if approved, a LangGraph node with restricted tool scopes.
- Never let sub-agents recursively spawn unlimited agents.
- Put max depth, max steps, max cost, and max wall-clock limits in policy.
- Surface sub-agent work as run steps for traceability.

## 8. Skill Manifest

Use a manifest close to MCP tool metadata, with enterprise governance fields. Call it MCP-style until actual MCP protocol integration is implemented and tested.

Example:

```yaml
id: ecommerce.competitor_price_monitor
version: 1.0.0
name: Competitor Price Monitor
description: Monitor competitor pricing and produce an operations report.
type: browser_skill
owner: solutions/ecommerce
input_schema:
  type: object
  required:
    - product_urls
  properties:
    product_urls:
      type: array
      items:
        type: string
output_schema:
  type: object
  properties:
    artifact_id:
      type: string
    price_changes:
      type: array
required_scopes:
  - browser.read
  - storage.write
  - knowledge.read:ecommerce
risk_level: medium
approval_required:
  - external_message.send
runtime:
  sandbox: browser
  timeout_seconds: 1800
billing_meters:
  - model_tokens
  - sandbox_minutes
  - browser_actions
tests:
  - tests/price_monitor_smoke.yaml
evals:
  - evals/report_format.yaml
```

Skill validation must reject:

- Missing required scopes.
- Invalid input/output schema.
- Undeclared external write action.
- Missing owner.
- Invalid version.
- Runtime timeout above tenant policy.

## 9. Knowledge and RAG Design

Use an internal knowledge service contract first. LlamaIndex can be added behind an adapter after tests prove ACL filtering remains enforced.

Required ingestion metadata:

```text
tenant_id
workspace_id
source_type
source_uri
source_document_id
uploaded_by_user_id
acl_subjects
sensitivity_level
document_version
content_hash
created_at
updated_at
```

Retrieval must filter by:

```sql
tenant_id = :tenant_id
AND workspace_id IN (:allowed_workspace_ids)
AND acl_subjects && :user_acl_subjects
AND sensitivity_level <= :user_clearance
```

Use a no-network in-memory retrieval fixture for tests. For a durable PoC, pgvector is the recommended candidate, but Q-003 must be answered before implementation. If retrieval volume or deployment constraints require it, evaluate Qdrant, Milvus, Weaviate, or a hosted vector provider behind the same retrieval contract.

## 10. Memory Design

Memory table fields:

```text
id
tenant_id
workspace_id
scope_type
scope_id
source_run_id
source_event_id
content
metadata
sensitivity_level
confidence
created_by
created_at
expires_at
status
```

Allowed `scope_type` values:

- `user`
- `team`
- `company`
- `agent`
- `task`

Memory write path:

```text
runtime proposes memory
  -> Memory Service validates scope and sensitivity
  -> optional user/admin approval
  -> write memory record
  -> emit audit event
```

## 11. Billing Design

Billing should start as meter-event logging.

Meter event fields:

```text
id
tenant_id
workspace_id
user_id
run_id
agent_id
skill_id
meter_type
quantity
unit
provider
model
cost_estimate
metadata
created_at
```

Meters:

- `model_tokens_input`
- `model_tokens_output`
- `model_tokens_cached_input`
- `model_call_count`
- `model_latency_ms`
- `sandbox_minutes`
- `browser_action_count`
- `tool_call_count`
- `storage_bytes`
- `artifact_bytes`
- `egress_bytes`
- `run_count`
- `skill_call_count`
- `trigger_invocation_count`
- `connector_invocation_count`

Do not hard-code pricing in runtime. Use a pricing config table or service so enterprise contracts can override price.

## 12. Audit and Policy Design

Audit events must be append-only.

Audit event examples:

- login and identity changes.
- knowledge document read.
- retrieval result used in an answer.
- tool call requested.
- tool call executed.
- external write attempted.
- approval requested/resolved.
- artifact created/downloaded.
- skill published/disabled.
- memory written/deleted.
- billing meter generated.

Policy checks should evaluate:

- role permission.
- tool scope.
- data sensitivity.
- network domain allow/deny list.
- model allow/deny list.
- sandbox type.
- budget limit.
- approval requirement.

## 13. Sandbox Strategy

PoC:

- Product flow calls `SandboxAdapter`. If Q-002 approves real cloud execution, add E2B or Kubernetes Docker behind `SandboxAdapter`; tests-only adapters stay outside product runs.
- Provide code execution and basic browser automation.
- Persist artifacts to object storage.
- Destroy sandbox by default after run unless policy allows snapshot.

Adapter interface:

```python
class SandboxAdapter:
    def create(self, tenant_id: str, workspace_id: str, run_id: str, template: str) -> str: ...
    def execute(self, session_id: str, command: str, timeout_seconds: int) -> dict: ...
    def upload_file(self, session_id: str, local_path: str, remote_path: str) -> dict: ...
    def download_file(self, session_id: str, remote_path: str) -> bytes: ...
    def snapshot(self, session_id: str) -> str: ...
    def destroy(self, session_id: str) -> None: ...
```

Future:

- Self-managed Sandbox Manager.
- Runtime images.
- Workspace snapshots.
- Browser sessions.
- microVM isolation.
- BYOC/private deployment.

## 14. Deployment Plan

First cloud deployment components:

```text
web
api
agent-runtime-worker
tool-gateway
sandbox-adapter
knowledge-service
billing-worker
postgres
redis
object-storage
observability-stack
```

Use Docker Compose for local development. Keep a Kubernetes manifest path as the likely cloud PoC deployment target, but do not treat it as frozen until deployment review confirms it. Keep secrets in a secret manager or Kubernetes secrets; never bake credentials into sandbox images.

## 15. Implementation Phases

### Phase 1: Domain and API Foundation

Implement:

- Shared domain schemas.
- FastAPI skeleton.
- PostgreSQL schema migrations.
- Run CRUD.
- Run event stream.
- Artifact metadata.
- Audit and meter event writes.

Verification:

- Unit tests for schema validation.
- API tests for run creation and event streaming.
- Cross-tenant access tests.

### Phase 2: Agent Runtime

Implement:

- Pydantic runtime state machine, with LangGraph adapter only after compatibility tests pass.
- OpenAI-compatible Model Gateway request/response contract.
- Tool Gateway request/response contract.
- Policy-check node.
- Approval pause/resume.
- Artifact creation node.

Verification:

- Full lifecycle integration test.
- Retry/failure test.
- Approval pause/resume test.

### Phase 3: Knowledge and Memory

Implement:

- Knowledge base CRUD.
- Document ingestion.
- Internal retrieval contract first; pgvector indexing only after Q-003 selects it for durable PoC.
- ACL-aware retrieval.
- Memory proposal and write validation.

Verification:

- User with access retrieves document.
- User without access cannot retrieve document.
- Memory write emits audit event.

### Phase 4: Tool Gateway and Skills

Implement:

- Skill manifest validation.
- Skill registry.
- Tool invocation boundary.
- Required scope checks.
- Meter and audit event generation.

Verification:

- Invalid manifest rejected.
- Missing scope blocks tool call.
- High-risk skill creates approval request.

### Phase 5: Sandbox Adapter

Implement:

- Sandbox Adapter contract.
- E2B or Kubernetes adapter only after Q-002 selects it.
- File upload/download.
- Sandbox execution event logging.

Verification:

- Contract tests may use tests-only fixture adapters that are never used in product flow.
- Smoke test runs against configured real adapter when credentials exist.

### Phase 6: Client Portal Slice

Implement the minimal local PoC workspace slice and keep the full portal as a later phase:

- Employee workspace.
- Run timeline.
- Artifact panel.
- Sandbox terminal output.
- Approval controls.
- Later admin console.
- Later skill marketplace.

Verification for the current and future frontend phases:

- Contract tests with typed API fixtures.
- Run event stream updates UI states.
- Permission-limited views hide forbidden data.
- Static contract test verifies `data-testid="chat-column"`.
- Static contract test verifies Enter sends and Shift+Enter creates a new line.
- Static contract test verifies the lower composer/help-text area remains reachable through `[data-testid="chat-column"] > div:nth-of-type(4) > div:nth-of-type(2)`.
- Later Playwright/component tests verify rendered desktop/mobile behavior.

## 16. Test Strategy

Required test categories:

- Unit tests for pure policy, schema, and validation functions.
- API tests for core endpoints.
- Integration tests for run lifecycle.
- Contract tests for sandbox adapters.
- ACL tests for tenant/workspace/role isolation.
- RAG tests for source filtering and citations.
- Billing tests for meter event completeness.
- Audit tests for sensitive event coverage.
- Frontend contract tests for the static workspace slice, with browser tests added when the frontend test stack is approved.

## 17. Acceptance Criteria

Architecture is ready for implementation when:

- Services and boundaries are clear.
- API contracts cover run, skills, knowledge, approvals, billing, and audit.
- Data model includes tenant/workspace/user/run isolation.
- Agent loop has an explicit state graph.
- Tool calls go through Tool Gateway.
- Sandbox implementation is replaceable.
- Knowledge retrieval is ACL-aware.
- Billing and audit are first-class from day one.
- Self-evolving is constrained to reviewed suggestions and versioned releases.

## 18. Reference Links

- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
- LangChain agents: https://docs.langchain.com/oss/python/langchain/agents
- LlamaIndex framework docs: https://developers.llamaindex.ai/python/framework/
- E2B docs: https://e2b.dev/docs
- MCP tools specification: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- Next.js App Router: https://nextjs.org/docs/app
- PostgreSQL row-level security: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- pgvector: https://github.com/pgvector/pgvector

## Verification

Run before approving this architecture:

```bash
rg -n "client portal|API/control plane|Agent Runtime|Tool Gateway|sandbox|Knowledge|Memory|Billing|Audit|Implementation Phases|Acceptance Criteria" docs/plans/2026-07-01-02-technical-architecture.md
rg -n "Current Repo Facts|Source-Backed Terminology|Implementation Wording Rules" docs/plans/research-grounding.md
python -m pytest -q
```

Expected final result: architecture terms are grounded in official sources, current repo capabilities are not overstated, and the existing backend tests still pass.

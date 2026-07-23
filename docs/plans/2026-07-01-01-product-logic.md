# Taroai Product Logic Implementation Plan


**Goal:** Define the product logic for an enterprise-deliverable Agent Cloud Workspace that gives employees governed cloud agent environments, shared knowledge, reusable skills, and lower cold-start cost.

**Architecture:** Taroai is designed around the `Run` lifecycle: an employee submits a task, the platform retrieves enterprise context, selects approved skills/tools, executes in a governed cloud workspace, delivers artifacts, and records memory, billing, and audit events. The product must separate employee execution, admin governance, skill publishing, and enterprise customization.

**Tech Stack:** Planned/candidate stack: Next.js client surfaces, FastAPI control plane, Pydantic backend models, candidate LangGraph/LangChain/LlamaIndex adapters, PostgreSQL, Redis, S3-compatible object storage, candidate vector backend such as pgvector, sandbox adapter, MCP-style skill manifest.

---

## 1. Product Positioning

Taroai is an enterprise Agent Cloud Workspace, not a consumer chatbot. The first enterprise value is not "a smarter blank chat box"; it is a controlled delivery platform where an enterprise can give employees agents that already understand company knowledge, approved tools, industry workflows, and custom skills.

The product should compete on:

- Cloud virtual environments for employees.
- Tenant/workspace/user permission boundaries.
- Shared enterprise knowledge and memory.
- Custom enterprise skills and reusable workflows.
- Skill marketplace for upload, approval, reuse, versioning, and analytics.
- Audit, billing, approval, and operational governance.

The product should not initially compete on:

- Fully open-ended consumer-style autonomous browsing.
- Unlimited multi-agent swarm behavior.
- Self-modifying production agents.
- Full Manus-level cloud desktop implementation from day one.

## 2. Enterprise vs To C Agent Logic

Consumer agents usually optimize for flexibility and personal productivity. Enterprise agents must optimize for delivery, governance, repeatability, and customization.

Key differences:

| Area | To C Agent | Taroai Enterprise Agent |
| --- | --- | --- |
| Starting point | Blank chat prompt | Prebuilt enterprise workspace, templates, skills |
| Knowledge | User-provided context | Shared knowledge bases with ACL-aware retrieval |
| Tools | Personal apps and browser | Approved enterprise tools through Tool Gateway |
| Execution | Flexible but opaque | Run lifecycle with logs, approvals, artifacts |
| Memory | Personal preference | User, team, company, agent, and task memory |
| Customization | Prompt-level | Deliverable custom skills, connectors, workflows |
| Governance | Minimal | RBAC, audit, billing, policy, approvals |
| Risk | User accepts risk | Enterprise controls data, actions, and costs |

Storage and identity are product boundaries, not implementation details:

- Redis-style short-term memory stores active run scratchpads, temporary plan state, streaming cursors, and tool observations that can expire.
- Long-term memory stores approved user/team/company/agent learnings and must be scoped by tenant, workspace, role, and sensitivity.
- PostgreSQL is the source of truth for tenants, users, roles, runs, approvals, billing, audit, memory metadata, and storage metadata.
- S3/MinIO object storage holds large artifacts, uploads, sandbox outputs, and snapshots.
- User accounts must store password hashes only; enterprise deployments should support SSO provider configuration and later OIDC/SAML protocol login.
- Permissions start with RBAC and expand into ABAC for document sensitivity, tool risk, workspace membership, and approval policies.

## 3. Client Surfaces

### Employee Client

The employee client is the daily workspace.

Required first-version surfaces:

- Chat/task input for natural language goals.
- Run timeline showing plan, steps, tool calls, approvals, errors, and completion.
- Artifact panel for generated reports, tables, files, pages, code, and downloaded assets.
- Workspace file area for uploads, sandbox outputs, and reusable documents.
- Virtual environment entry point for browser/code environment visibility.
- Agent library for selecting role-based agents such as sales, operations, support, research, and data analysis.

The first UI implementation must stay consistent with `https://agent.creao.ai/chat`. Treat the employee workspace as a chat-first execution surface, not a generic dashboard or marketing page.

Frontend baseline requirements:

- Preserve a primary chat column with `data-testid="chat-column"` so layout and tests can target the same semantic region.
- Keep the core chat column as a vertical flex layout with scrollable conversation content and a fixed lower composer region.
- The composer should match the CREAO-style behavior communicated by the reference text: pressing Enter sends, and Shift+Enter inserts a new line.
- The chat column structure must support the reference target shape: `[data-testid="chat-column"] > div:nth-of-type(4) > div:nth-of-type(2)` for the lower composer/help-text area.
- The first screen should center the employee on task input and agent execution, with run timeline/artifacts appearing as supporting panels rather than replacing the chat-first flow.
- Do not introduce a landing-page hero, marketing copy block, decorative card grid, or unrelated dashboard widgets into the employee workspace.
- Any frontend implementation must include a Playwright or component test that verifies the chat column test id, composer hint, Enter/Shift+Enter behavior, and stable chat-column layout.

### Admin Client

The admin client controls enterprise delivery.

Required first-version surfaces:

- Tenant, workspace, department, group, and user management.
- Role and permission assignment.
- Knowledge base management and sharing scope.
- Skill marketplace management.
- Approval policy configuration.
- Billing and usage dashboard.
- Audit event search.
- Model and sandbox usage policies.

### Skill Publisher Client

The skill publisher surface lets internal teams or solution engineers package repeatable enterprise capabilities.

Required first-version surfaces:

- Upload or create skill manifest.
- Define input/output schema.
- Declare required scopes, risk level, approval requirements, and billing meters.
- Attach tests and evaluation samples.
- Publish to tenant, workspace, department, or private scope.
- View usage, success rate, failure reasons, latency, and cost.

## 4. Core Product Objects

The product must be modeled around these objects:

| Object | Meaning |
| --- | --- |
| `Tenant` | A customer enterprise boundary. |
| `Workspace` | A team, department, project, or deployment workspace inside a tenant. |
| `User` | An employee or admin identity. |
| `Role` | A named permission bundle. |
| `Agent` | A configured role-specific agent template. |
| `Run` | One task execution lifecycle. |
| `RunStep` | One planned or executed unit inside a run. |
| `Skill` | A reusable governed capability. |
| `Tool` | Executable action exposed to an agent. |
| `KnowledgeBase` | Shared documents and indexes. |
| `MemoryRecord` | Durable context written by user/team/company/agent/task scope. |
| `Artifact` | A delivered file, report, table, page, script, or data output. |
| `ApprovalRequest` | A human decision gate for risky or policy-controlled actions. |
| `AuditEvent` | Immutable record of sensitive actions and data access. |
| `BillingMeter` | Metered usage event for tokens, sandbox, tool calls, storage, etc. |

## 5. Run Lifecycle

Every meaningful task is a `Run`. A run is more important than a chat message because it carries state, permissions, cost, artifacts, and auditability.

Required lifecycle:

1. Employee submits a goal in the workspace.
2. API creates a `Run` with tenant, workspace, user, agent, and initial message.
3. Intent classifier identifies domain, task type, and risk level.
4. Context retrieval loads allowed knowledge, memory, files, and prior run context.
5. Agent runtime creates an executable plan.
6. Policy Center checks permissions, data scope, tool scope, model policy, network policy, and cost policy.
7. Runtime executes steps through approved tools, connectors, browser, code, or workflow nodes.
8. High-risk actions pause the run and create `ApprovalRequest`.
9. Human approval resumes, modifies, or cancels the run.
10. Runtime delivers artifacts.
11. Platform writes audit events, billing meters, run trace, and optional memory.
12. User can reuse successful run patterns as a skill or agent template candidate.

Run status values:

- `created`
- `classifying`
- `retrieving_context`
- `planning`
- `awaiting_policy`
- `running`
- `awaiting_approval`
- `retrying`
- `succeeded`
- `failed`
- `cancelled`
- `timed_out`

## 6. Agent Modes

### Chat Agent

Use for lightweight enterprise help:

- Knowledge Q&A.
- Document lookup.
- Simple tool calls.
- Drafting and rewriting.
- Policy-safe internal answers.

### Workflow Agent

Use for repeatable enterprise SOP:

- Customer onboarding.
- Expense or approval process.
- Support ticket classification.
- Sales lead enrichment.
- E-commerce listing operations.

Workflow agent behavior should be more constrained than autonomous mode. The plan should mostly come from a configured workflow, not free-form reasoning.

### Autonomous Agent

Use for complex deliverables:

- Market research.
- Data analysis.
- Browser operations.
- Report generation.
- Multi-source information gathering.
- File transformation.

Autonomous mode must still be bounded by policy, budget, tool permissions, sandbox isolation, and approval gates.

## 7. Multi-Agent Product Logic

Taroai should support controlled expert collaboration, not uncontrolled swarm behavior.

First-version roles:

- `Planner Agent`: decomposes the task and coordinates execution.
- `Research Agent`: retrieves knowledge and web or document context.
- `Browser Agent`: performs browser-based operations through sandbox.
- `Data Agent`: runs code, SQL-like analysis, and table transformations.
- `Document Agent`: produces reports, docs, slides, and structured files.
- `Domain Skill Agent`: executes customer-specific skills.

Rules:

- The `Planner Agent` owns the final plan and final response.
- Sub-agents cannot call arbitrary tools; they only receive allowed tool scopes.
- Sub-agent outputs must be summarized into run trace.
- The user should see important sub-agent steps in the run timeline.
- A sub-agent cannot bypass approval, billing, or audit policy.

## 8. Knowledge Sharing

Knowledge sharing is a core enterprise feature.

Scopes:

- `personal`: only visible to the user.
- `workspace`: visible to members of a workspace.
- `department`: visible to department groups.
- `tenant`: visible to the whole enterprise, subject to ACL.
- `agent`: visible to a specific agent template.

Rules:

- Retrieval must be ACL-aware at query time, not only ingestion time.
- Every cited answer should include source metadata.
- Sensitive documents must carry sensitivity labels.
- Users cannot retrieve documents they cannot open in the source system.
- Shared knowledge must be versioned so runs can be reproduced.

## 9. Memory Logic

Memory is not the same as knowledge base. Knowledge is enterprise content; memory is learned operating context.

Memory layers:

| Memory Layer | Examples | Default Visibility |
| --- | --- | --- |
| `UserMemory` | preferred language, formatting, common customers | user only |
| `TeamMemory` | team SOP, accepted formats, common workflow rules | workspace/team |
| `CompanyMemory` | brand tone, company policies, product facts | tenant |
| `AgentMemory` | known failure cases, tool preferences, domain heuristics | agent owner and allowed users |
| `TaskMemory` | run state, intermediate decisions, temporary facts | run |

Write rules:

- Memory writes should be explicit or policy-approved.
- Sensitive data should be redacted or blocked.
- Memory records must include source run, author, timestamp, scope, and confidence.
- Memory can be disabled per tenant or workspace.

## 10. Skill Marketplace Logic

The skill marketplace is the main enterprise cold-start reduction mechanism.

Skill types:

- API Skill: call CRM, ERP, ticketing, inventory, or internal services.
- Browser Skill: operate a web admin page in sandbox.
- Document Skill: generate, parse, transform, or validate documents.
- Data Skill: analyze CSV, Excel, database extracts, or BI data.
- Communication Skill: draft or send email, Slack, Feishu, DingTalk, etc.
- Workflow Skill: package a multi-step SOP.
- Agent Template: package a role-specific agent configuration.

Publishing flow:

1. Create or upload skill manifest.
2. Validate schema.
3. Declare permissions and risk level.
4. Attach tests and evaluation examples.
5. Run sandbox validation where relevant.
6. Admin reviews and approves.
7. Publish to selected scope.
8. Track usage, success, failure, cost, and version adoption.

Skill governance:

- Skills must declare required scopes.
- Skills must declare approval requirements for risky actions.
- Skills must be versioned.
- Breaking changes require a new major version.
- Tenants can disable skills globally or per workspace.

## 11. Billing Logic

First version should record billing meters even if billing UI is simple.

Meter types:

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

Billing dimensions:

- tenant
- workspace
- user
- agent
- skill
- model
- sandbox type
- run

Enterprise admins should be able to view usage by department, user, agent, and skill.

## 12. Approval and Risk Logic

High-risk actions must pause the run.

Approval-triggering examples:

- Sending external messages.
- Posting to customer-facing systems.
- Writing CRM/ERP records.
- Submitting forms.
- Deleting or overwriting data.
- Payment, purchase, or contract actions.
- Accessing high-sensitivity documents.
- Exceeding budget or run time thresholds.

Approval decision values:

- `approved`
- `rejected`
- `approved_with_changes`
- `needs_more_context`
- `cancel_run`

## 13. Self-Evolving Logic

Self-evolving should be implemented as a controlled improvement pipeline, not as automatic production mutation.

Allowed first-version behavior:

1. Analyze failed or low-rated runs.
2. Classify failure reason.
3. Suggest prompt, workflow, tool, retrieval, or skill changes.
4. Generate a candidate patch or improvement note.
5. Run tests/evals.
6. Ask admin or skill owner to approve.
7. Publish a new version with rollback.

Blocked first-version behavior:

- Agent directly edits production skill.
- Agent bypasses review to change policy.
- Agent grants itself permissions.
- Agent changes connector credentials.
- Agent silently changes shared memory.

## 14. MVP Scope

MVP must prove enterprise delivery value.

P0:

- Employee chat/task workspace.
- Run lifecycle and event timeline.
- Cloud sandbox adapter.
- Basic knowledge base sharing.
- Skill manifest registration.
- Tenant/workspace/user permission model.
- Audit events.
- Meter events.
- Artifact storage and preview metadata.

P1:

- Admin console.
- Skill marketplace publishing flow.
- Approval policies.
- Team/company memory.
- Multi-agent controlled delegation.
- Billing dashboard.

P2:

- Self-evolving suggestion pipeline.
- Advanced skill analytics.
- BYOC/private deployment option.
- Custom connector builder.
- Sandbox snapshots and replay.

## 15. Acceptance Criteria

The product design is ready for technical implementation when:

- A user can start a run from chat.
- A run can be traced from input to plan, steps, tool calls, artifacts, cost, and audit.
- Admins can define what knowledge, skills, and tools employees can access.
- Skill owners can publish a reusable skill with scopes and tests.
- Knowledge retrieval respects workspace and role boundaries.
- Billing meters are generated for meaningful expensive operations.
- Risky actions require approval.
- Self-evolving is limited to suggestions and reviewed changes.

## Verification

Run before approving this product logic:

```bash
rg -n "Enterprise vs To C|Run Lifecycle|Skill Marketplace Logic|Memory Logic|Billing Logic|Self-Evolving Logic|MVP Scope|Acceptance Criteria" docs/plans/2026-07-01-01-product-logic.md
rg -n "Current Repo Facts|Source-Backed Terminology|Implementation Wording Rules" docs/plans/research-grounding.md
python -m pytest -q
```

Expected final result: product logic is reviewable against source-backed terminology, current-code facts, enterprise differentiation, MVP scope, and existing test gates.

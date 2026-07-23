# Billing, Audit, and Observability Implementation Plan


**Goal:** Make cost, audit, and traces first-class platform primitives so every run, model call, tool call, sandbox action, memory write, and artifact operation can be inspected and billed.

**Architecture:** Keep billing, audit, and observability as separate service boundaries. Use Pydantic meter/audit/trace models, PostgreSQL for durable events, Redis/queue for aggregation later, and OpenTelemetry-compatible spans for traces.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, OpenTelemetry, pytest, optional Langfuse/LangSmith adapter.

---

## Summary

Current state has billing meter records, including run-scoped and operation-level meters, a first-pass Pydantic pricing rule service that estimates meter cost without hard-coded runtime prices and supports global, tenant, and workspace-scoped pricing rules, in-memory and SQL-compatible pricing rule stores with management APIs, a first-pass billing invoice view and persisted invoice snapshots over tenant-scoped meters, tenant-scoped audit event records, a dedicated first-pass `taroai/audit` package, API audit writes plus identity/RBAC user-role lifecycle events routed through `AuditService`, safe embedding gateway usage audit records, embedding usage meters for standalone knowledge APIs and Agent Runtime retrieval, and a first-pass default enterprise audit coverage matrix with tenant coverage reports. This plan continues extracting durable contracts and service boundaries so enterprise admins can answer:

- Who did what?
- Which data was accessed?
- Which tool or model was called?
- How much did it cost?
- Why did a run fail?

## Task 1: Billing Package Structure

**Files:**

- Create: `apps/api/src/taroai/billing/__init__.py`
- Create: `apps/api/src/taroai/billing/models.py`
- Create: `apps/api/src/taroai/billing/service.py`
- Modify: `apps/api/src/taroai/domain.py`
- Test: `tests/api/test_billing_contract.py`

**Steps:**

1. Add architecture test requiring `billing/` package.
2. Move or mirror billing Pydantic models into `billing/models.py`.
3. Define `MeterType`, `MeterEventCreate`, `BillingMeterEvent`, and `CostEstimate`.
4. Implement in-memory billing service with `record_meter`, `list_by_tenant`, `summarize_by_run`.
5. Keep existing API behavior working.

**Acceptance Criteria:**

- Billing logic is not scattered across runtime/tool code.
- Every meter event has tenant, workspace, user, optional run, optional agent, optional skill, type, quantity, unit, provider, and timestamp.

## Task 2: Pricing Configuration

**Files:**

- Modify: `apps/api/src/taroai/billing/models.py`
- Modify: `apps/api/src/taroai/billing/service.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_billing_pricing.py`

**Steps:**

1. Add tests for pricing lookup by meter type/provider/model.
2. Add `PricingRule` Pydantic model.
3. Add cost estimate calculation separate from raw meter recording.
4. Pricing must be configurable per tenant; start with config-backed rules. Current implementation accepts optional tenant and workspace selectors on Pydantic pricing rules.
5. Do not hard-code provider prices inside Agent Runtime.

**Acceptance Criteria:**

- Meter recording works without pricing.
- Cost estimate is derived by billing service only.

**Current Implementation Notes:**

- `BillingPricingRule` and `BillingPricingService` are started in `apps/api/src/taroai/billing/`.
- `TAROAI_BILLING_PRICING_RULES` accepts a JSON list of pricing rules with optional tenant/workspace/skill selectors, meter type, unit, optional provider/model selectors, unit price, pricing unit quantity, and currency.
- `TAROAI_BILLING_PRICING_RULE_STORE_BACKEND` selects in-memory or SQL-compatible pricing rule persistence; API and worker startup merge settings-backed rules with persisted rules.
- Agent Runtime model token meters, Embedding Gateway token/call meters, and skill-backed runtime tool calls pass tenant/workspace/skill scope to the pricing service and can receive `cost_estimate` while still recording meters when no rule matches.
- Tiered pricing, discounts, tax/credit adjustments, invoice status workflow, external finance export, and pricing-rule versioning/approval workflow remain implementation work.

## Task 3: Audit Package Structure

**Files:**

- Create: `apps/api/src/taroai/audit/__init__.py`
- Create: `apps/api/src/taroai/audit/models.py`
- Create: `apps/api/src/taroai/audit/service.py`
- Test: `tests/api/test_audit.py`

**Steps:**

1. Define `AuditEventCreate`, `AuditEvent`, `AuditActor`, `AuditResource`, `AuditAction`.
2. Implement in-memory audit service with append-only semantics.
3. Add tests that audit events cannot be updated in place through public service methods.
4. Add tests for identity, role assignment, storage signed URL, memory write, tool call, approval, and artifact events.

**Acceptance Criteria:**

- Audit service is append-only.
- Sensitive payloads are redacted before storage.

**Current Implementation Notes:**

- `apps/api/src/taroai/audit/` defines Pydantic `AuditEventCreate`, `AuditActor`, `AuditResource`, `AuditAction`, `AuditCoverageRequirement`, `AuditCoverageFinding`, and `AuditCoverageReport` models and re-exports the persisted `AuditEvent` shape.
- `AuditService` records through the control-plane store, recursively redacts sensitive metadata keys, stamps settings-based audit retention metadata, enforces active-license `audit_retention_days` limits when runtime enforcement is enabled, returns defensive copies for records and tenant lists, and can check tenant audit events against a required coverage matrix.
- `GET /api/audit-events/coverage` is protected by `audit.read` and returns the current tenant's default enterprise coverage report.
- FastAPI business audit writes and audit list reads now route through `app.state.audit_service`; request-originated events include Pydantic actor attribution with tenant, user, actor type, IP address, and user agent in metadata.
- Agent Runtime model/tool audit events now route through injected `AuditService` and include Pydantic actor attribution from tenant/user context.
- Tenant bootstrap completion audit events now route through injected `AuditService`.
- Tool Gateway service-level blocked and approval-required audit events now route through injected `AuditService` and include Pydantic actor attribution from tenant/user context.
- In-memory and SQL identity services now emit user-created, user-disabled, role-created, and role-assigned audit events without password material.
- Default enterprise coverage requirements now name sensitive identity, RBAC, knowledge, embedding gateway, memory, tool, approval, storage, sandbox, browser, billing, and skill publication events with required metadata keys.
- In-memory and SQL control-plane meter writes now emit `billing.metered` audit records with meter IDs and meter types, approval resolution emits `approval.resolved`, and skill publication emits `skill.published`.
- OpenAI-compatible Embedding Gateway calls emit `embedding.gateway.called` audit metadata with purpose, provider, model, input count, embedding count, and provider usage only; standalone knowledge APIs and Agent Runtime retrieval calls emit `embedding_call_count` and `embedding_tokens` meters when workspace/user attribution exists.
- Event producers for remaining matrix items, actor attribution for bootstrap/identity/system paths, and SQL pushdown remain implementation work.

## Task 4: Trace and Run Observability

**Files:**

- Create: `apps/api/src/taroai/observability/__init__.py`
- Create: `apps/api/src/taroai/observability/models.py`
- Create: `apps/api/src/taroai/observability/service.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Test: `tests/api/test_observability_contract.py`

**Steps:**

1. Define `TraceSpan`, `TraceEvent`, `ErrorClassification`, and `RunTrace`.
2. Runtime records spans for classify, context load, planning, policy, tool call, sandbox, approval, artifact finalization.
3. Add error taxonomy: policy_denied, tool_failed, sandbox_failed, model_failed, timeout, approval_rejected, unknown.
4. Unit tests assert spans are created and failed runs include failure classification.
5. Keep adapter seam for OpenTelemetry/Langfuse/LangSmith.

**Acceptance Criteria:**

- Every run can be reconstructed as timeline + trace spans.
- Error reasons are categorized for debugging and future self-evolving analysis.

## Task 5: Service Integration

**Files:**

- Modify: `apps/api/src/taroai/agent/runtime.py`
- Modify: `apps/api/src/taroai/tool_gateway/service.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Modify: `apps/api/src/taroai/storage/catalog.py`
- Test: `tests/api/test_cross_service_billing_audit.py`

**Steps:**

1. Inject billing/audit/observability services into runtime/tool/memory/storage services.
2. Add tests for meter events on model calls, embedding calls with or without a run boundary, tool calls, sandbox minutes, storage bytes, and run count.
3. Add audit tests for memory write, storage object registration, signed URL, approval, and skill publication.
4. Ensure no raw secrets are stored in audit payloads.

**Acceptance Criteria:**

- Cost and audit coverage is not optional for expensive/sensitive operations.
- Tests fail if a new tool call path omits billing or audit.

## Task 6: Admin Query APIs

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Optional later split: `apps/api/src/taroai/routes/billing.py`, `routes/audit.py`, `routes/observability.py`
- Test: `tests/api/test_admin_billing_audit_api.py`

**Steps:**

1. Add `GET /api/billing/meters` filters by run, user, workspace, agent, skill, meter type.
2. Add `GET /api/billing/summary` grouped by workspace/user/agent.
3. Add `GET /api/audit-events` filters by action, resource, actor, run, date range.
4. Add `GET /api/runs/{run_id}/trace`.
5. Require admin/auditor permissions for tenant-wide reads.

**Current Implementation Notes:**

- `GET /api/billing/meters` and `GET /api/audit-events` exist and are tenant-scoped through request context.
- Tenant-wide billing reads now require `billing.read`; tenant-wide audit reads now require `audit.read`.
- `GET /api/billing/meters` supports first-pass filters for run, workspace, user, agent, skill, and meter type.
- `GET /api/billing/summary` supports first-pass grouped summaries by workspace, user, agent, skill, or meter type through `apps/api/src/taroai/billing/`.
- `GET /api/billing/invoice` supports period filters, billing meter filters, grouping by meter/workspace/user/agent/skill, priced totals, and unpriced event counts through a dedicated `BillingInvoiceService`; the route requires `billing.read`.
- `POST /api/billing/invoices`, `GET /api/billing/invoices`, and `GET /api/billing/invoices/{invoice_id}` support tenant-scoped invoice snapshot creation and retrieval through in-memory or SQL-compatible invoice stores; writes require `billing.manage` and emit safe invoice audit metadata.
- `GET` and `PUT /api/billing/pricing-rules` support tenant-scoped pricing rule read/manage paths through a dedicated pricing rule store; writes require `billing.manage`, refresh the effective pricing service, and emit safe pricing-rule audit metadata.
- `GET /api/audit-events` supports first-pass filters for event type, workspace, user, run, and created time range.
- `GET /api/runs/{run_id}/trace` returns a first-pass run trace with run state, run events, OTel-compatible `TraceSpan` entries, runtime-derived context/planning/step/tool/artifact/approval spans, sortable `TraceEvent` timeline entries, sanitized guardrail finding summaries, error classification, billing meters, and audit events through `apps/api/src/taroai/observability/`.
- A Settings-backed OTLP HTTP trace exporter boundary and `POST /api/runs/{run_id}/trace/export` route are started for safe run trace export. Live collector deployment verification, pagination, filter/summary pushdown into SQL, and route-module split remain implementation work.

**Acceptance Criteria:**

- Admins can answer cost and audit questions without raw database access.
- Employee users cannot read tenant-wide audit/billing data unless role permits it.

## Verification

Run after each task:

```bash
python -m pytest tests/api/test_billing_contract.py -q
python -m pytest tests/api/test_audit.py -q
python -m pytest tests/api/test_observability_contract.py -q
python -m pytest tests/api/test_cross_service_billing_audit.py -q
python -m pytest -q
```

Expected final result: billing, audit, and trace data are consistent across runs, tools, memory, storage, sandbox, and approvals.

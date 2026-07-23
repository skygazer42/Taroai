# Incident, SLO, and Support Operations Implementation Plan


**Goal:** Build operational controls for production incidents, customer support, SLO tracking, run quarantine, support access, and post-incident learning.

**Architecture:** Operations data is a first-class platform boundary. Incidents, alerts, support sessions, customer-impact events, run quarantines, and postmortems are typed Pydantic models linked to tenant, workspace, run, trace, audit, billing, and deployment metadata. Support access is time-bound, audited, and policy-controlled.

**Tech Stack:** FastAPI, Pydantic, pytest, OpenTelemetry-compatible traces, audit service, future alerting integrations.

---

## Summary

Enterprise customers will care about reliability and support workflow as much as feature depth. This plan makes production operations testable: how incidents are detected, triaged, escalated, communicated, resolved, and converted into fixes.

## Task 1: Incident Package and Models

**Files:**

- Create: `apps/api/src/taroai/incidents/__init__.py`
- Create: `apps/api/src/taroai/incidents/models.py`
- Create: `apps/api/src/taroai/incidents/service.py`
- Test: `tests/api/test_incident_models.py`

**Steps:**

1. Define `Incident` with ID, tenant scope, severity, status, summary, affected components, affected tenants, started at, resolved at, owner, and linked runs.
2. Define incident statuses: detected, triaging, mitigating, monitoring, resolved, and closed.
3. Define severities: sev1, sev2, sev3, sev4.
4. Add in-memory incident service for tests.
5. Add tests for create incident, status transition, invalid transition, and tenant-scoped visibility.

**Acceptance Criteria:**

- Incidents are structured objects.
- Tenant-specific incidents do not leak to other tenants.

**Current Implementation Notes:**

- `apps/api/src/taroai/incidents/` now defines Pydantic incident severity/status/create/read models and an in-memory incident service.
- Incident lifecycle transitions are constrained from detected through triaging, mitigating, monitoring, resolved, and closed; invalid transitions raise the shared transition error.
- `tests/api/test_incident_models.py` covers create, valid lifecycle transitions, invalid transition rejection, and tenant-scoped read/list behavior.

## Task 2: SLO and Error Budget Models

**Files:**

- Create: `apps/api/src/taroai/incidents/slo.py`
- Test: `tests/api/test_slo_error_budget.py`

**Steps:**

1. Define SLO targets for API availability, run creation latency, event stream availability, sandbox startup, model gateway availability, and connector sync success.
2. Define `SloWindow`, `SloMeasurement`, and `ErrorBudget`.
3. Support tenant-tier overrides for PoC, business, and enterprise.
4. Compute simple in-memory SLO status from measurement samples.
5. Add tests for healthy, warning, and breached states.

**Acceptance Criteria:**

- Reliability targets are explicit.
- Enterprise tiers can have stricter SLOs.

**Current Implementation Notes:**

- `apps/api/src/taroai/incidents/slo.py` now defines Pydantic SLO metrics, tiers, target direction, windows, measurements, error budgets, and status values.
- Default targets cover API availability, run creation latency, event stream availability, sandbox startup, model gateway availability, and connector sync success for PoC, business, and enterprise tiers; enterprise targets are stricter than business and PoC.
- `build_error_budget()` computes an in-memory average measurement, remaining error budget ratio, and healthy/warning/breached status.
- `tests/api/test_slo_error_budget.py` covers tier overrides plus healthy, warning, and breached states. Persistence, API exposure, alert linkage, and live metric ingestion remain later tasks.

## Task 3: Alert Routing and Escalation

**Files:**

- Create: `apps/api/src/taroai/incidents/alerts.py`
- Create: `docs/operations/alert-routing.md`
- Test: `tests/api/test_alert_routing.py`

**Steps:**

1. Define alert sources: API, worker, sandbox, model gateway, connector, billing, audit, storage, and frontend.
2. Define routing rules by severity, tenant tier, component, and business hours.
3. Add escalation policy model with primary, secondary, and executive escalation.
4. Emit audit event when customer-impacting alert is acknowledged.
5. Add tests for sev1 routing, business-hours routing, and tenant-tier escalation.

**Acceptance Criteria:**

- Alerts have owners and escalation paths.
- Customer-impacting alerts are auditable.

**Current Implementation Notes:**

- `apps/api/src/taroai/incidents/alerts.py` now defines Pydantic alert sources, alert create/read records, routing rules, escalation policies, route decisions, acknowledgements, and an in-memory routing service.
- Routing supports severity, source, component, tenant tier, priority, and UTC business-hours matching.
- Customer-impacting alert acknowledgements write safe `alert.acknowledged` audit metadata through the configured control-plane audit store without storing the raw alert summary.
- `docs/operations/alert-routing.md` documents the current routing model, audit boundary, and production follow-up work.
- `tests/api/test_alert_routing.py` covers sev1 executive escalation, business-hours routing, enterprise-tier escalation, and customer-impact acknowledgement audit.

## Task 4: Run Quarantine and Kill Switch

**Files:**

- Create: `apps/api/src/taroai/incidents/quarantine.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Modify: `apps/api/src/taroai/policy/service.py`
- Test: `tests/api/test_run_quarantine_kill_switch.py`

**Steps:**

1. Define quarantine states for run, agent, skill, connector, trigger, and tenant.
2. Add policy check that denies execution for quarantined resources.
3. Add kill switch model for disabling high-risk tool categories, external writes, model providers, or sandbox creation.
4. Emit audit events for quarantine and kill switch changes.
5. Add tests for quarantined run pause, quarantined skill denial, and tenant-level kill switch.

**Acceptance Criteria:**

- Operations can stop unsafe automation quickly.
- Kill switches do not require code deploys.

**Current Implementation Notes:**

- `apps/api/src/taroai/incidents/quarantine.py` now defines typed quarantine records for run, agent, skill, connector, trigger, and tenant targets, plus tenant-scoped kill switch records for high-risk tools, external writes, model providers, and sandbox creation.
- `InMemoryOperationalControlService` can enable run/resource quarantine and kill switches, records safe audit events for each change, and evaluates runtime execution/step policy decisions without storing prompts, tool inputs, or artifact content.
- `OperationalPolicyService` wraps the normal policy boundary with operational controls, so runtime blocking remains a policy decision rather than hard-coded tool behavior.
- `AgentRuntime` now checks operational policy before model planning and before each tool step; blocked runs move to `awaiting_policy`, emit `policy.blocked`, and avoid calling the model gateway, tool gateway, or automatic sandbox/session creation.
- `tests/api/test_run_quarantine_kill_switch.py` covers audit events, quarantined run blocking before planning, quarantined skill blocking before tool execution, sandbox creation kill switch behavior, and high-risk tool kill switch behavior.

## Task 5: Support Access and Customer Debugging

**Files:**

- Create: `apps/api/src/taroai/support/__init__.py`
- Create: `apps/api/src/taroai/support/models.py`
- Create: `apps/api/src/taroai/support/service.py`
- Test: `tests/api/test_support_access.py`

**Steps:**

1. Define `SupportSession` with tenant ID, requested by, approved by, scope, reason, expiration, status, and audit ID.
2. Support read-only access to run metadata, event timeline, redacted trace, artifact metadata, billing summary, and audit summary.
3. Require tenant owner approval for sensitive tenant debugging unless platform break-glass policy applies.
4. Redact prompts, documents, secrets, and artifact content by default.
5. Add tests for approved session, expired session, denied sensitive data, and break-glass audit.

**Acceptance Criteria:**

- Support can debug without uncontrolled customer data access.
- Every support access is time-bound and audited.

**Current Implementation Notes:**

- `apps/api/src/taroai/support/models.py` now defines Pydantic support session lifecycle models, access scopes, redacted run metadata, event summaries, artifact metadata, billing summaries, audit summaries, trace summaries, and a read-only run debug bundle.
- `apps/api/src/taroai/support/service.py` now provides an in-memory support access service with request, tenant-owner approval, break-glass approval, expiration enforcement, and safe audit events.
- Support debug bundles intentionally omit raw run messages, attachments, event payload values, artifact content, billing metadata values, and audit metadata values; they expose counts, IDs, event types, payload keys, metadata keys, and trace shape for debugging.
- Break-glass sessions are time-bound, approved by the requesting incident commander, and emit `support.session.break_glass` audit events with structured reason codes.
- `tests/api/test_support_access.py` covers approved redacted bundle generation, expired session denial, unapproved sensitive tenant debugging denial, and break-glass audit behavior. Persistence and FastAPI exposure remain later hardening work.

## Task 6: Postmortem and Improvement Linkage

**Files:**

- Create: `apps/api/src/taroai/incidents/postmortem.py`
- Create: `docs/operations/postmortem-template.md`
- Test: `tests/api/test_incident_postmortem.py`

**Steps:**

1. Define postmortem model with timeline, impact, root cause, contributing factors, remediation tasks, owners, due dates, and linked eval candidates.
2. Link incidents to self-evolving improvement candidates only after human review.
3. Record incident learnings as candidates, not direct production changes.
4. Add tests for required fields before closure.
5. Document customer-facing incident summary format.

**Acceptance Criteria:**

- Incidents feed improvement workflow safely.
- Closed incidents have owner, root cause, and follow-up actions.

**Current Implementation Notes:**

- `apps/api/src/taroai/incidents/postmortem.py` now defines structured postmortem timeline, impact, root cause, contributing factors, remediation tasks, owner, customer summary, linked runs, review metadata, and linked improvement candidate IDs.
- Incident closure through the postmortem service requires impact summary, root cause, timeline, remediation tasks, and customer-facing summary before moving a resolved incident to closed.
- Incident learnings create `pending_review` improvement candidates only after the postmortem has been human reviewed; candidates record target, source runs, risk, rationale, owner, and reviewer but do not apply production changes.
- `docs/operations/postmortem-template.md` documents the customer-safe postmortem format and the candidate review boundary, and release package required entries now include it.
- `tests/api/test_incident_postmortem.py` covers closure field enforcement, reviewed closure, human-review gating for candidates, and candidate creation without direct production publication.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_incident_models.py -q
python -m pytest tests/api/test_slo_error_budget.py -q
python -m pytest tests/api/test_alert_routing.py -q
python -m pytest tests/api/test_run_quarantine_kill_switch.py -q
python -m pytest tests/api/test_support_access.py -q
python -m pytest tests/api/test_incident_postmortem.py -q
python -m pytest -q
```

Expected final result: production operations can detect, triage, mitigate, support, audit, and learn from enterprise incidents without bypassing tenant controls.

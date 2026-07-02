# Incident, SLO, and Support Operations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

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

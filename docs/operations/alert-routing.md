# Alert Routing Runbook

This runbook defines the first-pass alert routing contract for Taroai private and
cloud deployments. It is a control-plane model and test contract, not a live
PagerDuty, Slack, email, or SMS integration.

## Current Scope

- Alert sources: API, worker, sandbox, model gateway, connector, billing, audit,
  storage, and frontend.
- Severity uses the incident severity model: `sev1`, `sev2`, `sev3`, and
  `sev4`.
- Tenant tiers use the SLO tier model: `poc`, `business`, and `enterprise`.
- Routing rules may match source, severity, tenant tier, component, and
  UTC business-hours windows.
- Escalation policy has primary, secondary, and executive contacts.
- Customer-impacting alert acknowledgement writes a safe audit event through the
  configured control-plane audit store.

## Routing Rules

Rules are evaluated by ascending `priority`. Empty match fields act as
wildcards. A business-hours-only rule matches Monday through Friday from
09:00 inclusive to 18:00 exclusive in UTC.

Suggested starting rules:

| Rule | Match | Contacts |
| --- | --- | --- |
| `sev1_customer_impact` | severity `sev1` | SRE primary, platform lead, executive |
| `enterprise_model_gateway` | source `model_gateway`, tier `enterprise` | model on-call, enterprise success |
| `business_hours_api` | source `api`, business hours | API day on-call |
| `default` | no explicit rule matched | platform on-call |

## Audit Boundary

Acknowledgement audit metadata intentionally excludes the alert summary and any
raw provider, request, prompt, artifact, or customer payload values. The current
metadata is limited to:

- alert ID
- source
- severity
- component
- customer-impacting flag
- acknowledging user ID

## Follow-Up Work

Production routing still needs provider adapters, delivery retries, contact
directory integration, persistence, APIs, and incident linkage.

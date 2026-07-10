# Customer Success Rollout Playbook

## Purpose

This playbook gives customer success, solution engineering, and tenant admins one repeatable path for moving a customer from discovery to production adoption. It should be used with `docs/plans/2026-07-01-13-enterprise-onboarding.md` and the selected solution pack baseline.

## Discovery

- Confirm business outcomes, target departments, first workflows, security constraints, and success metrics.
- Identify tenant owners, workspace admins, pilot users, approvers, data owners, and support contacts.
- Map required connectors, knowledge sources, custom skills, audit requirements, billing visibility, and deployment profile.
- Decide whether the first rollout uses ecommerce, sales, support, operations, or a customer-specific pack.

## Sandbox Tenant

- Create a sandbox tenant with non-production users, representative workspaces, and limited quotas.
- Install selected solution pack resources in disabled or draft mode until admins review them.
- Enable model, sandbox, browser, storage, audit, billing, and readiness checks for the tenant.
- Keep pilot credentials, secrets, and connector tokens separate from production.

## Data And Connector Setup

- Register connector definitions, OAuth or secret references, allowed scopes, and read/write approval gates.
- Load knowledge spaces for policies, SOPs, product docs, templates, and customer-specific references.
- Verify ACL-aware retrieval before pilot users can query knowledge.
- Run connector and knowledge readiness checks with tenant admins before enabling workflow execution.

## Pilot

- Select a narrow pilot group and a small number of repeated workflows.
- Run the solution pack workflows through the chat and task console.
- Review generated artifacts, approval pauses, audit events, billing meters, and feedback submissions weekly.
- Convert low-rated runs and repeated missing-skill feedback into evaluation or pack improvement candidates.

## Training

- Train tenant admins on tenant setup, roles, knowledge, skills, approvals, audit, billing, and incident contacts.
- Train employees on the chat and task console, artifacts, approvals, sharing, feedback, and safe use.
- Use the selected solution pack sample inputs as training exercises.
- Confirm that every pilot user knows when a human approval is required before external action.

## Production

- Promote approved skills, connector policies, knowledge spaces, and workspace templates from sandbox to production.
- Enable production tenant quotas, billing visibility, audit retention, and support escalation paths.
- Require go-live approval from customer owner, tenant admin, solution engineer, and security reviewer.
- Capture baseline adoption metrics during the first production week.

## Expansion

- Add new workspaces only after the first production workflows are stable.
- Reuse solution pack outcomes to prioritize the next department or custom skill.
- Compare active workspaces, completed runs, artifact downloads, approvals resolved, feedback submitted, and repeated workflows by workspace.
- Review pack improvements monthly and publish only after evaluation and approval.

## Go-Live Readiness

- Discovery outcomes are documented and mapped to a solution pack or custom skill backlog.
- Sandbox tenant passed readiness checks for model, sandbox, browser, storage, audit, and billing.
- Required connectors and knowledge spaces were tested with tenant ACLs.
- Admin and employee training sessions are complete.
- Approval gates are configured for external sends, write operations, regulated content, and sensitive data.
- Audit, billing, incident routing, and support access procedures are confirmed.
- Production rollout aligns with `docs/plans/2026-07-01-13-enterprise-onboarding.md`.

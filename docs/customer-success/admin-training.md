# Tenant Admin Training

## Purpose

This training outline prepares tenant admins to configure, govern, and support Taroai for their organization. It assumes the tenant has a sandbox workspace and at least one installed solution pack.

## Tenant Setup

- Review tenant profile, region, deployment profile, readiness checks, and owner account.
- Confirm workspace structure for the pilot department and production expansion.
- Validate model, sandbox, browser, storage, Redis, audit, and billing readiness before inviting users.

## Roles

- Assign tenant owner, workspace admin, approver, billing viewer, auditor, and standard employee roles.
- Grant least-privilege permissions for skills, connectors, knowledge, approvals, and storage artifacts.
- Review cross-tenant isolation expectations and support access policy.

## Knowledge

- Create knowledge spaces for policies, SOPs, product docs, templates, and customer-specific references.
- Configure document ownership, access controls, retention, and review cadence.
- Test retrieval with users who have different workspace and role scopes.

## Skills

- Review installed solution pack skills before enabling them.
- Keep high-risk or customer-specific skills disabled until policy and evaluation checks are complete.
- Use dry-run installation previews to understand conflicts, skipped resources, and approval requirements.

## Approvals

- Configure approval gates for external messages, connector writes, sensitive data, and regulated content.
- Assign approver groups and escalation contacts.
- Review approval history during pilot and adjust gates only after audit review.

## Audit

- Use audit trails to inspect login, connector, skill, tool, artifact, approval, and support access events.
- Confirm audit metadata avoids raw prompts, raw feedback comments, secret values, and sensitive artifact content.
- Export support bundles only through the redaction workflow.

## Billing

- Review operation-level meters for model usage, tool execution, sandbox time, browser captures, artifact storage, and solution pack activity.
- Check pilot cost trends before production rollout.
- Use billing visibility to identify workflows that need guardrails, quotas, or optimization.

## Practice Session

- Install one solution pack in dry-run mode.
- Enable one low-risk skill for a pilot workspace.
- Add one knowledge document and validate ACL-aware retrieval.
- Execute one task, approve one gated action, download one artifact, and inspect audit and billing events.

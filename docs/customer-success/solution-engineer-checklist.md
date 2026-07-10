# Solution Engineer Checklist

## Purpose

This checklist keeps custom solution delivery repeatable. It should be completed for each customer-specific pack, skill, or workflow before production use.

## Discovery

- Confirm business owner, tenant admin, pilot users, approvers, data owners, and support contacts.
- Capture the target workflow, expected inputs, expected artifacts, exception cases, and measurable outcomes.
- Identify required connectors, knowledge spaces, model policy scope, sandbox needs, browser needs, and approval gates.
- Decide whether the work extends an existing ecommerce, sales, support, or operations pack.

## Custom Skill Delivery

- Define skill intent, input schema, output artifact contract, allowed tools, required scopes, and disabled-by-default risk level.
- Keep customer-specific logic in versioned skill manifests or solution pack resources, not ad hoc production edits.
- Add evaluation cases for normal input, missing data, sensitive data, approval pause, and failure handling.
- Install in dry-run mode first and review conflicts, skipped resources, and approval requirements.

## Knowledge And Connector Readiness

- Verify every knowledge space has an owner, ACL policy, document source, retention plan, and review cadence.
- Verify connector definitions, OAuth or secret references, scopes, read/write policy, and retry behavior.
- Test tenant-isolated retrieval and connector access with pilot user roles.
- Confirm no workflow depends on unsupported production data paths.

## Evaluation And Approval

- Run sandbox tenant acceptance with representative inputs and expected artifacts.
- Check audit metadata, billing meters, approval events, and feedback capture for each workflow.
- Review content scanner, guardrail, and storage behavior for generated artifacts.
- Obtain customer owner, tenant admin, approver, and solution engineer sign-off before enabling production use.

## Go-Live

- Promote approved resources to production workspaces.
- Confirm readiness checks, quotas, alert routing, support access, and incident contacts.
- Train admins and employees with the selected workflow exercises.
- Schedule the first adoption review using active workspaces, completed runs, artifact downloads, approvals resolved, feedback submitted, and repeated workflows.

## Post-Go-Live Review

- Review failed runs, low-rated runs, missing-skill feedback, and repeated manual corrections.
- Convert eligible issues into evaluation candidates or solution pack improvement candidates.
- Publish pack updates only through review, approval, versioning, and rollback planning.

# Sales Solution Pack Baseline

## Business Outcomes

Help sales and customer success teams research accounts, prepare meetings, generate proposals, and keep CRM records updated without leaking customer context outside tenant controls.

## Taroai Resources

- Workspaces: sales, revenue-operations, customer-success.
- Agent templates: account-research-agent, proposal-agent, crm-update-agent, meeting-brief-agent.
- Skills: sales.account_research, sales.proposal_generator, sales.crm_update_assistant, sales.meeting_brief.
- Knowledge spaces: sales-playbook, pricing-policy, case-studies, legal-approved-terms.
- Evaluation cases: crm-field-safety, proposal-format, citation-required, approval-required-for-crm-write.

## Account Research

Builds account briefs from CRM records, approved web research, product usage summaries, and prior meeting notes.

## Proposal Generator

Drafts proposal outlines and commercial summaries from opportunity context, pricing policy, and approved templates.

## CRM Update Assistant

Prepares CRM updates from meeting notes and run artifacts. Writes require approval and connector policy checks.

## Meeting Brief

Creates a briefing artifact with stakeholders, recent activity, open risks, suggested agenda, and follow-up questions.

## Required Connectors

- CRM connector for accounts, opportunities, contacts, and activities.
- Calendar connector for meeting context.
- Internal API connector for product usage and contract metadata.

## Knowledge Spaces

- Sales playbook.
- Pricing and discount policy.
- Approved case studies.
- Legal-approved terms.

## Approval Gates

- CRM write operations require approval.
- Discount language requires approval.
- External proposal send requires approval.

## Sample Inputs

- Account ID.
- Opportunity ID.
- Meeting date.
- Proposal objective.

## Artifacts

- Account research brief.
- Proposal draft.
- CRM update summary.
- Meeting brief markdown.

## Success Metrics

- active_workspaces
- skills_installed
- runs_completed
- approvals_resolved
- repeated_workflows

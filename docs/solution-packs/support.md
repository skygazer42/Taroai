# Support Solution Pack Baseline

## Business Outcomes

Help support teams triage tickets, draft knowledge-grounded answers, review response quality, and escalate complex issues with consistent evidence.

## Taroai Resources

- Workspaces: support, support-quality, escalation.
- Agent templates: ticket-triage-agent, knowledge-answer-agent, qa-review-agent, escalation-agent.
- Skills: support.ticket_triage, support.knowledge_answer_draft, support.qa_review, support.escalation_summary.
- Knowledge spaces: help-center, support-macros, escalation-policy, product-known-issues.
- Evaluation cases: source-citation-required, pii-redaction-check, escalation-threshold, answer-tone.

## Ticket Triage

Classifies support tickets by product area, severity, customer tier, sentiment, and required next action.

## Knowledge Answer Draft

Drafts support replies grounded in approved help-center content and product known-issue notes.

## QA Review

Reviews support responses for policy fit, tone, required citations, and missing troubleshooting steps.

## Escalation Summary

Creates escalation artifacts with timeline, customer impact, attempted fixes, logs, and requested engineering action.

## Required Connectors

- Support ticket connector for ticket metadata and thread history.
- Knowledge base connector for articles and macros.
- Internal API connector for account tier and product status.

## Knowledge Spaces

- Help center.
- Support macros.
- Escalation policy.
- Product known issues.

## Approval Gates

- Customer-facing answer send requires approval for high-severity tickets.
- Escalations require support lead review.
- Any response containing sensitive account data requires approval.

## Sample Inputs

- Ticket ID.
- Customer tier.
- Product area.
- Escalation reason.

## Artifacts

- Ticket triage summary.
- Knowledge answer draft.
- QA review checklist.
- Escalation summary.

## Success Metrics

- active_workspaces
- skills_installed
- runs_completed
- feedback_submitted
- approvals_resolved

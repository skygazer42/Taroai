# Operations Solution Pack Baseline

## Business Outcomes

Help operations teams execute SOPs, clean spreadsheets, research vendors, and build recurring reports with auditable automation.

## Taroai Resources

- Workspaces: operations, finance-ops, vendor-management.
- Agent templates: sop-executor-agent, spreadsheet-cleanup-agent, vendor-research-agent, report-builder-agent.
- Skills: operations.sop_executor, operations.spreadsheet_cleanup, operations.vendor_research, operations.report_builder.
- Knowledge spaces: operating-sops, vendor-policy, reporting-templates, finance-controls.
- Evaluation cases: sop-step-completion, spreadsheet-output-format, vendor-source-citation, report-artifact-required.

## SOP Executor

Turns approved operating procedures into step-by-step task runs with evidence capture and approval pauses for risky actions.

## Spreadsheet Cleanup

Normalizes uploaded spreadsheets, detects missing columns, and generates cleaned artifacts under tenant storage controls.

## Vendor Research

Researches vendors from approved sources and produces comparison summaries with citations and risk notes.

## Report Builder

Builds recurring operations reports from governed inputs, prior artifacts, and approved templates.

## Required Connectors

- Internal API connector for operational records.
- Database connector for read-only reporting tables.
- Object storage for spreadsheet inputs and output artifacts.

## Knowledge Spaces

- Operating SOPs.
- Vendor policy.
- Reporting templates.
- Finance controls.

## Approval Gates

- External vendor outreach requires approval.
- Finance-sensitive report publication requires approval.
- SOP steps that change operational records require approval.

## Sample Inputs

- SOP ID.
- Spreadsheet object ID.
- Vendor list.
- Reporting period.

## Artifacts

- SOP execution log.
- Cleaned spreadsheet.
- Vendor comparison report.
- Operations report markdown.

## Success Metrics

- active_workspaces
- skills_installed
- runs_completed
- artifact_downloads
- repeated_workflows

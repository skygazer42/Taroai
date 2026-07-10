# MVP End-to-End Acceptance Scenario

This scenario is the first API-level proof that the cloud PoC can move from tenant setup to governed agent output.

## Scope

The acceptance path covers:

- Tenant bootstrap and owner login with Bearer auth.
- Tenant readiness.
- Knowledge base creation, document registration, and ACL-aware knowledge query.
- Run creation and inline execution.
- OpenAI-compatible Model Gateway boundary returning a two-step plan.
- `sandbox.command` producing `/workspace/artifacts/report.md`.
- Runtime artifact promotion into storage-backed run artifacts.
- Approval pause and approval resume for an external notification step.
- Run events, billing meters, audit events, and trace retrieval.
- Cross-tenant read rejection for tenant isolation.

## Expected Flow

1. Bootstrap tenant `acceptance` with an owner account.
2. Login as the owner and use the returned Bearer token for all API calls.
3. Create a workspace knowledge base and register a document with owner ACL.
4. Query knowledge with the matching ACL subject and clearance level.
5. Create an autonomous run in the starter workspace.
6. Execute the run.
7. Runtime creates a sandbox session, injects `session_id`, executes `sandbox.command`, and promotes `/workspace/artifacts/report.md`.
8. Runtime pauses before the approval-required notification step.
9. Approve the pending approval.
10. Runtime executes the approved step, destroys the sandbox session, and marks the run succeeded.
11. Read artifacts, storage object content, event stream, billing meters, audit events, and trace.
12. Login as a different tenant owner and confirm the first tenant run is not readable.

## Required Evidence

The test in `tests/api/test_mvp_end_to_end.py` proves:

- The readiness endpoint returns `ready=true`.
- Knowledge query returns the registered source document.
- The first execute response is `awaiting_approval`.
- Approval resume returns `succeeded`.
- The artifact list includes `report.md`.
- Downloaded artifact content contains `Governed artifact output from sandbox`.
- The event stream includes the ordered execution spine from `run.created` through `run.succeeded`.
- Billing includes run, model call, model token, and tool call meters.
- Audit includes model planning, tool execution, and approval resolution.
- Trace output includes run events, billing meters, audit events, and timeline events.
- Cross-tenant run read returns `403`.

## Current Boundary

This is an API-level acceptance test using local in-process services. It proves the control-plane and runtime contract without requiring external provider credentials. The live Compose verifier remains the deployment-level smoke test, and `local_cloud_poc_verification --require-model-execution` remains the strict live model gate once a real OpenAI-compatible provider is configured.

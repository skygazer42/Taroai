from urllib.parse import parse_qs, urlparse

import json
import pytest
from pydantic import ValidationError

from taroai.deployment.local_cloud_poc_verification import (
    LocalCloudPocHttpResponse,
    LocalCloudPocVerificationConfig,
    LocalCloudPocVerificationResult,
    assert_status,
    parse_args,
    safe_result_json,
    verify_local_cloud_poc,
    verify_web,
    write_safe_result_json,
)


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db4"
    "0000000049454e44ae426082"
)


def count_sse_events(body: str) -> int:
    return sum(1 for line in body.splitlines() if line.startswith("event: "))


def sse_sequence_label(body: str) -> str:
    sequences: list[int] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: ").strip())
        sequence = payload.get("sequence")
        if sequence is not None:
            sequences.append(int(sequence))
    if not sequences:
        return "No sequence"
    if sequences[0] == sequences[-1]:
        return f"#{sequences[0]} monotonic"
    return f"#{sequences[0]}-#{sequences[-1]} monotonic"


def sse_event_types(body: str) -> list[str]:
    return [
        line.removeprefix("event: ").strip()
        for line in body.splitlines()
        if line.startswith("event: ")
    ]


def sse_event_closure_label(body: str) -> str:
    event_types = sse_event_types(body)
    plan_indexes = [
        index
        for index, event_type in enumerate(event_types)
        if event_type in {"plan.created", "model.plan.created"}
    ]
    plan_index = min(plan_indexes) if plan_indexes else -1
    skill_index = (
        event_types.index("skill.workflow_invoked")
        if "skill.workflow_invoked" in event_types
        else -1
    )
    command_index = (
        event_types.index("sandbox.command.executed")
        if "sandbox.command.executed" in event_types
        else -1
    )
    browser_index = (
        event_types.index("browser.action.performed")
        if "browser.action.performed" in event_types
        else -1
    )
    artifact_index = (
        event_types.index("sandbox.artifact.promoted")
        if "sandbox.artifact.promoted" in event_types
        else -1
    )
    succeeded_index = (
        event_types.index("run.succeeded")
        if "run.succeeded" in event_types
        else -1
    )
    if command_index == -1:
        return "Waiting for command"
    if artifact_index == -1:
        return "Waiting for artifact"
    if succeeded_index == -1:
        return "Waiting for success"
    if not (command_index < artifact_index < succeeded_index):
        return "Closure out of order"
    if plan_index != -1:
        if not (plan_index < command_index):
            return "Closure out of order"
    if skill_index != -1 and not (skill_index < command_index):
        return "Closure out of order"
    if browser_index != -1 and not (browser_index < succeeded_index):
        return "Closure out of order"
    stages = [
        (plan_index if plan_index != -1 else None, "plan"),
        (skill_index if skill_index != -1 else None, "skill"),
        (command_index, "command"),
        (browser_index if browser_index != -1 else None, "browser"),
        (artifact_index, "artifact"),
        (succeeded_index, "succeeded"),
    ]
    return " -> ".join(
        label for _, label in sorted(stage for stage in stages if stage[0] is not None)
    )


def verifier_skill_manifest() -> dict:
    return {
        "id": "sales.erp_invoice_matching",
        "version": "1.0.0",
        "name": "ERP Invoice Matching",
        "description": "Match ERP invoices against renewal account data.",
        "type": "workflow_skill",
        "owner": "solutions/sales",
        "input_schema": {
            "type": "object",
            "required": ["account_id"],
            "properties": {"account_id": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["matches"],
            "properties": {"matches": {"type": "array", "items": {"type": "object"}}},
        },
        "required_scopes": ["erp.invoice.read"],
        "risk_level": "medium",
        "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
        "billing_meters": ["tool_call_count"],
        "tests": [],
        "evals": [],
    }


def verifier_solution_pack_entry(version: str, status: str, skills: list[dict]) -> dict:
    return {
        "tenant_id": "tenant_acme",
        "manifest": {
            "id": "sales.renewal_ops",
            "version": version,
            "name": "Renewal Operations",
            "description": "Starter solution pack.",
            "industry": "sales",
            "use_cases": ["renewal operations"],
            "skills": skills,
            "success_metrics": [],
            "rollout_checklist": [],
        },
        "status": status,
        "created_by_user_id": "user_owner",
        "created_at": "2026-07-03T14:00:00Z",
        "updated_at": "2026-07-03T14:00:00Z",
    }


class RecordingHttpClient:
    def __init__(
        self,
        model_gateway_configured: bool = False,
        workspace_html: str | None = None,
        workspace_script: str | None = None,
        artifacts_body: str | None = None,
        storage_objects_body: str | None = None,
        storage_object_content: str | None = None,
        storage_object_contents: dict[str, str] | None = None,
        run_events_body: str | None = None,
        run_trace_body: str | None = None,
        skill_run_events_body: str | None = None,
        skill_run_agent_id: str | None = "sales.erp_invoice_matching",
        skill_run_workspace_id: str = "workspace_acme",
        runtime_state_body: str | None = None,
        workspace_auth_statuses: list[str] | None = None,
        workspace_bootstrap_statuses: list[str] | None = None,
        workspace_readiness_statuses: list[str] | None = None,
        workspace_readiness_model_statuses: list[str] | None = None,
        workspace_readiness_sandbox_statuses: list[str] | None = None,
        workspace_status_text: str = "succeeded",
        workspace_status_texts: list[str] | None = None,
        workspace_artifact_text: str = "report.md",
        workspace_delivery_chain_status: str = "Delivery chain complete",
        workspace_delivery_chain_artifact_storage_id: str = "storage_report_1",
        workspace_delivery_chain_terminal_storage_id: str = (
            "storage_model_sandbox_output_1"
        ),
        workspace_delivery_chain_browser_storage_id: str = "--",
        workspace_browser_storage_id: str = "--",
        workspace_browser_preview_storage_id: str = "--",
        workspace_artifact_preview_storage_id: str = "storage_report_1",
        workspace_artifact_downloaded_storage_id: str = "storage_report_1",
        workspace_terminal_output_storage_id: str | None = None,
        workspace_terminal_output_uri: str | None = None,
        workspace_selected_history_sandbox_session_id: str = (
            "runtime_skill_sandbox_1"
        ),
        workspace_run_feedback_persists: bool = True,
        workspace_missing_skill_feedback_persists: bool = True,
        workspace_eval_candidate_persists: bool = True,
        workspace_eval_candidate_review_persists: bool = True,
        workspace_pack_candidate_persists: bool = True,
        workspace_pack_candidate_review_persists: bool = True,
        workspace_draft_apply_persists: bool = True,
        workspace_solution_pack_install_persists: bool = True,
        sandbox_configured: bool = True,
        sandbox_missing: list[str] | None = None,
        sandbox_provider: str = "local_process",
        sandbox_destroy_body: str | None = None,
        browser_delete_empty_response: bool = False,
        browser_unauthenticated_sessions_status_code: int | None = None,
        browser_unauthenticated_global_sessions_status_code: int | None = None,
        browser_unauthenticated_capabilities_status_code: int | None = None,
        browser_capabilities_body: str | None = None,
    ):
        self.model_gateway_configured = model_gateway_configured
        self.sandbox_configured = sandbox_configured
        self.sandbox_missing = list(sandbox_missing or [])
        self.sandbox_provider = sandbox_provider
        self.browser_delete_empty_response = browser_delete_empty_response
        self.browser_unauthenticated_sessions_status_code = (
            browser_unauthenticated_sessions_status_code
        )
        self.browser_unauthenticated_global_sessions_status_code = (
            browser_unauthenticated_global_sessions_status_code
        )
        self.browser_unauthenticated_capabilities_status_code = (
            browser_unauthenticated_capabilities_status_code
        )
        self.browser_capabilities_body = browser_capabilities_body or json.dumps(
            {
                "provider": "playwright",
                "auth_required": True,
                "session_ttl_enforced": True,
                "max_session_ttl_seconds": 1800,
                "max_sessions": 50,
                "max_sessions_per_tenant": 20,
                "max_sessions_per_run": 3,
                "navigation_allowlist_enforced": True,
                "navigation_allowed_host_count": 2,
            },
            separators=(",", ":"),
        )
        self.workspace_eval_candidate_reviewed = False
        self.workspace_eval_candidates_generated = False
        self.workspace_eval_candidate_persists = workspace_eval_candidate_persists
        self.workspace_eval_candidate_review_persists = (
            workspace_eval_candidate_review_persists
        )
        self.workspace_pack_candidates_generated = False
        self.workspace_pack_candidate_persists = workspace_pack_candidate_persists
        self.workspace_pack_candidate_review_persists = (
            workspace_pack_candidate_review_persists
        )
        self.workspace_pack_candidate_reviewed = False
        self.workspace_draft_apply_persists = workspace_draft_apply_persists
        self.workspace_solution_pack_install_persists = (
            workspace_solution_pack_install_persists
        )
        self.workspace_solution_pack_install_recorded = False
        self.workspace_missing_skill_feedback_count = 0
        self.workspace_missing_skill_feedback_persists = (
            workspace_missing_skill_feedback_persists
        )
        self.workspace_draft_status = "No draft selected"
        self.workspace_solution_pack_installed = False
        self.workspace_skill_invoked = False
        self.workspace_run_history_refreshed = False
        self.workspace_selected_history_run_id = None
        self.workspace_run_feedback_recorded = False
        self.workspace_run_feedback_persists = workspace_run_feedback_persists
        self.workspace_skill_feedback_recorded = False
        self.workspace_downloaded_storage_object_id = None
        self.workspace_delivery_chain_status = workspace_delivery_chain_status
        self.workspace_delivery_chain_artifact_storage_id = (
            workspace_delivery_chain_artifact_storage_id
        )
        self.workspace_delivery_chain_terminal_storage_id = (
            workspace_delivery_chain_terminal_storage_id
        )
        self.workspace_delivery_chain_browser_storage_id = (
            workspace_delivery_chain_browser_storage_id
        )
        self.workspace_browser_storage_id = workspace_browser_storage_id
        self.workspace_browser_preview_storage_id = workspace_browser_preview_storage_id
        self.workspace_artifact_preview_storage_id = workspace_artifact_preview_storage_id
        self.workspace_artifact_downloaded_storage_id = (
            workspace_artifact_downloaded_storage_id
        )
        self.workspace_terminal_output_storage_id = (
            workspace_terminal_output_storage_id
            or workspace_delivery_chain_terminal_storage_id
        )
        self.workspace_terminal_output_uri = workspace_terminal_output_uri
        self.workspace_selected_history_sandbox_session_id = (
            workspace_selected_history_sandbox_session_id
        )
        self.solution_pack_registered = False
        self.solution_pack_applied = False
        self.workspace_html = workspace_html or (
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="workspace-id" value="workspace_sales" autocomplete="off" />'
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<input id="tenant-slug" />'
            '<input id="owner-display-name" />'
            '<input id="bootstrap-token" type="password" />'
            '<button id="bootstrap-login-button">Bootstrap</button>'
            '<span data-bootstrap-status>Not bootstrapped</span>'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<section data-testid="run-controls">'
            '<span data-run-control-status>No active run</span>'
            '<button id="cancel-run-button">Cancel</button>'
            '<button id="retry-run-button">Retry</button>'
            "</section>"
            '<section data-testid="run-history">'
            '<span data-run-history-status>No runs loaded</span>'
            '<button data-run-history-refresh>Refresh</button>'
            '<ul data-run-history-list><li>No runs.</li></ul>'
            "</section>"
            '<span data-browser-storage-object>--</span>'
            '<span data-browser-preview-storage-object '
            'data-browser-preview-storage-object-id="">--</span>'
            '<span data-artifact-download-status data-download-state="idle">'
            "No artifact downloaded</span>"
            '<span data-artifact-downloaded-storage-object '
            'data-download-storage-object-id="">--</span>'
            '<span data-artifact-preview-status>Preview idle</span>'
            '<span data-artifact-preview-title>No artifact selected</span>'
            '<span data-artifact-preview-storage-object '
            'data-preview-storage-object-id="">--</span>'
            '<pre data-artifact-preview-content>Select an artifact preview.</pre>'
            '<div data-delivery-summary data-delivery-state="waiting">'
            "No artifacts delivered</div>"
            '<section data-testid="delivery-chain">'
            '<span data-delivery-chain-status data-delivery-chain-state="waiting">'
            "No delivery chain</span>"
            '<span data-delivery-chain-run>--</span>'
            '<span data-delivery-chain-sandbox>--</span>'
            '<span data-delivery-chain-artifact-storage>--</span>'
            '<span data-delivery-chain-terminal-storage>--</span>'
            '<span data-delivery-chain-browser-storage>--</span>'
            "</section>"
            '<section data-testid="event-integrity">'
            '<span data-event-integrity-status data-event-integrity-state="waiting">'
            "No event stream</span>"
            '<span data-event-integrity-count>--</span>'
            '<span data-event-integrity-sequence>--</span>'
            '<span data-event-integrity-closure>--</span>'
            "</section>"
            '<div data-run-feedback-panel>'
            '<span data-run-feedback-status data-run-feedback-state="waiting">'
            "Feedback unavailable</span>"
            '<button id="run-feedback-positive">Useful</button>'
            '<button id="run-feedback-negative">Needs work</button>'
            "</div>"
            '<section data-testid="approval-panel">'
            '<span data-approval-status>Clear</span>'
            '<p data-approval-copy>No pending approval.</p>'
            '<div data-approval-resolution data-resolution-state="idle">'
            "No approval decision yet.</div>"
            '<button id="approve-button">Approve</button>'
            '<button id="reject-button">Reject</button>'
            "</section>"
            '<section data-testid="solution-pack-panel">'
            '<span data-solution-pack-status>No packs loaded</span>'
            '<ul data-solution-pack-list><li>No solution packs.</li></ul>'
            '<button data-solution-pack-refresh>Refresh packs</button>'
            '<button id="install-solution-pack-button">Install to workspace</button>'
            '<span data-solution-pack-install-status>Select a published pack</span>'
            "</section>"
            '<section data-testid="workspace-skills-panel">'
            '<span data-skills-status>No skills loaded</span>'
            '<ul data-skills-list><li>No installed skills.</li></ul>'
            '<button data-skills-refresh>Refresh skills</button>'
            '<textarea id="skill-invoke-input"></textarea>'
            '<button id="invoke-skill-button">Invoke skill</button>'
            '<span data-skill-invoke-status>Select a ready skill</span>'
            "</section>"
            '<strong data-cs-missing-skill-status>Request idle</strong>'
            '<input id="cs-missing-skill-name" />'
            '<textarea id="cs-missing-skill-comment"></textarea>'
            '<input id="cs-missing-skill-solution-pack" />'
            '<button id="cs-submit-missing-skill">Record request</button>'
            '<span data-cs-candidate-action-status>Candidate actions idle</span>'
            '<button id="cs-create-eval-candidates">Generate eval candidates</button>'
            '<button id="cs-create-pack-candidates">Generate pack candidates</button>'
            '<span data-cs-eval-candidate-selected>No eval candidate selected</span>'
            '<button id="cs-accept-eval-candidate">Accept eval</button>'
            '<button id="cs-reject-eval-candidate">Reject eval</button>'
            '<span data-cs-pack-candidate-selected>No pack candidate selected</span>'
            '<button id="cs-accept-pack-candidate">Accept pack</button>'
            '<button id="cs-reject-pack-candidate">Reject pack</button>'
            '<section data-testid="run-trace">'
            '<span data-trace-status>Not loaded</span>'
            '<span data-trace-span-count>--</span>'
            '<span data-trace-event-count>--</span>'
            '<span data-trace-billing-count>--</span>'
            '<span data-trace-audit-count>--</span>'
            '<span data-trace-error-classification>No error</span>'
            '<ul data-trace-list><li>No trace loaded.</li></ul>'
            "</section>"
            '<section data-testid="runtime-state">'
            '<span data-runtime-state-status>Not loaded</span>'
            '<span data-runtime-current-step>--</span>'
            '<span data-runtime-completed-count>--</span>'
            '<span data-runtime-sandbox-session>--</span>'
            '<span data-runtime-browser-session>--</span>'
            '<span data-runtime-artifact-count>No promoted artifacts</span>'
            "</section>"
            '<section data-testid="execution-loop">'
            '<span data-execution-summary>No active run</span>'
            '<span data-execution-model-route>No model route</span>'
            '<span data-execution-run>Idle</span>'
            '<span data-execution-plan>Waiting</span>'
            '<span data-execution-sandbox>Waiting</span>'
            '<span data-execution-browser>Waiting</span>'
            '<span data-execution-artifact>Waiting</span>'
            "</section>"
            '<section data-testid="run-evidence">'
            '<span data-evidence-summary>No run evidence</span>'
            '<span data-evidence-plan>Waiting</span>'
            '<span data-evidence-sandbox>Waiting</span>'
            '<span data-evidence-artifact>Waiting</span>'
            '<span data-evidence-browser>Waiting</span>'
            '<span data-evidence-terminal>Waiting</span>'
            "</section>"
            '<section data-testid="sandbox-terminal">'
            '<span data-terminal-status>Waiting</span>'
            '<span data-terminal-output-storage-object '
            'data-terminal-storage-object-id="">--</span>'
            '<pre data-terminal-output>stdout and stderr will appear here.</pre>'
            "</section>"
            '<script src="./assets/main.js" type="module"></script>'
        )
        default_readiness_status = (
            "Preflight ready"
            if model_gateway_configured and sandbox_configured
            else "Preflight needs config"
        )
        default_readiness_model = (
            "Model ready"
            if model_gateway_configured
            else "Model missing: model, credential"
        )
        default_readiness_sandbox = (
            f"Sandbox PoC: {sandbox_provider}"
            if sandbox_configured
            else f"Sandbox {sandbox_provider} missing: {', '.join(self.sandbox_missing)}"
        )
        self.workspace_script = workspace_script or (
            "applyUrlConfiguration();"
            "new URLSearchParams(window.location.search);"
            'apiBase: "taroai.apiBase";'
            'tenantId: "taroai.tenantId";'
            'userId: "taroai.userId";'
            'workspaceId: "taroai.workspaceId";'
            'email: "taroai.authEmail";'
            "state[key] = value;"
            'urlParams.delete("accessToken");'
            'urlParams.delete("password");'
            "window.history.replaceState();"
            'apiFetch("/api/tenants/bootstrap");'
            'headers["X-Bootstrap-Token"] = bootstrapToken;'
            "tenant_slug: state.tenantSlug;"
            "owner_display_name: state.ownerDisplayName;"
            "owner_password: elements.loginPassword.value;"
            "result.starter_workspace_id;"
            'elements.bootstrapToken.value = "";'
            'localStorage.setItem("taroai.tenantSlug");'
            'localStorage.setItem("taroai.ownerDisplayName");'
            "bootstrapTenant();"
            'apiFetch("/api/auth/login");'
            'apiFetch("/readyz");'
            "result.tenant_id;"
            "result.user_id;"
            "state.workspaceId;"
            "const modelGateway = checks.model_gateway || {};"
            "const sandbox = checks.sandbox || {};"
            "const missing = modelGateway.missing || [];"
            "missing.join(', ');"
            'headers["Authorization"] = "Bearer ";'
            "`/api/runs/${state.currentRunId}/cancel`;"
            "`/api/runs/${state.currentRunId}/retry`;"
            "renderRunControls();"
            "loadRunHistory();"
            "renderRunHistory();"
            "selectRunFromHistory();"
            "data-run-history-id;"
            "renderBrowserPreviewStorageObject();"
            "browserPreviewStorageObject;"
            "dataset.browserPreviewStorageObjectId;"
            "previewArtifact();"
            "renderArtifactPreview();"
            "artifactPreviewStorageObject;"
            "dataset.previewStorageObjectId;"
            "data-preview-storage-object-id;"
            "data-artifact-download-status;"
            "renderArtifactDownloadStatus();"
            "artifactDownloadedStorageObject;"
            "dataset.downloadStorageObjectId;"
            "dataset.downloadState;"
            "renderDeliverySummary();"
            "elements.deliverySummary;"
            "dataset.deliveryState;"
            "downloadableArtifacts();"
            "renderDeliveryChain();"
            "buildDeliveryChainEvidence();"
            "deliveryChainStatus;"
            "deliveryChainRun;"
            "deliveryChainSandbox;"
            "deliveryChainArtifactStorage;"
            "deliveryChainTerminalStorage;"
            "deliveryChainBrowserStorage;"
            "renderEventIntegrity();"
            "buildEventIntegrityEvidence();"
            "eventIntegrityStatus;"
            "eventIntegritySequence;"
            "eventIntegrityClosure;"
            "event stream sequence;"
            "eventIdentity(event);"
            "eventStreamIntegrityIssues;"
            "recordEventStreamIntegrityIssues(newEvents);"
            "eventAlreadyLoaded(event);"
            "compareEventsBySequence;"
            "state.events.sort(compareEventsBySequence);"
            "lastFiniteEventSequence(state.events);"
            "eventSequence(event);"
            "eventsMissingSequence;"
            "event stream sequence is missing;"
            "incoming event stream sequence is not monotonic;"
            "eventLineType;"
            "eventLineId;"
            "dataLines;"
            "dataLines.join;"
            "parsed.type = parsed.type || eventLineType;"
            "parsed.id = parsed.id || eventLineId;"
            "readyStorageBackedArtifacts();"
            "autoPreviewFirstDeliveredArtifact();"
            "previewedRunIds;"
            "state.previewedRunIds.has(state.currentRunId);"
            "state.previewedRunIds.add(state.currentRunId);"
            "feedbackSubmittedRunIds;"
            "renderRunFeedback();"
            "submitRunFeedback();"
            "state.pendingApprovalId;"
            "approvalResolution;"
            "latestApprovalEvent();"
            "renderApprovalResolution();"
            "approvalResolutionParts();"
            "payload.approval_id;"
            "payload.resolved_by_user_id;"
            'event.type === "approval.resolved";'
            'event.type === "approval.rejected";'
            "`/api/runs/${state.currentRunId}/approvals`;"
            "`/api/runs/${state.currentRunId}/approvals/reject`;"
            "loadWorkspaceSkills();"
            "renderWorkspaceSkills();"
            "`/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills`;"
            "invocation_ready;"
            "missing_required_scopes;"
            "data-workspace-skill-id;"
            "invokeSelectedWorkspaceSkill();"
            "`/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills/${encodeURIComponent(skill.skill_id)}/invoke`;"
            "customerSuccessMissingSkillStatus;"
            "submitMissingSkillFeedback();"
            'feedback_type: "missing_skill";'
            'target_type: "solution_pack";'
            "solution_pack_id: solutionPackId;"
            "missing_skill_name: missingSkillName;"
            'source: "workspace_skill_request";'
            '"/api/customer-success/feedback";'
            'feedback_type: "thumbs_rating";'
            'target_type: "run";'
            "artifact_count: readyArtifacts.length;"
            "loadSolutionPacks();"
            "renderSolutionPacks();"
            'apiFetch("/api/solution-packs");'
            "data-solution-pack-id;"
            "installSelectedSolutionPack();"
            "`/api/solution-packs/${encodeURIComponent(pack.manifest.id)}/install`;"
            "workspace_ids: [state.workspaceId];"
            "customerSuccessCandidateStatus;"
            "createCustomerSuccessEvaluationCandidates();"
            "createCustomerSuccessSolutionPackCandidates();"
            "reviewSelectedEvaluationCandidate();"
            "evaluationCandidateReviewPayload();"
            "renderSolutionPackCandidateReview();"
            "selectedSolutionPackCandidate();"
            "reviewSelectedSolutionPackCandidate();"
            "solutionPackCandidateReviewPayload();"
            "cs-accept-eval-candidate;"
            'status: "accepted";'
            'status: "rejected";'
            "`/api/customer-success/evaluation-candidates/${candidate.id}/review`;"
            "`/api/customer-success/solution-pack-candidates/${candidate.id}/review`;"
            '"/api/customer-success/evaluation-candidates";'
            '"/api/customer-success/solution-pack-candidates";'
            "minimum_repeated_feedback: 3;"
            "Pack candidate accepted;"
            "publication_draft_id;"
            "loadRunTrace();"
            "renderRunTrace();"
            "`/api/runs/${state.currentRunId}/trace`;"
            "loadRuntimeState();"
            "renderRuntimeState();"
            "`/api/runs/${state.currentRunId}/state`;"
            "safeTerminalOutput();"
            "terminalOutputStorageObject;"
            "dataset.terminalStorageObjectId;"
            "storageObjectForTerminalOutputUri();"
            "renderExecutionLoop();"
            "renderRunEvidence();"
            "buildRunEvidenceItems();"
            "data-evidence-status;"
            "const executionLoopSummary = 'No active run';"
            "elements.executionLoopPlan;"
            'localStorage.setItem("taroai.tenantId", state.tenantId);'
            'localStorage.setItem("taroai.userId", state.userId);'
            'localStorage.setItem("taroai.workspaceId", state.workspaceId);'
            'clearAuthenticatedWorkspaceState("Authentication failed.");'
            "handleAuthExpired(response.status);"
            "status === 401 || status === 403;"
            'clearAuthenticatedWorkspaceState("Authentication expired.");'
            'renderAuth("Auth required");'
            "parseResponseBody(text);"
            "return { message: text };"
            "raiseStorageFetchError(response);"
            "clearAuthenticatedWorkspaceState();"
            "resetConversation();"
            "elements.conversation.replaceChildren();"
            "state.currentRunId = null;"
            "state.storageObjects = [];"
            'terminalMessage = "Signed out."'
            "renderTerminal(terminalMessage);"
            "sessionStorage.setItem('taroai.accessToken', state.accessToken);"
            "sessionStorage.removeItem('taroai.accessToken');"
        )
        self.artifacts_body = artifacts_body or (
            '[{"id":"artifact_1","tenant_id":"tenant_acme",'
            '"run_id":"run_1","name":"report.md",'
            '"artifact_type":"document",'
            '"uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/artifacts/report.md",'
            '"created_at":"2026-07-03T14:00:01Z"}]'
        )
        self.storage_objects_body = storage_objects_body or (
            '[{"id":"storage_report_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"artifacts","filename":"report.md",'
            '"content_type":"text/markdown","size_bytes":72,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/artifacts/report.md",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:01Z"},'
            '{"id":"storage_sandbox_output_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"sandbox-command-outputs","filename":"sandbox_1-output.json",'
            '"content_type":"application/json","size_bytes":142,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/sandbox_1-output.json",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:01Z"},'
            '{"id":"storage_model_sandbox_output_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"sandbox-command-outputs","filename":"model_sandbox-output.json",'
            '"content_type":"application/json","size_bytes":142,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:01Z"},'
            '{"id":"storage_browser_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"browser","filename":"sandbox_1.png",'
            '"content_type":"image/png","size_bytes":67,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:02Z"}]'
        )
        self.storage_object_content = storage_object_content or (
            "# Hello Report\n"
            "The local cloud PoC execution path produced this report.\n"
        )
        self.storage_object_contents = dict(storage_object_contents or {})
        self.skill_run_events_body = skill_run_events_body or (
            'id: 1\n'
            'event: skill.workflow_invoked\n'
            'data: {"id":"event_skill_0","sequence":1,"type":"skill.workflow_invoked","payload":{"skill_id":"sales.erp_invoice_matching","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
            'id: 2\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_skill_1","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
            'id: 3\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_skill_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
            'id: 4\n'
            'event: run.succeeded\n'
            'data: {"id":"event_skill_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        )
        self.skill_run_workspace_id = skill_run_workspace_id
        self.skill_run_agent_id = skill_run_agent_id
        self.run_events_body = run_events_body or (
            'id: 1\n'
            'event: plan.created\n'
            'data: {"id":"event_1","sequence":1,"type":"plan.created","payload":{"provider":null,"model":"gpt-enterprise-planner","usage":{"input_tokens":120,"output_tokens":45,"total_tokens":165,"cached_input_tokens":48},"steps":[{"id":"step_report","title":"Generate report","tool_name":"sandbox.command"}]}}\n\n'
            'id: 2\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_2","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
            'id: 3\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_3","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
            'id: 4\n'
            'event: run.succeeded\n'
            'data: {"id":"event_4","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        )
        self.run_trace_body = run_trace_body or json.dumps(
            {
                "run": {
                    "id": "run_1",
                    "tenant_id": "tenant_acme",
                    "workspace_id": "workspace_acme",
                    "user_id": "user_owner",
                    "agent_id": None,
                    "message": "Create a hello report.",
                    "attachments": [],
                    "mode": "autonomous",
                    "status": "succeeded",
                    "created_at": "2026-07-03T14:00:02Z",
                    "updated_at": "2026-07-03T14:00:03Z",
                },
                "events": [
                    {
                        "id": "event_1",
                        "type": "sandbox.command.executed",
                        "sequence": 1,
                        "payload": {
                            "exit_code": 0,
                            "stdout_length": 2,
                            "stderr_length": 0,
                        },
                    },
                    {
                        "id": "event_2",
                        "type": "sandbox.artifact.promoted",
                        "sequence": 2,
                        "payload": {
                            "artifact_name": "report.md",
                            "storage_object_id": "storage_report_1",
                        },
                    },
                    {
                        "id": "event_3",
                        "type": "run.succeeded",
                        "sequence": 3,
                        "payload": {"status": "succeeded"},
                    },
                ],
                "billing_meters": [
                    {
                        "id": "meter_1",
                        "meter_type": "tool_call_count",
                        "quantity": 1,
                        "unit": "count",
                    }
                ],
                "audit_events": [
                    {
                        "id": "audit_1",
                        "event_type": "tool.executed",
                        "metadata": {
                            "tool_name": "sandbox.command",
                            "run_id": "run_1",
                        },
                    }
                ],
                "spans": [
                    {
                        "trace_id": "run_1",
                        "span_id": "run:run_1",
                        "parent_span_id": None,
                        "name": "run",
                        "kind": "internal",
                        "status": "ok",
                        "attributes": {"workspace_id": "workspace_acme"},
                    },
                    {
                        "trace_id": "run_1",
                        "span_id": "runtime:tool_call:step_report",
                        "parent_span_id": "run:run_1",
                        "name": "runtime.tool_call",
                        "kind": "internal",
                        "status": "ok",
                        "attributes": {
                            "tool_name": "sandbox.command",
                            "status": "ok",
                        },
                    },
                    {
                        "trace_id": "run_1",
                        "span_id": "runtime:artifact:report",
                        "parent_span_id": "run:run_1",
                        "name": "runtime.artifact",
                        "kind": "internal",
                        "status": "ok",
                        "attributes": {"artifact_type": "document"},
                    },
                ],
                "trace_events": [
                    {
                        "trace_id": "run_1",
                        "span_id": "event:event_1",
                        "source": "run_event",
                        "name": "sandbox.command.executed",
                        "attributes": {"exit_code": 0},
                    },
                    {
                        "trace_id": "run_1",
                        "span_id": "meter:meter_1",
                        "source": "billing_meter",
                        "name": "billing.tool_call_count",
                        "attributes": {"meter_type": "tool_call_count"},
                    },
                    {
                        "trace_id": "run_1",
                        "span_id": "audit:audit_1",
                        "source": "audit_event",
                        "name": "audit.tool.executed",
                        "attributes": {"event_type": "tool.executed"},
                    },
                ],
                "error_classification": None,
            },
            separators=(",", ":"),
        )
        self.runtime_state_body = runtime_state_body or (
            '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
            '"user_id":"user_owner","run_id":"run_1","goal":"Create a hello report.",'
            '"status":"succeeded","plan":[],"current_step_id":"step_report",'
            '"completed_step_ids":["step_report"],"approved_step_ids":[],'
            '"approved_guardrail_keys":[],"pending_guardrail_approval_key":null,'
            '"pending_guardrail_approval_stage":null,"tool_results":[],'
            '"retrieved_context":{"knowledge_results":[],"memory_records":[]},'
            '"sandbox_session_id":"runtime_sandbox_1","browser_session_id":null,'
            '"promoted_sandbox_artifact_paths":["/workspace/artifacts/report.md"],'
            '"approval_id":null,"failure_reason":null}'
        )
        self.workspace_auth_statuses = list(workspace_auth_statuses or ["Bearer"])
        self.workspace_bootstrap_statuses = list(
            workspace_bootstrap_statuses or ["Tenant ready"]
        )
        self.workspace_readiness_statuses = list(
            workspace_readiness_statuses or [default_readiness_status]
        )
        self.workspace_readiness_model_statuses = list(
            workspace_readiness_model_statuses or [default_readiness_model]
        )
        self.workspace_readiness_sandbox_statuses = list(
            workspace_readiness_sandbox_statuses or [default_readiness_sandbox]
        )
        self.workspace_status_text = workspace_status_text
        self.sandbox_destroy_body = sandbox_destroy_body or (
            '{"id":"sandbox_1","status":"destroyed","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"provider":"local_process","image":"python:3.12",'
            '"network_mode":"disabled","created_at":"2026-07-03T14:00:00Z",'
            '"destroyed_at":"2026-07-03T14:00:02Z","timeout_seconds":300,'
            '"metadata":{}}'
        )
        self.destroyed_sandbox_sessions: set[str] = set()
        self.workspace_status_texts = (
            list(workspace_status_texts) if workspace_status_texts is not None else None
        )
        self.workspace_artifact_text = workspace_artifact_text
        self.calls: list[dict] = []

    def next_text(self, values: list[str]) -> str:
        text = values[0]
        if len(values) > 1:
            text = values.pop(0)
        return text

    def request(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> LocalCloudPocHttpResponse:
        parsed = urlparse(url)
        path = parsed.path
        self.calls.append(
            {
                "method": method,
                "host": parsed.netloc,
                "path": path,
                "payload": payload,
                "headers": headers or {},
            }
        )
        if parsed.netloc == "api.local" and method == "GET" and path == "/healthz":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body='{"status":"ok","service":"taroai-api"}',
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/readyz":
            sandbox = {
                "configured": self.sandbox_configured,
                "provider": self.sandbox_provider,
                "controller_required": self.sandbox_provider in {"k8s", "e2b"},
                "controller_configured": self.sandbox_provider in {"k8s", "e2b"}
                and self.sandbox_configured,
                "missing": self.sandbox_missing,
                "capabilities_checked": True,
                "network_isolation_declared": False,
                "filesystem_isolation_declared": False,
                "resource_limits_declared": False,
                "destroy_supported_declared": True,
                "session_ttl_enforced_declared": False,
                "max_session_ttl_seconds": None,
                "max_sessions": 50,
                "max_sessions_per_tenant": 20,
                "max_sessions_per_run": 3,
            }
            if self.model_gateway_configured:
                return LocalCloudPocHttpResponse(
                    status_code=200,
                    body=json.dumps(
                        {
                            "ready": True,
                            "checks": {
                                "model_gateway": {
                                    "configured": True,
                                    "missing": [],
                                },
                                "sandbox": sandbox,
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "ready": True,
                        "checks": {
                            "model_gateway": {
                                "configured": False,
                                "missing": ["model", "credential"],
                            },
                            "sandbox": sandbox,
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        if parsed.netloc == "browser.local" and method == "GET" and path == "/healthz":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body='{"status":"ok","service":"taroai-browser-controller"}',
            )
        if parsed.netloc == "browser.local" and method == "GET" and path == "/capabilities":
            unauthenticated_status_code = (
                self.browser_unauthenticated_capabilities_status_code
                if self.browser_unauthenticated_capabilities_status_code is not None
                else self.browser_unauthenticated_sessions_status_code
            )
            if (
                unauthenticated_status_code is not None
                and not (headers or {}).get("Authorization")
            ):
                return LocalCloudPocHttpResponse(
                    status_code=unauthenticated_status_code,
                    body='{"detail":"browser controller authentication required"}',
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.browser_capabilities_body,
            )
        if parsed.netloc == "web.local" and method == "GET" and path == "/":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.workspace_html,
            )
        if parsed.netloc == "web.local" and method == "GET" and path == "/assets/main.js":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.workspace_script,
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/tenants/bootstrap":
            assert headers == {"X-Bootstrap-Token": "bootstrap_token"}
            return LocalCloudPocHttpResponse(
                status_code=201,
                body=(
                    '{"tenant_id":"tenant_acme","tenant_slug":"acme",'
                    '"owner_user_id":"user_owner","owner_role_id":"tenant_owner",'
                    '"starter_workspace_id":"workspace_acme",'
                    '"starter_knowledge_base_id":"knowledge_acme",'
                    '"starter_skill_ids":["starter.artifact_writer"],'
                    '"readiness":{"ready":true,"blocking_checks":[],"warnings":[],"checks":[]}}'
                ),
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/auth/login":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"access_token":"access_token","token_type":"Bearer",'
                    '"tenant_id":"tenant_acme","user_id":"user_owner",'
                    '"session_id":"session_1","expires_at":"2026-07-03T15:00:00Z"}'
                ),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/tenants/current/readiness":
            assert headers == {"Authorization": "Bearer access_token"}
            return LocalCloudPocHttpResponse(
                status_code=200,
                body='{"tenant_id":"tenant_acme","user_id":"user_owner","ready":true,"blocking_checks":[],"warnings":[],"checks":[]}',
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/customer-success/feedback"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            feedback = []
            if self.workspace_run_feedback_recorded:
                feedback.append(
                    {
                        "id": "feedback_run_1",
                        "tenant_id": "tenant_acme",
                        "submitted_by_user_id": "user_owner",
                        "feedback_type": "thumbs_rating",
                        "target_type": "run",
                        "target_id": "run_1",
                        "run_id": "run_1",
                        "artifact_id": None,
                        "skill_id": None,
                        "solution_pack_id": None,
                        "onboarding_step_id": None,
                        "missing_skill_name": None,
                        "rating": -1,
                        "comment_present": False,
                        "metadata_present": True,
                        "metadata_key_count": 2,
                        "created_at": "2026-07-03T14:00:07Z",
                    }
                )
            if self.workspace_skill_feedback_recorded:
                feedback.append(
                    {
                        "id": "feedback_skill_1",
                        "tenant_id": "tenant_acme",
                        "submitted_by_user_id": "user_owner",
                        "feedback_type": "thumbs_rating",
                        "target_type": "run",
                        "target_id": "run_skill_1",
                        "run_id": "run_skill_1",
                        "artifact_id": None,
                        "skill_id": None,
                        "solution_pack_id": None,
                        "onboarding_step_id": None,
                        "missing_skill_name": None,
                        "rating": 1,
                        "comment_present": False,
                        "metadata_present": True,
                        "metadata_key_count": 2,
                        "created_at": "2026-07-03T14:00:07Z",
                    }
                )
            if (
                self.workspace_missing_skill_feedback_persists
                and self.workspace_missing_skill_feedback_count > 0
            ):
                for index in range(self.workspace_missing_skill_feedback_count):
                    feedback.append(
                        {
                            "id": f"feedback_missing_skill_{index + 1}",
                            "tenant_id": "tenant_acme",
                            "submitted_by_user_id": "user_owner",
                            "feedback_type": "missing_skill",
                            "target_type": "solution_pack",
                            "target_id": "sales.renewal_ops",
                            "run_id": None,
                            "artifact_id": None,
                            "skill_id": None,
                            "solution_pack_id": "sales.renewal_ops",
                            "onboarding_step_id": None,
                            "missing_skill_name": "ERP invoice reconciliation",
                            "rating": None,
                            "comment_present": True,
                            "metadata_present": True,
                            "metadata_key_count": 1,
                            "created_at": "2026-07-03T14:00:08Z",
                        }
                    )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(feedback, separators=(",", ":")),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/customer-success/evaluation-candidates"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            candidates = []
            if (
                self.workspace_eval_candidate_persists
                and self.workspace_eval_candidates_generated
            ):
                candidates.append(
                    {
                        "id": "eval_candidate_1",
                        "tenant_id": "tenant_acme",
                        "source_feedback_ids": ["feedback_run_1"],
                        "source_run_id": "run_1",
                        "failure_reason": "low_rating",
                        "proposed_eval_name": "Run quality regression",
                        "status": (
                            "accepted"
                            if self.workspace_eval_candidate_reviewed
                            and self.workspace_eval_candidate_review_persists
                            else "pending_review"
                        ),
                        "human_reviewed_by_user_id": "user_owner",
                        "production_change_applied": False,
                        "reviewed_by_user_id": (
                            "user_owner"
                            if self.workspace_eval_candidate_reviewed
                            and self.workspace_eval_candidate_review_persists
                            else None
                        ),
                        "reviewed_at": (
                            "2026-07-03T14:00:09Z"
                            if self.workspace_eval_candidate_reviewed
                            and self.workspace_eval_candidate_review_persists
                            else None
                        ),
                        "review_note": (
                            "Create eval case from workspace feedback."
                            if self.workspace_eval_candidate_reviewed
                            and self.workspace_eval_candidate_review_persists
                            else None
                        ),
                        "evaluation_case_id": (
                            "eval_case_1"
                            if self.workspace_eval_candidate_reviewed
                            and self.workspace_eval_candidate_review_persists
                            else None
                        ),
                        "created_at": "2026-07-03T14:00:08Z",
                    }
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(candidates, separators=(",", ":")),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/customer-success/solution-pack-candidates"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            candidates = []
            if (
                self.workspace_pack_candidate_persists
                and self.workspace_pack_candidates_generated
            ):
                pack_review_persisted = (
                    self.workspace_pack_candidate_reviewed
                    and self.workspace_pack_candidate_review_persists
                )
                candidates.append(
                    {
                        "id": "pack_candidate_1",
                        "tenant_id": "tenant_acme",
                        "source_feedback_ids": [
                            "feedback_missing_skill_1",
                            "feedback_missing_skill_2",
                            "feedback_missing_skill_3",
                        ],
                        "solution_pack_id": "sales.renewal_ops",
                        "requested_skill_name": "ERP invoice reconciliation",
                        "proposed_change_summary": "Add a reusable invoice reconciliation skill.",
                        "status": (
                            "accepted"
                            if pack_review_persisted
                            else "pending_review"
                        ),
                        "human_reviewed_by_user_id": "user_owner",
                        "production_change_applied": False,
                        "reviewed_by_user_id": (
                            "user_owner" if pack_review_persisted else None
                        ),
                        "reviewed_at": (
                            "2026-07-03T14:00:10Z"
                            if pack_review_persisted
                            else None
                        ),
                        "review_note": (
                            "Draft solution pack skill from workspace feedback."
                            if pack_review_persisted
                            else None
                        ),
                        "publication_draft_id": (
                            "pack_draft_1" if pack_review_persisted else None
                        ),
                        "created_at": "2026-07-03T14:00:09Z",
                    }
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(candidates, separators=(",", ":")),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/customer-success/solution-pack-drafts"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            drafts = []
            if (
                self.workspace_pack_candidate_persists
                and self.workspace_pack_candidate_reviewed
                and self.workspace_pack_candidate_review_persists
            ):
                draft_status = "draft"
                applied = False
                if self.workspace_draft_status == "Draft in review":
                    draft_status = "in_review"
                if self.workspace_draft_status == "Draft approved":
                    draft_status = "approved"
                if self.workspace_draft_status == "Draft applied":
                    draft_status = (
                        "applied"
                        if self.workspace_draft_apply_persists
                        else "approved"
                    )
                    applied = self.workspace_draft_apply_persists
                drafts.append(
                    {
                        "id": "pack_draft_1",
                        "tenant_id": "tenant_acme",
                        "source_candidate_id": "pack_candidate_1",
                        "source_feedback_ids": [
                            "feedback_missing_skill_1",
                            "feedback_missing_skill_2",
                            "feedback_missing_skill_3",
                        ],
                        "solution_pack_id": "sales.renewal_ops",
                        "requested_skill_name": "ERP Invoice Matching",
                        "proposed_change_summary": "Add governed invoice matching skill draft.",
                        "proposed_pack_version": "1.0.1",
                        "proposed_skill_manifest": verifier_skill_manifest(),
                        "proposed_skill_manifests": [],
                        "status": draft_status,
                        "created_by_user_id": "user_owner",
                        "production_change_applied": applied,
                        "created_at": "2026-07-03T14:00:10Z",
                    }
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(drafts, separators=(",", ":")),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/solution-packs":
            assert headers == {"Authorization": "Bearer access_token"}
            packs = []
            if self.solution_pack_registered:
                if self.solution_pack_applied:
                    packs.append(
                        verifier_solution_pack_entry(
                            "1.0.1",
                            "published",
                            [verifier_skill_manifest()],
                        )
                    )
                else:
                    packs.append(verifier_solution_pack_entry("1.0.0", "draft", []))
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(packs, separators=(",", ":")),
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/solution-packs":
            assert headers == {"Authorization": "Bearer access_token"}
            assert payload["id"] == "sales.renewal_ops"
            self.solution_pack_registered = True
            return LocalCloudPocHttpResponse(
                status_code=201,
                body=json.dumps(
                    {
                        "tenant_id": "tenant_acme",
                        "manifest": payload,
                        "status": "draft",
                        "created_by_user_id": "user_owner",
                        "created_at": "2026-07-03T14:00:00Z",
                        "updated_at": "2026-07-03T14:00:00Z",
                    },
                    separators=(",", ":"),
                ),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/solution-packs/sales.renewal_ops/versions"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            versions = [verifier_solution_pack_entry("1.0.0", "draft", [])]
            if self.solution_pack_applied:
                versions.append(
                    verifier_solution_pack_entry(
                        "1.0.1",
                        "published",
                        [verifier_skill_manifest()],
                    )
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(versions, separators=(",", ":")),
            )
        if (
            parsed.netloc == "api.local"
            and method == "POST"
            and path == "/api/solution-packs/sales.renewal_ops/install"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            assert payload == {"workspace_ids": ["workspace_acme"]}
            assert self.solution_pack_applied is True
            self.workspace_solution_pack_install_recorded = True
            return LocalCloudPocHttpResponse(
                status_code=201,
                body=(
                    '{"tenant_id":"tenant_acme","pack_id":"sales.renewal_ops",'
                    '"version":"1.0.1","workspace_ids":["workspace_acme"],'
                    '"installed_skill_ids":["sales.erp_invoice_matching"],'
                    '"status":"installed","installed_by_user_id":"user_owner",'
                    '"created_at":"2026-07-03T14:00:00Z",'
                    '"updated_at":"2026-07-03T14:00:00Z"}'
                ),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/solution-pack-installations":
            assert headers == {"Authorization": "Bearer access_token"}
            installations = []
            if self.workspace_solution_pack_install_recorded:
                installations.append(
                    {
                        "tenant_id": "tenant_acme",
                        "pack_id": "sales.renewal_ops",
                        "version": "1.0.1",
                        "workspace_ids": ["workspace_acme"],
                        "installed_skill_ids": ["sales.erp_invoice_matching"],
                        "status": "installed",
                        "installed_by_user_id": "user_owner",
                        "created_at": "2026-07-03T14:00:00Z",
                        "updated_at": "2026-07-03T14:00:00Z",
                    }
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(installations, separators=(",", ":")),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/workspaces/workspace_acme/skills"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            skills = []
            if self.workspace_solution_pack_install_recorded:
                skills.append(
                    {
                        "tenant_id": "tenant_acme",
                        "workspace_id": "workspace_acme",
                        "skill_id": "sales.erp_invoice_matching",
                        "status": "installed",
                        "invocation_mode": "agent_workflow",
                        "invocation_ready": True,
                        "missing_required_scopes": [],
                        "installed_by_user_id": "user_owner",
                        "created_at": "2026-07-03T14:00:00Z",
                        "updated_at": "2026-07-03T14:00:00Z",
                    }
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(skills, separators=(",", ":")),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/skills":
            assert headers == {"Authorization": "Bearer access_token"}
            skills = []
            if self.solution_pack_applied:
                skills.append(
                    {
                        "tenant_id": "tenant_acme",
                        "manifest": verifier_skill_manifest(),
                        "status": "published",
                        "created_by_user_id": "user_owner",
                        "created_at": "2026-07-03T14:00:00Z",
                        "updated_at": "2026-07-03T14:00:00Z",
                    }
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(skills, separators=(",", ":")),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/skills/sales.erp_invoice_matching"
        ):
            assert headers == {"Authorization": "Bearer access_token"}
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "tenant_id": "tenant_acme",
                        "manifest": verifier_skill_manifest(),
                        "status": "published",
                        "created_by_user_id": "user_owner",
                        "created_at": "2026-07-03T14:00:00Z",
                        "updated_at": "2026-07-03T14:00:00Z",
                    },
                    separators=(",", ":"),
                ),
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/runs":
            return LocalCloudPocHttpResponse(
                status_code=201,
                body='{"run_id":"run_1","status":"created","events_url":"/api/runs/run_1/events"}',
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/runs/run_1/execute":
            if self.model_gateway_configured:
                return LocalCloudPocHttpResponse(
                    status_code=200,
                    body='{"status":"succeeded","run_id":"run_1"}',
                )
            return LocalCloudPocHttpResponse(
                status_code=503,
                body='{"code":"model_gateway_unavailable","message":"model gateway model is not configured","details":{},"retryable":true}',
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/runs/run_skill_1":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"id":"run_skill_1","tenant_id":"tenant_acme",'
                    '"workspace_id":"'
                    + self.skill_run_workspace_id
                    + '","user_id":"user_owner",'
                    '"agent_id":'
                    + json.dumps(self.skill_run_agent_id)
                    + ',"message":"Invoke ERP Invoice Matching.",'
                    '"attachments":[],"mode":"autonomous","status":"succeeded",'
                    '"created_at":"2026-07-03T14:00:02Z",'
                    '"updated_at":"2026-07-03T14:00:03Z"}'
                ),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/runs/run_skill_1/events"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.skill_run_events_body,
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/runs/run_skill_1/state"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                    '"user_id":"user_owner","run_id":"run_skill_1",'
                    '"goal":"Invoke ERP Invoice Matching.",'
                    '"status":"succeeded","plan":[],"current_step_id":"step_report",'
                    '"completed_step_ids":["step_report"],"approved_step_ids":[],'
                    '"approved_guardrail_keys":[],'
                    '"pending_guardrail_approval_key":null,'
                    '"pending_guardrail_approval_stage":null,"tool_results":[],'
                    '"retrieved_context":{"knowledge_results":[],"memory_records":[]},'
                    '"sandbox_session_id":"runtime_skill_sandbox_1",'
                    '"browser_session_id":null,'
                    '"promoted_sandbox_artifact_paths":["/workspace/artifacts/report.md"],'
                    '"approval_id":null,"failure_reason":null}'
                ),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/runs/run_skill_1/trace"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "run": {
                            "id": "run_skill_1",
                            "tenant_id": "tenant_acme",
                            "workspace_id": "workspace_acme",
                            "user_id": "user_owner",
                            "agent_id": None,
                            "message": "Invoke ERP Invoice Matching.",
                            "attachments": [],
                            "mode": "autonomous",
                            "status": "succeeded",
                            "created_at": "2026-07-03T14:00:02Z",
                            "updated_at": "2026-07-03T14:00:03Z",
                        },
                        "events": [
                            {
                                "id": "event_skill_1",
                                "type": "sandbox.command.executed",
                                "sequence": 1,
                                "payload": {
                                    "exit_code": 0,
                                    "stdout_length": 2,
                                    "stderr_length": 0,
                                },
                            },
                            {
                                "id": "event_skill_2",
                                "type": "sandbox.artifact.promoted",
                                "sequence": 2,
                                "payload": {
                                    "artifact_name": "report.md",
                                    "storage_object_id": "storage_skill_report_1",
                                },
                            },
                            {
                                "id": "event_skill_3",
                                "type": "run.succeeded",
                                "sequence": 3,
                                "payload": {"status": "succeeded"},
                            },
                        ],
                        "billing_meters": [
                            {
                                "id": "meter_skill_1",
                                "meter_type": "tool_call_count",
                                "quantity": 1,
                                "unit": "count",
                            }
                        ],
                        "audit_events": [
                            {
                                "id": "audit_skill_1",
                                "event_type": "tool.executed",
                                "metadata": {
                                    "tool_name": "sandbox.command",
                                    "run_id": "run_skill_1",
                                },
                            }
                        ],
                        "spans": [
                            {
                                "trace_id": "run_skill_1",
                                "span_id": "run:run_skill_1",
                                "parent_span_id": None,
                                "name": "run",
                                "kind": "internal",
                                "status": "ok",
                                "attributes": {"workspace_id": "workspace_acme"},
                            },
                            {
                                "trace_id": "run_skill_1",
                                "span_id": "runtime:tool_call:step_report",
                                "parent_span_id": "run:run_skill_1",
                                "name": "runtime.tool_call",
                                "kind": "internal",
                                "status": "ok",
                                "attributes": {
                                    "tool_name": "sandbox.command",
                                    "status": "ok",
                                },
                            },
                            {
                                "trace_id": "run_skill_1",
                                "span_id": "runtime:artifact:report",
                                "parent_span_id": "run:run_skill_1",
                                "name": "runtime.artifact",
                                "kind": "internal",
                                "status": "ok",
                                "attributes": {"artifact_type": "document"},
                            },
                        ],
                        "trace_events": [
                            {
                                "trace_id": "run_skill_1",
                                "span_id": "event:event_skill_1",
                                "source": "run_event",
                                "name": "sandbox.command.executed",
                                "attributes": {"exit_code": 0},
                            },
                            {
                                "trace_id": "run_skill_1",
                                "span_id": "meter:meter_skill_1",
                                "source": "billing_meter",
                                "name": "billing.tool_call_count",
                                "attributes": {"meter_type": "tool_call_count"},
                            },
                            {
                                "trace_id": "run_skill_1",
                                "span_id": "audit:audit_skill_1",
                                "source": "audit_event",
                                "name": "audit.tool.executed",
                                "attributes": {"event_type": "tool.executed"},
                            },
                        ],
                        "error_classification": None,
                    },
                    separators=(",", ":"),
                ),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/runs/run_1/trace"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.run_trace_body,
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/runs/run_skill_1/artifacts"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '[{"id":"artifact_skill_1","tenant_id":"tenant_acme",'
                    '"run_id":"run_skill_1","name":"report.md",'
                    '"artifact_type":"document",'
                    '"uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/artifacts/report.md",'
                    '"created_at":"2026-07-03T14:00:03Z"}]'
                ),
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/runs/run_skill_1/storage-objects"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '[{"id":"storage_skill_report_1","tenant_id":"tenant_acme",'
                    '"workspace_id":"workspace_acme","run_id":"run_skill_1",'
                    '"purpose":"artifacts","filename":"report.md",'
                    '"content_type":"text/markdown","size_bytes":72,'
                    '"acl_subjects":[],"sensitivity_level":0,'
                    '"bucket":"taroai-artifacts",'
                    '"key":"tenant_acme/workspace_acme/runs/run_skill_1/artifacts/report.md",'
                    '"retention_expires_at":null,"deleted_at":null,'
                    '"created_at":"2026-07-03T14:00:03Z"},'
                    '{"id":"storage_skill_sandbox_output_1","tenant_id":"tenant_acme",'
                    '"workspace_id":"workspace_acme","run_id":"run_skill_1",'
                    '"purpose":"sandbox-command-outputs",'
                    '"filename":"sandbox_skill_1-output.json",'
                    '"content_type":"application/json","size_bytes":48,'
                    '"acl_subjects":[],"sensitivity_level":0,'
                    '"bucket":"taroai-artifacts",'
                    '"key":"tenant_acme/workspace_acme/runs/run_skill_1/'
                    'sandbox-command-outputs/sandbox_skill_1-output.json",'
                    '"retention_expires_at":null,"deleted_at":null,'
                    '"created_at":"2026-07-03T14:00:03Z"}]'
                ),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/runs/run_1":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"id":"run_1","tenant_id":"tenant_acme",'
                    '"workspace_id":"workspace_acme","user_id":"user_owner",'
                    '"agent_id":null,"message":"Create a hello report.",'
                    '"attachments":[],"mode":"autonomous","status":"succeeded",'
                    '"created_at":"2026-07-03T14:00:00Z",'
                    '"updated_at":"2026-07-03T14:00:01Z"}'
                ),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/runs/run_1/events":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.run_events_body,
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/runs/run_1/state":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.runtime_state_body,
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/runs/run_1/artifacts":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.artifacts_body,
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/runs/run_1/storage-objects":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.storage_objects_body,
            )
        storage_prefix = "/api/storage/objects/"
        storage_suffix = "/content"
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path.startswith(storage_prefix)
            and path.endswith(storage_suffix)
        ):
            storage_object_id = path[len(storage_prefix) : -len(storage_suffix)]
            if storage_object_id in self.storage_object_contents:
                return LocalCloudPocHttpResponse(
                    status_code=200,
                    body=self.storage_object_contents[storage_object_id],
                )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/storage/objects/storage_report_1/content"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.storage_object_content,
            )
        if (
            parsed.netloc == "api.local"
            and method == "GET"
            and path == "/api/storage/objects/storage_skill_report_1/content"
        ):
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.storage_object_content,
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/storage/objects/storage_notes_1/content":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.storage_object_content,
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/storage/objects/storage_sandbox_output_1/content":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"session_id":"sandbox_1","workspace_id":"workspace_acme",'
                    '"run_id":"run_1","command":"python --version",'
                    '"exit_code":0,"stdout":"Python 3.12.13\\\\n","stderr":""}'
                ),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/storage/objects/storage_model_sandbox_output_1/content":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"session_id":"runtime_sandbox_1",'
                    '"workspace_id":"workspace_acme","run_id":"run_1",'
                    '"command":"python generate_report.py",'
                    '"exit_code":0,"stdout":"","stderr":""}'
                ),
            )
        if parsed.netloc == "api.local" and method == "GET" and path == "/api/storage/objects/storage_browser_1/content":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body_bytes=PNG_BYTES,
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/sandbox/sessions":
            return LocalCloudPocHttpResponse(
                status_code=201,
                body=(
                    '{"id":"sandbox_1","tenant_id":"tenant_acme",'
                    '"workspace_id":"workspace_acme","run_id":"run_1",'
                    '"provider":"local_process","image":"python:3.12",'
                    '"network_mode":"disabled","status":"active",'
                    '"created_at":"2026-07-03T14:00:00Z",'
                    '"destroyed_at":null,"timeout_seconds":300,"metadata":{}}'
                ),
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/sandbox/sessions/sandbox_1/commands":
            if "sandbox_1" in self.destroyed_sandbox_sessions:
                return LocalCloudPocHttpResponse(
                    status_code=409,
                    body='{"detail":"Sandbox session is not active: sandbox_1"}',
                )
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                    '"run_id":"run_1","session_id":"sandbox_1",'
                    '"command":"python --version","stdout":"Python 3.12.13\\\\n",'
                    '"stderr":"","exit_code":0,'
                    '"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/sandbox_1-output.json",'
                    '"created_at":"2026-07-03T14:00:01Z"}'
                ),
            )
        if parsed.netloc == "api.local" and method == "DELETE" and path == "/api/sandbox/sessions/sandbox_1":
            self.destroyed_sandbox_sessions.add("sandbox_1")
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=self.sandbox_destroy_body,
            )
        if parsed.netloc == "api.local" and method == "POST" and path == "/api/browser/sessions/sandbox_1/actions":
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                    '"run_id":"run_1","session_id":"sandbox_1",'
                    '"action_type":"screenshot","current_url":"about:blank",'
                    '"text":null,'
                    '"screenshot_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png",'
                    '"metadata":{},"created_at":"2026-07-03T14:00:02Z"}'
                ),
            )
        if parsed.netloc == "browser.local" and method == "POST" and path == "/sessions":
            return LocalCloudPocHttpResponse(
                status_code=201,
                body='{"session_id":"browser_verify_1","tenant_id":"tenant_acme","workspace_id":"workspace_acme","run_id":"run_1","current_url":null,"actions":[],"created_at":"2026-07-03T14:00:03Z"}',
            )
        if parsed.netloc == "browser.local" and method == "GET" and path == "/sessions":
            if not (headers or {}).get("Authorization"):
                unauthenticated_status_code = self.browser_unauthenticated_sessions_status_code
                if not parsed.query:
                    unauthenticated_status_code = (
                        self.browser_unauthenticated_global_sessions_status_code
                        if self.browser_unauthenticated_global_sessions_status_code
                        is not None
                        else self.browser_unauthenticated_sessions_status_code
                    )
                if unauthenticated_status_code is not None:
                    return LocalCloudPocHttpResponse(
                        status_code=unauthenticated_status_code,
                        body='{"detail":"browser controller authentication required"}',
                    )
            tenant_id = parse_qs(parsed.query).get("tenant_id", [""])[0]
            sessions = []
            if tenant_id == "tenant_acme":
                sessions = [
                    {
                        "session_id": "browser_verify_1",
                        "tenant_id": "tenant_acme",
                        "workspace_id": "workspace_acme",
                        "run_id": "run_1",
                        "current_url": None,
                        "actions": [],
                        "created_at": "2026-07-03T14:00:03Z",
                    }
                ]
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=json.dumps({"sessions": sessions}, separators=(",", ":")),
            )
        if (
            parsed.netloc == "browser.local"
            and method == "DELETE"
            and path == "/sessions/browser_verify_1"
        ):
            query = parse_qs(parsed.query)
            workspace_id = query.get("workspace_id", [""])[0]
            run_id = query.get("run_id", [""])[0]
            if workspace_id and workspace_id != "workspace_acme":
                return LocalCloudPocHttpResponse(
                    status_code=404,
                    body='{"detail":"Browser session not found: browser_verify_1"}',
                )
            if run_id and run_id != "run_1":
                return LocalCloudPocHttpResponse(
                    status_code=404,
                    body='{"detail":"Browser session not found: browser_verify_1"}',
                )
            if self.browser_delete_empty_response:
                return LocalCloudPocHttpResponse(status_code=204)
            return LocalCloudPocHttpResponse(
                status_code=200,
                body=(
                    '{"session_id":"browser_verify_1","tenant_id":"tenant_acme",'
                    '"workspace_id":"workspace_acme","run_id":"run_1",'
                    '"current_url":null,"actions":[],'
                    '"created_at":"2026-07-03T14:00:03Z"}'
                ),
            )
        if (
            parsed.netloc == "browser.local"
            and method == "GET"
            and path == "/sessions/browser_verify_1"
        ):
            query = parse_qs(parsed.query)
            workspace_id = query.get("workspace_id", [""])[0]
            run_id = query.get("run_id", [""])[0]
            if workspace_id == "workspace_acme" and run_id == "run_1":
                return LocalCloudPocHttpResponse(
                    status_code=404,
                    body='{"detail":"Browser session not found: browser_verify_1"}',
                )
            return LocalCloudPocHttpResponse(
                status_code=404,
                body='{"detail":"Browser session not found: browser_verify_1"}',
            )
        if parsed.netloc == "browser.local" and method == "POST" and path == "/actions":
            if payload["action_type"] == "navigate":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"navigate","current_url":"'
                        + payload["url"]
                        + '","text":null,"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:04Z"}'
                    ),
                )
            if payload["action_type"] in {"type", "click"}:
                if payload.get("selector") == "#cs-submit-missing-skill":
                    self.workspace_missing_skill_feedback_count += 1
                if payload.get("selector") == "#cs-create-eval-candidates":
                    self.workspace_eval_candidates_generated = True
                if payload.get("selector") == "#cs-create-pack-candidates":
                    self.workspace_pack_candidates_generated = True
                if payload.get("selector") == "#cs-accept-eval-candidate":
                    self.workspace_eval_candidate_reviewed = True
                if payload.get("selector") == "#cs-accept-pack-candidate":
                    self.workspace_pack_candidate_reviewed = True
                    self.workspace_draft_status = "Status: draft"
                if payload.get("selector") == "#cs-draft-save":
                    self.workspace_draft_status = "Draft saved"
                if payload.get("selector") == "#cs-draft-submit":
                    self.workspace_draft_status = "Draft in review"
                if payload.get("selector") == "#cs-draft-approve":
                    self.workspace_draft_status = "Draft approved"
                if payload.get("selector") == "#cs-draft-apply":
                    self.workspace_draft_status = "Draft applied"
                    self.solution_pack_applied = True
                if payload.get("selector") == "#install-solution-pack-button":
                    self.workspace_solution_pack_installed = True
                    if self.workspace_solution_pack_install_persists:
                        self.workspace_solution_pack_install_recorded = True
                if payload.get("selector") == "#invoke-skill-button":
                    self.workspace_skill_invoked = True
                if payload.get("selector") == "[data-run-history-refresh]":
                    self.workspace_run_history_refreshed = True
                if payload.get("selector") == '[data-run-history-id="run_skill_1"]':
                    self.workspace_selected_history_run_id = "run_skill_1"
                if payload.get("selector") == '[data-storage-object-id="storage_report_1"]':
                    self.workspace_downloaded_storage_object_id = (
                        self.workspace_artifact_downloaded_storage_id
                    )
                if payload.get("selector") == '[data-storage-object-id="storage_skill_report_1"]':
                    self.workspace_downloaded_storage_object_id = (
                        "storage_skill_report_1"
                    )
                if (
                    payload.get("selector") == "#run-feedback-negative"
                    and self.workspace_run_feedback_persists
                ):
                    self.workspace_run_feedback_recorded = True
                if payload.get("selector") == "#run-feedback-positive":
                    self.workspace_skill_feedback_recorded = True
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"'
                        + payload["action_type"]
                        + '","current_url":"http://web.internal",'
                        '"text":null,"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            if payload.get("selector") == "[data-bootstrap-status]":
                bootstrap_status = self.next_text(self.workspace_bootstrap_statuses)
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + bootstrap_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            bootstrap_context_values = {
                "#tenant-id": "tenant_acme",
                "#user-id": "user_owner",
                "#workspace-id": "workspace_acme",
                "#bootstrap-token": "",
            }
            if payload.get("selector") in bootstrap_context_values:
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + bootstrap_context_values[payload["selector"]]
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            if payload.get("selector") == "[data-auth-status]":
                auth_status = self.workspace_auth_statuses[0]
                if len(self.workspace_auth_statuses) > 1:
                    auth_status = self.workspace_auth_statuses.pop(0)
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + auth_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            if payload.get("selector") == "[data-readiness-status]":
                readiness_status = self.next_text(self.workspace_readiness_statuses)
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + readiness_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            if payload.get("selector") == "[data-readiness-model]":
                readiness_model = self.next_text(
                    self.workspace_readiness_model_statuses
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + readiness_model
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            if payload.get("selector") == "[data-readiness-sandbox]":
                readiness_sandbox = self.next_text(
                    self.workspace_readiness_sandbox_statuses
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + readiness_sandbox
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            if payload.get("selector") == "[data-testid='conversation-log']":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"Generate a hello report. Run run_1 created. '
                        '503 model gateway model is not configured",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-status-pill]":
                workspace_status_text = (
                    self.next_text(self.workspace_status_texts)
                    if self.workspace_status_texts is not None
                    else self.workspace_status_text
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + workspace_status_text
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-artifact-list]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + self.workspace_artifact_text
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-evidence-summary]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"Artifact delivery proven",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-delivery-summary]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"Ready to download: report.md",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") in {
                "[data-delivery-chain-status]",
                "[data-delivery-chain-run]",
                "[data-delivery-chain-sandbox]",
                "[data-delivery-chain-artifact-storage]",
                "[data-delivery-chain-terminal-storage]",
                "[data-delivery-chain-browser-storage]",
            }:
                selected_skill_run = (
                    self.workspace_selected_history_run_id == "run_skill_1"
                )
                delivery_chain_text = {
                    "[data-delivery-chain-status]": self.workspace_delivery_chain_status,
                    "[data-delivery-chain-run]": (
                        "run_skill_1" if selected_skill_run else "run_1"
                    ),
                    "[data-delivery-chain-sandbox]": (
                        self.workspace_selected_history_sandbox_session_id
                        if selected_skill_run
                        else "runtime_sandbox_1"
                    ),
                    "[data-delivery-chain-artifact-storage]": (
                        "storage_skill_report_1"
                        if selected_skill_run
                        else self.workspace_delivery_chain_artifact_storage_id
                    ),
                    "[data-delivery-chain-terminal-storage]": (
                        "storage_skill_sandbox_output_1"
                        if selected_skill_run
                        else self.workspace_delivery_chain_terminal_storage_id
                    ),
                    "[data-delivery-chain-browser-storage]": (
                        self.workspace_delivery_chain_browser_storage_id
                    ),
                }[payload["selector"]]
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":'
                        + json.dumps(delivery_chain_text)
                        + ',"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") in {
                "[data-event-integrity-status]",
                "[data-event-integrity-count]",
                "[data-event-integrity-sequence]",
                "[data-event-integrity-closure]",
            }:
                selected_skill_run = (
                    self.workspace_selected_history_run_id == "run_skill_1"
                )
                active_events_body = (
                    self.skill_run_events_body
                    if selected_skill_run
                    else self.run_events_body
                )
                integrity_text = {
                    "[data-event-integrity-status]": "Event stream verified",
                    "[data-event-integrity-count]": (
                        f"{count_sse_events(active_events_body)} events"
                    ),
                    "[data-event-integrity-sequence]": (
                        sse_sequence_label(active_events_body)
                    ),
                    "[data-event-integrity-closure]": (
                        sse_event_closure_label(active_events_body)
                    ),
                }[payload["selector"]]
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":'
                        + json.dumps(integrity_text)
                        + ',"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") in {
                "[data-browser-storage-object]",
                "[data-browser-preview-storage-object]",
            }:
                browser_text = {
                    "[data-browser-storage-object]": self.workspace_browser_storage_id,
                    "[data-browser-preview-storage-object]": (
                        self.workspace_browser_preview_storage_id
                    ),
                }[payload["selector"]]
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + browser_text
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-artifact-preview-content]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":'
                        + json.dumps(self.storage_object_content)
                        + ',"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-artifact-preview-storage-object]":
                previewed_storage_object_id = (
                    "storage_skill_report_1"
                    if self.workspace_selected_history_run_id == "run_skill_1"
                    else self.workspace_artifact_preview_storage_id
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + previewed_storage_object_id
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-run-feedback-status]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"Feedback recorded",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-cs-missing-skill-status]":
                missing_skill_status = (
                    "Skill request recorded"
                    if self.workspace_missing_skill_feedback_count > 0
                    else "Request idle"
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + missing_skill_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-cs-candidate-action-status]":
                if self.workspace_pack_candidate_reviewed:
                    candidate_action_status = "Pack candidate accepted, draft pack_draft_1"
                elif self.workspace_pack_candidates_generated:
                    candidate_action_status = "Pack candidates generated: 1"
                elif self.workspace_eval_candidate_reviewed:
                    candidate_action_status = "Eval candidate accepted, case eval_case_1"
                else:
                    candidate_action_status = "Eval candidates generated: 1"
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + candidate_action_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-cs-draft-status]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + self.workspace_draft_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-solution-pack-install-status]":
                install_status = (
                    "Solution pack installed: 1 skills"
                    if self.workspace_solution_pack_installed
                    else "Select a published pack"
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + install_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-skill-invoke-status]":
                skill_status = (
                    "Run run_skill_1"
                    if self.workspace_skill_invoked
                    else (
                        "Ready: sales.erp_invoice_matching"
                        if self.workspace_solution_pack_installed
                        else "No installed skills"
                    )
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + skill_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-run-history-status]":
                history_status = (
                    "2 recent runs"
                    if self.workspace_run_history_refreshed
                    else "No runs loaded"
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + history_status
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-run-history-list]":
                history_text = (
                    "Invoke ERP Invoice Matching. succeeded run_skill_1 "
                    "Create a hello report. succeeded run_1"
                    if self.workspace_run_history_refreshed
                    else "No runs."
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":'
                        + json.dumps(history_text)
                        + ',"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") in {
                "[data-trace-status]",
                "[data-trace-span-count]",
                "[data-trace-event-count]",
                "[data-trace-billing-count]",
                "[data-trace-audit-count]",
                "[data-trace-error-classification]",
            }:
                trace_text = {
                    "[data-trace-status]": "Loaded",
                    "[data-trace-span-count]": "3",
                    "[data-trace-event-count]": "3",
                    "[data-trace-billing-count]": "1",
                    "[data-trace-audit-count]": "1",
                    "[data-trace-error-classification]": "No error",
                }[payload["selector"]]
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + trace_text
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") in {
                "[data-runtime-state-status]",
                "[data-runtime-sandbox-session]",
                "[data-runtime-artifact-count]",
                "[data-execution-summary]",
                "[data-execution-model-route]",
                "[data-execution-sandbox]",
                "[data-execution-artifact]",
            }:
                run_detail_text = {
                    "[data-runtime-state-status]": "succeeded",
                    "[data-runtime-sandbox-session]": (
                        self.workspace_selected_history_sandbox_session_id
                    ),
                    "[data-runtime-artifact-count]": "1 promoted artifact paths",
                    "[data-execution-summary]": "Artifact ready",
                    "[data-execution-model-route]": (
                        "provider unknown · gpt-enterprise-planner · 165 tokens"
                    ),
                    "[data-execution-sandbox]": "Promoted",
                    "[data-execution-artifact]": "1 ready",
                }[payload["selector"]]
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + run_detail_text
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-artifact-download-status]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"Downloaded report.md",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-artifact-downloaded-storage-object]":
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + (self.workspace_downloaded_storage_object_id or "--")
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-terminal-output]":
                selected_skill_run = (
                    self.workspace_selected_history_run_id == "run_skill_1"
                )
                terminal_output_uri = self.workspace_terminal_output_uri or (
                    "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/"
                    "sandbox-command-outputs/sandbox_skill_1-output.json"
                    if selected_skill_run
                    else (
                        "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
                        "sandbox-command-outputs/model_sandbox-output.json"
                    )
                    if self.workspace_delivery_chain_terminal_storage_id
                    == "storage_model_sandbox_output_1"
                    else (
                        "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
                        "sandbox-command-outputs/sandbox_1-output.json"
                    )
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"exit 0\\nstdout 2 bytes\\nstderr 0 bytes\\n'
                        + terminal_output_uri
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == "[data-terminal-output-storage-object]":
                terminal_output_storage_id = (
                    "storage_skill_sandbox_output_1"
                    if self.workspace_selected_history_run_id == "run_skill_1"
                    else self.workspace_terminal_output_storage_id
                )
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"'
                        + terminal_output_storage_id
                        + '",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            if payload.get("selector") == '[data-testid="chat-column"]':
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"How can I help, luke? Press Enter to send, Shift+Enter for a new line.",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:05Z"}'
                    ),
                )
            return LocalCloudPocHttpResponse(
                status_code=201,
                body='{"tenant_id":"tenant_acme","workspace_id":"workspace_acme","run_id":"run_1","session_id":"browser_verify_1","action_type":"extract","current_url":"data:text/html,ok","text":"Browser smoke OK","screenshot_uri":null,"metadata":{},"created_at":"2026-07-03T14:00:05Z"}',
            )
        raise AssertionError(f"unexpected request: {method} {url}")


def test_local_cloud_poc_verification_cli_parses_core_inputs():
    output_path = "/tmp/local-cloud-poc-result.json"
    config = parse_args(
        [
            "--api-base-url",
            "http://api.local",
            "--browser-base-url",
            "http://browser.local",
            "--web-base-url",
            "http://web.local",
            "--bootstrap-token",
            "bootstrap_token",
            "--tenant-slug",
            "acme",
            "--browser-workspace-url",
            "http://web.internal",
            "--browser-workspace-api-base-url",
            "http://api.internal",
            "--browser-controller-api-key",
            "browser_secret",
            "--browser-workspace-auth-poll-interval-seconds",
            "0.25",
            "--browser-workspace-submit-message",
            "Generate a hello report.",
            "--browser-workspace-submit-expected-text",
            "model gateway model is not configured",
            "--browser-workspace-submit-poll-interval-seconds",
            "0.5",
            "--browser-workspace-submit-poll-attempts",
            "42",
            "--model-artifact-required-name",
            "report.md",
            "--output",
            output_path,
        ]
    )

    assert config.api_base_url == "http://api.local"
    assert config.browser_base_url == "http://browser.local"
    assert config.web_base_url == "http://web.local"
    assert config.bootstrap_token == "bootstrap_token"
    assert config.tenant_slug == "acme"
    assert config.browser_workspace_url == "http://web.internal"
    assert config.browser_workspace_api_base_url == "http://api.internal"
    assert config.browser_controller_api_key == "browser_secret"
    assert config.browser_workspace_auth_poll_interval_seconds == 0.25
    assert config.browser_workspace_submit_message == "Generate a hello report."
    assert (
        config.browser_workspace_submit_expected_text
        == "model gateway model is not configured"
    )
    assert config.browser_workspace_submit_poll_interval_seconds == 0.5
    assert config.browser_workspace_submit_poll_attempts == 42
    assert config.model_artifact_required_name == "report.md"
    assert config.output_path == output_path
    assert "sandbox.command" in config.run_message
    assert "/workspace/artifacts/report.md" in config.run_message


def test_local_cloud_poc_verification_default_run_message_targets_runtime_artifact():
    config = LocalCloudPocVerificationConfig(bootstrap_token="bootstrap_token")

    assert "sandbox.command" in config.run_message
    assert "/workspace/artifacts/report.md" in config.run_message


def test_local_cloud_poc_config_rejects_unknown_fields():
    with pytest.raises(ValidationError) as error:
        LocalCloudPocVerificationConfig(
            bootstrap_token="bootstrap_token",
            browser_workspace_submit_poll_attemps=1,
        )

    assert "browser_workspace_submit_poll_attemps" in str(error.value)


def test_local_cloud_poc_status_errors_redact_sensitive_response_body():
    response = LocalCloudPocHttpResponse(
        status_code=500,
        body=(
            '{"access_token":"session-secret-value","password":"owner-secret",'
            '"message":"Authorization: Bearer user-session-token-1234567890",'
            '"callback":"https://agent:secret-value@api.customer.local/v1"}'
        ),
    )

    with pytest.raises(RuntimeError) as error:
        assert_status(response, {200}, "owner login failed")

    message = str(error.value)
    assert "session-secret-value" not in message
    assert "owner-secret" not in message
    assert "user-session-token-1234567890" not in message
    assert "secret-value" not in message
    assert "[REDACTED:sensitive_field]" in message
    assert "[REDACTED:bearer_token]" in message
    assert "[REDACTED:credentialed_url]" in message


def test_local_cloud_poc_result_json_redacts_accidental_sensitive_fields():
    output = safe_result_json(
        {
            "browser_workspace_bootstrap_status": "Tenant ready",
            "bootstrap_token": "bootstrap_token",
            "browser_controller_api_key": "browser_controller_secret_2026",
            "message": "Authorization: Bearer user-session-token-1234567890",
        }
    )

    assert "Tenant ready" in output
    assert "bootstrap_token" in output
    assert "browser_controller_api_key" in output
    assert "user-session-token-1234567890" not in output
    assert "browser_controller_secret_2026" not in output
    assert '"bootstrap_token": "[REDACTED:sensitive_field]"' in output
    assert '"browser_controller_api_key": "[REDACTED:sensitive_field]"' in output
    assert "[REDACTED:bearer_token]" in output


def test_local_cloud_poc_result_json_can_be_written_as_redacted_evidence(tmp_path):
    output_path = tmp_path / "evidence" / "local-cloud-poc-result.json"

    output = write_safe_result_json(
        output_path,
        {
            "tenant_id": "tenant_acme",
            "bootstrap_token": "bootstrap_token",
            "message": "Authorization: Bearer user-session-token-1234567890",
        },
    )

    assert output_path.read_text(encoding="utf-8") == f"{output}\n"
    assert "tenant_acme" in output
    assert "bootstrap_token" in output
    assert "user-session-token-1234567890" not in output
    assert '"bootstrap_token": "[REDACTED:sensitive_field]"' in output


def test_local_cloud_poc_verification_sends_browser_controller_bearer_token():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        browser_unauthenticated_sessions_status_code=401,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        browser_controller_api_key="browser_secret",
        web_base_url="http://web.local",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        bootstrap_token="bootstrap_token",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    result = verify_local_cloud_poc(config, client)

    browser_calls = [
        call
        for call in client.calls
        if (
            call["host"] == "browser.local"
            and call["path"] != "/healthz"
            and call["headers"].get("Authorization")
        )
    ]
    assert browser_calls
    assert {
        call["headers"].get("Authorization") for call in browser_calls
    } == {"Bearer browser_secret"}
    assert result.browser_controller_auth_enforced is True
    assert result.browser_controller_auth_tenant_session_list_challenge_enforced is True
    assert result.browser_controller_auth_global_session_list_challenge_enforced is True
    assert result.browser_controller_auth_capabilities_challenge_enforced is True
    assert result.browser_controller_capabilities_checked is True
    assert result.browser_controller_session_ttl_enforced is True
    assert result.browser_controller_max_sessions_per_tenant == 20
    assert result.browser_controller_max_sessions_per_run == 3
    assert ("GET", "/capabilities") in [
        (call["method"], call["path"])
        for call in client.calls
        if call["host"] == "browser.local"
    ]


def test_local_cloud_poc_verification_result_rejects_unknown_evidence_fields():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        browser_unauthenticated_sessions_status_code=401,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        browser_controller_api_key="browser_secret",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )
    result = verify_local_cloud_poc(config, client)
    payload = result.model_dump()
    payload["browser_controller_auth_typo"] = True

    with pytest.raises(ValidationError) as error:
        LocalCloudPocVerificationResult.model_validate(payload)

    assert "browser_controller_auth_typo" in str(error.value)


def test_local_cloud_poc_verification_rejects_browser_controller_without_auth_challenge():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        browser_unauthenticated_sessions_status_code=200,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        browser_controller_api_key="browser_secret",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="browser controller did not reject unauthenticated requests",
    ):
        verify_local_cloud_poc(config, client)


def test_local_cloud_poc_verification_rejects_browser_global_sessions_without_auth_challenge():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        browser_unauthenticated_sessions_status_code=401,
        browser_unauthenticated_global_sessions_status_code=200,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        browser_controller_api_key="browser_secret",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="browser controller did not reject unauthenticated global session list requests",
    ):
        verify_local_cloud_poc(config, client)


def test_local_cloud_poc_verification_rejects_browser_capabilities_without_auth_challenge():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        browser_unauthenticated_sessions_status_code=401,
        browser_unauthenticated_capabilities_status_code=200,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        browser_controller_api_key="browser_secret",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="browser controller did not reject unauthenticated capabilities requests",
    ):
        verify_local_cloud_poc(config, client)


def test_local_cloud_poc_verification_rejects_browser_controller_without_capacity_capabilities():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        browser_unauthenticated_sessions_status_code=401,
        browser_capabilities_body=json.dumps(
            {
                "provider": "playwright",
                "auth_required": True,
                "session_ttl_enforced": False,
                "max_session_ttl_seconds": 0,
                "max_sessions": 50,
                "max_sessions_per_tenant": 0,
                "max_sessions_per_run": 0,
                "navigation_allowlist_enforced": False,
                "navigation_allowed_host_count": 0,
            },
            separators=(",", ":"),
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        browser_controller_api_key="browser_secret",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="browser controller capabilities are not ready",
    ):
        verify_local_cloud_poc(config, client)


def test_parse_args_defaults_browser_workspace_expected_text_to_success_in_strict_mode():
    config = parse_args(
        [
            "--api-base-url",
            "http://api.local",
            "--browser-base-url",
            "http://browser.local",
            "--bootstrap-token",
            "bootstrap_token",
            "--browser-workspace-url",
            "http://web.internal",
            "--browser-workspace-api-base-url",
            "http://api.internal",
            "--browser-workspace-submit-message",
            "Generate a hello report.",
            "--require-model-execution",
        ]
    )

    assert config.browser_workspace_submit_expected_text == "succeeded"
    assert config.browser_workspace_submit_poll_attempts == 30


def test_local_cloud_poc_config_rejects_workspace_submit_without_workspace_urls():
    with pytest.raises(ValidationError) as error:
        LocalCloudPocVerificationConfig(
            bootstrap_token="bootstrap_token",
            browser_workspace_submit_message="Generate a hello report.",
        )

    message = str(error.value)
    assert "browser_workspace_url is required when browser workspace submit is enabled" in message
    assert (
        "browser_workspace_api_base_url is required when browser workspace submit is enabled"
        in message
    )


def test_local_cloud_poc_config_rejects_workspace_api_base_without_workspace_url():
    with pytest.raises(ValidationError) as error:
        LocalCloudPocVerificationConfig(
            bootstrap_token="bootstrap_token",
            browser_workspace_api_base_url="http://api.internal",
        )

    assert (
        "browser_workspace_url is required when browser_workspace_api_base_url is configured"
        in str(error.value)
    )


def test_local_cloud_poc_config_rejects_strict_workspace_without_api_base():
    with pytest.raises(ValidationError) as error:
        LocalCloudPocVerificationConfig(
            bootstrap_token="bootstrap_token",
            browser_workspace_url="http://web.internal",
            require_model_execution=True,
        )

    assert (
        "browser_workspace_api_base_url is required when strict model execution uses browser workspace"
        in str(error.value)
    )


def test_local_cloud_poc_config_rejects_strict_workspace_without_submit_message():
    with pytest.raises(ValidationError) as error:
        LocalCloudPocVerificationConfig(
            bootstrap_token="bootstrap_token",
            browser_workspace_url="http://web.internal",
            browser_workspace_api_base_url="http://api.internal",
            browser_workspace_submit_expected_text="succeeded",
            require_model_execution=True,
        )

    assert (
        "browser_workspace_submit_message is required when strict model execution uses browser workspace"
        in str(error.value)
    )


def test_local_cloud_poc_verification_waits_for_delayed_browser_workspace_submit_status():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_status_texts=["running"] * 6 + ["succeeded"],
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        browser_workspace_submit_poll_interval_seconds=0,
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert "succeeded" in result.browser_workspace_submit_text
    status_extracts = [
        call
        for call in client.calls
        if call["host"] == "browser.local"
        and call["path"] == "/actions"
        and call["payload"].get("selector") == "[data-status-pill]"
    ]
    assert len(status_extracts) == 7


def test_local_cloud_poc_verification_rejects_workspace_without_login_contract():
    client = RecordingHttpClient(workspace_html="<title>Taroai Workspace</title>")
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(RuntimeError, match="web workspace response did not include"):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_readiness_contract():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            '<span data-readiness-status>Preflight unchecked</span>',
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include readiness status",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_workspace_selector():
    default_client = RecordingHttpClient()
    workspace_html = default_client.workspace_html.replace(
        '<input id="workspace-id" value="workspace_sales" autocomplete="off" />',
        "",
    )
    client = RecordingHttpClient(workspace_html=workspace_html)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include workspace input",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_browser_storage_contract():
    client = RecordingHttpClient(
        workspace_html=(
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<section data-testid="run-controls">'
            '<span data-run-control-status>No active run</span>'
            '<button id="cancel-run-button">Cancel</button>'
            '<button id="retry-run-button">Retry</button>'
            "</section>"
            '<section data-testid="run-history">'
            '<span data-run-history-status>No runs loaded</span>'
            '<button data-run-history-refresh>Refresh</button>'
            '<ul data-run-history-list><li>No runs.</li></ul>'
            "</section>"
            '<section data-testid="browser-panel">'
            '<span data-browser-status>Waiting</span>'
            '<span data-browser-session>--</span>'
            '<span data-browser-action>--</span>'
            '<span data-browser-url>--</span>'
            '<a data-browser-screenshot href="#">Capture</a>'
            '<img data-browser-screenshot-preview />'
            '<span data-browser-empty>No browser actions.</span>'
            "</section>"
            '<span data-artifact-preview-status>Preview idle</span>'
            '<span data-artifact-preview-title>No artifact selected</span>'
            '<span data-artifact-preview-storage-object '
            'data-preview-storage-object-id="">--</span>'
            '<pre data-artifact-preview-content>Select an artifact preview.</pre>'
            '<div data-run-feedback-panel>'
            '<span data-run-feedback-status data-run-feedback-state="waiting">'
            "Feedback unavailable</span>"
            '<button id="run-feedback-positive">Useful</button>'
            '<button id="run-feedback-negative">Needs work</button>'
            "</div>"
            '<section data-testid="solution-pack-panel">'
            '<span data-solution-pack-status>No packs loaded</span>'
            '<ul data-solution-pack-list><li>No solution packs.</li></ul>'
            '<button data-solution-pack-refresh>Refresh packs</button>'
            '<button id="install-solution-pack-button">Install to workspace</button>'
            '<span data-solution-pack-install-status>Select a published pack</span>'
            "</section>"
            '<section data-testid="workspace-skills-panel">'
            '<span data-skills-status>No skills loaded</span>'
            '<ul data-skills-list><li>No installed skills.</li></ul>'
            '<button data-skills-refresh>Refresh skills</button>'
            '<textarea id="skill-invoke-input"></textarea>'
            '<button id="invoke-skill-button">Invoke skill</button>'
            '<span data-skill-invoke-status>Select a ready skill</span>'
            "</section>"
            '<strong data-cs-missing-skill-status>Request idle</strong>'
            '<input id="cs-missing-skill-name" />'
            '<textarea id="cs-missing-skill-comment"></textarea>'
            '<input id="cs-missing-skill-solution-pack" />'
            '<button id="cs-submit-missing-skill">Record request</button>'
            '<span data-cs-candidate-action-status>Candidate actions idle</span>'
            '<button id="cs-create-eval-candidates">Generate eval candidates</button>'
            '<button id="cs-create-pack-candidates">Generate pack candidates</button>'
            '<span data-cs-eval-candidate-selected>No eval candidate selected</span>'
            '<button id="cs-accept-eval-candidate">Accept eval</button>'
            '<button id="cs-reject-eval-candidate">Reject eval</button>'
            '<span data-cs-pack-candidate-selected>No pack candidate selected</span>'
            '<button id="cs-accept-pack-candidate">Accept pack</button>'
            '<button id="cs-reject-pack-candidate">Reject pack</button>'
            '<script src="./assets/main.js" type="module"></script>'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )
    client.workspace_html = RecordingHttpClient().workspace_html.replace(
        '<span data-browser-storage-object>--</span>', ""
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include browser storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_execution_loop_contract():
    execution_html = (
        '<section data-testid="execution-loop">'
        '<span data-execution-summary>No active run</span>'
        '<span data-execution-model-route>No model route</span>'
        '<span data-execution-run>Idle</span>'
        '<span data-execution-plan>Waiting</span>'
        '<span data-execution-sandbox>Waiting</span>'
        '<span data-execution-browser>Waiting</span>'
        '<span data-execution-artifact>Waiting</span>'
        "</section>"
    )
    client = RecordingHttpClient()
    client.workspace_html = client.workspace_html.replace(execution_html, "")
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include execution loop",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_browser_preview_storage_contract():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<span data-browser-preview-storage-object '
                'data-browser-preview-storage-object-id="">--</span>'
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include browser preview storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_browser_preview_storage_provenance():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace(
                "renderBrowserPreviewStorageObject();",
                "",
            )
            .replace("browserPreviewStorageObject;", "")
            .replace("dataset.browserPreviewStorageObjectId;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include browser preview storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_bearer_login():
    client = RecordingHttpClient(workspace_script='console.log("workspace");')
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(RuntimeError, match="web workspace script did not include"):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_url_config_prefill():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "applyUrlConfiguration();",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include URL config prefill",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_workspace_bootstrap():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            'apiFetch("/api/tenants/bootstrap");',
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include tenant bootstrap endpoint",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_readiness_fetch():
    client = RecordingHttpClient(
        workspace_script=(
            'apiFetch("/api/auth/login");'
            'headers["Authorization"] = "Bearer ";'
            "sessionStorage.setItem('taroai.accessToken', state.accessToken);"
            "sessionStorage.removeItem('taroai.accessToken');"
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include readiness endpoint",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_tenant_sync():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "result.tenant_id;",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include tenant login sync",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_user_sync():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "result.user_id;",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include user login sync",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_auth_failure_clear():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            'clearAuthenticatedWorkspaceState("Authentication failed.");',
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include auth failure state clear",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_auth_expiry_clear():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            'clearAuthenticatedWorkspaceState("Authentication expired.");',
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include auth expiry state clear",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_storage_auth_handler():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "raiseStorageFetchError(response);",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include storage content auth error handler",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_artifact_auto_preview():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "autoPreviewFirstDeliveredArtifact();",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include artifact auto preview",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_artifact_preview_retry_state():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("previewedRunIds;", "")
            .replace("state.previewedRunIds.has(state.currentRunId);", "")
            .replace("state.previewedRunIds.add(state.currentRunId);", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include artifact preview retry state",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_logout_clear():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "clearAuthenticatedWorkspaceState();",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include logout state clear",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_conversation_reset():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=default_client.workspace_script.replace(
            "resetConversation();",
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include logout conversation reset",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_raw_terminal_streams():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script
            + 'const stdout = latest.stdout || "";'
            + 'renderTerminal([stdout, stderr].filter(Boolean).join("\\n"));'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script included raw sandbox command stream",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_terminal_output_object():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<span data-terminal-output-storage-object '
                'data-terminal-storage-object-id="">--</span>'
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include terminal output storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_terminal_output_object_provenance():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("terminalOutputStorageObject;", "")
            .replace("dataset.terminalStorageObjectId;", "")
            .replace("storageObjectForTerminalOutputUri();", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include terminal output storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_run_controls():
    client = RecordingHttpClient(
        workspace_html=(
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<span data-browser-storage-object>--</span>'
            '<script src="./assets/main.js" type="module"></script>'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )
    client.workspace_html = RecordingHttpClient().workspace_html.replace(
        'data-testid="run-controls"', ""
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include run controls",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_artifact_preview():
    client = RecordingHttpClient(
        workspace_html=(
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<section data-testid="run-controls">'
            '<span data-run-control-status>No active run</span>'
            '<button id="cancel-run-button">Cancel</button>'
            '<button id="retry-run-button">Retry</button>'
            "</section>"
            '<section data-testid="run-history">'
            '<span data-run-history-status>No runs loaded</span>'
            '<button data-run-history-refresh>Refresh</button>'
            '<ul data-run-history-list><li>No runs.</li></ul>'
            "</section>"
            '<span data-browser-storage-object>--</span>'
            '<span data-browser-preview-storage-object '
            'data-browser-preview-storage-object-id="">--</span>'
            '<span data-artifact-download-status data-download-state="idle">'
            "No artifact downloaded</span>"
            '<span data-artifact-downloaded-storage-object '
            'data-download-storage-object-id="">--</span>'
            '<script src="./assets/main.js" type="module"></script>'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )
    client.workspace_html = RecordingHttpClient().workspace_html.replace(
        "data-artifact-preview-status", ""
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include artifact preview status",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_artifact_download_object():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<span data-artifact-downloaded-storage-object '
                'data-download-storage-object-id="">--</span>'
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include artifact downloaded storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_delivery_summary():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<div data-delivery-summary data-delivery-state="waiting">'
                "No artifacts delivered</div>"
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include delivery summary",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_delivery_chain():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<section data-testid="delivery-chain">'
                '<span data-delivery-chain-status data-delivery-chain-state="waiting">'
                "No delivery chain</span>"
                '<span data-delivery-chain-run>--</span>'
                '<span data-delivery-chain-sandbox>--</span>'
                '<span data-delivery-chain-artifact-storage>--</span>'
                '<span data-delivery-chain-terminal-storage>--</span>'
                '<span data-delivery-chain-browser-storage>--</span>'
                "</section>"
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include delivery chain panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_delivery_chain():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("renderDeliveryChain();", "")
            .replace("buildDeliveryChainEvidence();", "")
            .replace("deliveryChainStatus;", "")
            .replace("deliveryChainRun;", "")
            .replace("deliveryChainSandbox;", "")
            .replace("deliveryChainArtifactStorage;", "")
            .replace("deliveryChainTerminalStorage;", "")
            .replace("deliveryChainBrowserStorage;", "")
            .replace("readyStorageBackedArtifacts();", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include delivery chain renderer",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_event_integrity():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<section data-testid="event-integrity">'
                '<span data-event-integrity-status data-event-integrity-state="waiting">'
                "No event stream</span>"
                '<span data-event-integrity-count>--</span>'
                '<span data-event-integrity-sequence>--</span>'
                '<span data-event-integrity-closure>--</span>'
                "</section>"
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include event integrity panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_event_integrity():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("renderEventIntegrity();", "")
            .replace("buildEventIntegrityEvidence();", "")
            .replace("eventIntegrityStatus;", "")
            .replace("eventIntegritySequence;", "")
            .replace("eventIntegrityClosure;", "")
            .replace("event stream sequence", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include event integrity renderer",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_event_merge():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("eventIdentity(event);", "")
            .replace("eventStreamIntegrityIssues;", "")
            .replace("recordEventStreamIntegrityIssues(newEvents);", "")
            .replace("eventAlreadyLoaded(event);", "")
            .replace("compareEventsBySequence;", "")
            .replace("lastFiniteEventSequence(state.events);", "")
            .replace("eventSequence(event);", "")
            .replace("incoming event stream sequence is not monotonic;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include event stream",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_sse_parser():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("eventLineType;", "")
            .replace("eventLineId;", "")
            .replace("dataLines;", "")
            .replace("dataLines.join;", "")
            .replace("parsed.type = parsed.type || eventLineType;", "")
            .replace("parsed.id = parsed.id || eventLineId;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include SSE event type parser",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_artifact_preview_object():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<span data-artifact-preview-storage-object '
                'data-preview-storage-object-id="">--</span>'
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include artifact preview storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_download_object_provenance():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("artifactDownloadedStorageObject;", "")
            .replace("dataset.downloadStorageObjectId;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include artifact downloaded storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_preview_object_provenance():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("artifactPreviewStorageObject;", "")
            .replace("dataset.previewStorageObjectId;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include artifact preview storage object",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_run_feedback_controls():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<div data-run-feedback-panel>'
                '<span data-run-feedback-status data-run-feedback-state="waiting">'
                "Feedback unavailable</span>"
                '<button id="run-feedback-positive">Useful</button>'
                '<button id="run-feedback-negative">Needs work</button>'
                "</div>"
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include run feedback controls",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_approval_resolution():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=default_client.workspace_html.replace(
            (
                '<div data-approval-resolution data-resolution-state="idle">'
                "No approval decision yet.</div>"
            ),
            "",
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include approval resolution",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_run_feedback_submit():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("feedbackSubmittedRunIds;", "")
            .replace("renderRunFeedback();", "")
            .replace("submitRunFeedback();", "")
            .replace('"/api/customer-success/feedback";', "")
            .replace('feedback_type: "thumbs_rating";', "")
            .replace('target_type: "run";', "")
            .replace("artifact_count: readyArtifacts.length;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include run feedback submission",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_approval_resolution_renderer():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("renderApprovalResolution();", "")
            .replace("approvalResolutionParts();", "")
            .replace("latestApprovalEvent();", "")
            .replace("approvalResolution;", "")
            .replace('event.type === "approval.resolved";', "")
            .replace('event.type === "approval.rejected";', "")
            .replace("payload.approval_id;", "")
            .replace("payload.resolved_by_user_id;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include approval latest event",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_missing_skill_feedback_controls():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=(
            default_client.workspace_html.replace(
                '<strong data-cs-missing-skill-status>Request idle</strong>',
                "",
            )
            .replace('<input id="cs-missing-skill-name" />', "")
            .replace('<textarea id="cs-missing-skill-comment"></textarea>', "")
            .replace('<input id="cs-missing-skill-solution-pack" />', "")
            .replace('<button id="cs-submit-missing-skill">Record request</button>', "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include missing skill feedback status",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_skill_invoke_controls():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=(
            default_client.workspace_html.replace(
                '<section data-testid="workspace-skills-panel">',
                "",
            )
            .replace('<span data-skills-status>No skills loaded</span>', "")
            .replace('<ul data-skills-list><li>No installed skills.</li></ul>', "")
            .replace('<button data-skills-refresh>Refresh skills</button>', "")
            .replace('<textarea id="skill-invoke-input"></textarea>', "")
            .replace('<button id="invoke-skill-button">Invoke skill</button>', "")
            .replace('<span data-skill-invoke-status>Select a ready skill</span>', "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include workspace skills panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_solution_pack_install_controls():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=(
            default_client.workspace_html.replace(
                '<section data-testid="solution-pack-panel">',
                "",
            )
            .replace('<span data-solution-pack-status>No packs loaded</span>', "")
            .replace('<ul data-solution-pack-list><li>No solution packs.</li></ul>', "")
            .replace('<button data-solution-pack-refresh>Refresh packs</button>', "")
            .replace(
                '<button id="install-solution-pack-button">Install to workspace</button>',
                "",
            )
            .replace(
                '<span data-solution-pack-install-status>Select a published pack</span>',
                "",
            )
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include solution pack panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_solution_pack_install():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("loadSolutionPacks();", "")
            .replace("renderSolutionPacks();", "")
            .replace('apiFetch("/api/solution-packs");', "")
            .replace("data-solution-pack-id;", "")
            .replace("installSelectedSolutionPack();", "")
            .replace(
                "`/api/solution-packs/${encodeURIComponent(pack.manifest.id)}/install`;",
                "",
            )
            .replace("workspace_ids: [state.workspaceId];", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include solution pack loader",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_missing_skill_feedback():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("submitMissingSkillFeedback();", "")
            .replace("customerSuccessMissingSkillStatus;", "")
            .replace('feedback_type: "missing_skill";', "")
            .replace('target_type: "solution_pack";', "")
            .replace("missing_skill_name: missingSkillName;", "")
            .replace('source: "workspace_skill_request";', "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include missing skill feedback",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_candidate_generation_controls():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=(
            default_client.workspace_html.replace(
                '<span data-cs-candidate-action-status>Candidate actions idle</span>',
                "",
            )
            .replace(
                '<button id="cs-create-eval-candidates">Generate eval candidates</button>',
                "",
            )
            .replace(
                '<button id="cs-create-pack-candidates">Generate pack candidates</button>',
                "",
            )
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include candidate generation controls",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_candidate_generation():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("customerSuccessCandidateStatus;", "")
            .replace("createCustomerSuccessEvaluationCandidates();", "")
            .replace("createCustomerSuccessSolutionPackCandidates();", "")
            .replace('"/api/customer-success/evaluation-candidates";', "")
            .replace('"/api/customer-success/solution-pack-candidates";', "")
            .replace("minimum_repeated_feedback: 3;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include candidate generation",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_pack_candidate_review_controls():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_html=(
            default_client.workspace_html.replace(
                '<span data-cs-pack-candidate-selected>No pack candidate selected</span>',
                "",
            )
            .replace(
                '<button id="cs-accept-pack-candidate">Accept pack</button>',
                "",
            )
            .replace(
                '<button id="cs-reject-pack-candidate">Reject pack</button>',
                "",
            )
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include pack candidate review selected",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_script_without_pack_candidate_review():
    default_client = RecordingHttpClient()
    client = RecordingHttpClient(
        workspace_script=(
            default_client.workspace_script.replace("renderSolutionPackCandidateReview();", "")
            .replace("selectedSolutionPackCandidate();", "")
            .replace("reviewSelectedSolutionPackCandidate();", "")
            .replace("solutionPackCandidateReviewPayload();", "")
            .replace(
                "`/api/customer-success/solution-pack-candidates/${candidate.id}/review`;",
                "",
            )
            .replace("Pack candidate accepted;", "")
            .replace("publication_draft_id;", "")
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace script did not include pack candidate review",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_run_history():
    client = RecordingHttpClient(
        workspace_html=(
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<section data-testid="run-controls">'
            '<span data-run-control-status>No active run</span>'
            '<button id="cancel-run-button">Cancel</button>'
            '<button id="retry-run-button">Retry</button>'
            "</section>"
            '<span data-browser-storage-object>--</span>'
            '<span data-browser-preview-storage-object '
            'data-browser-preview-storage-object-id="">--</span>'
            '<span data-artifact-download-status data-download-state="idle">'
            "No artifact downloaded</span>"
            '<span data-artifact-downloaded-storage-object '
            'data-download-storage-object-id="">--</span>'
            '<span data-artifact-preview-status>Preview idle</span>'
            '<span data-artifact-preview-title>No artifact selected</span>'
            '<span data-artifact-preview-storage-object '
            'data-preview-storage-object-id="">--</span>'
            '<pre data-artifact-preview-content>Select an artifact preview.</pre>'
            '<div data-run-feedback-panel>'
            '<span data-run-feedback-status data-run-feedback-state="waiting">'
            "Feedback unavailable</span>"
            '<button id="run-feedback-positive">Useful</button>'
            '<button id="run-feedback-negative">Needs work</button>'
            "</div>"
            '<section data-testid="solution-pack-panel">'
            '<span data-solution-pack-status>No packs loaded</span>'
            '<ul data-solution-pack-list><li>No solution packs.</li></ul>'
            '<button data-solution-pack-refresh>Refresh packs</button>'
            '<button id="install-solution-pack-button">Install to workspace</button>'
            '<span data-solution-pack-install-status>Select a published pack</span>'
            "</section>"
            '<section data-testid="workspace-skills-panel">'
            '<span data-skills-status>No skills loaded</span>'
            '<ul data-skills-list><li>No installed skills.</li></ul>'
            '<button data-skills-refresh>Refresh skills</button>'
            '<textarea id="skill-invoke-input"></textarea>'
            '<button id="invoke-skill-button">Invoke skill</button>'
            '<span data-skill-invoke-status>Select a ready skill</span>'
            "</section>"
            '<strong data-cs-missing-skill-status>Request idle</strong>'
            '<input id="cs-missing-skill-name" />'
            '<textarea id="cs-missing-skill-comment"></textarea>'
            '<input id="cs-missing-skill-solution-pack" />'
            '<button id="cs-submit-missing-skill">Record request</button>'
            '<span data-cs-candidate-action-status>Candidate actions idle</span>'
            '<button id="cs-create-eval-candidates">Generate eval candidates</button>'
            '<button id="cs-create-pack-candidates">Generate pack candidates</button>'
            '<span data-cs-eval-candidate-selected>No eval candidate selected</span>'
            '<button id="cs-accept-eval-candidate">Accept eval</button>'
            '<button id="cs-reject-eval-candidate">Reject eval</button>'
            '<span data-cs-pack-candidate-selected>No pack candidate selected</span>'
            '<button id="cs-accept-pack-candidate">Accept pack</button>'
            '<button id="cs-reject-pack-candidate">Reject pack</button>'
            '<script src="./assets/main.js" type="module"></script>'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )
    client.workspace_html = RecordingHttpClient().workspace_html.replace(
        'data-testid="run-history"', ""
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include run history panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_trace_panel():
    client = RecordingHttpClient(
        workspace_html=(
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<section data-testid="run-controls">'
            '<span data-run-control-status>No active run</span>'
            '<button id="cancel-run-button">Cancel</button>'
            '<button id="retry-run-button">Retry</button>'
            "</section>"
            '<section data-testid="run-history">'
            '<span data-run-history-status>No runs loaded</span>'
            '<button data-run-history-refresh>Refresh</button>'
            '<ul data-run-history-list><li>No runs.</li></ul>'
            "</section>"
            '<span data-browser-storage-object>--</span>'
            '<span data-browser-preview-storage-object '
            'data-browser-preview-storage-object-id="">--</span>'
            '<span data-artifact-download-status data-download-state="idle">'
            "No artifact downloaded</span>"
            '<span data-artifact-downloaded-storage-object '
            'data-download-storage-object-id="">--</span>'
            '<span data-artifact-preview-status>Preview idle</span>'
            '<span data-artifact-preview-title>No artifact selected</span>'
            '<span data-artifact-preview-storage-object '
            'data-preview-storage-object-id="">--</span>'
            '<pre data-artifact-preview-content>Select an artifact preview.</pre>'
            '<div data-run-feedback-panel>'
            '<span data-run-feedback-status data-run-feedback-state="waiting">'
            "Feedback unavailable</span>"
            '<button id="run-feedback-positive">Useful</button>'
            '<button id="run-feedback-negative">Needs work</button>'
            "</div>"
            '<section data-testid="solution-pack-panel">'
            '<span data-solution-pack-status>No packs loaded</span>'
            '<ul data-solution-pack-list><li>No solution packs.</li></ul>'
            '<button data-solution-pack-refresh>Refresh packs</button>'
            '<button id="install-solution-pack-button">Install to workspace</button>'
            '<span data-solution-pack-install-status>Select a published pack</span>'
            "</section>"
            '<section data-testid="workspace-skills-panel">'
            '<span data-skills-status>No skills loaded</span>'
            '<ul data-skills-list><li>No installed skills.</li></ul>'
            '<button data-skills-refresh>Refresh skills</button>'
            '<textarea id="skill-invoke-input"></textarea>'
            '<button id="invoke-skill-button">Invoke skill</button>'
            '<span data-skill-invoke-status>Select a ready skill</span>'
            "</section>"
            '<strong data-cs-missing-skill-status>Request idle</strong>'
            '<input id="cs-missing-skill-name" />'
            '<textarea id="cs-missing-skill-comment"></textarea>'
            '<input id="cs-missing-skill-solution-pack" />'
            '<button id="cs-submit-missing-skill">Record request</button>'
            '<span data-cs-candidate-action-status>Candidate actions idle</span>'
            '<button id="cs-create-eval-candidates">Generate eval candidates</button>'
            '<button id="cs-create-pack-candidates">Generate pack candidates</button>'
            '<span data-cs-eval-candidate-selected>No eval candidate selected</span>'
            '<button id="cs-accept-eval-candidate">Accept eval</button>'
            '<button id="cs-reject-eval-candidate">Reject eval</button>'
            '<span data-cs-pack-candidate-selected>No pack candidate selected</span>'
            '<button id="cs-accept-pack-candidate">Accept pack</button>'
            '<button id="cs-reject-pack-candidate">Reject pack</button>'
            '<script src="./assets/main.js" type="module"></script>'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )
    client.workspace_html = RecordingHttpClient().workspace_html.replace(
        'data-testid="run-trace"', ""
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include trace panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_rejects_workspace_without_runtime_state():
    client = RecordingHttpClient(
        workspace_html=(
            '<title>Taroai Workspace</title>'
            '<main data-testid="chat-column">'
            "How can I help, luke?"
            "Press Enter to send, Shift+Enter for a new line."
            "</main>"
            '<input id="login-email" />'
            '<input id="login-password" />'
            '<button id="login-button">Login</button>'
            '<button id="logout-button">Logout</button>'
            '<span data-auth-status>No token</span>'
            '<span data-readiness-status>Preflight unchecked</span>'
            '<span data-readiness-model>Model unchecked</span>'
            '<span data-readiness-sandbox>Sandbox unchecked</span>'
            '<section data-testid="run-controls">'
            '<span data-run-control-status>No active run</span>'
            '<button id="cancel-run-button">Cancel</button>'
            '<button id="retry-run-button">Retry</button>'
            "</section>"
            '<section data-testid="run-history">'
            '<span data-run-history-status>No runs loaded</span>'
            '<button data-run-history-refresh>Refresh</button>'
            '<ul data-run-history-list><li>No runs.</li></ul>'
            "</section>"
            '<section data-testid="run-trace">'
            '<span data-trace-status>Not loaded</span>'
            '<span data-trace-span-count>--</span>'
            '<span data-trace-event-count>--</span>'
            '<span data-trace-billing-count>--</span>'
            '<span data-trace-audit-count>--</span>'
            '<span data-trace-error-classification>No error</span>'
            '<ul data-trace-list><li>No trace loaded.</li></ul>'
            "</section>"
            '<span data-browser-storage-object>--</span>'
            '<span data-browser-preview-storage-object '
            'data-browser-preview-storage-object-id="">--</span>'
            '<span data-artifact-download-status data-download-state="idle">'
            "No artifact downloaded</span>"
            '<span data-artifact-downloaded-storage-object '
            'data-download-storage-object-id="">--</span>'
            '<span data-artifact-preview-status>Preview idle</span>'
            '<span data-artifact-preview-title>No artifact selected</span>'
            '<span data-artifact-preview-storage-object '
            'data-preview-storage-object-id="">--</span>'
            '<pre data-artifact-preview-content>Select an artifact preview.</pre>'
            '<div data-run-feedback-panel>'
            '<span data-run-feedback-status data-run-feedback-state="waiting">'
            "Feedback unavailable</span>"
            '<button id="run-feedback-positive">Useful</button>'
            '<button id="run-feedback-negative">Needs work</button>'
            "</div>"
            '<section data-testid="solution-pack-panel">'
            '<span data-solution-pack-status>No packs loaded</span>'
            '<ul data-solution-pack-list><li>No solution packs.</li></ul>'
            '<button data-solution-pack-refresh>Refresh packs</button>'
            '<button id="install-solution-pack-button">Install to workspace</button>'
            '<span data-solution-pack-install-status>Select a published pack</span>'
            "</section>"
            '<section data-testid="workspace-skills-panel">'
            '<span data-skills-status>No skills loaded</span>'
            '<ul data-skills-list><li>No installed skills.</li></ul>'
            '<button data-skills-refresh>Refresh skills</button>'
            '<textarea id="skill-invoke-input"></textarea>'
            '<button id="invoke-skill-button">Invoke skill</button>'
            '<span data-skill-invoke-status>Select a ready skill</span>'
            "</section>"
            '<strong data-cs-missing-skill-status>Request idle</strong>'
            '<input id="cs-missing-skill-name" />'
            '<textarea id="cs-missing-skill-comment"></textarea>'
            '<input id="cs-missing-skill-solution-pack" />'
            '<button id="cs-submit-missing-skill">Record request</button>'
            '<span data-cs-candidate-action-status>Candidate actions idle</span>'
            '<button id="cs-create-eval-candidates">Generate eval candidates</button>'
            '<button id="cs-create-pack-candidates">Generate pack candidates</button>'
            '<span data-cs-eval-candidate-selected>No eval candidate selected</span>'
            '<button id="cs-accept-eval-candidate">Accept eval</button>'
            '<button id="cs-reject-eval-candidate">Reject eval</button>'
            '<span data-cs-pack-candidate-selected>No pack candidate selected</span>'
            '<button id="cs-accept-pack-candidate">Accept pack</button>'
            '<button id="cs-reject-pack-candidate">Reject pack</button>'
            '<script src="./assets/main.js" type="module"></script>'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
    )
    client.workspace_html = RecordingHttpClient().workspace_html.replace(
        'data-testid="runtime-state"', ""
    )

    with pytest.raises(
        RuntimeError,
        match="web workspace response did not include runtime state panel",
    ):
        verify_web(client, config)


def test_local_cloud_poc_verification_runs_auth_sandbox_and_browser_smoke():
    client = RecordingHttpClient()
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.api_health_ok is True
    assert result.browser_health_ok is True
    assert result.web_ok is True
    assert result.tenant_id == "tenant_acme"
    assert result.tenant_ready is True
    assert result.model_gateway_configured is False
    assert result.sandbox_configured is True
    assert result.sandbox_provider == "local_process"
    assert result.sandbox_missing == []
    assert result.sandbox_capabilities_checked is True
    assert result.sandbox_network_isolation_declared is False
    assert result.sandbox_filesystem_isolation_declared is False
    assert result.sandbox_resource_limits_declared is False
    assert result.sandbox_destroy_supported_declared is True
    assert result.sandbox_session_ttl_enforced_declared is False
    assert result.sandbox_runtime_isolation_declared is False
    assert result.sandbox_image_policy_enforced_declared is False
    assert result.sandbox_allowed_image_count == 0
    assert result.sandbox_max_session_ttl_seconds == 0
    assert result.sandbox_max_sessions == 50
    assert result.sandbox_max_sessions_per_tenant == 20
    assert result.sandbox_max_sessions_per_run == 3
    assert result.execute_status_code == 503
    assert result.execute_code == "model_gateway_unavailable"
    assert result.sandbox_exit_code == 0
    assert result.sandbox_output_uri == (
        "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
        "sandbox-command-outputs/sandbox_1-output.json"
    )
    assert result.sandbox_output_storage_object_id == "storage_sandbox_output_1"
    assert result.sandbox_output_download_bytes > 0
    assert result.sandbox_session_destroyed is True
    assert result.sandbox_destroy_status_confirmed is True
    assert result.sandbox_post_destroy_command_blocked is True
    assert result.browser_screenshot_storage_object_id == "storage_browser_1"
    assert result.browser_screenshot_download_bytes == len(PNG_BYTES)
    assert result.browser_controller_capabilities_checked is True
    assert result.browser_controller_session_ttl_enforced is True
    assert result.browser_controller_max_sessions == 50
    assert result.browser_session_listed is True
    assert result.browser_tenant_session_scope_enforced is True
    assert result.browser_session_read_scope_enforced is True
    assert result.browser_session_delete_scope_enforced is True
    assert result.browser_extract_text == "Browser smoke OK"
    assert "How can I help" in result.browser_workspace_text
    assert ("DELETE", "/sessions/browser_verify_1") in [
        (call["method"], call["path"]) for call in client.calls
    ]
    assert ("GET", "/sessions/browser_verify_1") in [
        (call["method"], call["path"]) for call in client.calls
    ]
    assert [call["path"] for call in client.calls] == [
            "/healthz",
            "/readyz",
            "/healthz",
            "/capabilities",
            "/",
        "/assets/main.js",
        "/api/tenants/bootstrap",
        "/api/auth/login",
        "/api/tenants/current/readiness",
        "/api/runs",
        "/api/runs/run_1/execute",
        "/api/sandbox/sessions",
        "/api/sandbox/sessions/sandbox_1/commands",
        "/api/runs/run_1/storage-objects",
        "/api/storage/objects/storage_sandbox_output_1/content",
        "/api/browser/sessions/sandbox_1/actions",
        "/api/runs/run_1/storage-objects",
        "/api/storage/objects/storage_browser_1/content",
        "/api/sandbox/sessions/sandbox_1",
        "/api/sandbox/sessions/sandbox_1/commands",
        "/sessions",
        "/sessions",
        "/sessions",
        "/sessions/browser_verify_1",
        "/sessions/browser_verify_1",
        "/sessions",
        "/actions",
        "/actions",
        "/actions",
        "/actions",
        "/sessions/browser_verify_1",
        "/sessions/browser_verify_1",
    ]


def test_local_cloud_poc_verification_rejects_empty_browser_delete_response():
    client = RecordingHttpClient(browser_delete_empty_response=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
    )

    with pytest.raises(
        RuntimeError,
        match="browser session deletion response did not include deleted session",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_configured_model_before_run_creation():
    client = RecordingHttpClient()
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="model gateway is not configured for strict model execution: missing model, credential",
    ):
        verify_local_cloud_poc(config, client=client)

    assert "/api/runs" not in [call["path"] for call in client.calls]


def test_local_cloud_poc_verification_requires_configured_sandbox_before_run_creation():
    client = RecordingHttpClient(
        sandbox_configured=False,
        sandbox_missing=["sandbox_controller_base_url"],
        sandbox_provider="k8s",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
    )

    with pytest.raises(
        RuntimeError,
        match="sandbox is not configured for local cloud PoC execution: missing sandbox_controller_base_url",
    ):
        verify_local_cloud_poc(config, client=client)

    assert "/api/runs" not in [call["path"] for call in client.calls]


def test_local_cloud_poc_verification_requires_sandbox_destroyed_status():
    client = RecordingHttpClient(
        sandbox_destroy_body=(
            '{"id":"sandbox_1","status":"active","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"provider":"local_process","image":"python:3.12",'
            '"network_mode":"disabled","created_at":"2026-07-03T14:00:00Z",'
            '"destroyed_at":null,"timeout_seconds":300,"metadata":{}}'
        )
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
    )

    with pytest.raises(
        RuntimeError,
        match="sandbox session destroy did not return destroyed status",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_logs_in_through_browser_workspace():
    client = RecordingHttpClient()
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.local_smoke_ready is True
    assert result.strict_model_ready is False
    assert result.workspace_execution_ready is False
    assert result.skill_reuse_ready is False
    assert result.demo_ready is False
    assert result.demo_readiness_summary == "local smoke ready; model gateway missing"
    assert result.browser_workspace_auth_status == "Bearer"
    assert result.browser_workspace_readiness_status == "Preflight needs config"
    assert result.browser_workspace_readiness_model == "Model missing: model, credential"
    assert result.browser_workspace_readiness_sandbox == "Sandbox PoC: local_process"
    browser_actions = [
        call["payload"]
        for call in client.calls
        if call["host"] == "browser.local" and call["path"] == "/actions"
    ]
    workspace_navigation_url = next(
        action["url"]
        for action in browser_actions
        if action["action_type"] == "navigate"
        and action.get("url", "").startswith("http://web.internal")
    )
    workspace_navigation_query = parse_qs(urlparse(workspace_navigation_url).query)
    assert workspace_navigation_query["apiBase"] == ["http://api.internal"]
    assert workspace_navigation_query["tenantId"] == ["tenant_acme"]
    assert workspace_navigation_query["userId"] == ["user_owner"]
    assert workspace_navigation_query["workspaceId"] == ["workspace_acme"]
    assert workspace_navigation_query["email"] == ["owner@example.com"]
    assert "accessToken" not in workspace_navigation_query
    assert "password" not in workspace_navigation_query
    observed = [
        (
            action["action_type"],
            action.get("selector"),
            action.get("text"),
        )
        for action in browser_actions
    ]
    assert ("type", "#api-base", "http://api.internal") in observed
    assert ("type", "#tenant-id", "tenant_acme") in observed
    assert ("type", "#workspace-id", "workspace_acme") in observed
    assert ("type", "#login-email", "owner@example.com") in observed
    assert ("type", "#login-password", "correct horse battery staple") in observed
    assert ("click", "#login-button", None) in observed
    assert ("extract", "[data-auth-status]", None) in observed
    assert ("extract", "[data-readiness-status]", None) in observed
    assert ("extract", "[data-readiness-model]", None) in observed
    assert ("extract", "[data-readiness-sandbox]", None) in observed


def test_local_cloud_poc_verification_bootstraps_through_browser_workspace():
    client = RecordingHttpClient()
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_display_name="Acme Owner",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.browser_workspace_bootstrap_status == "Tenant ready"
    assert result.browser_workspace_bootstrap_tenant_id == "tenant_acme"
    assert result.browser_workspace_bootstrap_user_id == "user_owner"
    assert result.browser_workspace_bootstrap_workspace_id == "workspace_acme"
    assert result.browser_workspace_bootstrap_token_cleared is True
    browser_actions = [
        call["payload"]
        for call in client.calls
        if call["host"] == "browser.local" and call["path"] == "/actions"
    ]
    observed = [
        (
            action["action_type"],
            action.get("selector"),
            action.get("text"),
        )
        for action in browser_actions
    ]
    assert ("type", "#tenant-slug", "acme") in observed
    assert ("type", "#owner-display-name", "Acme Owner") in observed
    assert ("type", "#bootstrap-token", "bootstrap_token") in observed
    assert ("click", "#bootstrap-login-button", None) in observed
    assert ("extract", "[data-bootstrap-status]", None) in observed
    assert ("extract", "#tenant-id", None) in observed
    assert ("extract", "#user-id", None) in observed
    assert ("extract", "#workspace-id", None) in observed
    assert ("extract", "#bootstrap-token", None) in observed


def test_local_cloud_poc_verification_requires_ready_browser_workspace_preflight_in_strict_mode():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_readiness_statuses=["Preflight needs config"],
        workspace_readiness_model_statuses=["Model missing: model, credential"],
        workspace_readiness_sandbox_statuses=["Sandbox PoC: local_process"],
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace readiness did not reach Preflight ready",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_accepts_ready_browser_workspace_preflight_in_strict_mode():
    client = RecordingHttpClient(model_gateway_configured=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.browser_workspace_readiness_status == "Preflight ready"
    assert result.browser_workspace_readiness_model == "Model ready"
    assert result.browser_workspace_readiness_sandbox == "Sandbox PoC: local_process"


def test_local_cloud_poc_verification_waits_for_browser_workspace_login(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(
        "taroai.deployment.local_cloud_poc_verification.time.sleep",
        sleep_calls.append,
    )
    client = RecordingHttpClient(workspace_auth_statuses=["Signing in", "Bearer"])
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_auth_poll_interval_seconds=0.25,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.browser_workspace_auth_status == "Bearer"
    assert sleep_calls == [0.25]


def test_local_cloud_poc_verification_submits_run_through_browser_workspace():
    client = RecordingHttpClient()
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="model gateway model is not configured",
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert "model gateway model is not configured" in result.browser_workspace_submit_text
    browser_actions = [
        call["payload"]
        for call in client.calls
        if call["host"] == "browser.local" and call["path"] == "/actions"
    ]
    observed = [
        (
            action["action_type"],
            action.get("selector"),
            action.get("text"),
        )
        for action in browser_actions
    ]
    assert ("type", "#composer-input", "Generate a hello report.") in observed
    assert ("click", "#send-button", None) in observed
    assert ("extract", "[data-testid='conversation-log']", None) in observed


def test_local_cloud_poc_verification_accepts_browser_workspace_status_submit_text():
    client = RecordingHttpClient(model_gateway_configured=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.local_smoke_ready is True
    assert result.strict_model_ready is True
    assert result.workspace_execution_ready is True
    assert result.skill_reuse_ready is True
    assert result.demo_ready is True
    assert result.demo_readiness_summary == "strict workspace demo ready"
    assert "succeeded" in result.browser_workspace_submit_text
    assert (
        result.browser_workspace_execution_model_route
        == "provider unknown · gpt-enterprise-planner · 165 tokens"
    )
    assert result.browser_workspace_evidence_summary == "Artifact delivery proven"
    assert result.browser_workspace_delivery_summary == "Ready to download: report.md"
    assert (
        result.browser_workspace_delivery_chain_status
        == "Delivery chain complete"
    )
    assert result.browser_workspace_delivery_chain_run_id == "run_1"
    assert (
        result.browser_workspace_delivery_chain_sandbox_session_id
        == "runtime_sandbox_1"
    )
    assert (
        result.browser_workspace_delivery_chain_artifact_storage_object_id
        == "storage_report_1"
    )
    assert result.browser_workspace_event_integrity_status == "Event stream verified"
    assert result.browser_workspace_event_integrity_count == "4 events"
    assert result.browser_workspace_event_integrity_sequence == "#1-#4 monotonic"
    assert (
        result.browser_workspace_event_integrity_closure
        == "plan -> command -> artifact -> succeeded"
    )
    assert result.browser_workspace_trace_status_text == "Loaded"
    assert result.browser_workspace_trace_span_count_text == "3"
    assert result.browser_workspace_trace_event_count_text == "3"
    assert result.browser_workspace_trace_billing_count_text == "1"
    assert result.browser_workspace_trace_audit_count_text == "1"
    assert result.browser_workspace_trace_error_text == "No error"
    assert (
        result.model_sandbox_command_output_storage_object_id
        == "storage_model_sandbox_output_1"
    )
    assert result.model_sandbox_command_output_uri == (
        "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
        "sandbox-command-outputs/model_sandbox-output.json"
    )
    assert (
        result.browser_workspace_delivery_chain_terminal_storage_object_id
        == "storage_model_sandbox_output_1"
    )
    assert (
        config.model_artifact_required_text
        in result.browser_workspace_artifact_preview_text
    )
    assert (
        result.browser_workspace_artifact_preview_storage_object_id
        == "storage_report_1"
    )
    assert (
        result.browser_workspace_artifact_download_storage_object_id
        == "storage_report_1"
    )
    assert result.browser_workspace_artifact_download_status == "Downloaded report.md"
    assert (
        result.browser_workspace_artifact_downloaded_storage_object_id
        == "storage_report_1"
    )
    assert "stdout 2 bytes" in result.browser_workspace_terminal_text
    assert (
        result.browser_workspace_terminal_output_storage_object_id
        == "storage_model_sandbox_output_1"
    )
    assert result.browser_workspace_feedback_status == "Feedback recorded"
    assert result.browser_workspace_feedback_api_seen is True
    assert result.browser_workspace_feedback_rating == -1
    assert (
        result.browser_workspace_missing_skill_feedback_status
        == "Skill request recorded"
    )
    assert result.browser_workspace_missing_skill_feedback_api_count == 3

    assert (
        result.browser_workspace_candidate_status
        == "Eval candidate accepted, case eval_case_1"
    )
    assert result.browser_workspace_eval_candidate_api_count == 1
    assert result.browser_workspace_eval_candidate_review_api_count == 1
    assert (
        result.browser_workspace_pack_candidate_status
        == "Pack candidate accepted, draft pack_draft_1"
    )
    assert result.browser_workspace_pack_candidate_api_count == 1
    assert result.browser_workspace_pack_candidate_review_api_count == 1
    assert result.browser_workspace_draft_status == "Draft applied"
    assert result.browser_workspace_draft_api_status == "applied"
    assert result.browser_workspace_draft_api_applied is True
    assert (
        result.browser_workspace_solution_pack_install_status
        == "Solution pack installed: 1 skills"
    )
    assert result.browser_workspace_solution_pack_install_api_seen is True
    assert result.browser_workspace_solution_pack_install_skill_count == 1
    assert (
        result.browser_workspace_skill_invoke_status
        == "Ready: sales.erp_invoice_matching"
    )
    assert result.browser_workspace_skill_run_status == "Run run_skill_1"
    assert (
        result.browser_workspace_skill_evidence_summary
        == "Artifact delivery proven"
    )
    assert (
        result.browser_workspace_skill_delivery_summary
        == "Ready to download: report.md"
    )
    assert (
        config.model_artifact_required_text
        in result.browser_workspace_skill_artifact_preview_text
    )
    assert result.browser_workspace_skill_run_id == "run_skill_1"
    assert result.browser_workspace_skill_run_api_status == "succeeded"
    assert result.browser_workspace_skill_run_artifact_count == 1
    assert result.browser_workspace_skill_run_artifact_download_bytes > 0
    assert result.browser_workspace_skill_run_required_text_found is True
    assert result.browser_workspace_skill_invocation_event_seen is True
    assert result.browser_workspace_skill_invocation_event_matches_skill is True
    assert result.browser_workspace_skill_run_sandbox_command_event_seen is True
    assert result.browser_workspace_skill_run_artifact_promoted_event_seen is True
    assert result.browser_workspace_skill_run_event_payload_safe is True
    assert result.browser_workspace_skill_runtime_state_status == "succeeded"
    assert (
        result.browser_workspace_skill_runtime_sandbox_session_id
        == "runtime_skill_sandbox_1"
    )
    assert result.browser_workspace_skill_runtime_required_artifact_path_found is True
    assert result.browser_workspace_skill_trace_span_count >= 3
    assert result.browser_workspace_skill_trace_event_count >= 3
    assert result.browser_workspace_skill_trace_billing_meter_count >= 1
    assert result.browser_workspace_skill_trace_audit_event_count >= 1
    assert result.browser_workspace_skill_trace_runtime_tool_call_seen is True
    assert result.browser_workspace_skill_trace_billing_tool_call_seen is True
    assert result.browser_workspace_skill_trace_audit_tool_executed_seen is True
    assert result.browser_workspace_skill_trace_payload_safe is True
    assert result.browser_workspace_skill_trace_status_text == "Loaded"
    assert result.browser_workspace_skill_trace_span_count_text == "3"
    assert result.browser_workspace_skill_trace_event_count_text == "3"
    assert result.browser_workspace_skill_trace_billing_count_text == "1"
    assert result.browser_workspace_skill_trace_audit_count_text == "1"
    assert result.browser_workspace_skill_trace_error_text == "No error"
    assert result.browser_workspace_skill_run_history_status == "2 recent runs"
    assert "run_skill_1" in result.browser_workspace_skill_run_history_text
    assert "Invoke ERP Invoice Matching." in (
        result.browser_workspace_skill_run_history_text
    )
    assert result.browser_workspace_skill_history_selection_trace_status == "Loaded"
    assert (
        result.browser_workspace_skill_history_selection_delivery_summary
        == "Ready to download: report.md"
    )
    assert (
        result.browser_workspace_skill_history_selection_delivery_chain_status
        == "Delivery chain complete"
    )
    assert (
        result.browser_workspace_skill_history_selection_delivery_chain_run_id
        == "run_skill_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_delivery_chain_sandbox_session
        == "runtime_skill_sandbox_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_delivery_chain_artifact_storage
        == "storage_skill_report_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_delivery_chain_terminal_storage
        == "storage_skill_sandbox_output_1"
    )
    assert config.model_artifact_required_text in (
        result.browser_workspace_skill_history_selection_artifact_preview_text
    )
    assert (
        result.browser_workspace_skill_history_selection_previewed_storage_object_id
        == "storage_skill_report_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_runtime_state_status
        == "succeeded"
    )
    assert (
        result.browser_workspace_skill_history_selection_runtime_sandbox_session
        == "runtime_skill_sandbox_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_runtime_artifact_count
        == "1 promoted artifact paths"
    )
    assert (
        result.browser_workspace_skill_history_selection_execution_summary
        == "Artifact ready"
    )
    assert (
        result.browser_workspace_skill_history_selection_execution_model_route
        == "provider unknown · gpt-enterprise-planner · 165 tokens"
    )
    assert (
        result.browser_workspace_skill_history_selection_execution_sandbox
        == "Promoted"
    )
    assert (
        result.browser_workspace_skill_history_selection_execution_artifact
        == "1 ready"
    )
    assert (
        result.browser_workspace_skill_history_selection_download_storage_object_id
        == "storage_skill_report_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_download_status
        == "Downloaded report.md"
    )
    assert (
        result.browser_workspace_skill_history_selection_downloaded_storage_object_id
        == "storage_skill_report_1"
    )
    assert (
        result.browser_workspace_skill_history_selection_feedback_status
        == "Feedback recorded"
    )
    assert result.browser_workspace_skill_history_selection_feedback_api_seen is True
    assert result.browser_workspace_skill_history_selection_feedback_rating == 1
    assert result.solution_pack_reuse_version == "1.0.1"
    assert result.solution_pack_reuse_skill_id == "sales.erp_invoice_matching"
    assert result.solution_pack_reuse_version_count == 2
    assert result.solution_pack_reuse_marketplace_visible is True
    assert result.solution_pack_reuse_workspace_installed is True
    assert result.solution_pack_reuse_invocation_ready is True
    assert result.solution_pack_reuse_missing_required_scopes == []
    browser_actions = [
        call
        for call in client.calls
        if call["host"] == "browser.local" and call["path"] == "/actions"
    ]
    api_calls = [
        (call["method"], call["path"])
        for call in client.calls
        if call["host"] == "api.local"
    ]
    browser_click_selectors = [
        call["payload"].get("selector")
        for call in browser_actions
        if call["payload"].get("action_type") == "click"
    ]
    assert "#install-solution-pack-button" in browser_click_selectors
    assert "[data-skills-refresh]" in browser_click_selectors
    assert "#invoke-skill-button" in browser_click_selectors
    assert "[data-run-history-refresh]" in browser_click_selectors
    assert '[data-run-history-id="run_skill_1"]' in browser_click_selectors
    assert (
        '[data-storage-object-id="storage_skill_report_1"]'
        in browser_click_selectors
    )
    assert "#run-feedback-positive" in browser_click_selectors
    assert client.workspace_selected_history_run_id == "run_skill_1"
    observed = [
        (
            action["payload"].get("action_type"),
            action["payload"].get("selector"),
            action["payload"].get("text"),
        )
        for action in browser_actions
    ]
    browser_extract_selectors = [
        call["payload"].get("selector")
        for call in browser_actions
        if call["payload"].get("action_type") == "extract"
    ]
    assert ("click", "[data-preview-storage-object-id]", None) not in observed
    assert ("click", "#run-feedback-negative", None) in observed
    assert ("click", "[data-workbench-view-toggle='admin']", None) in observed
    assert (
        "type",
        "#cs-missing-skill-name",
        "ERP invoice reconciliation",
    ) in observed
    assert (
        "type",
        "#cs-missing-skill-comment",
        "Need this repeated workflow in a reusable solution pack.",
    ) in observed
    assert ("type", "#cs-missing-skill-solution-pack", "sales.renewal_ops") in observed
    assert ("click", "#cs-submit-missing-skill", None) in observed
    assert observed.count(("click", "#cs-submit-missing-skill", None)) == 3
    assert ("click", "#cs-create-eval-candidates", None) in observed
    assert ("click", "#cs-accept-eval-candidate", None) in observed
    assert ("click", "#cs-create-pack-candidates", None) in observed
    assert ("click", "#cs-accept-pack-candidate", None) in observed
    assert ("type", "#cs-draft-skill", "ERP Invoice Matching") in observed
    assert (
        "type",
        "#cs-draft-summary",
        "Add governed invoice matching skill draft.",
    ) in observed
    assert ("type", "#cs-draft-pack-version", "1.0.1") in observed
    assert any(
        action == "type"
        and selector == "#cs-draft-skill-manifest"
        and text is not None
        and "sales.erp_invoice_matching" in text
        for action, selector, text in observed
    )
    assert ("click", "#cs-draft-save", None) in observed
    assert ("click", "#cs-draft-submit", None) in observed
    assert ("click", "#cs-draft-approve", None) in observed
    assert ("click", "#cs-draft-apply", None) in observed
    assert ("GET", "/api/solution-packs") in api_calls
    assert ("POST", "/api/solution-packs") in api_calls
    assert ("GET", "/api/solution-packs/sales.renewal_ops/versions") in api_calls
    assert ("POST", "/api/solution-packs/sales.renewal_ops/install") in api_calls
    assert ("GET", "/api/solution-pack-installations") in api_calls
    assert ("GET", "/api/workspaces/workspace_acme/skills") in api_calls
    assert ("GET", "/api/skills") in api_calls
    assert ("GET", "/api/runs/run_skill_1") in api_calls
    assert ("GET", "/api/customer-success/feedback") in api_calls
    assert ("GET", "/api/runs/run_skill_1/events") in api_calls
    assert ("GET", "/api/runs/run_skill_1/state") in api_calls
    assert ("GET", "/api/runs/run_skill_1/trace") in api_calls
    assert ("GET", "/api/runs/run_skill_1/artifacts") in api_calls
    assert ("GET", "/api/runs/run_skill_1/storage-objects") in api_calls
    assert "[data-status-pill]" in browser_extract_selectors
    assert "[data-evidence-summary]" in browser_extract_selectors
    assert "[data-delivery-summary]" in browser_extract_selectors
    assert "[data-delivery-chain-status]" in browser_extract_selectors
    assert "[data-delivery-chain-run]" in browser_extract_selectors
    assert "[data-delivery-chain-sandbox]" in browser_extract_selectors
    assert "[data-delivery-chain-artifact-storage]" in browser_extract_selectors
    assert "[data-delivery-chain-terminal-storage]" in browser_extract_selectors
    assert "[data-event-integrity-status]" in browser_extract_selectors
    assert "[data-event-integrity-sequence]" in browser_extract_selectors
    assert "[data-event-integrity-closure]" in browser_extract_selectors
    assert browser_extract_selectors.count("[data-evidence-summary]") >= 2
    assert browser_extract_selectors.count("[data-delivery-summary]") >= 2
    assert "[data-artifact-preview-content]" in browser_extract_selectors
    assert browser_extract_selectors.count("[data-artifact-preview-content]") >= 2
    assert "[data-trace-status]" in browser_extract_selectors
    assert "[data-trace-span-count]" in browser_extract_selectors
    assert "[data-trace-event-count]" in browser_extract_selectors
    assert "[data-trace-billing-count]" in browser_extract_selectors
    assert "[data-trace-audit-count]" in browser_extract_selectors
    assert "[data-trace-error-classification]" in browser_extract_selectors
    assert "[data-runtime-state-status]" in browser_extract_selectors
    assert "[data-runtime-sandbox-session]" in browser_extract_selectors
    assert "[data-runtime-artifact-count]" in browser_extract_selectors
    assert "[data-execution-summary]" in browser_extract_selectors
    assert "[data-execution-model-route]" in browser_extract_selectors
    assert "[data-execution-sandbox]" in browser_extract_selectors
    assert "[data-execution-artifact]" in browser_extract_selectors
    assert "[data-artifact-download-status]" in browser_extract_selectors
    assert "[data-run-history-status]" in browser_extract_selectors
    assert "[data-run-history-list]" in browser_extract_selectors
    assert "[data-terminal-output]" in browser_extract_selectors
    assert "[data-run-feedback-status]" in browser_extract_selectors
    assert "[data-cs-candidate-action-status]" in browser_extract_selectors
    assert "[data-cs-missing-skill-status]" in browser_extract_selectors
    assert "[data-cs-draft-status]" in browser_extract_selectors


def test_local_cloud_poc_verification_rejects_browser_model_route_mismatch():
    run_events_body = (
        'id: 1\n'
        'event: plan.created\n'
        'data: {"id":"event_1","sequence":1,"type":"plan.created","payload":{"provider":"openai-primary","model":"gpt-enterprise-planner","usage":{"input_tokens":120,"output_tokens":45,"total_tokens":165,"cached_input_tokens":48},"steps":[]}}\n\n'
        'id: 2\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_2","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_3","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_4","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace execution model route did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_matches_model_plan_created_route():
    run_events_body = (
        'id: 1\n'
        'event: model.plan.created\n'
        'data: {"id":"event_1","sequence":1,"type":"model.plan.created","payload":{"provider":"openai-primary","model":"gpt-enterprise-planner","usage":{"input_tokens":120,"output_tokens":45,"total_tokens":165,"cached_input_tokens":48},"steps":[]}}\n\n'
        'id: 2\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_2","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_3","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_4","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace execution model route did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_checks_selected_skill_run_event_integrity():
    skill_run_events_body = (
        'id: 10\n'
        'event: skill.workflow_invoked\n'
        'data: {"id":"event_skill_0","sequence":10,"type":"skill.workflow_invoked","payload":{"skill_id":"sales.erp_invoice_matching","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
        'id: 11\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_1","sequence":11,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 12\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_2","sequence":12,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
        'id: 13\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_3","sequence":13,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert (
        result.browser_workspace_skill_history_selection_event_integrity_status
        == "Event stream verified"
    )
    assert result.browser_workspace_skill_history_selection_event_integrity_count == (
        "4 events"
    )
    assert result.browser_workspace_skill_history_selection_event_integrity_sequence == (
        "#10-#13 monotonic"
    )
    assert result.browser_workspace_skill_history_selection_event_integrity_closure == (
        "skill -> command -> artifact -> succeeded"
    )


def test_local_cloud_poc_verification_checks_selected_skill_run_terminal_provenance():
    client = RecordingHttpClient(model_gateway_configured=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert (
        "runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"
        in result.browser_workspace_skill_history_selection_terminal_text
    )
    assert (
        result.browser_workspace_skill_history_selection_terminal_output_storage_object_id
        == "storage_skill_sandbox_output_1"
    )


def test_local_cloud_poc_verification_rejects_selected_history_model_route_mismatch():
    skill_run_events_body = (
        'id: 1\n'
        'event: plan.created\n'
        'data: {"id":"event_skill_1","sequence":1,"type":"plan.created","payload":{"provider":"openai-primary","model":"gpt-enterprise-planner","usage":{"input_tokens":120,"output_tokens":45,"total_tokens":165},"steps":[]}}\n\n'
        'id: 2\n'
        'event: skill.workflow_invoked\n'
        'data: {"id":"event_skill_0","sequence":2,"type":"skill.workflow_invoked","payload":{"skill_id":"sales.erp_invoice_matching","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
        'id: 3\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_2","sequence":3,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 4\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_3","sequence":4,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
        'id: 5\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_4","sequence":5,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace selected history execution model route did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_current_run_feedback_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_run_feedback_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace run feedback API did not include current run feedback",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_missing_skill_feedback_without_api_records():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_missing_skill_feedback_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace missing skill feedback API did not include enough records",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_eval_candidate_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_eval_candidate_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace evaluation candidate API did not include generated candidate",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_eval_candidate_review_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_eval_candidate_review_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace evaluation candidate API did not include accepted candidate",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_pack_candidate_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_pack_candidate_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace solution pack candidate API did not include generated candidate",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_pack_candidate_review_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_pack_candidate_review_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace solution pack candidate API did not include accepted candidate",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_draft_apply_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_draft_apply_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace solution pack draft API did not include applied draft",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_solution_pack_install_without_api_record():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_solution_pack_install_persists=False,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace solution pack installation API did not include installed skill",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_skill_run_workspace_mismatch():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_workspace_id="workspace_other",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill run did not belong to workspace",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_skill_run_agent_mismatch():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_agent_id="starter.artifact_writer",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill run did not record invoked skill",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_skill_run_without_invocation_event():
    skill_run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 2\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
        'id: 3\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill run did not emit skill.workflow_invoked",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_skill_run_invocation_event_mismatch():
    skill_run_events_body = (
        'id: 1\n'
        'event: skill.workflow_invoked\n'
        'data: {"id":"event_skill_0","sequence":1,"type":"skill.workflow_invoked","payload":{"skill_id":"starter.artifact_writer","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
        'id: 2\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_1","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill invocation event did not match invoked skill",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_skill_invocation_before_command():
    skill_run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 2\n'
        'event: skill.workflow_invoked\n'
        'data: {"id":"event_skill_0","sequence":2,"type":"skill.workflow_invoked","payload":{"skill_id":"sales.erp_invoice_matching","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill run event stream order was not closed",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_selected_skill_run_runtime_sandbox_mismatch():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_selected_history_sandbox_session_id="stale_skill_sandbox",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace selected history runtime sandbox did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_ordered_skill_run_events():
    skill_run_events_body = (
        'id: 1\n'
        'event: skill.workflow_invoked\n'
        'data: {"id":"event_skill_0","sequence":1,"type":"skill.workflow_invoked","payload":{"skill_id":"sales.erp_invoice_matching","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
        'id: 2\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_1","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 3\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        'id: 4\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_2","sequence":4,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill run event stream order was not closed",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_monotonic_skill_run_event_sequence():
    skill_run_events_body = (
        'id: 1\n'
        'event: skill.workflow_invoked\n'
        'data: {"id":"event_skill_0","sequence":1,"type":"skill.workflow_invoked","payload":{"skill_id":"sales.erp_invoice_matching","skill_version":"1.0.1","input_keys":["invoice_id"]}}\n\n'
        'id: 2\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_skill_1","sequence":3,"type":"sandbox.command.executed","payload":{"session_id":"runtime_skill_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_skill_1/sandbox-command-outputs/sandbox_skill_1-output.json"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_skill_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_skill_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_skill_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        skill_run_events_body=skill_run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace skill run event stream sequence was not monotonic",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_delivery_chain_in_strict_mode():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_delivery_chain_status="Collecting delivery evidence",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace delivery chain did not complete",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_event_count_match():
    class EventCountMismatchClient(RecordingHttpClient):
        def request(
            self,
            method: str,
            url: str,
            payload: dict | None = None,
            headers: dict | None = None,
        ) -> LocalCloudPocHttpResponse:
            parsed = urlparse(url)
            if (
                parsed.netloc == "browser.local"
                and method == "POST"
                and parsed.path == "/actions"
                and payload
                and payload.get("selector") == "[data-event-integrity-count]"
            ):
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"2 events",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            return super().request(method, url, payload=payload, headers=headers)

    client = EventCountMismatchClient(model_gateway_configured=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace event integrity count did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_event_sequence_match():
    class EventSequenceMismatchClient(RecordingHttpClient):
        def request(
            self,
            method: str,
            url: str,
            payload: dict | None = None,
            headers: dict | None = None,
        ) -> LocalCloudPocHttpResponse:
            parsed = urlparse(url)
            if (
                parsed.netloc == "browser.local"
                and method == "POST"
                and parsed.path == "/actions"
                and payload
                and payload.get("selector") == "[data-event-integrity-sequence]"
            ):
                return LocalCloudPocHttpResponse(
                    status_code=201,
                    body=(
                        '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
                        '"run_id":"run_1","session_id":"browser_verify_1",'
                        '"action_type":"extract","current_url":"http://web.internal",'
                        '"text":"#1-#2 monotonic",'
                        '"screenshot_uri":null,"metadata":{},'
                        '"created_at":"2026-07-03T14:00:06Z"}'
                    ),
                )
            return super().request(method, url, payload=payload, headers=headers)

    client = EventSequenceMismatchClient(model_gateway_configured=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace event integrity sequence did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_delivery_chain_artifact_id_match():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_delivery_chain_artifact_storage_id="storage_other_report",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace delivery chain artifact storage did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_delivery_chain_terminal_id_match():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_delivery_chain_terminal_storage_id="storage_other_terminal",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace delivery chain terminal storage did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_matches_browser_terminal_to_model_output():
    storage_objects_body = (
        '[{"id":"storage_report_1","tenant_id":"tenant_acme",'
        '"workspace_id":"workspace_acme","run_id":"run_1",'
        '"purpose":"artifacts","filename":"report.md",'
        '"content_type":"text/markdown","size_bytes":72,'
        '"acl_subjects":[],"sensitivity_level":0,'
        '"bucket":"taroai-artifacts",'
        '"key":"tenant_acme/workspace_acme/runs/run_1/artifacts/report.md",'
        '"retention_expires_at":null,"deleted_at":null,'
        '"created_at":"2026-07-03T14:00:01Z"},'
        '{"id":"storage_sandbox_output_1","tenant_id":"tenant_acme",'
        '"workspace_id":"workspace_acme","run_id":"run_1",'
        '"purpose":"sandbox-command-outputs","filename":"sandbox_1-output.json",'
        '"content_type":"application/json","size_bytes":142,'
        '"acl_subjects":[],"sensitivity_level":0,'
        '"bucket":"taroai-artifacts",'
        '"key":"tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/sandbox_1-output.json",'
        '"retention_expires_at":null,"deleted_at":null,'
        '"created_at":"2026-07-03T14:00:01Z"},'
        '{"id":"storage_model_sandbox_output_1","tenant_id":"tenant_acme",'
        '"workspace_id":"workspace_acme","run_id":"run_1",'
        '"purpose":"sandbox-command-outputs","filename":"model_sandbox-output.json",'
        '"content_type":"application/json","size_bytes":142,'
        '"acl_subjects":[],"sensitivity_level":0,'
        '"bucket":"taroai-artifacts",'
        '"key":"tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json",'
        '"retention_expires_at":null,"deleted_at":null,'
        '"created_at":"2026-07-03T14:00:01Z"},'
        '{"id":"storage_browser_1","tenant_id":"tenant_acme",'
        '"workspace_id":"workspace_acme","run_id":"run_1",'
        '"purpose":"browser","filename":"sandbox_1.png",'
        '"content_type":"image/png","size_bytes":67,'
        '"acl_subjects":[],"sensitivity_level":0,'
        '"bucket":"taroai-artifacts",'
        '"key":"tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png",'
        '"retention_expires_at":null,"deleted_at":null,'
        '"created_at":"2026-07-03T14:00:02Z"}]'
    )
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 3\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        storage_objects_body=storage_objects_body,
        run_events_body=run_events_body,
        workspace_delivery_chain_terminal_storage_id="storage_sandbox_output_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace delivery chain terminal storage did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_terminal_output_object_match():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_terminal_output_storage_id="storage_sandbox_output_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace terminal output storage object did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_terminal_output_uri_match():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_terminal_output_uri=(
            "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
            "sandbox-command-outputs/sandbox_1-output.json"
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace terminal output URI did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_artifact_preview_object_match():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_artifact_preview_storage_id="storage_notes_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace artifact preview storage object did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_artifact_download_object_match():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        workspace_artifact_downloaded_storage_id="storage_notes_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace artifact download did not expose the downloaded storage object id",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_storage_chain_match():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: browser.action.performed\n'
        'data: {"id":"event_browser_1","sequence":2,"type":"browser.action.performed","payload":{"session_id":"runtime_browser_1","action_type":"screenshot","current_url":"https://example.com","screenshot_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png","storage_object_id":"storage_browser_1"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
        runtime_state_body=(
            '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
            '"user_id":"user_owner","run_id":"run_1","goal":"Create a hello report.",'
            '"status":"succeeded","plan":[],"current_step_id":"step_report",'
            '"completed_step_ids":["step_report"],"approved_step_ids":[],'
            '"approved_guardrail_keys":[],"pending_guardrail_approval_key":null,'
            '"pending_guardrail_approval_stage":null,"tool_results":[],'
            '"retrieved_context":{"knowledge_results":[],"memory_records":[]},'
            '"sandbox_session_id":"runtime_sandbox_1",'
            '"browser_session_id":"runtime_browser_1",'
            '"promoted_sandbox_artifact_paths":["/workspace/artifacts/report.md"],'
            '"approval_id":null,"failure_reason":null}'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="browser workspace delivery chain browser storage did not match API evidence",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_event_session_match():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: browser.action.performed\n'
        'data: {"id":"event_browser_1","sequence":2,"type":"browser.action.performed","payload":{"session_id":"runtime_browser_1","action_type":"screenshot","current_url":"https://example.com","screenshot_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png","storage_object_id":"storage_browser_1"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
        workspace_delivery_chain_browser_storage_id="storage_browser_1",
        workspace_browser_storage_id="storage_browser_1",
        workspace_browser_preview_storage_id="storage_browser_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run browser action session did not match runtime state",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_browser_screenshot_uri_match():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: browser.action.performed\n'
        'data: {"id":"event_browser_1","sequence":2,"type":"browser.action.performed","payload":{"session_id":"runtime_browser_1","action_type":"screenshot","current_url":"https://example.com","screenshot_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/browser/other.png","storage_object_id":"storage_browser_1"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
        runtime_state_body=(
            '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
            '"user_id":"user_owner","run_id":"run_1","goal":"Create a hello report.",'
            '"status":"succeeded","plan":[],"current_step_id":"step_report",'
            '"completed_step_ids":["step_report"],"approved_step_ids":[],'
            '"approved_guardrail_keys":[],"pending_guardrail_approval_key":null,'
            '"pending_guardrail_approval_stage":null,"tool_results":[],'
            '"retrieved_context":{"knowledge_results":[],"memory_records":[]},'
            '"sandbox_session_id":"runtime_sandbox_1",'
            '"browser_session_id":"runtime_browser_1",'
            '"promoted_sandbox_artifact_paths":["/workspace/artifacts/report.md"],'
            '"approval_id":null,"failure_reason":null}'
        ),
        workspace_delivery_chain_browser_storage_id="storage_browser_1",
        workspace_browser_storage_id="storage_browser_1",
        workspace_browser_preview_storage_id="storage_browser_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run browser action screenshot URI did not match storage object",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_ordered_model_events():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":2,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run event stream order was not closed",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_cleanup_failure_events():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 3\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        'id: 4\n'
        'event: browser.session.destroy_failed\n'
        'data: {"id":"event_4","sequence":4,"type":"browser.session.destroy_failed","payload":{"session_id":"runtime_browser_1","reason":"success","error_type":"BrowserProviderUnavailableError"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run cleanup failed: browser.session.destroy_failed",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_monotonic_model_event_sequence():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":1,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 3\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run event stream sequence was not monotonic",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_sequence_for_every_model_event():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 3\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run event stream sequence was missing",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_accepts_browser_capture_storage_chain():
    run_events_body = (
        'id: 1\n'
        'event: sandbox.command.executed\n'
        'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0,"output_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/sandbox-command-outputs/model_sandbox-output.json"}}\n\n'
        'id: 2\n'
        'event: browser.action.performed\n'
        'data: {"id":"event_browser_1","sequence":2,"type":"browser.action.performed","payload":{"session_id":"runtime_browser_1","action_type":"screenshot","current_url":"https://example.com","screenshot_uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png","storage_object_id":"storage_browser_1"}}\n\n'
        'id: 3\n'
        'event: sandbox.artifact.promoted\n'
        'data: {"id":"event_2","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
        'id: 4\n'
        'event: run.succeeded\n'
        'data: {"id":"event_3","sequence":4,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=run_events_body,
        runtime_state_body=(
            '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
            '"user_id":"user_owner","run_id":"run_1","goal":"Create a hello report.",'
            '"status":"succeeded","plan":[],"current_step_id":"step_report",'
            '"completed_step_ids":["step_report"],"approved_step_ids":[],'
            '"approved_guardrail_keys":[],"pending_guardrail_approval_key":null,'
            '"pending_guardrail_approval_stage":null,"tool_results":[],'
            '"retrieved_context":{"knowledge_results":[],"memory_records":[]},'
            '"sandbox_session_id":"runtime_sandbox_1",'
            '"browser_session_id":"runtime_browser_1",'
            '"promoted_sandbox_artifact_paths":["/workspace/artifacts/report.md"],'
            '"approval_id":null,"failure_reason":null}'
        ),
        workspace_delivery_chain_browser_storage_id="storage_browser_1",
        workspace_browser_storage_id="storage_browser_1",
        workspace_browser_preview_storage_id="storage_browser_1",
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        browser_workspace_url="http://web.internal",
        browser_workspace_api_base_url="http://api.internal",
        browser_workspace_submit_message="Generate a hello report.",
        browser_workspace_submit_expected_text="succeeded",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.model_browser_action_storage_object_id == "storage_browser_1"
    assert result.model_runtime_browser_session_id == "runtime_browser_1"
    assert (
        result.browser_workspace_delivery_chain_browser_storage_object_id
        == "storage_browser_1"
    )
    assert result.browser_workspace_browser_storage_object_id == "storage_browser_1"
    assert (
        result.browser_workspace_browser_preview_storage_object_id
        == "storage_browser_1"
    )
    assert (
        result.browser_workspace_event_integrity_closure
        == "command -> browser -> artifact -> succeeded"
    )


def test_local_cloud_poc_verification_requires_model_artifact_when_configured():
    client = RecordingHttpClient(model_gateway_configured=True)
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.local_smoke_ready is True
    assert result.strict_model_ready is True
    assert result.workspace_execution_ready is False
    assert result.skill_reuse_ready is False
    assert result.demo_ready is True
    assert result.demo_readiness_summary == "strict API demo ready"
    assert result.model_gateway_configured is True
    assert result.execute_status_code == 200
    assert result.execute_code is None
    assert result.run_status == "succeeded"
    assert result.artifact_count == 1
    assert result.artifact_names == ["report.md"]
    assert result.model_artifact_required_name_found is True
    assert result.model_artifact_storage_object_count == 1
    assert result.model_artifact_total_download_bytes == 72
    assert result.model_artifact_storage_object_id == "storage_report_1"
    assert result.model_artifact_download_bytes == 72
    assert result.model_artifact_required_text_found is True
    assert result.model_sandbox_command_event_seen is True
    assert result.model_artifact_promoted_event_seen is True
    assert result.model_run_event_payload_safe is True
    assert result.model_sandbox_command_exit_code == 0
    assert result.model_sandbox_command_output_uri == (
        "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
        "sandbox-command-outputs/model_sandbox-output.json"
    )
    assert (
        result.model_sandbox_command_output_storage_object_id
        == "storage_model_sandbox_output_1"
    )
    assert result.model_artifact_promoted_storage_object_id == "storage_report_1"
    assert result.model_artifact_event_matches_storage_object is True
    assert result.model_runtime_state_status == "succeeded"
    assert result.model_runtime_sandbox_session_id == "runtime_sandbox_1"
    assert result.model_runtime_completed_step_count == 1
    assert result.model_runtime_promoted_artifact_path_count == 1
    assert result.model_runtime_required_artifact_path_found is True
    assert result.model_trace_span_count == 3
    assert result.model_trace_event_count == 3
    assert result.model_trace_billing_meter_count == 1
    assert result.model_trace_audit_event_count == 1
    assert result.model_trace_runtime_tool_call_seen is True
    assert result.model_trace_billing_tool_call_seen is True
    assert result.model_trace_audit_tool_executed_seen is True
    assert result.model_trace_payload_safe is True
    assert result.model_run_event_types == [
        "plan.created",
        "sandbox.command.executed",
        "sandbox.artifact.promoted",
        "run.succeeded",
    ]
    assert "/api/runs/run_1/state" in [call["path"] for call in client.calls]
    assert "/api/runs/run_1/trace" in [call["path"] for call in client.calls]


def test_local_cloud_poc_verification_requires_trace_evidence_for_strict_run():
    trace_body = json.dumps(
        {
            "spans": [
                {
                    "trace_id": "run_1",
                    "span_id": "runtime:tool_call:step_report",
                    "name": "runtime.tool_call",
                }
            ],
            "trace_events": [],
            "billing_meters": [],
            "audit_events": [
                {
                    "id": "audit_1",
                    "event_type": "tool.executed",
                }
            ],
        },
        separators=(",", ":"),
    )
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_trace_body=trace_body,
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
        run_status_poll_interval_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="run trace did not include tool_call_count billing meter",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_requires_runtime_state_for_strict_run():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        runtime_state_body=(
            '{"tenant_id":"tenant_acme","workspace_id":"workspace_acme",'
            '"user_id":"user_owner","run_id":"run_1","goal":"Create a hello report.",'
            '"status":"succeeded","plan":[],"current_step_id":"step_report",'
            '"completed_step_ids":["step_report"],"approved_step_ids":[],'
            '"approved_guardrail_keys":[],"pending_guardrail_approval_key":null,'
            '"pending_guardrail_approval_stage":null,"tool_results":[],'
            '"retrieved_context":{"knowledge_results":[],"memory_records":[]},'
            '"sandbox_session_id":null,"browser_session_id":null,'
            '"promoted_sandbox_artifact_paths":[],"approval_id":null,'
            '"failure_reason":null}'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run runtime state did not record sandbox session",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_event_runtime_sandbox_session_mismatch():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=(
            'id: 1\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"other_sandbox","exit_code":0,"stdout_length":2,"stderr_length":0}}\n\n'
            'id: 2\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
            'id: 3\n'
            'event: run.succeeded\n'
            'data: {"id":"event_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run sandbox command session did not match runtime state",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_unexpected_model_artifact_name():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        artifacts_body=(
            '[{"id":"artifact_1","tenant_id":"tenant_acme",'
            '"run_id":"run_1","name":"notes.txt",'
            '"artifact_type":"document",'
            '"uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/artifacts/notes.txt",'
            '"created_at":"2026-07-03T14:00:01Z"}]'
        ),
        storage_objects_body=(
            '[{"id":"storage_notes_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"artifacts","filename":"notes.txt",'
            '"content_type":"text/plain","size_bytes":72,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/artifacts/notes.txt",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:01Z"}]'
        ),
        run_events_body=(
            'id: 1\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0}}\n\n'
            'id: 2\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"notes.txt","storage_object_id":"storage_notes_1"}}\n\n'
            'id: 3\n'
            'event: run.succeeded\n'
            'data: {"id":"event_3","sequence":3,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run did not publish required artifact: report.md",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_rejects_raw_sandbox_output_in_run_events():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=(
            'id: 1\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout":"ok"}}\n\n'
            'id: 2\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md"}}\n\n'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(RuntimeError, match="run event stream leaked raw sandbox output"):
        verify_local_cloud_poc(config, client=client)
    assert [call["path"] for call in client.calls] == [
            "/healthz",
            "/readyz",
            "/healthz",
            "/capabilities",
            "/",
        "/assets/main.js",
        "/api/tenants/bootstrap",
        "/api/auth/login",
        "/api/tenants/current/readiness",
        "/api/runs",
        "/api/runs/run_1/execute",
        "/api/runs/run_1",
        "/api/runs/run_1/artifacts",
        "/api/runs/run_1/storage-objects",
        "/api/storage/objects/storage_report_1/content",
        "/api/runs/run_1/events",
    ]


def test_local_cloud_poc_verification_requires_every_model_artifact_storage_object_downloadable():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        artifacts_body=(
            '[{"id":"artifact_1","tenant_id":"tenant_acme",'
            '"run_id":"run_1","name":"report.md",'
            '"artifact_type":"document",'
            '"uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/artifacts/report.md",'
            '"created_at":"2026-07-03T14:00:01Z"},'
            '{"id":"artifact_2","tenant_id":"tenant_acme",'
            '"run_id":"run_1","name":"extra.txt",'
            '"artifact_type":"document",'
            '"uri":"s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/artifacts/extra.txt",'
            '"created_at":"2026-07-03T14:00:02Z"}]'
        ),
        storage_objects_body=(
            '[{"id":"storage_report_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"artifacts","filename":"report.md",'
            '"content_type":"text/markdown","size_bytes":72,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/artifacts/report.md",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:01Z"},'
            '{"id":"storage_extra_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"artifacts","filename":"extra.txt",'
            '"content_type":"text/plain","size_bytes":0,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/artifacts/extra.txt",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:02Z"},'
            '{"id":"storage_browser_1","tenant_id":"tenant_acme",'
            '"workspace_id":"workspace_acme","run_id":"run_1",'
            '"purpose":"browser","filename":"sandbox_1.png",'
            '"content_type":"image/png","size_bytes":67,'
            '"acl_subjects":[],"sensitivity_level":0,'
            '"bucket":"taroai-artifacts",'
            '"key":"tenant_acme/workspace_acme/runs/run_1/browser/sandbox_1.png",'
            '"retention_expires_at":null,"deleted_at":null,'
            '"created_at":"2026-07-03T14:00:03Z"}]'
        ),
        storage_object_contents={"storage_extra_1": ""},
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run artifact storage object content was empty",
    ):
        verify_local_cloud_poc(config, client=client)


def test_local_cloud_poc_verification_accepts_extra_artifact_promoted_after_required_one():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        artifacts_body=json.dumps(
            [
                {
                    "id": "artifact_1",
                    "tenant_id": "tenant_acme",
                    "run_id": "run_1",
                    "name": "report.md",
                    "artifact_type": "document",
                    "uri": (
                        "s3://taroai-artifacts/tenant_acme/workspace_acme/"
                        "runs/run_1/artifacts/report.md"
                    ),
                    "created_at": "2026-07-03T14:00:01Z",
                },
                {
                    "id": "artifact_2",
                    "tenant_id": "tenant_acme",
                    "run_id": "run_1",
                    "name": "extra.txt",
                    "artifact_type": "document",
                    "uri": (
                        "s3://taroai-artifacts/tenant_acme/workspace_acme/"
                        "runs/run_1/artifacts/extra.txt"
                    ),
                    "created_at": "2026-07-03T14:00:02Z",
                },
            ],
            separators=(",", ":"),
        ),
        storage_objects_body=json.dumps(
            [
                {
                    "id": "storage_report_1",
                    "tenant_id": "tenant_acme",
                    "workspace_id": "workspace_acme",
                    "run_id": "run_1",
                    "purpose": "artifacts",
                    "filename": "report.md",
                    "content_type": "text/markdown",
                    "size_bytes": 72,
                    "acl_subjects": [],
                    "sensitivity_level": 0,
                    "bucket": "taroai-artifacts",
                    "key": (
                        "tenant_acme/workspace_acme/runs/run_1/"
                        "artifacts/report.md"
                    ),
                    "retention_expires_at": None,
                    "deleted_at": None,
                    "created_at": "2026-07-03T14:00:01Z",
                },
                {
                    "id": "storage_extra_1",
                    "tenant_id": "tenant_acme",
                    "workspace_id": "workspace_acme",
                    "run_id": "run_1",
                    "purpose": "artifacts",
                    "filename": "extra.txt",
                    "content_type": "text/plain",
                    "size_bytes": 17,
                    "acl_subjects": [],
                    "sensitivity_level": 0,
                    "bucket": "taroai-artifacts",
                    "key": (
                        "tenant_acme/workspace_acme/runs/run_1/"
                        "artifacts/extra.txt"
                    ),
                    "retention_expires_at": None,
                    "deleted_at": None,
                    "created_at": "2026-07-03T14:00:02Z",
                },
                {
                    "id": "storage_sandbox_output_1",
                    "tenant_id": "tenant_acme",
                    "workspace_id": "workspace_acme",
                    "run_id": "run_1",
                    "purpose": "sandbox-command-outputs",
                    "filename": "sandbox_1-output.json",
                    "content_type": "application/json",
                    "size_bytes": 142,
                    "acl_subjects": [],
                    "sensitivity_level": 0,
                    "bucket": "taroai-artifacts",
                    "key": (
                        "tenant_acme/workspace_acme/runs/run_1/"
                        "sandbox-command-outputs/sandbox_1-output.json"
                    ),
                    "retention_expires_at": None,
                    "deleted_at": None,
                    "created_at": "2026-07-03T14:00:03Z",
                },
                {
                    "id": "storage_browser_1",
                    "tenant_id": "tenant_acme",
                    "workspace_id": "workspace_acme",
                    "run_id": "run_1",
                    "purpose": "browser",
                    "filename": "sandbox_1.png",
                    "content_type": "image/png",
                    "size_bytes": 67,
                    "acl_subjects": [],
                    "sensitivity_level": 0,
                    "bucket": "taroai-artifacts",
                    "key": (
                        "tenant_acme/workspace_acme/runs/run_1/"
                        "browser/sandbox_1.png"
                    ),
                    "retention_expires_at": None,
                    "deleted_at": None,
                    "created_at": "2026-07-03T14:00:04Z",
                },
            ],
            separators=(",", ":"),
        ),
        storage_object_contents={"storage_extra_1": "Extra artifact OK"},
        run_events_body=(
            'id: 1\n'
            'event: plan.created\n'
            'data: {"id":"event_1","sequence":1,"type":"plan.created","payload":{"provider":null,"model":"gpt-enterprise-planner","steps":[{"id":"step_report","title":"Generate report","tool_name":"sandbox.command"}]}}\n\n'
            'id: 2\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_2","sequence":2,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0}}\n\n'
            'id: 3\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_3","sequence":3,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_report_1"}}\n\n'
            'id: 4\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_4","sequence":4,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"extra.txt","storage_object_id":"storage_extra_1"}}\n\n'
            'id: 5\n'
            'event: run.succeeded\n'
            'data: {"id":"event_5","sequence":5,"type":"run.succeeded","payload":{"status":"succeeded"}}\n\n'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    result = verify_local_cloud_poc(config, client=client)

    assert result.artifact_count == 2
    assert result.model_artifact_storage_object_count == 2
    assert result.model_artifact_promoted_storage_object_id == "storage_report_1"
    assert result.model_artifact_event_matches_storage_object is True


def test_local_cloud_poc_verification_rejects_artifact_event_storage_mismatch():
    client = RecordingHttpClient(
        model_gateway_configured=True,
        run_events_body=(
            'id: 1\n'
            'event: sandbox.command.executed\n'
            'data: {"id":"event_1","sequence":1,"type":"sandbox.command.executed","payload":{"session_id":"runtime_sandbox_1","exit_code":0,"stdout_length":2,"stderr_length":0}}\n\n'
            'id: 2\n'
            'event: sandbox.artifact.promoted\n'
            'data: {"id":"event_2","sequence":2,"type":"sandbox.artifact.promoted","payload":{"artifact_name":"report.md","storage_object_id":"storage_other"}}\n\n'
        ),
    )
    config = LocalCloudPocVerificationConfig(
        api_base_url="http://api.local",
        browser_base_url="http://browser.local",
        web_base_url="http://web.local",
        bootstrap_token="bootstrap_token",
        tenant_slug="acme",
        owner_email="owner@example.com",
        owner_password="correct horse battery staple",
        browser_session_id="browser_verify_1",
        require_model_execution=True,
    )

    with pytest.raises(
        RuntimeError,
        match="configured model run artifact event did not match downloaded storage object",
    ):
        verify_local_cloud_poc(config, client=client)

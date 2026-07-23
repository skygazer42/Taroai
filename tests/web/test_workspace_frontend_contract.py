from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "apps" / "web"


class Element:
    def __init__(self, tag: str, attrs: dict[str, str], parent: "Element | None"):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Element] = []
        self.text_parts: list[str] = []

    @property
    def text(self) -> str:
        own_text = "".join(self.text_parts)
        child_text = "".join(child.text for child in self.children)
        return own_text + child_text

    def find_by_attr(self, attr: str, value: str) -> "Element | None":
        if self.attrs.get(attr) == value:
            return self
        for child in self.children:
            match = child.find_by_attr(attr, value)
            if match is not None:
                return match
        return None

    def direct_children(self, tag: str) -> list["Element"]:
        return [child for child in self.children if child.tag == tag]


class TreeParser(HTMLParser):
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__()
        self.root = Element("document", {}, None)
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Element(
            tag,
            {key: value or "" for key, value in attrs},
            self.current,
        )
        self.current.children.append(node)
        if tag not in self.VOID_TAGS:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data: str) -> None:
        self.current.text_parts.append(data)


def parse_index() -> Element:
    index_path = WEB_ROOT / "index.html"
    assert index_path.exists(), "apps/web/index.html must exist for the workspace UI"
    parser = TreeParser()
    parser.feed(index_path.read_text())
    return parser.root


def test_chat_column_preserves_creao_selector_contract():
    root = parse_index()
    chat_column = root.find_by_attr("data-testid", "chat-column")
    assert chat_column is not None
    assert "How can I help" in chat_column.text

    composer_shell = chat_column.find_by_attr("data-testid", "chat-composer")
    assert composer_shell is not None
    assert "Press Enter to send, Shift+Enter for a new line." in composer_shell.text
    network_status = composer_shell.find_by_attr("data-chat-network-state", "")
    assert network_status is not None and network_status.attrs.get("role") == "status"


def test_workspace_surfaces_cover_execution_mvp():
    root = parse_index()
    for test_id in [
        "run-timeline",
        "run-controls",
        "sandbox-terminal",
        "browser-panel",
        "artifact-list",
        "delivery-chain",
        "approval-panel",
        "customer-success-panel",
    ]:
        assert root.find_by_attr("data-testid", test_id) is not None


def test_workspace_groups_workbench_into_operational_views():
    root = parse_index()
    workbench = root.find_by_attr("data-testid", "workspace-workbench")
    assert workbench is not None
    switcher = root.find_by_attr("data-testid", "workspace-view-switcher")
    assert switcher is not None
    for view in ["run", "inspect", "admin"]:
        assert switcher.find_by_attr("data-workbench-view-toggle", view) is not None
        assert workbench.find_by_attr("data-workbench-view", view) is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "activeWorkbenchView",
        "switchWorkbenchView(",
        "data-workbench-view-toggle",
        "data-workbench-view",
        "elements.workbenchViews",
        "elements.workbenchViewToggles",
        "view.hidden = viewName !== activeView",
        "button.classList.toggle(\"is-active\"",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_browser_panel_renders_runtime_browser_observations():
    root = parse_index()
    browser_panel = root.find_by_attr("data-testid", "browser-panel")
    assert browser_panel is not None
    for attr in [
        "data-browser-status",
        "data-browser-session",
        "data-browser-action",
        "data-browser-url",
        "data-browser-storage-object",
        "data-browser-preview-storage-object",
        "data-browser-screenshot",
        "data-browser-screenshot-preview",
        "data-browser-empty",
    ]:
        assert browser_panel.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "renderBrowser()",
        'event.type === "browser.action.performed"',
        "current_url",
        "screenshot_uri",
        "storage_object_id",
        "browserStatus",
        "browserScreenshot",
        "browserScreenshotPreview",
        "browserStorageObject",
        "browserPreviewStorageObject",
        "dataset.browserPreviewStorageObjectId",
        "renderBrowserPreviewStorageObject(",
        "storageObject.id",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_renders_delivery_chain_evidence():
    root = parse_index()
    delivery_chain = root.find_by_attr("data-testid", "delivery-chain")
    assert delivery_chain is not None
    for attr in [
        "data-delivery-chain-status",
        "data-delivery-chain-run",
        "data-delivery-chain-sandbox",
        "data-delivery-chain-artifact-storage",
        "data-delivery-chain-terminal-storage",
        "data-delivery-chain-browser-storage",
    ]:
        assert delivery_chain.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "renderDeliveryChain()",
        "deliveryChainRun",
        "deliveryChainSandbox",
        "deliveryChainArtifactStorage",
        "deliveryChainTerminalStorage",
        "deliveryChainBrowserStorage",
        "deliveryChainStatus",
        "buildDeliveryChainEvidence()",
        "storageObjectForTerminalOutputUri(",
        'storageObject.purpose === "sandbox-command-outputs"',
        "outputs.at(-1)",
        "storageObjectForBrowserCapture(",
        "readyStorageBackedArtifacts()",
        'chain.sandboxSessionId !== "--"',
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_renders_event_integrity_evidence():
    root = parse_index()
    event_integrity = root.find_by_attr("data-testid", "event-integrity")
    assert event_integrity is not None
    for attr in [
        "data-event-integrity-status",
        "data-event-integrity-count",
        "data-event-integrity-sequence",
        "data-event-integrity-closure",
    ]:
        assert event_integrity.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "renderEventIntegrity()",
        "buildEventIntegrityEvidence()",
        "eventIntegrityStatus",
        "eventIntegritySequence",
        "eventIntegrityClosure",
        "sandbox.command.executed",
        "skill.workflow_invoked",
        "sandbox.artifact.promoted",
        "run.succeeded",
        "browser.action.performed",
        "eventClosureStages(",
        'label: "plan"',
        'label: "skill"',
        'label: "browser"',
        "event stream sequence",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_merges_event_stream_by_stable_identity_and_sequence_order():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "eventStreamIntegrityIssues",
        "recordEventStreamIntegrityIssues(newEvents)",
        "eventIdentity(event)",
        "eventAlreadyLoaded(event)",
        "compareEventsBySequence",
        "state.events.sort(compareEventsBySequence)",
        "lastFiniteEventSequence(state.events)",
        "eventSequence(event)",
        "incoming event stream sequence is not monotonic",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_event_integrity_flags_events_without_sequence():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "eventsMissingSequence",
        "event stream sequence is missing",
        "state.events.length !== eventSequences.length",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_parses_sse_event_id_type_and_multiline_data():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "eventLineType",
        "eventLineId",
        "dataLines",
        "dataLines.join",
        "parsed.type = parsed.type || eventLineType",
        "parsed.id = parsed.id || eventLineId",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_javascript_calls_real_backend_contracts():
    script_path = WEB_ROOT / "assets" / "main.js"
    assert script_path.exists(), "apps/web/assets/main.js must exist"
    source = script_path.read_text()

    required_fragments = [
        '"/api/runs"',
        '`/api/runs/${state.currentRunId}/execute`',
        '`/api/runs/${state.currentRunId}/events`',
        '`/api/runs/${state.currentRunId}/artifacts`',
        '`/api/runs/${state.currentRunId}/approvals`',
        '`/api/runs/${state.currentRunId}/approvals/reject`',
        '"X-Tenant-ID"',
        '"X-User-ID"',
        'event.key === "Enter"',
        "!event.shiftKey",
        "event.preventDefault()",
        "submitRun()",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_approval_panel_preserves_resolution_evidence():
    root = parse_index()
    approval = root.find_by_attr("data-testid", "approval-panel")
    assert approval is not None
    assert approval.find_by_attr("data-approval-status", "") is not None
    assert approval.find_by_attr("data-approval-copy", "") is not None
    assert approval.find_by_attr("data-approval-resolution", "") is not None
    assert approval.find_by_attr("id", "approve-button") is not None
    assert approval.find_by_attr("id", "reject-button") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "approvalResolution",
        "renderApprovalResolution(",
        'event.type === "approval.resolved"',
        'event.type === "approval.rejected"',
        '"Approved"',
        '"Rejected"',
        "approvalResolutionParts(",
        "payload.approval_id",
        "payload.resolved_by_user_id",
        "No approval decision yet.",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_use_bearer_auth_when_dev_headers_are_disabled():
    root = parse_index()
    for element_id in [
        "login-email",
        "login-password",
        "login-button",
        "logout-button",
    ]:
        assert root.find_by_attr("id", element_id) is not None
    assert root.find_by_attr("data-auth-status", "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        '"/api/auth/login"',
        '`/api/auth/logout`',
        '"Authorization"',
        '"Bearer "',
        'sessionStorage.setItem("taroai.accessToken", state.accessToken)',
        'localStorage.setItem("taroai.accessToken", state.accessToken)',
        "sessionStorage.removeItem",
        'localStorage.getItem("taroai.accessToken")',
        "elements.rememberLogin.checked",
        "remember_me: elements.rememberLogin.checked",
        "elements.passwordToggle.setAttribute",
        "access_token",
        "result.tenant_id",
        "result.user_id",
        "result.workspace_id",
        'localStorage.setItem("taroai.tenantId"',
        'localStorage.setItem("taroai.userId"',
        'localStorage.setItem("taroai.workspaceId"',
        'clearAuthenticatedWorkspaceState("Authentication failed.");',
        "handleAuthExpired(response.status)",
        "status === 401 && state.accessToken",
        'clearAuthenticatedWorkspaceState("Authentication expired.");',
        'renderAuth("Session expired")',
        "parseResponseBody(text)",
        "return { message: text }",
        "login()",
        "logout()",
    ]
    for fragment in required_fragments:
        assert fragment in source
    login_source = source[
        source.index("async function login(") : source.index("async function logout()")
    ]
    assert "tenant_id: state.tenantId" not in login_source
    assert "email,\n        password," in login_source


def test_workspace_can_bootstrap_first_tenant_without_persisting_bootstrap_token():
    root = parse_index()
    for element_id in [
        "tenant-slug",
        "owner-display-name",
        "bootstrap-token",
        "bootstrap-login-button",
    ]:
        assert root.find_by_attr("id", element_id) is not None
    bootstrap_token = root.find_by_attr("id", "bootstrap-token")
    assert bootstrap_token is not None
    assert bootstrap_token.attrs.get("type") == "password"
    assert root.find_by_attr("data-bootstrap-status", "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        '"/api/tenants/bootstrap"',
        '"X-Bootstrap-Token"',
        "tenant_slug: state.tenantSlug",
        "owner_display_name: state.ownerDisplayName",
        "owner_password: elements.loginPassword.value",
        "result.starter_workspace_id",
        "elements.bootstrapToken.value = \"\"",
        'localStorage.setItem("taroai.tenantSlug"',
        'localStorage.setItem("taroai.ownerDisplayName"',
        "bootstrapTenant()",
    ]
    for fragment in required_fragments:
        assert fragment in source
    assert 'localStorage.setItem("taroai.bootstrapToken"' not in source
    assert 'sessionStorage.setItem("taroai.bootstrapToken"' not in source


def test_workspace_can_prefill_connection_from_url_without_url_secrets():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "applyUrlConfiguration();",
        "new URLSearchParams(window.location.search)",
        'apiBase: "taroai.apiBase"',
        'tenantId: "taroai.tenantId"',
        'userId: "taroai.userId"',
        'workspaceId: "taroai.workspaceId"',
        'email: "taroai.authEmail"',
        'urlParams.get("runId")',
        "await refreshRun()",
        "state[key] = value",
        "localStorage.setItem(storageKey, value)",
        'urlParams.has("accessToken")',
        'urlParams.has("password")',
        'urlParams.delete("accessToken")',
        'urlParams.delete("password")',
        "window.history.replaceState",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_logout_clears_execution_surfaces():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "clearAuthenticatedWorkspaceState()",
        "state.currentRunId = null",
        "state.events = []",
        "state.artifacts = []",
        "state.storageObjects = []",
        "state.runTrace = null",
        "state.runtimeState = null",
        "state.pendingApprovalId = null",
        "state.runStatus = \"idle\"",
        "clearBrowserPreview()",
        'terminalMessage = "Signed out."',
        "renderTerminal(terminalMessage)",
        "renderArtifacts()",
        "renderBrowser()",
        "renderRunTrace()",
        "renderRuntimeState()",
        "renderExecutionLoop()",
        "renderRunEvidence()",
        "renderApproval()",
        "resetConversation()",
        "elements.conversation.replaceChildren",
        "Start a governed run.",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_chat_topbar_keeps_runtime_status_screen_reader_only():
    root = parse_index()
    status = root.find_by_attr("data-thread-presence", "")
    assert status is not None
    assert "visually-hidden" in status.attrs["class"]
    assert status.attrs["aria-live"] == "polite"

    source = (WEB_ROOT / "assets" / "main.js").read_text()
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    assert 'elements.status.className = "visually-hidden"' in source
    assert 'elements.status.className = "status-pill"' not in source
    assert ".status-pill" not in styles


def test_workspace_storage_content_fetches_clear_auth_expiry():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    assert "async function raiseStorageFetchError(response)" in source
    assert "handleAuthExpired(response.status)" in source
    assert source.count("await raiseStorageFetchError(response)") >= 3


def test_workspace_surfaces_model_and_sandbox_readiness_before_execution():
    root = parse_index()
    for attr in [
        "data-readiness-status",
        "data-readiness-model",
        "data-readiness-sandbox",
    ]:
        assert root.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        '"/readyz"',
        "loadReadiness()",
        "renderReadiness(",
        "model_gateway",
        "sandbox",
        "missing.join",
        "controller_required",
        "controller_configured",
        "capabilities_checked",
        "network_isolation_declared",
        "filesystem_isolation_declared",
        "resource_limits_declared",
        "Sandbox PoC:",
        "Sandbox isolated:",
        "state.readiness",
        "readiness.ready && modelGateway.configured && sandbox.configured",
        "error.status === 503",
        "error.body?.ready === false",
        "elements.readinessStatus",
        "elements.readinessModel",
        "elements.readinessSandbox",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_refreshes_readiness_after_bearer_login():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    assert 'renderAuth("Signed in");\n    await loadReadiness();' in source


def test_workspace_surfaces_customer_success_dashboard_data():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "customer-success-panel")
    assert panel is not None
    for attr in [
        "data-cs-status",
        "data-cs-health",
        "data-cs-runs",
        "data-cs-feedback",
        "data-cs-eval-candidates",
        "data-cs-pack-candidates",
        "data-cs-refresh",
    ]:
        assert panel.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "loadCustomerSuccess()",
        "renderCustomerSuccess(",
        '"/api/customer-success/summary"',
        '"/api/customer-success/feedback"',
        '"/api/customer-success/evaluation-candidates"',
        '"/api/customer-success/solution-pack-candidates"',
        "elements.customerSuccessStatus",
        "elements.customerSuccessHealth",
        "elements.customerSuccessEvalCandidates",
        "elements.customerSuccessPackCandidates",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_generate_customer_success_candidates_from_feedback():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "customer-success-panel")
    assert panel is not None
    assert panel.find_by_attr("data-cs-candidate-action-status", "") is not None
    assert panel.find_by_attr("data-cs-eval-candidate-selected", "") is not None
    assert panel.find_by_attr("data-cs-pack-candidate-selected", "") is not None
    for element_id in [
        "cs-create-eval-candidates",
        "cs-create-pack-candidates",
        "cs-accept-eval-candidate",
        "cs-reject-eval-candidate",
        "cs-accept-pack-candidate",
        "cs-reject-pack-candidate",
    ]:
        assert panel.find_by_attr("id", element_id) is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "customerSuccessCandidateStatus",
        "customerSuccessCreateEvalCandidates",
        "customerSuccessCreatePackCandidates",
        "createCustomerSuccessEvaluationCandidates(",
        "createCustomerSuccessSolutionPackCandidates(",
        '"/api/customer-success/evaluation-candidates"',
        '"/api/customer-success/solution-pack-candidates"',
        "minimum_repeated_feedback: 3",
        "Eval candidates generated",
        "Pack candidates generated",
        "renderEvaluationCandidateReview(",
        "selectedEvaluationCandidate(",
        "candidates.findLast(",
        "reviewSelectedEvaluationCandidate(",
        "`/api/customer-success/evaluation-candidates/${candidate.id}/review`",
        "renderSolutionPackCandidateReview(",
        "selectedSolutionPackCandidate(",
        "reviewSelectedSolutionPackCandidate(",
        "`/api/customer-success/solution-pack-candidates/${candidate.id}/review`",
        "solutionPackCandidateReviewPayload(",
        'status: "accepted"',
        'status: "rejected"',
        "Eval candidate accepted",
        "evaluation_case_id",
        "Pack candidate accepted",
        "publication_draft_id",
        "elements.customerSuccessCreateEvalCandidates.addEventListener",
        "elements.customerSuccessCreatePackCandidates.addEventListener",
        "elements.customerSuccessEvalAccept.addEventListener",
        "elements.customerSuccessEvalReject.addEventListener",
        "elements.customerSuccessPackAccept.addEventListener",
        "elements.customerSuccessPackReject.addEventListener",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_manage_solution_pack_publication_drafts():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "customer-success-panel")
    assert panel is not None
    for attr in [
        "data-cs-drafts-list",
        "data-cs-draft-selected",
        "data-cs-draft-status",
    ]:
        assert panel.find_by_attr(attr, "") is not None
    for element_id in [
        "cs-draft-skill",
        "cs-draft-summary",
        "cs-draft-pack-version",
        "cs-draft-skill-manifest",
        "cs-draft-save",
        "cs-draft-submit",
        "cs-draft-approve",
        "cs-draft-reject",
        "cs-draft-apply",
    ]:
        assert panel.find_by_attr("id", element_id) is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "publicationDrafts",
        '"/api/customer-success/solution-pack-drafts"',
        "`/api/customer-success/solution-pack-drafts/${draft.id}`",
        "`/api/customer-success/solution-pack-drafts/${draft.id}/submit`",
        "`/api/customer-success/solution-pack-drafts/${draft.id}/review`",
        "`/api/customer-success/solution-pack-drafts/${draft.id}/apply`",
        "proposed_pack_version",
        "proposed_skill_manifest",
        "proposed_skill_manifests",
        "Array.isArray(parsedManifest)",
        "renderSolutionPackDrafts(",
        "selectSolutionPackDraft(",
        "saveSelectedSolutionPackDraft(",
        "submitSelectedSolutionPackDraft(",
        "reviewSelectedSolutionPackDraft(",
        "applySelectedSolutionPackDraft(",
        '"approved"',
        '"rejected"',
    ]
    for fragment in required_fragments:
        assert fragment in source

    save_draft_source = source.split(
        "async function saveSelectedSolutionPackDraft() {", 1
    )[1].split("\n}\n\nfunction parseDraftSkillManifest", 1)[0]
    assert "renderSolutionPackDrafts(" not in save_draft_source
    assert save_draft_source.index("const payload = {") < save_draft_source.index(
        'textContent = "Saving draft"'
    )

    review_candidate_source = source.split(
        "async function reviewSelectedSolutionPackCandidate(status) {", 1
    )[1].split("\n}\n\nfunction solutionPackCandidateReviewPayload", 1)[0]
    assert review_candidate_source.index(
        "await loadCustomerSuccess();"
    ) < review_candidate_source.index(
        "selectSolutionPackDraft(updated.publication_draft_id);"
    )


def test_workspace_can_download_storage_backed_artifacts():
    root = parse_index()
    artifacts_panel = root.find_by_attr("data-testid", "artifact-list")
    assert artifacts_panel is not None
    download_status = artifacts_panel.find_by_attr(
        "data-artifact-download-status",
        "",
    )
    assert download_status is not None
    assert download_status.attrs["data-download-state"] == "idle"
    downloaded_storage = artifacts_panel.find_by_attr(
        "data-artifact-downloaded-storage-object",
        "",
    )
    assert downloaded_storage is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "storageObjects",
        '`/api/runs/${state.currentRunId}/storage-objects`',
        '`/api/storage/objects/${storageObject.id}/content`',
        "downloadArtifact(",
        "renderArtifactDownloadStatus(",
        "artifactDownloadStatus",
        "artifactDownloadedStorageObject",
        "dataset.downloadStorageObjectId",
        "dataset.downloadState",
        "URL.createObjectURL",
        "link.download",
        "data-storage-object-id",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_preview_storage_backed_text_artifacts():
    root = parse_index()
    artifacts_panel = root.find_by_attr("data-testid", "artifact-list")
    assert artifacts_panel is not None
    for attr in [
        "data-artifact-preview-status",
        "data-artifact-preview-title",
        "data-artifact-preview-storage-object",
        "data-artifact-preview-content",
    ]:
        assert artifacts_panel.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "previewArtifact(",
        "renderArtifactPreview(",
        "clearArtifactPreview(",
        "data-preview-storage-object-id",
        "`/api/storage/objects/${storageObject.id}/content`",
        "response.text()",
        "slice(0, ARTIFACT_PREVIEW_MAX_CHARACTERS)",
        "artifactPreviewContent",
        "artifactPreviewStorageObject",
        "dataset.previewStorageObjectId",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_artifact_panel_has_persistent_delivery_summary():
    root = parse_index()
    artifacts_panel = root.find_by_attr("data-testid", "artifact-list")
    assert artifacts_panel is not None
    assert artifacts_panel.find_by_attr("data-delivery-summary", "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "deliverySummary",
        "renderDeliverySummary()",
        "elements.deliverySummary",
        "downloadableArtifacts()",
        "Ready to download",
        "Waiting for artifact storage",
        "No artifacts delivered",
        "elements.deliverySummary.dataset.deliveryState",
        "renderDeliverySummary();",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_submit_run_feedback_after_artifact_delivery():
    root = parse_index()
    artifacts_panel = root.find_by_attr("data-testid", "artifact-list")
    assert artifacts_panel is not None
    feedback_panel = artifacts_panel.find_by_attr("data-run-feedback-panel", "")
    assert feedback_panel is not None
    for attr in [
        "data-run-feedback-status",
    ]:
        assert feedback_panel.find_by_attr(attr, "") is not None
    assert feedback_panel.find_by_attr("data-run-feedback-state", "waiting") is not None
    for element_id in [
        "run-feedback-positive",
        "run-feedback-negative",
    ]:
        assert feedback_panel.find_by_attr("id", element_id) is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "feedbackSubmittedRunIds",
        "renderRunFeedback(",
        "submitRunFeedback(",
        '"/api/customer-success/feedback"',
        'feedback_type: "thumbs_rating"',
        'target_type: "run"',
        "rating,",
        "artifact_count: readyArtifacts.length",
        "elements.runFeedbackPositive.disabled = true;",
        "elements.runFeedbackNegative.disabled = true;",
        "state.feedbackSubmittedRunIds.add(state.currentRunId)",
        "state.feedbackSubmittedRunIds.clear()",
        "state.feedbackSubmittedRunIds.delete(state.currentRunId)",
        "elements.runFeedbackPositive.addEventListener",
        "elements.runFeedbackNegative.addEventListener",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_capture_missing_skill_feedback_for_solution_pack():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "customer-success-panel")
    assert panel is not None
    assert panel.find_by_attr("data-cs-missing-skill-status", "") is not None
    for element_id in [
        "cs-missing-skill-name",
        "cs-missing-skill-comment",
        "cs-missing-skill-solution-pack",
        "cs-submit-missing-skill",
    ]:
        assert panel.find_by_attr("id", element_id) is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "customerSuccessMissingSkillStatus",
        "submitMissingSkillFeedback(",
        'feedback_type: "missing_skill"',
        'target_type: "solution_pack"',
        "solution_pack_id: solutionPackId",
        "missing_skill_name: missingSkillName",
        'source: "workspace_skill_request"',
        "Skill request recorded",
        "elements.customerSuccessSubmitMissingSkill.addEventListener",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_list_and_invoke_installed_skills():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "workspace-skills-panel")
    assert panel is not None
    for attr in [
        "data-skills-status",
        "data-skills-list",
        "data-skill-invoke-status",
    ]:
        assert panel.find_by_attr(attr, "") is not None
    for element_id in [
        "skill-invoke-input",
        "invoke-skill-button",
    ]:
        assert panel.find_by_attr("id", element_id) is not None
    assert panel.find_by_attr("data-skills-refresh", "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "workspaceSkills",
        "selectedSkillId",
        "loadWorkspaceSkills()",
        "renderWorkspaceSkills(",
        "`/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills`",
        "`/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills/${encodeURIComponent(skill.skill_id)}/invoke`",
        "invocation_ready",
        "missing_required_scopes",
        "data-workspace-skill-id",
        "invokeSelectedWorkspaceSkill()",
        "state.currentRunId = result.run_id || result.output?.run_id",
        "state.runTrace = null",
        "state.runtimeState = null",
        "clearArtifactPreview()",
        "clearArtifactDownloadStatus()",
        "refreshRun()",
    ]
    for fragment in required_fragments:
        assert fragment in source
    invoke_skill = source.split(
        "async function invokeSelectedWorkspaceSkill()", 1
    )[1].split("function renderCustomerSuccess", 1)[0]
    for fragment in [
        "state.runTrace = null",
        "state.runtimeState = null",
        "clearArtifactPreview()",
        "clearArtifactDownloadStatus()",
    ]:
        assert fragment in invoke_skill


def test_workspace_can_install_solution_pack_and_refresh_skills():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "solution-pack-panel")
    assert panel is not None
    for attr in [
        "data-solution-pack-status",
        "data-solution-pack-list",
        "data-solution-pack-install-status",
    ]:
        assert panel.find_by_attr(attr, "") is not None
    assert panel.find_by_attr("data-solution-pack-refresh", "") is not None
    assert panel.find_by_attr("id", "install-solution-pack-button") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "solutionPacks",
        "selectedSolutionPackId",
        "loadSolutionPacks()",
        "renderSolutionPacks(",
        'apiFetch("/api/solution-packs")',
        "data-solution-pack-id",
        "installSelectedSolutionPack()",
        "`/api/solution-packs/${encodeURIComponent(pack.manifest.id)}/install`",
        "workspace_ids: [state.workspaceId]",
        "Solution pack installed",
        "loadWorkspaceSkills()",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_announces_successful_artifact_delivery_once():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "deliveredRunIds",
        "announceRunDelivery()",
        "downloadableArtifacts()",
        "state.runStatus !== \"succeeded\"",
        "state.deliveredRunIds.has(state.currentRunId)",
        "state.deliveredRunIds.add(state.currentRunId)",
        "switchWorkbenchView(\"run\")",
        "appendMessage(\"agent\",",
        "delivered",
        "state.deliveredRunIds.clear()",
        "await announceRunDelivery();",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_auto_previews_first_delivered_artifact():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "autoPreviewFirstDeliveredArtifact(",
        "await autoPreviewFirstDeliveredArtifact(readyArtifacts);",
        "const first = readyArtifacts[0];",
        "previewArtifact(first.storageObject.id)",
        "Preview loaded",
        "Ready to download",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_retries_auto_preview_until_fetch_succeeds():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "previewedRunIds",
        "state.previewedRunIds.has(state.currentRunId)",
        "const previewed = await previewArtifact(first.storageObject.id);",
        "if (previewed) {",
        "state.previewedRunIds.add(state.currentRunId);",
        "return true;",
        "return false;",
        "state.previewedRunIds.clear()",
        "state.previewedRunIds.delete(state.currentRunId)",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_stops_forcing_run_view_after_delivery_and_preview_are_done():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "const deliveryAnnounced = state.deliveredRunIds.has(state.currentRunId);",
        "const previewComplete = state.previewedRunIds.has(state.currentRunId);",
        "if (deliveryAnnounced && previewComplete) {",
        "if (!deliveryAnnounced || !previewComplete) {",
        "if (deliveryAnnounced) {",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_download_storage_backed_browser_captures():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "storageObjectForBrowserCapture(",
        "previewBrowserCapture(",
        "downloadBrowserCapture(",
        "data-browser-storage-object-id",
        '`/api/storage/objects/${storageObject.id}/content`',
        "state.browserPreviewStorageObjectId",
        "URL.createObjectURL",
        "setBrowserPreviewSource(objectUrl)",
        "browserScreenshotPreview",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_terminal_uses_safe_sandbox_command_events():
    root = parse_index()
    terminal = root.find_by_attr("data-testid", "sandbox-terminal")
    assert terminal is not None
    assert terminal.find_by_attr("data-terminal-status", "") is not None
    assert terminal.find_by_attr("data-terminal-output", "") is not None
    assert terminal.find_by_attr("data-terminal-output-storage-object", "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        'event.type === "sandbox.command.executed"',
        "stdout_length",
        "stderr_length",
        "output_uri",
        "terminalOutputStorageObject",
        "dataset.terminalStorageObjectId",
        "storageObjectForTerminalOutputUri(",
        "renderTerminalFromEvents()",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_terminal_does_not_render_raw_command_stream_fields():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    unsafe_fragments = [
        "latest.stdout ||",
        "latest.stderr ||",
        "[stdout, stderr]",
        "...payload",
    ]
    for fragment in unsafe_fragments:
        assert fragment not in source
    required_fragments = [
        "safeTerminalOutput(",
        "stdout_length",
        "stderr_length",
        "output_uri",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_polls_long_running_runs_until_terminal_status():
    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "pollTimer",
        "pollingInFlight",
        "startRunPolling()",
        "stopRunPolling()",
        "isRunTerminalStatus(",
        "window.setInterval",
        "window.clearInterval",
        '`/api/runs/${state.currentRunId}`',
        '"succeeded"',
        '"failed"',
        '"cancelled"',
        '"timed_out"',
        "await refreshRun()",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_cancel_and_retry_runs_from_control_panel():
    root = parse_index()
    controls = root.find_by_attr("data-testid", "run-controls")
    assert controls is not None
    for element_id in [
        "cancel-run-button",
        "retry-run-button",
    ]:
        assert controls.find_by_attr("id", element_id) is not None
    assert controls.find_by_attr("data-run-control-status", "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "runControlStatus",
        "cancelRun()",
        "retryRun()",
        "`/api/runs/${state.currentRunId}/cancel`",
        "`/api/runs/${state.currentRunId}/retry`",
        '"operator_cancelled"',
        '"operator_retry"',
        "renderRunControls()",
        "elements.cancelRun.disabled",
        "elements.retryRun.disabled",
        "elements.cancelRun.addEventListener",
        "elements.retryRun.addEventListener",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_load_and_select_run_history():
    root = parse_index()
    history = root.find_by_attr("data-testid", "run-history")
    assert history is not None
    for attr in [
        "data-run-history-status",
        "data-run-history-list",
        "data-run-history-refresh",
    ]:
        assert history.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "runHistory",
        "selectedRunHistoryId",
        "loadRunHistory()",
        "renderRunHistory(",
        "selectRunFromHistory(",
        "`/api/runs?workspace_id=${encodeURIComponent(state.workspaceId)}&limit=20`",
        "data-run-history-id",
        "await refreshRun()",
        "elements.runHistoryList.addEventListener",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_can_render_run_trace_evidence():
    root = parse_index()
    trace_panel = root.find_by_attr("data-testid", "run-trace")
    assert trace_panel is not None
    for attr in [
        "data-trace-status",
        "data-trace-span-count",
        "data-trace-event-count",
        "data-trace-billing-count",
        "data-trace-audit-count",
        "data-trace-error-classification",
        "data-trace-list",
    ]:
        assert trace_panel.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "runTrace",
        "loadRunTrace()",
        "renderRunTrace(",
        "`/api/runs/${state.currentRunId}/trace`",
        "trace.spans",
        "trace.trace_events",
        "trace.billing_meters",
        "trace.audit_events",
        "trace.error_classification",
        "elements.traceList",
    ]
    for fragment in required_fragments:
        assert fragment in source
    refresh_run = source.split("async function refreshRun()", 1)[1].split(
        "async function loadRunStatus()", 1
    )[0]
    assert refresh_run.index("await announceRunDelivery();") < refresh_run.index(
        "await loadRunTrace();"
    )


def test_workspace_can_render_runtime_state_snapshot():
    root = parse_index()
    state_panel = root.find_by_attr("data-testid", "runtime-state")
    assert state_panel is not None
    for attr in [
        "data-runtime-state-status",
        "data-runtime-current-step",
        "data-runtime-completed-count",
        "data-runtime-sandbox-session",
        "data-runtime-browser-session",
        "data-runtime-artifact-count",
    ]:
        assert state_panel.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "runtimeState",
        "loadRuntimeState()",
        "renderRuntimeState(",
        "`/api/runs/${state.currentRunId}/state`",
        "runtime.current_step_id",
        "runtime.completed_step_ids",
        "runtime.sandbox_session_id",
        "runtime.browser_session_id",
        "runtime.promoted_sandbox_artifact_paths",
        "elements.runtimeStateStatus",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_surfaces_execution_loop_progress():
    root = parse_index()
    execution = root.find_by_attr("data-testid", "execution-loop")
    assert execution is not None
    for attr in [
        "data-execution-summary",
        "data-execution-model-route",
        "data-execution-run",
        "data-execution-plan",
        "data-execution-sandbox",
        "data-execution-browser",
        "data-execution-artifact",
    ]:
        assert execution.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "renderExecutionLoop()",
        "executionLoopSummary",
        "executionModelRoute",
        "modelRouteLabel()",
        "latestModelRouteEvent()",
        '"model.plan.created"',
        '"model.operation.recorded"',
        "provider_attempts",
        "total_tokens",
        "executionLoopStageLabel(",
        "sandbox.session.created",
        "browser.session.created",
        "sandbox.artifact.promoted",
        "artifact.created",
        "elements.executionLoopPlan",
        "state.runtimeState",
        "state.artifacts",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_workspace_exposes_its_sidecar_state():
    root = parse_index()
    state = root.find_by_attr("data-sidecar-state", "")
    source = (WEB_ROOT / "assets" / "main.js").read_text()

    assert state is not None
    assert state.text.strip() == "closed"
    assert "elements.sidecarState.textContent" in source


def test_bootstrap_waits_for_automatic_login_before_reporting_ready():
    source = (WEB_ROOT / "assets" / "main.js").read_text()
    start = source.index("async function bootstrapTenant()")
    end = source.index("async function login(", start)
    bootstrap = source[start:end]

    assert bootstrap.index("await login();") < bootstrap.index(
        'renderBootstrap("Tenant ready");'
    )


def test_workspace_surfaces_run_evidence_checklist():
    root = parse_index()
    evidence = root.find_by_attr("data-testid", "run-evidence")
    assert evidence is not None
    for attr in [
        "data-evidence-summary",
        "data-evidence-plan",
        "data-evidence-sandbox",
        "data-evidence-artifact",
        "data-evidence-browser",
        "data-evidence-terminal",
    ]:
        assert evidence.find_by_attr(attr, "") is not None

    script_path = WEB_ROOT / "assets" / "main.js"
    source = script_path.read_text()
    required_fragments = [
        "renderRunEvidence()",
        "buildRunEvidenceItems(",
        "data-evidence-status",
        "sandbox.command.executed",
        "sandbox.artifact.promoted",
        "browser.action.performed",
        "storageObjectForArtifact(",
        "latestBrowserEvent()",
    ]
    for fragment in required_fragments:
        assert fragment in source


def test_static_workspace_is_packaged_with_local_cloud_poc():
    compose_path = ROOT / "infra" / "docker-compose.yml"
    compose = compose_path.read_text()
    nginx = (ROOT / "infra" / "nginx" / "local.conf").read_text()
    main = (WEB_ROOT / "assets" / "main.js").read_text()
    chat_api = (WEB_ROOT / "assets" / "chat-api.js").read_text()
    assert "web:" in compose
    assert "../apps/web:/usr/share/nginx/html:ro" in compose
    assert "./nginx/local.conf:/etc/nginx/conf.d/default.conf:ro" in compose
    assert "${TAROAI_WEB_PORT:-3000}:80" in compose
    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000" in nginx
    assert 'add_header Cache-Control "no-store, no-cache, must-revalidate"' in nginx
    assert 'localStorage.getItem("taroai.apiBase") || window.location.origin' in main
    assert '"taroai.apiBase", window.location.origin' in chat_api


def test_frontend_uses_plain_static_assets_without_runtime_fixtures():
    package_path = WEB_ROOT / "package.json"
    assert package_path.exists(), "apps/web/package.json must document web commands"
    package_json = package_path.read_text()
    assert '"serve"' in package_json
    assert '"build"' not in package_json

    combined = "\n".join(
        path.read_text()
        for path in [
            WEB_ROOT / "index.html",
            WEB_ROOT / "assets" / "main.js",
            WEB_ROOT / "assets" / "styles.css",
        ]
    )
    for forbidden in ["MockModelProvider", "mock provider", "fake provider"]:
        assert forbidden not in combined

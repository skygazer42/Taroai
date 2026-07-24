import re
from pathlib import Path

from tests.web.test_workspace_frontend_contract import Element, parse_index


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "apps" / "web"


def is_descendant(node: Element, ancestor: Element) -> bool:
    current = node.parent
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def script_source() -> str:
    return (WEB_ROOT / "assets" / "main.js").read_text()


def chat_controller_source() -> str:
    return (WEB_ROOT / "assets" / "chat-controller.js").read_text()


def asset_source(name: str) -> str:
    return (WEB_ROOT / "assets" / name).read_text()


def function_source(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start + len(signature))
    return source[start:end]


def test_chat_shell_exposes_creao_layout_regions():
    root = parse_index()
    sidebar = root.find_by_attr("data-testid", "app-sidebar")
    chat = root.find_by_attr("data-testid", "chat-column")
    empty_state = root.find_by_attr("data-testid", "chat-empty-state")
    composer = root.find_by_attr("data-testid", "chat-composer")
    artifact_panel = root.find_by_attr("data-testid", "artifact-panel")
    operations = root.find_by_attr("data-testid", "operations-drawer")

    assert sidebar is not None
    assert chat is not None
    assert empty_state is not None and is_descendant(empty_state, chat)
    assert composer is not None and is_descendant(composer, chat)
    assert artifact_panel is not None
    assert operations is not None

    run_history = root.find_by_attr("data-testid", "run-history")
    artifact_list = root.find_by_attr("data-testid", "artifact-list")
    assert run_history is not None and is_descendant(run_history, operations)
    assert artifact_list is not None and is_descendant(artifact_list, artifact_panel)


def test_thread_operations_stay_out_of_the_primary_chat_toolbar():
    root = parse_index()
    menu = root.find_by_attr("data-thread-actions-menu", "")
    trigger = root.find_by_attr("data-thread-more", "")

    assert menu is not None and menu.attrs.get("role") == "menu"
    assert trigger is not None
    assert trigger.attrs.get("aria-haspopup") == "menu"
    assert trigger.attrs.get("aria-controls") == menu.attrs.get("id")
    for attr in ["data-thread-share", "data-thread-create-agent", "data-open-artifacts", "data-open-code", "data-open-queue"]:
        action = root.find_by_attr(attr, "")
        assert action is not None and action.attrs.get("role") == "menuitem" and is_descendant(action, menu)
    for attr in ["data-thread-rename", "data-thread-pin", "data-thread-archive", "data-thread-delete"]:
        action = root.find_by_attr(attr, "")
        assert action is not None and action.attrs.get("role") == "menuitem" and is_descendant(action, menu)
    assert root.find_by_attr("data-open-files-dialog", "") is None
    assert root.find_by_attr("data-plan-pill", "") is None


def test_thread_archive_uses_the_persisted_backend_status():
    source = chat_controller_source()
    archive = function_source(source, "  async archiveThread(", "  async deleteCurrentThread(")

    assert 'return this.updateThread(threadId, { status: "archived" })' in archive
    assert "server archive support is not ready" not in source


def test_create_agent_primary_cta_uses_the_guided_builder():
    source = chat_controller_source()
    agents = asset_source("agents-ui.js")
    clicks = function_source(source, "  onClick(event)", "  onInput(event)")
    add_command = function_source(source, "  handleAddCommand(command)", "  async selectModel(")
    builder = function_source(source, "  openAgentBuilderDialog()", "  openCreateAgentDialog()")
    agent_clicks = function_source(agents, "  click(event)", "  async change(event)")

    assert 'if (command === "agent") return this.openAgentBuilderDialog();' in add_command
    assert '[data-thread-create-agent]' in clicks and "openCreateAgentDialog()" in clicks
    for view in ['form', 'connectors', 'skills']:
        assert f'data-agent-builder-go="{view}"' in builder
    assert 'data-agent-builder-manage="${type}s"' in builder
    assert "window.location.hash = `brain/${manage.dataset.agentBuilderManage}`" in builder
    assert 'await this.sendThreadMessage(null, null, "autonomous")' in builder
    assert "taroai.pendingAgent." in builder
    assert "persistPendingAgent(outcome)" in source
    assert 'this.api.post("/api/agents"' in source
    assert "/extract-agent" in source
    assert "instructions: pending.description || extracted.version.instructions" in source
    assert 'Create an agent named "${name}"' in builder
    assert "workflow.spec DAG" in builder
    assert "native agent.create_draft tool" in builder
    assert "required instructions field" in builder
    assert "do not add pass-through input or output-only nodes" in builder
    assert "Do not execute the workflow during creation" in builder
    assert "persist-as-workflow-agent skill" not in builder
    assert 'window.location.hash = "chat"' in agent_clicks
    assert "window.taroaiChat?.openAgentBuilderDialog()" in agent_clicks


def test_agent_builder_does_not_duplicate_a_tool_created_draft():
    source = chat_controller_source()
    persist = function_source(source, "  async persistPendingAgent(", "  openCreateAgentDialog()")

    assert 'event.run_id === chatState.currentRunId && eventType(event) === "app_created"' in persist
    assert "if (createdEvent)" in persist
    assert 'new CustomEvent("taroai:agents-changed")' in persist
    assert "return;" in persist


def test_composer_add_menu_uses_real_intents_and_resource_flows():
    root = parse_index()
    source = chat_controller_source()
    handler = function_source(source, "  handleAddCommand(command)", "  async selectModel(key)")
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")

    agent_mode = root.find_by_attr("data-add-command", "agent")
    assert agent_mode is not None and agent_mode.attrs.get("role") == "menuitem"
    assert "selectCreateIntent(command)" in handler
    assert 'command === "agent"' in handler
    assert "chatState.creationCapabilities[command] === true" in handler
    assert "openComposerResourceDialog(command)" in handler
    assert "toggleBrowserProfileMenu()" in handler
    for fake_command in ["/image ", "/video ", "/voice ", "/browser ", "/workflow ", "/slides "]:
        assert fake_command not in handler
    assert "The user explicitly chose image generation" in source
    assert 'this.api.post("/api/browser/profiles"' in source
    assert "payload.composer_creation" in source
    assert "renderCreationCapabilities()" in source
    assert 'browser.hidden = capabilities.browser !== true' in source
    assert 'mode: runMode' in send
    assert 'runMode === "chat" ? "Thinking…" : "Starting agent…"' in send


def test_composer_exposes_accessible_add_and_model_menus():
    root = parse_index()
    contracts = [
        ("composer-add-button", "composer-add-menu"),
        ("model-selector-button", "model-selector-menu"),
    ]

    for button_id, menu_id in contracts:
        button = root.find_by_attr("id", button_id)
        menu = root.find_by_attr("id", menu_id)
        assert button is not None
        assert button.attrs.get("aria-haspopup") == "menu"
        assert button.attrs.get("aria-controls") == menu_id
        assert button.attrs.get("aria-expanded") == "false"
        assert menu is not None
        assert menu.attrs.get("role") == "menu"
        assert "hidden" in menu.attrs

    browser = root.find_by_attr("data-add-command", "browser")
    browser_menu = root.find_by_attr("id", "composer-browser-menu")
    assert browser is not None and browser.attrs.get("aria-controls") == "composer-browser-menu"
    assert browser.attrs.get("aria-expanded") == "false"
    assert browser_menu is not None and browser_menu.attrs.get("role") == "menu"


def test_account_auth_is_separate_from_developer_operations():
    root = parse_index()
    auth_dialog = root.find_by_attr("id", "auth-dialog")
    operations = root.find_by_attr("data-testid", "operations-drawer")
    account = root.find_by_attr("data-account-menu-toggle", "")
    account_menu = root.find_by_attr("data-account-menu", "")
    login_email = root.find_by_attr("id", "login-email")
    remember_login = root.find_by_attr("id", "remember-login")
    password_toggle = root.find_by_attr("data-password-toggle", "")
    signup_name = root.find_by_attr("id", "signup-name")
    auth_mode_toggle = root.find_by_attr("data-auth-mode-toggle", "")
    auth_close = root.find_by_attr("data-auth-dialog-close", "")
    bootstrap_token = root.find_by_attr("id", "bootstrap-token")
    operations_button = root.find_by_attr("aria-label", "Open developer operations")

    assert auth_dialog is not None and auth_dialog.tag == "dialog"
    assert account is not None and account.attrs.get("aria-haspopup") == "dialog"
    assert account.attrs.get("aria-controls") == "auth-dialog"
    assert account_menu is not None and account_menu.attrs.get("role") == "menu"
    assert "hidden" in account_menu.attrs
    assert login_email is not None and is_descendant(login_email, auth_dialog)
    assert login_email.attrs.get("placeholder") == "username@email.com"
    assert remember_login is not None and "checked" in remember_login.attrs
    assert password_toggle is not None and is_descendant(password_toggle, auth_dialog)
    assert signup_name is not None and is_descendant(signup_name, auth_dialog)
    assert auth_mode_toggle is not None and is_descendant(auth_mode_toggle, auth_dialog)
    assert auth_close is not None and "hidden" in auth_close.attrs
    assert bootstrap_token is not None and is_descendant(bootstrap_token, auth_dialog)
    assert operations is not None and not is_descendant(login_email, operations)
    assert operations_button is not None and "hidden" in operations_button.attrs

    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    assert ".auth-dialog[open]" in styles
    assert ".auth-dialog::before" in styles
    assert "width: min(384px" in styles


def test_account_menu_and_login_surface_offer_persistent_language_switching():
    root = parse_index()
    account_menu = root.find_by_attr("data-account-menu", "")
    auth_dialog = root.find_by_attr("id", "auth-dialog")
    assert account_menu.find_by_attr("data-locale-choice", "zh-CN") is not None
    assert account_menu.find_by_attr("data-locale-choice", "en") is not None
    assert auth_dialog.find_by_attr("data-locale-choice", "zh-CN") is not None
    assert auth_dialog.find_by_attr("data-locale-choice", "en") is not None

    i18n = asset_source("i18n.js")
    assert 'localStorage.getItem("taroai.locale")' in i18n
    assert 'localStorage.setItem("taroai.locale", nextLocale)' in i18n
    assert "function setLocale(nextLocale)" in i18n


def test_protected_workspace_data_waits_for_sign_in():
    chat_source = chat_controller_source()
    main_source = script_source()
    init = function_source(chat_source, "  async init()", "  stopOwnedEvent(")
    auth_changed = function_source(chat_source, "  async onAuthChanged(event)", "  onWindowMessage(event)")
    load_threads = function_source(chat_source, "  async loadThreads()", "  renderThreadListNotice(")
    load_catalog = function_source(chat_source, "  async loadModelCatalog()", "  findModel(")
    initialize = function_source(main_source, "function initializeControls()", "function switchWorkbenchView(")
    sync = function_source(main_source, "async function syncStoredSession()", "async function login(")
    login = function_source(main_source, "async function login(", "async function registerAccount()")
    operations = function_source(main_source, "function setOperationsOpen(open)", "function renderAttachmentChips()")
    startup = function_source(main_source, "async function startApp()", "startApp();")

    assert "if (this.api.settings().accessToken)" in init
    assert load_threads.count("if (!this.api.settings().accessToken)") == 2
    assert load_threads.index("if (!this.api.settings().accessToken)") < load_threads.index("this.api.get(")
    assert load_catalog.index("if (!this.api.settings().accessToken)") < load_catalog.index("this.api.get(")
    assert "this.loadModelCatalog(), this.loadCapabilities()" in auth_changed
    assert 'apiFetch("/api/auth/session")' in sync
    assert "if (!session.authenticated)" in sync
    assert "loadCustomerSuccess();" not in initialize
    for hidden_loader in ["loadCustomerSuccess()", "loadSolutionPacks()", "loadWorkspaceSkills()", "loadRunHistory()"]:
        assert hidden_loader not in login
        assert hidden_loader not in startup
        assert hidden_loader in operations
    assert startup.index("await syncStoredSession()") < startup.index("createChatController()")
    assert startup.index("createChatController()") < startup.index("loadHomepageAgents()")
    assert "refreshRouteData(state.appRoute)" in startup


def test_login_refreshes_the_active_product_route():
    source = script_source()
    login = function_source(source, "async function login(", "async function logout()")

    assert 'routeFromHash() !== "chat"' in login
    assert 'window.dispatchEvent(new Event("hashchange"))' in login


def test_login_failure_stays_in_the_auth_dialog():
    source = script_source()
    login = function_source(source, "async function login(", "async function logout()")

    assert "error.status === 401" in login
    assert "Email or password is incorrect." in login
    assert "Sign-in is unavailable. Try again." in login
    assert 'elements.loginPassword.value = ""' in login
    assert 'toggleAttribute("aria-invalid", invalidCredentials)' in login
    assert 'appendMessage("agent", error.message)' not in login


def test_registration_reuses_tenant_bootstrap_without_exposing_its_token():
    source = script_source()
    register = function_source(source, "async function registerAccount()", "async function logout()")
    login = function_source(source, "async function login(", "async function registerAccount()")

    assert 'apiFetch("/api/auth/register"' in register
    assert "login(result.tenant_id)" in register
    assert "bootstrapToken" not in register
    assert "tenantId ? { tenant_id: tenantId } : {}" in login
    assert "tenant_id: state.tenantId" not in login
    assert 'state.authDisplayName = result.display_name || ""' in login


def test_model_catalog_discards_a_selection_that_is_no_longer_available():
    source = chat_controller_source()
    load_catalog = function_source(
        source,
        "  async loadModelCatalog()",
        "  findModel(",
    )

    assert "const selectedKey = chatState.selectedModel ? modelKey(chatState.selectedModel) : null" in load_catalog
    assert "modelKey(model) === selectedKey" in load_catalog
    assert "modelKey(model) === threadKey" in load_catalog
    assert '{ cache: "no-store" }' in load_catalog
    assert "chatState.selectedModel = chatState.selectedModel ||" not in load_catalog


def test_model_policy_denial_refreshes_catalog_and_disables_sending_without_a_model():
    source = chat_controller_source()
    create_thread = function_source(source, "  async createThread()", "  async loadThread(")
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")
    sync = function_source(source, "  syncComposer()", "  async sendThreadMessage(")

    assert 'error.body?.code === "model_policy_denied"' in create_thread
    assert "await this.loadModelCatalog()" in create_thread
    assert 'error.body?.code === "model_policy_denied"' in send
    assert "await this.loadModelCatalog()" in send
    assert "!chatState.selectedModel || uploadBlocked" in sync


def test_failed_upload_cannot_be_silently_dropped_from_a_message():
    source = chat_controller_source()
    sync = function_source(source, "  syncComposer()", "  async sendThreadMessage(")
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")

    assert 'upload.status !== "Ready"' in sync
    assert 'upload.status === "Failed"' in send
    assert 'this.network("Remove failed files before sending", "warning")' in send


def test_model_menu_only_renders_models_and_efforts_returned_by_the_api():
    html = (WEB_ROOT / "index.html").read_text()
    source = chat_controller_source()
    load_catalog = function_source(source, "  async loadModelCatalog()", "  findModel(")
    render_menu = function_source(source, "  renderModelMenu()", "  renderModelButton()")

    assert "data-model-option" not in html
    assert "Claude Sonnet 5" not in html
    assert 'chatState.modelCatalog = []' in load_catalog
    assert 'queryAll("[data-model-option]")' not in load_catalog
    assert "const groups = new Map()" in render_menu
    assert "for (const model of chatState.modelCatalog)" in render_menu
    assert "visibleModels" not in render_menu
    assert "label.textContent = providerLabel(provider)" in render_menu
    assert 'if (model.reasoning_efforts.length)' in render_menu
    assert 'empty.textContent = signedIn ? "No models available for this workspace." : "Sign in to load models."' in render_menu
    assert 'signIn.dataset.authDialogOpen = ""' in render_menu
    assert '["none"]' not in source
    assert '"glm-4.7": { name: "GLM 4.7"' in source
    assert 'zhipu: "Zhipu AI"' in source
    assert 'key.startsWith(`${id}-`)' in source


def test_new_chat_restores_the_catalog_default_reasoning_effort():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")
    new_chat = function_source(source, "  startNewChat(", "  async updateThread(")
    render_menu = function_source(source, "  renderModelMenu()", "  renderModelButton()")

    assert "chatState.modelCatalog.find((model) => modelKey(model) === selectedModelKey)" in new_chat
    assert "selected ? chatState.selectedModel?.reasoning_effort : model.reasoning_effort" in render_menu
    assert "this.renderModelMenu()" in load_thread and "this.renderModelButton()" in load_thread
    assert "this.renderModelMenu()" in new_chat and "this.renderModelButton()" in new_chat


def test_missing_thread_route_returns_to_new_chat():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")

    assert "if (error.status === 404)" in load_thread
    assert "chatState.loading = false;" in load_thread
    assert "this.startNewChat();" in load_thread
    assert '"Thread unavailable"' in load_thread
    assert "It may have been deleted, archived, or belong to another workspace." in load_thread


def test_creao_query_thread_deep_link_is_supported():
    source = chat_controller_source()
    read_route = function_source(
        source, "function threadIdFromHash()", "function updateThreadHash("
    )
    write_route = function_source(
        source, "function updateThreadHash(", "function modelKey("
    )

    assert 'new URLSearchParams(window.location.search).get("threadId")' in read_route
    assert 'url.searchParams.delete("threadId")' in write_route


def test_secret_capture_reloads_the_thread_to_resume_streaming():
    source = chat_controller_source()
    capture = function_source(source, "  renderSecretCapture()", "  renderAgentAppResult()")

    assert "const threadId = chatState.currentThreadId;" in capture
    assert "if (threadId) await this.loadThread(threadId, false);" in capture


def test_discover_only_opens_the_selected_published_agent():
    source = script_source()
    cards = function_source(source, "function routeCards(", "function handleRouteAction(")
    action = function_source(source, "function handleRouteAction(", "function setSidebarCollapsed(")
    skills = asset_source("skills-ui.js")
    skill_load = function_source(skills, "  async load()", "  filtered()")

    assert '.filter((agent) => agent.status === "published")' in cards
    assert 'action: `agent:${agent.id || agent.agent_id}`' in cards
    assert 'action.startsWith("agent:")' in action
    assert 'window.location.hash = `agents/${encodeURIComponent' in action
    assert "state.storeItems.map((item)" in cards
    assert 'action: `store:${item.id}`' in cards
    assert 'action.startsWith("store:")' in action
    assert 'window.location.hash = `skills/${encodeURIComponent' in action
    assert "skill.id === requestedId" in skill_load
    assert '`${state.workspaceSkills.length} installed`' in cards


def test_agent_updates_use_persisted_unread_notifications():
    source = script_source()
    cards = function_source(source, "function routeCards(", "function handleRouteAction(")
    load = function_source(source, "async function loadNotifications(", "async function markNotificationsRead(")
    mark_read = function_source(source, "async function markNotificationsRead(", "function startNotificationPolling(")
    open_notification = function_source(source, "async function openNotification(", "function startNotificationPolling(")

    assert "state.notifications.slice(0, 12)" in cards
    assert 'action: `notification:${notification.id}`' in cards
    assert 'apiFetch("/api/notifications?limit=20")' in load
    assert 'apiFetch("/api/notifications/unread-count")' in load
    assert 'apiFetch("/api/notifications/read-all", { method: "POST" })' in mark_read
    assert "/api/notifications/${encodeURIComponent(notificationId)}/read" in open_notification
    assert "selectRunFromHistory(notification.run_id)" in open_notification
    assert "const count = state.unreadNotificationCount;" in source


def test_workspace_route_manages_real_organization_resources():
    source = asset_source("workspace-ui.js")

    for route in [
        'this.api.get("/api/tenants/current")',
        'this.api.post("/api/workspaces"',
        'this.api.patch("/api/tenants/current"',
        'this.api.post("/api/tenants/current/invitations"',
        'this.api.delete(`/api/tenants/current/members/',
    ]:
        assert route in source
    assert 'localStorage.setItem("taroai.workspaceId", id)' in source
    assert 'new CustomEvent("taroai:workspace-changed"' in source
    assert "textContent = member.display_name" in source


def test_workspace_invitation_can_create_an_authenticated_session():
    source = script_source()
    accept = function_source(source, "async function acceptInvitation()", "async function logout()")

    assert 'apiFetch("/api/tenant-invitations/accept"' in accept
    assert "tenant_id: state.invitationTenantId" in accept
    assert 'state.accessToken = result.access_token || ""' in accept
    assert 'url.searchParams.delete("invite")' in accept
    assert 'state.authMode === "invite"' in source


def test_builtin_store_items_are_visible_and_installable_from_skills():
    source = asset_source("skills-ui.js")
    load = function_source(source, "  async load()", "  filtered()")
    install = function_source(source, "  async install()", "  async evaluate()")

    assert 'this.api.get("/api/store/items?kind=solution_pack")' in load
    assert '__store: true, origin: "builtin"' in load
    assert "renderStoreDetail(detail" in source
    assert '`/api/store/items/${encodeURIComponent(this.selected.id)}/install`' in install
    assert "expected_digest: this.selected.digest" in install
    assert 'apiFetch("/api/store/items?kind=solution_pack")' in script_source()


def test_thread_status_exposes_the_terminal_run_state():
    source = chat_controller_source()
    render_details = function_source(source, "  renderDetails()", "  renderAll()")

    assert "const terminalRun = [...chatState.events]" in render_details
    assert 'eventType(event).startsWith("run.")' in render_details
    assert "terminalStatus[0].toUpperCase()" in render_details
    assert "this.refs.moreButton.hidden = !chatState.currentThreadId" in render_details


def test_thread_bootstrap_can_rebuild_a_completed_assistant_message_from_events():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")

    assert 'eventType(event) === "assistant.message.completed"' in load_thread
    assert "completedAssistant.message_id" in load_thread
    assert "!chatState.messages.some" in load_thread
    assert 'role: "assistant"' in load_thread
    assert "terminalEvent" in load_thread
    assert "? eventType(terminalEvent).split" in load_thread


def test_streamed_reply_recovers_after_a_discarded_tool_preamble():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")
    stream = function_source(source, "  applyStreamEvent(frame)", "  captureArtifactFromEvent(event)")

    assert 'eventType(event) === "assistant.stream.reset"' in load_thread
    assert 'type === "assistant.stream.reset"' in stream
    assert "chatState.messages.filter((message) => message.id !== streamId)" in stream


def test_chat_collapses_runtime_events_into_one_thinking_state():
    source = chat_controller_source()
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    render = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert 'thinking.className = "chat-thinking"' in render
    assert 'label.textContent = "Thinking"' in render
    assert "if (!thoughtRendered && !liveAssistantId)" in render
    assert "renderExecutionCard" not in source
    assert "this.renderApprovalCard(messageRunId)" in render
    assert "currentApprovalCard = this.renderApprovalCard()" in render
    assert ".conversation-day" in styles
    assert ".agent-trace-stack" not in styles


def test_waiting_for_user_renders_backend_response_options():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")
    stream = function_source(source, "  applyStreamEvent(frame)", "  captureArtifactFromEvent(event)")
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert 'eventType(event) === "agent.waiting_for_user"' in load_thread
    assert 'activeRun?.status || ""' in load_thread
    assert 'arrayFrom(eventPayload(waitingEvent), "options")' in load_thread
    assert '["queued", "ready"].includes(status)' in send
    assert 'arrayFrom(payloadDetail, "options")' in stream
    assert 'type === "agent.steering.applied"' in stream
    assert "!chatState.running ? this.renderInputRequest() : null" in conversation
    assert 'arrayFrom(payload, "questions")' in source
    assert 'submit.dataset.inputSubmit = ""' in source
    assert 'answers.join(" · ")' in source


def test_composer_waits_for_upload_scanning_before_sending():
    source = chat_controller_source()
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")

    assert '!["Ready", "Failed"].includes(upload.status)' in send
    assert "Wait for files to finish uploading and scanning" in send


def test_composer_has_one_accessible_state_owner_and_clear_running_copy():
    source = chat_controller_source()
    keydown = function_source(source, "  onKeydown(event)", "  onChange(event)")
    composer = function_source(source, "  syncComposer()", "  async sendThreadMessage(")
    legacy = script_source()
    styles = asset_source("styles.css")

    assert "event.isComposing || event.keyCode === 229" in keydown
    assert 'const activeRun = chatState.running && !assistantResponseReady()' in composer
    assert '"Add a follow-up while Taroai is working..."' in composer
    assert '"Queue follow-up"' in composer
    assert 'this.refs.dropzone.dataset.composerState' in composer
    assert "if (window.__taroaiThreadChat) return" in legacy
    assert ".composer-surface:focus-within" in styles
    assert "min-height: 158px" in styles
    assert "max-height: min(150px, 35dvh)" in styles
    assert '.workspace-shell[data-chat-state="thread"] .composer-surface' not in styles
    assert '.workspace-shell[data-chat-state="thread"] #composer-input' not in styles
    assert ".composer-create-agent" in styles
    assert "@media (pointer: coarse)" in styles


def test_composer_leaves_tool_choice_to_the_agent_model():
    source = chat_controller_source()
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert "AGENT_TOOL_INTENTS" not in source
    assert "requiresAgentTools" not in source
    assert 'createIntent ? "autonomous" : "chat"' in send
    assert 'message.kind === "agent" ? "agent" : null' in source
    assert "const shouldFollowOutput" in conversation
    assert "this.refs.chatScroll" not in conversation


def test_streaming_only_follows_when_the_reader_is_near_the_bottom():
    source = chat_controller_source()
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    distance = conversation.index("const shouldFollowOutput")
    replace = conversation.index("this.refs.conversation.replaceChildren()")
    guard = conversation.index("if (shouldFollowOutput)", replace)
    scroll = conversation.index(
        "this.refs.conversation.scrollTop = this.refs.conversation.scrollHeight",
        guard,
    )

    assert distance < replace < guard < scroll


def test_composer_sends_display_text_and_skill_bindings_separately():
    source = chat_controller_source()
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")

    assert "content: submittedContent" in send
    assert "display_content: displayContent" in send
    assert 'timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"' in send
    assert 'content: displayContent' in send
    assert 'createIntent && createIntent !== "workflow"' in send
    assert 'skill_ids: optimistic.resource_refs.filter(({ type }) => type === "skill")' in send
    assert 'resource_refs: optimistic.resource_refs.filter(({ type }) => type !== "skill")' in send


def test_grouped_connector_capabilities_keep_the_connector_resource_type():
    mentions = asset_source("mentions.js")
    candidate_type = function_source(
        mentions, "function candidateType(", "function normalizeOne("
    )

    assert "const raw = fallback || candidate.type" in candidate_type
    assert "normalizeOne(candidate, candidate.__type)" in mentions


def test_failed_run_shows_a_safe_retry_notice():
    source = chat_controller_source()
    render = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert '["run.failed", "run.timed_out"].includes(eventType(event))' in render
    assert "Any completed output is preserved. Retry the message or choose another model." in render
    assert "This workspace reached its model usage limit." in render
    assert "The selected model provider did not complete the response." in render
    assert "The selected model is not allowed for this request." in render
    assert "A workflow task failed." in render
    assert "Nothing was changed." not in render


def test_failed_run_exposes_real_recovery_actions():
    source = chat_controller_source()
    render = function_source(source, "  renderConversation()", "  renderSuggestions(")
    retry = function_source(source, "  async retryRun(", "  continueFailedRun(")
    restore = function_source(source, "  restoreMessageToComposer(", "  retryMessage(")

    assert "retry.dataset.runRetry" in render
    assert "continueButton.dataset.runContinue" in render
    assert "newChat.dataset.newChat" in render
    assert "model_budget_exceeded" in render
    assert "cost_budget_exhausted" in render
    assert "model_policy_denied" in render
    assert "`/api/runs/${encodeURIComponent(runId)}/retry`" in retry
    assert '{ reason_code: "operator_retry" }' in retry
    assert "if (!runId || chatState.running) return" in retry
    assert "chatState.resourceRefs = refs.filter" in restore
    assert 'item.type === "browser_profile"' in restore
    assert "message.kind === \"workflow\"" in restore
    assert 'dispatchStatus(item) === "failed"' in source
    assert 'statusValue === "failed" && message.optimistic' in source


def test_temporary_worker_retry_is_visible_in_chat():
    source = chat_controller_source()
    thought = function_source(source, "  renderThoughtCard(", "  setInputRequest(")

    assert 'runStatus === "retrying"' in source
    assert "Retrying…" in source
    assert "hit a temporary error · retrying" in source
    assert "Temporary execution error · retrying" in thought
    assert "Retry resumed" in thought
    assert "Model connection interrupted · retrying" in source


def test_policy_refusal_replaces_the_generic_failure_notice():
    source = chat_controller_source()
    render = function_source(source, "  renderConversation()", "  renderSuggestions(")
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()

    assert 'eventType(event) === "classifier_refusal"' in render
    assert 'eventType(event) === "policy.blocked"' in render
    assert "Safety filter declined this request" in render
    assert "The model's safety filter flagged this request." in render
    assert '"Request blocked"' in render
    assert "This request cannot be completed under the current policy." in render
    assert ".inline-system-warning" in styles
    assert "} else if (" in render


def test_run_activity_renders_only_useful_safe_events():
    source = chat_controller_source()
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    describe = function_source(source, "  describeActivityEvent(event)", "  renderSearchCard(")
    render_thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")

    assert 'card.className = "chat-thought"' in render_thought
    assert 'kind: "thinking"' in describe
    assert "duration_ms" not in describe
    assert 'eventType(event) === "model.operation.recorded"' in render_thought
    assert 'steps.push({ text: "Prepared the answer." })' not in render_thought
    assert "const keyedSteps = new Map()" in render_thought
    assert "steps[keyedSteps.get(step.key)] = step" in render_thought
    assert "detail.hidden = false" in render_thought
    assert 'step.kind === "thinking"' in render_thought
    assert 'type.startsWith("tool_call.")' in source
    assert 'key: `tool:${toolActivityKey(event) || eventSequence(event)}`' in source
    assert "runActivityEvents()" in source
    assert 'control.matches("[data-thought-toggle]")' not in source
    assert ".chat-thought-detail" in styles
    assert ".chat-thought-step.is-thinking" in styles


def test_activity_timeline_pairs_safe_lifecycle_events_in_sequence():
    source = chat_controller_source()
    describe = function_source(source, "  describeActivityEvent(event)", "  renderSearchCard(")
    thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    for event_type in ["model.operation.started", "model.operation.completed", "model.operation.failed"]:
        assert event_type in describe
    assert '["decide", "respond_or_act", "respond"].includes(operation)' in describe
    assert 'status === "started"' in describe
    assert 'operation === "verify"' in describe
    assert '"verification:current"' in describe
    assert '`model:${p.operation_id || eventSequence(event)}`' in describe
    assert 'kind: "thinking", transient: true' in describe
    assert '["agent.conversation.loaded", "agent.loop.started", "agent.cycle.started"]' in describe
    assert 'type === "run.attachments.materialized"' in describe
    assert 'Prepared uploaded file · ${filename}' in describe
    assert "return leftSequence - rightSequence" in source
    assert "steps[keyedSteps.get(step.key)] = step" in thought
    assert "latestStep = step" in thought
    assert "if (step.transient) continue" in thought
    assert "hasModelLifecycle" in thought
    assert "this.renderSearchCard(runId, step.actionKey)" in thought
    assert "this.renderCodeCard(runId, step.actionKey)" in thought
    assert "this.renderToolCards(runId, step.actionKey)" in thought
    assert "rationale" not in describe and "feedback" not in describe
    assert "this.renderSearchCard(messageRunId)" not in conversation
    assert "this.renderCodeCard(messageRunId)" not in conversation


def test_completed_response_stops_presenting_backend_cleanup_as_thinking():
    source = chat_controller_source()
    stream = function_source(source, "  applyStreamEvent(frame)", "  captureArtifactFromEvent(event)")
    thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")
    composer = function_source(source, "  syncComposer()", "  async sendThreadMessage(")
    details = function_source(source, "  renderDetails()", "  renderAll()")

    assert 'this.network("Response ready", "success")' in stream
    assert 'eventType(event) === "assistant.message.completed"' not in thought
    assert "&& !responseReady" in thought
    assert "chatState.running && !responseReady && !currentApprovalCard" in conversation
    assert "chatState.running && !assistantResponseReady()" in composer
    assert '? "Response ready"' in details
    assert 'classList.toggle("running", chatState.running && !responseReady)' in details


def test_web_search_results_render_as_a_structured_source_block():
    source = chat_controller_source()
    search = function_source(source, "  renderSearchCard(runId = chatState.currentRunId, actionKey = null)", "  renderThoughtCard(runId = chatState.currentRunId)")
    thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")

    assert '"tool_call.started", "tool_call.completed", "tool_call.failed", "tool_call.cancelled", "tool_call.approval_required"' in search
    assert 'eventType(item) === "agent.decision.created"' in search
    assert 'eventType(item) === "agent.observation.recorded"' in search
    assert "result?.output || {}" in search
    assert 'waiting ? "Search needs approval"' in search
    assert 'cancelled ? "Search cancelled"' in search
    assert 'if (running || waiting || cancelled || failed)' in search
    assert 'details.setAttribute("aria-busy", "true")' in search
    assert 'body.className = "chat-search-results"' in search
    assert 'query.className = "chat-search-query"' in search
    assert 'sourceIcon.className = "chat-search-source-icon"' in search
    assert 'tool === "web.fetch"' in source
    assert 'p.summary || fallback' in source
    assert 'link.rel = "noopener noreferrer"' in search
    assert 'note.textContent = "Source details were not retained for this run."' in search
    assert "this.renderSearchCard(runId, step.actionKey)" in thought
    assert ".chat-search-card" in asset_source("styles.css")
    assert ".chat-search-results" in asset_source("styles.css")


def test_sandbox_command_renders_as_a_structured_code_block():
    source = chat_controller_source()
    code = function_source(source, "  renderCodeCard(runId = chatState.currentRunId, actionKey = null)", "  renderThoughtCard(runId = chatState.currentRunId)")
    output_loader = function_source(source, "  async loadCommandOutput(event)", "  renderApprovalCard(runId = chatState.currentRunId)")
    thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")

    assert 'value.tool_name === "sandbox.command"' in code
    assert "const commandCopy = commandActivity(commandPayload)" in code
    assert "commandSubject(command, commandPayload.command_kind)" in code
    assert 'kind !== "run_command"' in source
    assert "title.textContent = activity" in code
    assert 'detail.className = "chat-tool-summary-detail"' in code
    assert "detail.textContent = subject" in code
    assert 'read_file: { started: "Reading file", completed: "Read file"' in source
    assert 'list_files: { started: "Listing files", completed: "Listed files"' in source
    assert 'search_files: { started: "Searching files", completed: "Found files"' in source
    assert 'run_command: { started: "Running command", completed: "Ran command"' in source
    assert "decision?.tool_input?.command" in code
    assert 'eventType(item) === "agent.observation.recorded"' in code
    assert 'item === decision' in code
    assert 'details.setAttribute("aria-busy", "true")' in code
    assert "failed || waiting" in code
    assert "for (const [index, decision] of decisions.entries())" in code
    assert "/api/storage/objects/${encodeURIComponent(storageObjectId)}/content" in output_loader
    assert "stdout: safeCommandStream(output.stdout)" in output_loader
    assert "stderr: safeCommandStream(output.stderr)" in output_loader
    assert "output.command" not in output_loader
    assert '[["stdout", streams.stdout], ["stderr", streams.stderr]]' in code
    assert "stream.textContent = value" in code
    assert "… output truncated …" in source
    assert "fragment.append(details)" in code
    assert "bindDisclosure(" in code
    assert "chatState.disclosureOpen" in source
    assert "this.renderCodeCard(runId, step.actionKey)" in thought
    assert ".chat-code-card" in asset_source("styles.css")


def test_other_tools_render_from_safe_lifecycle_events():
    source = chat_controller_source()
    tools = function_source(
        source,
        "  renderToolCards(runId = chatState.currentRunId, actionKey = null)",
        "  renderUiElement(",
    )
    thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")

    assert 'type.startsWith("tool_call.")' in tools
    assert "p.action_id || p.step_id" in tools
    assert 'eventType(item) === "agent.observation.recorded"' in tools
    assert "observationPayload.safe_error" in tools
    assert "payload.result || observationPayload.result" in tools
    assert '["web.search", "sandbox.command", "tool.search", "ui.render"]' in tools
    assert 'eventType(item) !== "agent.decision.created"' in tools
    assert "decision?.tool_input" in tools
    assert '[["Input", input], ["Result", result], ["Error", error]]' in tools
    assert "safeToolInput(value, tool)" in source
    assert "password|passwd|secret|token" in source
    assert 'tool === "browser.action" && /^(text|value)$/i.test(key)' in source
    assert "retry.dataset.runRetry" in tools
    assert "retry.dataset.messageRetry" in tools
    assert "continueButton.dataset.runContinue" in tools
    assert "newChat.dataset.newChat" in tools
    assert '`Loading ${skillName}`' in tools
    assert '`Used ${skillName}`' in tools
    assert '`${skillFileCount} file${skillFileCount === 1 ? "" : "s"} ready`' in tools
    assert "this.renderToolCards(runId, step.actionKey)" in thought


def test_historical_run_blocks_stay_with_their_assistant_message():
    source = chat_controller_source()
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")
    activity = function_source(
        source,
        "  runActivityEvents(runId = chatState.currentRunId)",
        "  describeActivityEvent(event)",
    )
    thought = function_source(
        source,
        "  renderThoughtCard(runId = chatState.currentRunId)",
        "  renderConversation()",
    )

    assert 'eventType(event) !== "assistant.message.completed"' in conversation
    assert "eventPayload(event).message_id" in conversation
    assert "const messageRunId = assistantRunIds.get(message.id) || fallbackRunId" in conversation
    assert "this.renderThoughtCard(messageRunId)" in conversation
    assert "this.renderSearchCard(runId, step.actionKey)" in thought
    assert "this.renderCodeCard(runId, step.actionKey)" in thought
    assert "const lastRunId = runId ||" in activity
    assert "chatState.running && runId === chatState.currentRunId" in thought


def test_resumed_run_blocks_follow_the_user_reply_instead_of_the_first_assistant_message():
    source = chat_controller_source()
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert "const activityMessageIds = new Map()" in conversation
    assert "activityMessageIds.set(runId, messageId)" in conversation
    assert "activityMessageIds.set(chatState.currentRunId, liveAssistantId)" in conversation
    assert "message.id === activityMessageIds.get(messageRunId)" in conversation


def test_ui_render_events_replace_blocks_and_use_safe_dom_text():
    source = chat_controller_source()
    renderer = function_source(source, "  renderUiElement(", "  renderThoughtCard(runId = chatState.currentRunId)")
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert 'eventType(event) !== "ui_render"' in renderer
    assert "this.runActivityEvents(runId)" in renderer
    assert "blocks.set(payload.blockId, payload)" in renderer
    assert 'element.type === "Card"' in renderer
    assert 'element.type === "Stack"' in renderer
    assert "node.textContent" in renderer
    assert "appendMarkdown(node, String(props.text" in renderer
    assert "node.innerHTML" not in renderer
    assert "this.renderUiCards(messageRunId)" in conversation
    assert "!renderedRunIds.has(chatState.currentRunId)" in conversation
    assert "this.renderUiCards()" in conversation
    assert ".chat-ui-render" in asset_source("styles.css")


def test_agent_brain_memory_panel_loads_real_user_memories():
    source = asset_source("brain-ui.js")
    render = function_source(source, "  renderMemory(root)", "  click(event)")

    assert "/api/memory?scope_type=user&scope_id=${user}" in source
    assert "this.memories = memories.status" in source
    assert "memory.content" in render
    assert "New memories always require your approval." in render
    assert 'this.prefill("请记住：")' in source
    assert 'this.api.delete(`/api/memory/${encodeURIComponent(memoryId)}`)' in source
    assert "this.memories.filter" in source
    assert ".brain-memory-ledger" in asset_source("styles.css")


def test_agent_brain_can_create_and_enable_an_mcp_server():
    source = asset_source("brain-ui.js")

    assert 'data-mcp-create>Add MCP server' in source
    assert 'type: "mcp_server"' in source
    assert 'auth_mode: token ? "mcp" : "none"' in source
    assert '/mcp-credential`' in source
    assert 'metadata: { mcp: { url:' in source
    assert 'this.api.post(`/api/connectors/${encodeURIComponent(created.id)}/enable`' in source
    assert 'this.toast("MCP server connected", "success")' in source


def test_workflow_approval_uses_the_structured_preview():
    source = chat_controller_source()
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    approval = function_source(source, "  renderApprovalCard(runId = chatState.currentRunId)", "  async resolveApproval(")
    resolution = function_source(source, "  async resolveApproval(", "  renderWorkflowProgress(runId = chatState.currentRunId)")

    assert 'eventType(item) === "workflow_preview"' in approval
    assert "strong.textContent = actionComplete" in approval
    assert '"Review workflow"' in approval
    assert 'No task has run yet.' in approval
    assert 'list.className = "chat-workflow-preview"' in approval
    assert "await this.loadThread(chatState.currentThreadId, false)" in resolution
    assert "this.startEventStream()" in resolution
    assert '"Awaiting approval"' in source
    assert ".chat-workflow-preview" in styles


def test_connector_action_manifest_is_reviewed_then_applied_from_chat():
    source = chat_controller_source()
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    approval = function_source(source, "  renderApprovalCard(runId = chatState.currentRunId)", "  async resolveApproval(")
    resolution = function_source(source, "  async resolveApproval(", "  renderWorkflowProgress(runId = chatState.currentRunId)")

    assert 'type === "action_approval"' in approval
    assert 'applied: "Action applied"' in approval
    assert '["approved", "applied"].includes(actionStatus) ? "✓"' in approval
    assert 'summary.className = "chat-approval-preview"' in approval
    assert '"Approve & run"' in approval
    assert "/action-manifests/${encodeURIComponent(approvalId)}" in resolution
    assert "`${base}/${decision}`" in resolution
    assert '`${base}/apply`' in resolution
    assert 'kind === "connector_action"' in resolution
    assert "this.abortStream()" in resolution
    assert ".chat-approval-preview" in styles
    assert ".chat-approval:is(.is-approved, .is-applied)" in styles


def test_chat_approval_resolution_stays_visible():
    source = chat_controller_source()
    styles = asset_source("styles.css")
    approval = function_source(source, "  renderApprovalCard(runId = chatState.currentRunId)", "  async resolveApproval(")
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")

    assert '["approval.resolved", "approval.rejected"].includes(type)' in approval
    assert 'eventType(item) === "approval.requested"' in approval
    assert "eventPayload(item).approval_id === resolutionId" in approval
    assert 'approved: "Approved. The agent continued."' in approval
    assert 'rejected: "Rejected. The action was not run."' in approval
    assert "this.renderApprovalCard(messageRunId)" in conversation
    assert "this.renderWorkflowProgress(messageRunId)" in conversation
    assert "const runEvents = this.runActivityEvents(runId)" in approval
    assert '"approval.execution_updated"' not in conversation
    assert ".chat-approval:is(.is-approved, .is-applied)" in styles


def test_run_lifecycle_cards_precede_the_final_answer():
    conversation = function_source(chat_controller_source(), "  renderConversation()", "  renderSuggestions(")
    message = conversation.index("this.refs.conversation.append(this.renderMessage(message))")

    assert conversation.index("this.renderApprovalCard(messageRunId)") < message
    assert conversation.index("this.renderWorkflowProgress(messageRunId)") < message


def test_pending_approval_is_not_hidden_by_a_late_previous_result():
    approval = function_source(
        chat_controller_source(),
        "  renderApprovalCard(runId = chatState.currentRunId)",
        "  async resolveApproval(",
    )

    assert "const resolvedApprovalIds = new Set(" in approval
    assert "const pendingEvent = [...approvalEvents].reverse().find" in approval
    assert "!resolvedApprovalIds.has(approvalId)" in approval
    assert "const event = pendingEvent || approvalEvents.at(-1)" in approval


def test_sidebar_thread_refresh_uses_the_chat_thread_loader():
    source = chat_controller_source()
    click = function_source(source, "  onClick(event)", "  onInput(event)")

    assert '"[data-run-history-refresh], [data-thread-refresh]' in source
    assert 'control.matches("[data-thread-refresh]")' in click
    assert "return this.loadThreads()" in click


def test_workflow_progress_controls_real_workers_and_lifecycle():
    source = chat_controller_source()
    styles = asset_source("styles.css")
    event_type = function_source(source, "function eventType(event)", "function eventPayload(event)")
    progress = function_source(
        source,
        "  renderWorkflowProgress(runId = chatState.currentRunId)",
        "  async toggleWorkflowTaskMessages(",
    )

    assert 'type === "workflow_completed" ? "workflow.completed" : type' in event_type
    assert 'inspect.textContent = "View worker"' in progress
    assert "const runEvents = this.runActivityEvents(runId)" in progress
    assert "if (!executionStarted) return null" in progress
    assert "toggleWorkflowTaskMessages(workflowId, task.id" in progress
    assert '["running", "paused"].includes(status)' in progress
    assert 'this.controlWorkflow(workflowId, "cancel")' in progress
    assert "/tasks/${encodeURIComponent(taskId)}/messages" in source
    assert "/api/workflows/${encodeURIComponent(workflowId)}/${action}" in source
    assert ".chat-workflow-transcript" in styles
    assert ".chat-workflow-actions" in styles


def test_agent_result_deep_links_to_the_created_draft_and_shows_mounted_files():
    chat = chat_controller_source()
    agents = asset_source("agents-ui.js")
    styles = asset_source("styles.css")

    assert "window.location.hash = `agents/${encodeURIComponent(payload.agentId)}`" in chat
    assert 'window.location.hash.replace(/^#/, "").split("/")[1]' in agents
    assert "/api/agents/${encodeURIComponent(id)}/files" in agents
    assert 'class="agent-mounted-files"' in agents
    assert "agent.write_autonomy" in agents
    assert 'list(agent.skill_bindings, "items").length} skills' in agents
    assert ".agent-mounted-files" in styles


def test_live_run_shows_compact_activity_without_rebuilding_the_conversation():
    source = chat_controller_source()
    render_thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")

    assert "if (!responseReady && latestStep?.transient)" in render_thought
    assert 'card.classList.toggle("is-live", live)' in render_thought
    assert "detail.hidden = false" in render_thought
    assert 'row.classList.add("is-thinking")' in render_thought
    assert "disclosureKey" not in render_thought


def test_streaming_batches_dom_work_and_preserves_terminal_statuses():
    controller = chat_controller_source()
    api = asset_source("chat-api.js")
    stream = function_source(controller, "  applyStreamEvent(frame)", "  captureArtifactFromEvent(event)")

    assert "if (isTextDelta) this.scheduleConversationRender()" in stream
    assert '"run.cancelled": "cancelled"' in stream
    assert "`${runSubject()} stopped`" in stream
    assert "`Stopping ${runSubject().toLowerCase()}…`" in controller
    assert 'if (terminalStatus === "succeeded") this.loadSuggestions()' in stream
    assert "framesSinceYield === 24" in api
    assert "await new Promise((resolve) => setTimeout(resolve, 0))" in api


def test_new_thread_draft_is_removed_after_thread_creation():
    source = chat_controller_source()
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(content)")

    assert "const startedWithoutThread = !chatState.currentThreadId" in send
    assert 'localStorage.removeItem("taroai.threadDraft.new")' in send


def test_thought_activity_avoids_misleading_wall_clock_duration():
    source = chat_controller_source()
    render_thought = function_source(source, "  renderThoughtCard(runId = chatState.currentRunId)", "  renderConversation()")

    assert "pausedMilliseconds" not in render_thought
    assert "Thought for" not in render_thought


def test_chat_and_agent_runs_keep_distinct_status_copy():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")
    stream = function_source(source, "  applyStreamEvent(frame)", "  captureArtifactFromEvent(event)")

    assert 'currentRunMode: "chat"' in source
    assert "activeRun?.mode" in load_thread
    assert 'type === "run.created"' in stream
    assert 'payloadDetail.mode' in stream
    assert '"Response is streaming"' in source
    assert '"Live · agent is working"' not in source
    assert 'if (chatState.running) this.network(workingStatus(true), "active")' in stream


def test_mobile_chat_has_an_accessible_navigation_drawer():
    root = parse_index()
    source = script_source()
    styles = asset_source("styles.css")
    sidebar = root.find_by_attr("id", "app-sidebar")
    trigger = root.find_by_attr("data-mobile-nav-toggle", "")

    assert sidebar is not None
    assert trigger is not None
    assert trigger.attrs.get("aria-controls") == "app-sidebar"
    assert trigger.attrs.get("aria-expanded") == "false"
    assert "function setMobileNavOpen(open)" in source
    assert 'toggleAttribute("inert", state.mobileNavOpen)' in source
    assert 'event.key === "Escape" && state.mobileNavOpen' in source
    assert ".workspace-shell.is-mobile-nav-open .app-sidebar" in styles
    assert "transform: translateX(-100%)" in styles
    assert (
        '.workspace-shell[data-chat-state="empty"] .topbar-action:not(.mobile-nav-toggle)'
        in styles
    )


def test_motion_and_composer_growth_respect_user_and_content():
    styles = asset_source("styles.css")

    assert "@starting-style" in styles
    assert "display 140ms allow-discrete" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "*::before" in styles and "*::after" in styles
    assert "animation-duration: 0.01ms !important" in styles
    assert "max-height: min(150px, 35dvh)" in styles
    assert ".is-chat-entering" in styles
    assert ".chat-dialog[open]" in styles
    assert "visibility 0s linear 150ms" in styles


def test_assistant_streaming_shows_a_live_caret_and_paragraph_blocks():
    source = chat_controller_source()
    styles = (WEB_ROOT / "assets" / "styles.css").read_text()
    render_message = function_source(source, "  renderMessage(message)", "  runActivityEvents(runId = chatState.currentRunId)")

    assert 'body.className = "message-body"' in render_message
    assert "appendMarkdown(body, visibleMessageContent(message))" in render_message
    assert 'document.createElement("pre")' in source
    assert 'document.createElement("table")' in source
    assert 'link.target = "_blank"' in source
    assert 'link.href = match[5] || match[6]' in source
    assert 'link.textContent = match[4] || match[6]' in source
    assert "function appendInlineText" in source
    assert 'article.classList.add("is-streaming")' in render_message
    assert ".message-agent.is-streaming .message-body p::after" in styles
    assert "@keyframes message-caret" in styles


def test_browser_profile_is_explicit_and_message_actions_match_the_chat_surface():
    source = chat_controller_source()
    capabilities = function_source(source, "  async loadCapabilities()", "  renderCreationCapabilities()")
    new_chat = function_source(source, "  startNewChat(", "  async updateThread(")
    render_message = function_source(source, "  renderMessage(message)", "  runActivityEvents(runId = chatState.currentRunId)")

    assert "profiles.find((item) => item.is_default)" not in capabilities
    assert "savedId" not in capabilities
    assert "chatState.browserProfile = null" in new_chat
    for label in ["Good response", "Bad response", "More options"]:
        assert label in render_message


def test_chat_actions_use_the_backend_route_contracts_directly():
    chat = chat_controller_source()
    speech = asset_source("speech-ui.js")

    for route in [
        "/api/speech/capabilities",
        "/api/speech/transcribe",
        "/api/speech/summarize",
        "/api/speech/synthesize",
        "/shares`,",
        "/continue`,",
        "/approvals${suffix}",
    ]:
        assert route in speech + chat
    for obsolete in [
        "/api/speech/transcriptions",
        "/api/speech/synthesis",
        "/queue/dispatch",
        "/messages/${encodeURIComponent(messageId)}/steer",
        "/approvals/${decision}",
    ]:
        assert obsolete not in speech + chat

    assert "capability.transcription" in speech
    assert "capability.summarization" in speech
    assert "capability.text_to_speech" in speech


def test_terminal_threads_do_not_reconnect_the_event_stream():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async restoreFromHash()")
    stream = function_source(source, "  startEventStream()", "  applyStreamEvent(frame)")

    assert "/bootstrap?event_limit=500`" in load_thread
    assert "activeRun?.status" in load_thread
    assert "!terminalOutcome && (" in load_thread
    assert 'terminalOutcome === "succeeded"' in load_thread
    assert "!chatState.running || chatState.streamAbort" in stream
    assert "threadId === chatState.currentThreadId && chatState.running" in stream


def test_share_dialog_keeps_the_thread_it_was_created_for():
    source = chat_controller_source()
    dialog = function_source(source, "  openShareDialog(share)", "  openCreateAgentDialog()")

    assert "const threadId = chatState.currentThreadId" in dialog
    assert "encodeURIComponent(threadId)" in dialog


def test_shared_chat_api_expires_only_unauthorized_sessions():
    api = asset_source("chat-api.js")
    main = script_source()
    notifier = function_source(api, "function notifyAuthExpired(status)", "function fileAsBase64")
    handler = function_source(main, "function handleAuthExpired(status)", "window.addEventListener(\"taroai:auth-expired\"")

    assert 'status !== 401' in notifier
    assert 'new CustomEvent("taroai:auth-expired")' in notifier
    assert "sessionStorage" not in notifier
    assert "localStorage" not in notifier
    assert 'status === 401 && state.accessToken' in handler
    assert 'status === 403' not in handler
    assert 'new CustomEvent("taroai:auth-changed"' in handler


def test_feature_modules_share_one_chat_state_instance():
    match = re.search(r'from "(\./chat-controller\.js\?v=[^"]+)"', script_source())
    assert match is not None
    version = match.group(1)
    for name in ["agents-ui.js", "skills-ui.js", "speech-ui.js"]:
        assert version in asset_source(name)


def test_skill_package_actions_use_the_manifest_version():
    source = asset_source("skills-ui.js")

    assert "record?.package?.manifest?.version" in source
    assert "this.packageRecord.package.version" not in source


def test_agent_run_pins_the_selected_version_for_draft_preview():
    source = asset_source("agents-ui.js")
    select = function_source(source, "  async select(id)", "  renderInspector(loading = false)")
    run = function_source(source, "  async run(form)", "  async openDraft")

    assert select.index("definition.latest_version") < select.index("definition.published_version")
    assert "/files?version=${encodeURIComponent(this.selected.latest_version)}" in select
    assert "status: activeVersion?.status || definition.status" in select
    assert "this.detail?.version || this.selected.latest_version" in run
    assert "version: Number(version)" in run


def test_agent_cards_show_a_newer_unpublished_version_as_draft():
    cards = function_source(asset_source("agents-ui.js"), "  renderCards(", "  async select(id)")

    assert 'agent.latest_version !== agent.published_version ? "draft"' in cards
    assert "${status} · v${version}" in cards


def test_reviewed_agent_draft_can_be_published_without_creating_another_version():
    source = asset_source("agents-ui.js")

    assert "data-agent-publish-version" in source
    assert "publishDraft(button.dataset.agentPublishVersion)" in source
    assert "/versions/${encodeURIComponent(version)}/publish" in source
    assert "model_policy: {}" in source


def test_agent_editor_pins_installed_skills_and_explicit_sandbox_access():
    source = asset_source("agents-ui.js")
    editor = function_source(source, "  async openDraft(", "  async saveDraft(")
    save = function_source(source, "  async saveDraft(", "  async restore(")

    assert "/api/workspaces/${workspace}/skills" in editor
    assert 'checkbox.name = "skill_binding"' in editor
    assert 'name="sandbox_enabled"' in editor
    assert 'this.api.get("/readyz")' in editor
    assert "readiness.value?.checks?.sandbox" in editor
    assert "sandboxNetworkModes(sandboxReadiness)" in editor
    assert '<option value="allowlist">' not in editor
    assert '["disabled", "open"]' in source
    assert ': ["disabled"]' in source
    assert 'data.getAll("skill_binding")' in save
    assert "version: skill.version || skill.installed_version" in save
    assert 'sandbox_enabled: data.has("sandbox_enabled")' in save
    assert "/api/connectors?workspace_id=${workspace}" in editor
    assert "/api/knowledge-bases?workspace_id=${workspace}" in editor
    assert 'data.getAll("connector_binding")' in save
    assert 'data.getAll("knowledge_binding")' in save
    assert ")).values()).filter" in editor
    assert "))).values()).filter" not in editor


def test_agent_mutations_refresh_the_shared_homepage_cards():
    source = asset_source("agents-ui.js")

    for start, end in [
        ("  async change(event)", "  async exportAgent"),
        ("  async saveDraft(form, dialog, agent = {})", "  async restore"),
        ("  async restore(version)", "  async publishDraft"),
        ("  async publishDraft(version)", "  async evaluateVersion"),
    ]:
        assert 'new CustomEvent("taroai:agents-changed")' in function_source(
            source, start, end
        )


def test_agent_schedule_tab_connects_the_trigger_lifecycle():
    source = asset_source("agents-ui.js")

    assert 'data-agent-tab="schedule"' in source
    assert 'this.api.get("/api/triggers")' in source
    assert 'await this.api.post("/api/triggers"' in source
    assert "data-trigger-toggle" in source
    assert "data-trigger-delete" in source
    assert "agent-schedule-delete" in source
    assert 'this.switchTab("schedule")' in source


def test_agent_api_tab_exposes_published_contract_and_scoped_key_lifecycle():
    source = asset_source("agents-ui.js")
    api_panel = function_source(source, "  apiMarkup(agent, schema)", "  async copyApiValue")
    create_key = function_source(source, "  openApiKeyDialog()", "  async revokeApiKey")
    revoke_key = function_source(source, "  async revokeApiKey", "  scheduleMarkup(")

    assert 'data-agent-tab="api"' in source
    assert "agent.published_version" in api_panel
    assert "API Trigger" in api_panel
    assert "Publish this Agent to enable API access" in api_panel
    assert "/api/v1/apps/${encodeURIComponent(id)}/runs" in api_panel
    assert "const request = { inputs: schemaExample(schema) }" in api_panel
    assert "Input JSON schema" in api_panel
    assert "/api/api-keys?agent_id=${encodeURIComponent(id)}" in source
    assert 'this.api.post("/api/api-keys"' in create_key
    assert "result.rawToken" in create_key
    assert "/api/api-keys/${encodeURIComponent(button.dataset.agentApiKeyRevoke)}" in revoke_key
    assert ".agent-api-panel" in asset_source("styles.css")


def test_account_settings_aggregates_agent_api_keys_without_broad_key_creation():
    root = parse_index()
    menu = root.find_by_attr("data-account-menu", "")
    settings_button = root.find_by_attr("data-account-settings", "")
    dialog = root.find_by_attr("id", "settings-dialog")
    close = root.find_by_attr("data-settings-dialog-close", "")
    source = script_source()
    render = function_source(
        source, "function renderSettingsApiKeys", "async function loadSettingsApiKeys"
    )
    load = function_source(
        source, "async function loadSettingsApiKeys", "async function openSettingsDialog"
    )
    revoke = function_source(
        source, "async function revokeSettingsApiKey", "function openAgentFromSettings"
    )
    navigation = function_source(
        source, "function openAgentFromSettings", "async function openFilesDialog"
    )

    assert settings_button is not None and is_descendant(settings_button, menu)
    assert settings_button.attrs.get("role") == "menuitem"
    assert dialog is not None and dialog.tag == "dialog"
    assert dialog.attrs.get("aria-labelledby") == "settings-dialog-title"
    assert close is not None and is_descendant(close, dialog)
    assert 'elements.accountSettings.hidden = !hasToken' in source
    assert 'apiFetch("/api/api-keys")' in load
    assert "/api/agents?workspace_id=${encodeURIComponent(state.workspaceId)}" in load
    assert "Promise.allSettled" in load and "state.homepageAgents" in load
    for field in ["token_prefix", "created_at", "last_used_at", "revoked_at"]:
        assert f"key.{field}" in render
    assert 'method: "DELETE"' in revoke
    assert 'method: "POST"' not in load + revoke
    assert "window.location.hash = `agents/${encodeURIComponent(agentId)}`" in navigation
    assert ".settings-api-key-row" in asset_source("styles.css")
    assert '"Settings": "设置"' in asset_source("i18n.js")
    index = (WEB_ROOT / "index.html").read_text()
    assert "styles.css?v=20260724-flow140" in index
    assert "i18n.js?v=20260723-flow132" in index
    assert "main.js?v=20260724-flow140" in index


def test_agent_sessions_escape_persisted_content_before_rendering():
    source = asset_source("agents-ui.js")

    assert "escapeHtml(session.title || session.input_summary" in source
    assert "escapeHtml(session.status || \"completed\")" in source
    assert "escapeHtml(session.thread_id)" in source


def test_composer_exposes_files_dialog_and_attachment_selection():
    root = parse_index()
    dialog = root.find_by_attr("id", "files-dialog")
    assert dialog is not None and dialog.tag == "dialog"
    assert dialog.attrs.get("aria-labelledby") == "files-dialog-title"
    for attr in [
        "data-files-list",
        "data-files-search",
        "data-files-confirm",
        "data-attachment-chips",
    ]:
        assert root.find_by_attr(attr, "") is not None

    source = script_source()
    for fragment in [
        "selectedAttachments",
        "openFilesDialog()",
        "renderAttachmentChips()",
        "attachments: state.selectedAttachments.map",
    ]:
        assert fragment in source


def test_history_selection_rebuilds_visible_chat_state():
    source = script_source()
    selection = function_source(
        source,
        "async function selectRunFromHistory",
        "function renderWorkspaceSkills",
    )
    assert "renderConversationForRun(run)" in selection
    assert "function renderConversationForRun(run)" in source


def test_operations_follow_the_selected_chat_run_without_replacing_the_transcript():
    main = script_source()
    chat = chat_controller_source()
    publish = function_source(chat, "function publishChatContext()", "function modelKey(")
    operations = function_source(main, "function setOperationsOpen(open)", "function renderAttachmentChips()")
    sync = function_source(main, "async function syncOperationsRun()", "function renderWorkspaceSkills(")
    selection = function_source(main, "async function selectRunFromHistory", "async function syncOperationsRun()")
    publishers = [
        function_source(chat, "  async loadThread(", "  async hydrateMessageAttachments("),
        function_source(chat, "  startNewChat(", "  async updateThread("),
        function_source(chat, "  async sendThreadMessage(", "  updateThreadPreview("),
        function_source(chat, "  applyStreamEvent(", "  captureArtifactFromEvent("),
    ]

    assert 'new CustomEvent("taroai:chat-context-changed"' in publish
    assert "threadId: chatState.currentThreadId" in publish
    assert "runId: chatState.currentRunId" in publish
    assert 'window.addEventListener("taroai:chat-context-changed"' in main
    assert "Promise.allSettled(loads).then(syncOperationsRun)" in operations
    assert "await selectRunFromHistory(state.chatRunId, false)" in sync
    assert "if (showConversation) renderConversationForRun(run)" in selection
    assert "if (showConversation) renderConversationForRun({ ...run, status: state.runStatus })" in selection
    assert all("publishChatContext();" in caller for caller in publishers)


def test_artifact_preview_controls_conditional_panel():
    root = parse_index()
    panel = root.find_by_attr("data-testid", "artifact-panel")
    close_button = root.find_by_attr("data-artifact-panel-close", "")
    assert panel is not None
    assert panel.attrs.get("aria-labelledby") == "artifact-panel-title"
    assert close_button is not None

    source = script_source()
    preview = function_source(
        source,
        "async function previewArtifact",
        "function renderArtifactPreview",
    )
    assert "setArtifactPanelOpen(true)" in preview
    assert "setArtifactPanelOpen(false)" in source


def test_chat_attachment_names_survive_send_reload_and_retry():
    source = chat_controller_source()
    load = function_source(source, "  async loadThread(", "  async hydrateMessageAttachments(")
    hydrate = function_source(source, "  async hydrateMessageAttachments(", "  async restoreFromHash()")
    send = function_source(source, "  async sendThreadMessage(", "  updateThreadPreview(")
    retry = function_source(source, "  restoreMessageToComposer(", "  speakMessage(")
    stream = function_source(source, "  applyStreamEvent(", "  captureArtifactFromEvent(")

    assert "await this.hydrateMessageAttachments(" in load
    assert "/files?include_run_files=true" in hydrate
    assert "filename: file.filename || file.logical_path || id" in hydrate
    assert "attachments: displayAttachments" in send
    assert "displayAttachments.find((item) => item.id === id)" in send
    assert "chatState.uploads = arrayFrom(message.attachments" in retry
    assert "chatState.resourceRefs = refs.filter" in retry
    assert "chatState.browserProfile = refs.find" in retry
    assert ".then(async (messages)" in stream
    assert "await this.hydrateMessageAttachments(" in stream


def test_user_message_bubble_keeps_a_directional_shape_and_flat_evidence_row():
    source = chat_controller_source()
    render_message = function_source(source, "  renderMessage(message)", "  runActivityEvents(")
    styles = asset_source("styles.css")

    assert 'copyButton.className = "message-user-copy"' in render_message
    assert "copyButton.dataset.messageCopy = message.id" in render_message
    assert "border-radius: 20px 20px 7px 20px" in styles
    assert ".message-user:hover .message-user-copy" in styles
    assert ".message-user-copy:focus-visible" in styles
    assert ".message-user + .message-user" in styles
    assert ".message-user .message-evidence-chips span" in styles
    assert "background: transparent" in styles
    assert "max-width: 90%" in styles


def test_created_artifacts_are_available_inside_the_conversation():
    source = chat_controller_source()
    conversation = function_source(
        source,
        "  renderConversation()",
        "  renderSuggestions(",
    )
    artifacts = function_source(
        source,
        "  renderArtifacts()",
        "  async openArtifact(",
    )
    capture = function_source(
        source,
        "  captureArtifactFromEvent(event)",
        "  renderApprovalCard(runId = chatState.currentRunId)",
    )
    group = function_source(
        source,
        "  renderArtifactGroup(artifacts)",
        "  renderThoughtCard(runId = chatState.currentRunId)",
    )
    assert 'type === "artifact.created"' in source
    assert "run_id: artifact.run_id || event.run_id || payload.run_id" in capture
    assert 'outputs.className = "chat-inline-artifacts"' in group
    assert "outputs.append(this.renderArtifactButton(artifact))" in group
    assert "artifact.run_id === messageRunId" in conversation
    assert "!artifact.run_id || !renderedRunIds.has(artifact.run_id)" in conversation
    assert "this.renderArtifactGroup(unattachedArtifacts)" in conversation
    assert "this.refs.artifactList.append(this.renderArtifactButton(artifact))" in artifacts
    assert "button.dataset.threadArtifact" in artifacts
    assert ".chat-inline-artifacts" in asset_source("styles.css")


def test_empty_runtime_result_placeholder_is_not_presented_as_a_file():
    source = chat_controller_source()
    conversation = function_source(source, "  renderConversation()", "  renderSuggestions(")
    artifacts = function_source(source, "  renderArtifacts()", "  renderArtifactButton(")

    assert 'artifact.name !== "agent-result.md"' in source
    assert "chatState.artifacts.filter(isDisplayableArtifact)" in conversation
    assert "chatState.artifacts.filter(isDisplayableArtifact)" in artifacts


def test_artifact_card_loads_the_governed_preview_before_rendering():
    source = asset_source("artifacts-ui.js")
    open_artifact = function_source(source, "  async open(artifact)", "  async click(event)")
    assert "/api/artifacts/${encodeURIComponent(artifact.id)}/preview" in open_artifact
    assert "artifact = { ...artifact, ...preview }" in open_artifact
    assert "this.api.blob(`/api/artifacts/${encodeURIComponent(artifact.id)}/download`)" in open_artifact
    assert "this.previewUrl = URL.createObjectURL(blob)" in open_artifact


def test_binary_artifact_preview_uses_authenticated_blob_and_releases_it():
    source = asset_source("artifacts-ui.js")
    pdf = function_source(source, "  renderPdf(stage)", "  renderImage(stage)")
    image = function_source(source, "  renderImage(stage)", "  renderDashboard(stage)")
    close = function_source(source, "  close()", "  revokeUrls()")
    revoke = function_source(source, "  revokeUrls()", "}\n\nlet singleton")

    assert "this.previewUrl" in pdf
    assert "!this.artifact.id" in pdf
    assert "this.previewUrl" in image
    assert "!this.artifact.id" in image
    assert "stage.replaceChildren()" in close
    assert "stage.hidden = true" in close
    assert "this.openToken += 1" in close
    assert "URL.revokeObjectURL(url)" in revoke
    assert "this.previewUrl = null" in revoke


def test_persisted_artifact_download_keeps_the_authenticated_api_request():
    source = asset_source("artifacts-ui.js")
    download = function_source(source, "  async download()", "  share()")

    authenticated = "this.api.blob(`/api/artifacts/${encodeURIComponent(this.artifact.id)}/download`)"
    assert authenticated in download
    assert download.index("if (this.artifact.id)") < download.index(authenticated)
    assert "direct && !this.artifact.id" in download


def test_new_chat_and_thread_switch_clear_the_previous_artifact_sidecar():
    source = chat_controller_source()
    load_thread = function_source(source, "  async loadThread(", "  async hydrateMessageAttachments(")
    new_chat = function_source(source, "  startNewChat(", "  async updateThread(")
    close = function_source(source, "  closeArtifactSidecar()", "  renderDetails()")

    assert "this.closeArtifactSidecar()" in load_thread
    assert "this.closeArtifactSidecar()" in new_chat
    assert "window.taroaiArtifacts?.close?.()" in close
    assert 'classList.remove("is-artifact-open", "is-chat-sidecar-open")' in close
    assert "this.refs.artifactStage.replaceChildren()" in close
    assert "this.refs.artifactStage.hidden = true" in close


def test_popovers_have_keyboard_and_outside_click_dismissal():
    source = script_source()
    for fragment in [
        "function setActivePopover(",
        'event.key === "Escape"',
        "activePopover",
        "document.addEventListener(\"click\"",
        "returnFocus",
    ]:
        assert fragment in source


def test_first_submit_replaces_empty_state_placeholders():
    source = script_source()
    submit = function_source(
        source,
        "async function submitRun",
        "async function cancelRun",
    )
    empty_guard = 'elements.shell.dataset.chatState === "empty"'
    clear_placeholders = "elements.conversation.replaceChildren();"
    append_user = 'appendMessage("user", message);'
    assert empty_guard in submit
    assert clear_placeholders in submit
    assert submit.index(empty_guard) < submit.index(clear_placeholders) < submit.index(append_user)


def test_product_navigation_uses_a_real_hash_router():
    root = parse_index()
    route_surface = root.find_by_attr("data-testid", "product-route")
    assert route_surface is not None
    assert route_surface.attrs.get("aria-live") == "polite"

    for route in [
        "chat",
        "search",
        "discover",
        "feed",
        "agents",
        "workspaces",
        "files",
        "brain",
    ]:
        assert root.find_by_attr("data-app-route", route) is not None
    assert root.find_by_attr("data-app-route", "rewards") is None

    source = script_source()
    for fragment in [
        "const ROUTE_DEFINITIONS =",
        "function renderAppRoute(",
        "function refreshRouteData(",
        "function routeFromHash(",
        "elements.routeLinks.forEach",
        'window.addEventListener("hashchange"',
        "refreshRouteData(routeName)",
        "renderAppRoute(routeFromHash()",
    ]:
        assert fragment in source


def test_homepage_primary_actions_are_wired():
    root = parse_index()
    for attr in [
        "data-homepage-agents",
        "data-explore-agents",
    ]:
        assert root.find_by_attr(attr, "") is not None

    source = script_source()
    for fragment in [
        "function renderHomepageAgents(",
        "async function loadHomepageAgents(",
        "`/api/agents?workspace_id=",
        "elements.agentCardRail.addEventListener",
        "elements.exploreAgents.addEventListener",
        "open.dataset.openAgentLibrary = agent.id || agent.agent_id",
        "window.location.hash = `agents/${encodeURIComponent(agentId)}`",
    ]:
        assert fragment in source
    html = (WEB_ROOT / "index.html").read_text()
    assert "Case Study Writer" not in html
    assert "Cash Flow Forecaster" not in html


def test_agent_runs_use_the_current_chat_model_as_an_explicit_override():
    source = asset_source("agents-ui.js")
    run = function_source(source, "  async run(form)", "  async openDraft(")

    assert 'properties = { request: { type: "string"' in source
    assert 'schema = { ...schema, required: ["request"] }' in source
    assert "const model = chatState.selectedModel" in run
    assert "provider_id: model.provider_id" in run
    assert "model_id: model.model_id" in run
    assert "reasoning_effort: model.reasoning_effort || null" in run


def test_agent_run_handoff_moves_to_chat_and_is_consumed_once():
    agents = asset_source("agents-ui.js")
    run = function_source(agents, "  async run(form)", "  async openDraft(")
    chat = asset_source("chat-controller.js")
    restore = function_source(chat, "  async restoreFromHash()", "  async runAgentHandoff(")
    handoff = function_source(chat, "  async runAgentHandoff(", "  startNewChat(")

    assert "data-agent-run-form novalidate" in agents
    assert "form.reportValidity()" in run
    assert "queueAgentRunHandoff({" in run
    assert 'window.location.hash = "chat"' in run
    assert "/api/agents/${encodeURIComponent(id)}/runs" not in run
    assert "sessionStorage.removeItem(AGENT_RUN_HANDOFF_KEY)" in chat
    assert "if (handoff) return this.runAgentHandoff(handoff)" in restore
    assert "/api/agents/${encodeURIComponent(agentId)}/runs" in handoff
    assert "const threadId = result.thread_id" in handoff
    assert "await this.loadThread(threadId, true)" in handoff
    assert "result.run_id" not in handoff

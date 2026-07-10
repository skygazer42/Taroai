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
    assert run_history is not None and is_descendant(run_history, sidebar)
    assert artifact_list is not None and is_descendant(artifact_list, artifact_panel)


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
        "rewards",
    ]:
        assert root.find_by_attr("data-app-route", route) is not None

    source = script_source()
    for fragment in [
        "const ROUTE_DEFINITIONS =",
        "function renderAppRoute(",
        "function routeFromHash(",
        "elements.routeLinks.forEach",
        'window.addEventListener("hashchange"',
        "renderAppRoute(routeFromHash()",
    ]:
        assert fragment in source


def test_homepage_primary_actions_are_wired():
    root = parse_index()
    for attr in [
        "data-agent-prompt",
        "data-agent-carousel-next",
        "data-create-agent",
        "data-explore-agents",
    ]:
        assert root.find_by_attr(attr, "") is not None

    source = script_source()
    for fragment in [
        "function prefillAgentRun(",
        "elements.agentRunButtons.forEach",
        "elements.agentCarouselNext.addEventListener",
        "elements.createAgent.addEventListener",
        "elements.exploreAgents.addEventListener",
    ]:
        assert fragment in source

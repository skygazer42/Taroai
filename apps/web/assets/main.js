import { createChatController } from "./chat-controller.js";
import { createSkillsUI } from "./skills-ui.js";
import { createAgentsUI } from "./agents-ui.js";
import { createArtifactsUI } from "./artifacts-ui.js";
import { createSpeechUI } from "./speech-ui.js";
import { createAgentBrainUI } from "./brain-ui.js";
import { createFilesUI } from "./files-ui.js";

window.__taroaiThreadChat = true;

const state = {
  currentRunId: null,
  lastSequence: 0,
  events: [],
  eventStreamIntegrityIssues: [],
  artifacts: [],
  storageObjects: [],
  workspaceFiles: [],
  runHistory: [],
  runTrace: null,
  runtimeState: null,
  selectedRunHistoryId: null,
  deliveredRunIds: new Set(),
  previewedRunIds: new Set(),
  feedbackSubmittedRunIds: new Set(),
  pendingApprovalId: null,
  browserPreviewObjectUrl: null,
  browserPreviewStorageObjectId: null,
  pollTimer: null,
  pollingInFlight: false,
  pollIntervalMs: 1500,
  runStatus: "idle",
  readiness: null,
  customerSuccess: null,
  solutionPacks: [],
  selectedSolutionPackId: null,
  workspaceSkills: [],
  selectedSkillId: null,
  selectedSolutionPackDraftId: null,
  selectedModel: localStorage.getItem("taroai.selectedModel") || "Claude Sonnet 5",
  selectedAttachments: [],
  filesDialogSelection: new Set(),
  activePopover: null,
  returnFocus: null,
  artifactPanelOpen: false,
  operationsOpen: false,
  sidebarCollapsed: localStorage.getItem("taroai.sidebarCollapsed") === "true",
  appRoute: "chat",
  activeWorkbenchView: localStorage.getItem("taroai.activeWorkbenchView") || "run",
  apiBase: localStorage.getItem("taroai.apiBase") || "http://localhost:8000",
  tenantId: localStorage.getItem("taroai.tenantId") || "tenant_acme",
  userId: localStorage.getItem("taroai.userId") || "user_luke",
  workspaceId: localStorage.getItem("taroai.workspaceId") || "workspace_sales",
  tenantSlug: localStorage.getItem("taroai.tenantSlug") || "acme",
  ownerDisplayName: localStorage.getItem("taroai.ownerDisplayName") || "Owner",
  agentId: localStorage.getItem("taroai.agentId") || "agent_workspace",
  authEmail: localStorage.getItem("taroai.authEmail") || "owner@example.com",
  accessToken: sessionStorage.getItem("taroai.accessToken") || "",
};

applyUrlConfiguration();

const ACTIVE_RUN_STATUSES = [
  "created",
  "queued",
  "classifying",
  "retrieving_context",
  "planning",
  "awaiting_policy",
  "running",
  "awaiting_approval",
  "retrying",
];
const RETRYABLE_RUN_STATUSES = ["failed", "cancelled", "timed_out"];
const ARTIFACT_PREVIEW_MAX_CHARACTERS = 6000;
const ROUTE_DEFINITIONS = {
  chat: {
    eyebrow: "Taroai",
    title: "Chat",
    description: "Run governed agent tasks from a persistent workspace shell.",
    cards: [],
  },
  search: {
    eyebrow: "Find anything",
    title: "Search",
    description: "Search product areas and recently loaded runs.",
    cards: [],
  },
  discover: {
    eyebrow: "Explore",
    title: "Discover agents",
    description: "Start with a focused workflow, then adapt it in Chat.",
    cards: [
      { title: "Case Study Writer", description: "Turn customer interviews into a publishable case study.", meta: "Gmail · Docs", action: "agent:Case Study Writer", actionLabel: "Run agent" },
      { title: "Product Description Writer", description: "Create consistent product copy from structured inputs.", meta: "Gmail · Sheets", action: "agent:Product Description Writer", actionLabel: "Run agent" },
      { title: "Cash Flow Forecaster", description: "Build and explain a rolling cash-flow view.", meta: "Sheets · Finance", action: "agent:Cash Flow Forecaster", actionLabel: "Run agent" },
    ],
  },
  feed: {
    eyebrow: "Workspace activity",
    title: "Feed",
    description: "Recent shared activity will appear here when the team feed API is connected.",
    cards: [
      { title: "No shared activity yet", description: "Start a governed run and its evidence will remain available in Operations.", meta: "Local workspace", action: "chat", actionLabel: "Start a run" },
      { title: "Run evidence", description: "Inspect timelines, terminal output, approvals, and artifacts already supported by Taroai.", meta: "Operations", action: "operations", actionLabel: "Open Operations" },
    ],
  },
  agents: {
    eyebrow: "Reusable work",
    title: "Agents",
    description: "Prepare a reusable agent from a successful conversation.",
    cards: [
      { title: "Create from Chat", description: "Describe the reusable workflow you want to build.", meta: "Draft flow", action: "prompt:Create a reusable agent from this conversation.", actionLabel: "Create agent" },
      { title: "Popular agents", description: "Browse ready-to-run workflow ideas.", meta: "Templates", action: "route:discover", actionLabel: "Browse" },
      { title: "Agent operations", description: "Review the current runtime and governed execution controls.", meta: "Run control", action: "operations", actionLabel: "Open" },
    ],
  },
  skills: {
    eyebrow: "Workspace capabilities",
    title: "Skills",
    description: "Install, inspect, evaluate, and version reusable execution guidance.",
    cards: [],
  },
  workspaces: {
    eyebrow: "Organize work",
    title: "Workspaces",
    description: "Keep runs, files, skills, and evidence within a tenant-scoped workspace.",
    cards: [
      { title: "workspace_sales", description: "The active workspace configured for this local session.", meta: "Current workspace", action: "operations", actionLabel: "Open workspace" },
      { title: "Plan a workspace", description: "Use Chat to define a new workspace structure without pretending it already exists.", meta: "Guided setup", action: "prompt:Plan a new workspace for my team.", actionLabel: "Plan in Chat" },
    ],
  },
  files: {
    eyebrow: "Workspace drive",
    title: "Files",
    description: "Select storage-backed run files and artifacts already available to this session.",
    cards: [
      { title: "Chat files", description: "Choose existing storage objects to attach to the next Run.", meta: "Storage objects", action: "files", actionLabel: "Open files" },
      { title: "Artifact library", description: "Preview or download artifacts created by the selected Run.", meta: "Run outputs", action: "operations", actionLabel: "View artifacts" },
    ],
  },
  brain: {
    eyebrow: "Agent context",
    title: "Agent Brain",
    description: "Inspect the skills and governed context surfaces connected to this workspace.",
    cards: [
      { title: "Workspace skills", description: "View installed skills and invoke their supported workflows.", meta: "Skills", action: "inspect", actionLabel: "Open skills" },
      { title: "Knowledge and memory", description: "Ask Chat to retrieve and organize workspace context.", meta: "Context", action: "prompt:Review the knowledge and memory available in this workspace.", actionLabel: "Ask Chat" },
    ],
  },
  rewards: {
    eyebrow: "Taroai",
    title: "Rewards",
    description: "Referral rewards are not connected in this local build.",
    cards: [
      { title: "Local preview", description: "No referral data is sent or generated from this page.", meta: "No external side effects", action: "chat", actionLabel: "Back to Chat" },
    ],
  },
};

const elements = {
  shell: document.querySelector("[data-app='taroai-workspace']"),
  sidebarCollapse: document.querySelector("[data-sidebar-collapse]"),
  newChat: document.querySelector("[data-new-chat]"),
  routeLinks: document.querySelectorAll("[data-app-route]"),
  routeSurface: document.querySelector("[data-testid='product-route']"),
  routeEyebrow: document.querySelector("[data-route-eyebrow]"),
  routeTitle: document.querySelector("[data-route-title]"),
  routeDescription: document.querySelector("[data-route-description]"),
  routeCards: document.querySelector("[data-route-cards]"),
  routeSearchShell: document.querySelector("[data-route-search-shell]"),
  routeSearch: document.querySelector("[data-route-search]"),
  routeSearchResults: document.querySelector("[data-route-search-results]"),
  agentRunButtons: document.querySelectorAll("[data-agent-prompt]"),
  agentCarouselNext: document.querySelector("[data-agent-carousel-next]"),
  agentCardRail: document.querySelector(".agent-card-rail"),
  createAgent: document.querySelector("[data-create-agent]"),
  createAgentPrompt: document.querySelector("[data-create-agent-prompt]"),
  exploreAgents: document.querySelector("[data-explore-agents]"),
  modelSelectorButton: document.querySelector("#model-selector-button"),
  modelSelectorMenu: document.querySelector("#model-selector-menu"),
  selectedModel: document.querySelector("[data-selected-model]"),
  modelOptions: document.querySelectorAll("[data-model-option]"),
  composerAddButton: document.querySelector("#composer-add-button"),
  composerAddMenu: document.querySelector("#composer-add-menu"),
  addCommands: document.querySelectorAll("[data-add-command]"),
  composerFileInput: document.querySelector("#composer-file-input"),
  attachmentChips: document.querySelector("[data-attachment-chips]"),
  filesDialog: document.querySelector("#files-dialog"),
  filesDialogOpeners: document.querySelectorAll("[data-open-files-dialog]"),
  filesDialogClose: document.querySelector("[data-files-dialog-close]"),
  filesList: document.querySelector("[data-files-list]"),
  filesSearch: document.querySelector("[data-files-search]"),
  filesConfirm: document.querySelector("[data-files-confirm]"),
  filesSelectionStatus: document.querySelector("[data-files-selection-status]"),
  sidecar: document.querySelector("[data-workspace-sidecar]"),
  artifactPanelClose: document.querySelector("[data-artifact-panel-close]"),
  operationsOpeners: document.querySelectorAll("[data-open-operations]"),
  operationsClose: document.querySelector("[data-operations-close]"),
  workbenchViews: document.querySelectorAll("[data-workbench-view]"),
  workbenchViewToggles: document.querySelectorAll("[data-workbench-view-toggle]"),
  apiBase: document.querySelector("#api-base"),
  tenantId: document.querySelector("#tenant-id"),
  userId: document.querySelector("#user-id"),
  workspaceId: document.querySelector("#workspace-id"),
  tenantSlug: document.querySelector("#tenant-slug"),
  ownerDisplayName: document.querySelector("#owner-display-name"),
  loginEmail: document.querySelector("#login-email"),
  loginPassword: document.querySelector("#login-password"),
  bootstrapToken: document.querySelector("#bootstrap-token"),
  bootstrapLoginButton: document.querySelector("#bootstrap-login-button"),
  bootstrapStatus: document.querySelector("[data-bootstrap-status]"),
  loginButton: document.querySelector("#login-button"),
  logoutButton: document.querySelector("#logout-button"),
  authStatus: document.querySelector("[data-auth-status]"),
  readinessStatus: document.querySelector("[data-readiness-status]"),
  readinessModel: document.querySelector("[data-readiness-model]"),
  readinessSandbox: document.querySelector("[data-readiness-sandbox]"),
  customerSuccessStatus: document.querySelector("[data-cs-status]"),
  customerSuccessHealth: document.querySelector("[data-cs-health]"),
  customerSuccessRuns: document.querySelector("[data-cs-runs]"),
  customerSuccessFeedback: document.querySelector("[data-cs-feedback]"),
  customerSuccessEvalCandidates: document.querySelector("[data-cs-eval-candidates]"),
  customerSuccessPackCandidates: document.querySelector("[data-cs-pack-candidates]"),
  customerSuccessMissingSkillStatus: document.querySelector("[data-cs-missing-skill-status]"),
  customerSuccessMissingSkillName: document.querySelector("#cs-missing-skill-name"),
  customerSuccessMissingSkillComment: document.querySelector("#cs-missing-skill-comment"),
  customerSuccessMissingSkillSolutionPack: document.querySelector(
    "#cs-missing-skill-solution-pack"
  ),
  customerSuccessSubmitMissingSkill: document.querySelector("#cs-submit-missing-skill"),
  customerSuccessCandidateStatus: document.querySelector("[data-cs-candidate-action-status]"),
  customerSuccessEvalSelected: document.querySelector("[data-cs-eval-candidate-selected]"),
  customerSuccessPackSelected: document.querySelector("[data-cs-pack-candidate-selected]"),
  customerSuccessCreateEvalCandidates: document.querySelector("#cs-create-eval-candidates"),
  customerSuccessCreatePackCandidates: document.querySelector("#cs-create-pack-candidates"),
  customerSuccessEvalAccept: document.querySelector("#cs-accept-eval-candidate"),
  customerSuccessEvalReject: document.querySelector("#cs-reject-eval-candidate"),
  customerSuccessPackAccept: document.querySelector("#cs-accept-pack-candidate"),
  customerSuccessPackReject: document.querySelector("#cs-reject-pack-candidate"),
  customerSuccessDraftsList: document.querySelector("[data-cs-drafts-list]"),
  customerSuccessDraftSelected: document.querySelector("[data-cs-draft-selected]"),
  customerSuccessDraftStatus: document.querySelector("[data-cs-draft-status]"),
  customerSuccessDraftSkill: document.querySelector("#cs-draft-skill"),
  customerSuccessDraftSummary: document.querySelector("#cs-draft-summary"),
  customerSuccessDraftPackVersion: document.querySelector("#cs-draft-pack-version"),
  customerSuccessDraftSkillManifest: document.querySelector("#cs-draft-skill-manifest"),
  customerSuccessDraftSave: document.querySelector("#cs-draft-save"),
  customerSuccessDraftSubmit: document.querySelector("#cs-draft-submit"),
  customerSuccessDraftApprove: document.querySelector("#cs-draft-approve"),
  customerSuccessDraftReject: document.querySelector("#cs-draft-reject"),
  customerSuccessDraftApply: document.querySelector("#cs-draft-apply"),
  customerSuccessRefresh: document.querySelector("[data-cs-refresh]"),
  solutionPackStatus: document.querySelector("[data-solution-pack-status]"),
  solutionPackList: document.querySelector("[data-solution-pack-list]"),
  solutionPackRefresh: document.querySelector("[data-solution-pack-refresh]"),
  solutionPackInstallButton: document.querySelector("#install-solution-pack-button"),
  solutionPackInstallStatus: document.querySelector("[data-solution-pack-install-status]"),
  workspaceSkillsStatus: document.querySelector("[data-skills-status]"),
  workspaceSkillsList: document.querySelector("[data-skills-list]"),
  workspaceSkillsRefresh: document.querySelector("[data-skills-refresh]"),
  skillInvokeInput: document.querySelector("#skill-invoke-input"),
  skillInvokeButton: document.querySelector("#invoke-skill-button"),
  skillInvokeStatus: document.querySelector("[data-skill-invoke-status]"),
  input: document.querySelector("#composer-input"),
  send: document.querySelector("#send-button"),
  refresh: document.querySelector("#refresh-button"),
  cancelRun: document.querySelector("#cancel-run-button"),
  retryRun: document.querySelector("#retry-run-button"),
  runControlStatus: document.querySelector("[data-run-control-status]"),
  executionLoopSummary: document.querySelector("[data-execution-summary]"),
  executionModelRoute: document.querySelector("[data-execution-model-route]"),
  executionLoopRun: document.querySelector("[data-execution-run]"),
  executionLoopPlan: document.querySelector("[data-execution-plan]"),
  executionLoopSandbox: document.querySelector("[data-execution-sandbox]"),
  executionLoopBrowser: document.querySelector("[data-execution-browser]"),
  executionLoopArtifact: document.querySelector("[data-execution-artifact]"),
  evidenceSummary: document.querySelector("[data-evidence-summary]"),
  evidencePlan: document.querySelector("[data-evidence-plan]"),
  evidenceSandbox: document.querySelector("[data-evidence-sandbox]"),
  evidenceArtifact: document.querySelector("[data-evidence-artifact]"),
  evidenceBrowser: document.querySelector("[data-evidence-browser]"),
  evidenceTerminal: document.querySelector("[data-evidence-terminal]"),
  deliveryChainStatus: document.querySelector("[data-delivery-chain-status]"),
  deliveryChainRun: document.querySelector("[data-delivery-chain-run]"),
  deliveryChainSandbox: document.querySelector("[data-delivery-chain-sandbox]"),
  deliveryChainArtifactStorage: document.querySelector(
    "[data-delivery-chain-artifact-storage]",
  ),
  deliveryChainTerminalStorage: document.querySelector(
    "[data-delivery-chain-terminal-storage]",
  ),
  deliveryChainBrowserStorage: document.querySelector(
    "[data-delivery-chain-browser-storage]",
  ),
  eventIntegrityStatus: document.querySelector("[data-event-integrity-status]"),
  eventIntegrityCount: document.querySelector("[data-event-integrity-count]"),
  eventIntegritySequence: document.querySelector("[data-event-integrity-sequence]"),
  eventIntegrityClosure: document.querySelector("[data-event-integrity-closure]"),
  runHistoryStatus: document.querySelector("[data-run-history-status]"),
  runHistoryList: document.querySelector("[data-run-history-list]"),
  runHistoryRefresh: document.querySelector("[data-run-history-refresh]"),
  conversation: document.querySelector("[data-testid='conversation-log']"),
  status: document.querySelector("[data-status-pill]"),
  timeline: document.querySelector("[data-timeline-list]"),
  traceStatus: document.querySelector("[data-trace-status]"),
  traceSpanCount: document.querySelector("[data-trace-span-count]"),
  traceEventCount: document.querySelector("[data-trace-event-count]"),
  traceBillingCount: document.querySelector("[data-trace-billing-count]"),
  traceAuditCount: document.querySelector("[data-trace-audit-count]"),
  traceErrorClassification: document.querySelector("[data-trace-error-classification]"),
  traceList: document.querySelector("[data-trace-list]"),
  runtimeStateStatus: document.querySelector("[data-runtime-state-status]"),
  runtimeCurrentStep: document.querySelector("[data-runtime-current-step]"),
  runtimeCompletedCount: document.querySelector("[data-runtime-completed-count]"),
  runtimeSandboxSession: document.querySelector("[data-runtime-sandbox-session]"),
  runtimeBrowserSession: document.querySelector("[data-runtime-browser-session]"),
  runtimeArtifactCount: document.querySelector("[data-runtime-artifact-count]"),
  terminal: document.querySelector("[data-terminal-output]"),
  terminalStatus: document.querySelector("[data-terminal-status]"),
  browserStatus: document.querySelector("[data-browser-status]"),
  browserSession: document.querySelector("[data-browser-session]"),
  browserAction: document.querySelector("[data-browser-action]"),
  browserUrl: document.querySelector("[data-browser-url]"),
  browserStorageObject: document.querySelector("[data-browser-storage-object]"),
  browserPreviewStorageObject: document.querySelector(
    "[data-browser-preview-storage-object]",
  ),
  browserScreenshot: document.querySelector("[data-browser-screenshot]"),
  browserScreenshotPreview: document.querySelector("[data-browser-screenshot-preview]"),
  browserEmpty: document.querySelector("[data-browser-empty]"),
  artifacts: document.querySelector("[data-artifact-list]"),
  artifactCount: document.querySelector("[data-artifact-count]"),
  deliverySummary: document.querySelector("[data-delivery-summary]"),
  terminalOutputStorageObject: document.querySelector(
    "[data-terminal-output-storage-object]",
  ),
  artifactDownloadStatus: document.querySelector("[data-artifact-download-status]"),
  artifactDownloadedStorageObject: document.querySelector(
    "[data-artifact-downloaded-storage-object]",
  ),
  runFeedbackStatus: document.querySelector("[data-run-feedback-status]"),
  runFeedbackPositive: document.querySelector("#run-feedback-positive"),
  runFeedbackNegative: document.querySelector("#run-feedback-negative"),
  artifactPreviewStatus: document.querySelector("[data-artifact-preview-status]"),
  artifactPreviewTitle: document.querySelector("[data-artifact-preview-title]"),
  artifactPreviewStorageObject: document.querySelector(
    "[data-artifact-preview-storage-object]",
  ),
  artifactPreviewContent: document.querySelector("[data-artifact-preview-content]"),
  approvalStatus: document.querySelector("[data-approval-status]"),
  approvalCopy: document.querySelector("[data-approval-copy]"),
  approvalResolution: document.querySelector("[data-approval-resolution]"),
  approve: document.querySelector("#approve-button"),
  reject: document.querySelector("#reject-button"),
};

function applyUrlConfiguration() {
  const urlParams = new URLSearchParams(window.location.search);
  const connectionParams = {
    apiBase: "taroai.apiBase",
    tenantId: "taroai.tenantId",
    userId: "taroai.userId",
    workspaceId: "taroai.workspaceId",
    agentId: "taroai.agentId",
    email: "taroai.authEmail",
  };
  const stateKeyByParam = {
    apiBase: "apiBase",
    tenantId: "tenantId",
    userId: "userId",
    workspaceId: "workspaceId",
    agentId: "agentId",
    email: "authEmail",
  };

  for (const [param, storageKey] of Object.entries(connectionParams)) {
    const value = normalizedUrlConfigValue(param, urlParams.get(param));
    if (!value) {
      continue;
    }
    const key = stateKeyByParam[param];
    state[key] = value;
    localStorage.setItem(storageKey, value);
  }

  const hadUrlSecret =
    urlParams.has("accessToken") ||
    urlParams.has("access_token") ||
    urlParams.has("token") ||
    urlParams.has("password");
  if (!hadUrlSecret) {
    return;
  }
  urlParams.delete("accessToken");
  urlParams.delete("access_token");
  urlParams.delete("token");
  urlParams.delete("password");
  const query = urlParams.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState({}, document.title, nextUrl);
}

function normalizedUrlConfigValue(param, rawValue) {
  const value = (rawValue || "").trim();
  if (!value) {
    return "";
  }
  if (param === "apiBase") {
    return value.replace(/\/+$/, "");
  }
  return value;
}

function initializeControls() {
  elements.apiBase.value = state.apiBase;
  elements.tenantId.value = state.tenantId;
  elements.userId.value = state.userId;
  elements.workspaceId.value = state.workspaceId;
  elements.tenantSlug.value = state.tenantSlug;
  elements.ownerDisplayName.value = state.ownerDisplayName;
  elements.loginEmail.value = state.authEmail;
  setSelectedModel(state.selectedModel);
  renderAttachmentChips();
  setActivePopover(null);
  setArtifactPanelOpen(false);
  setOperationsOpen(false);
  setSidebarCollapsed(state.sidebarCollapsed);
  setChatState(state.currentRunId ? "thread" : "empty");
  renderAppRoute(routeFromHash(), false);
  syncComposerState();
  switchWorkbenchView(state.activeWorkbenchView);
  renderBootstrap();
  renderAuth();
  renderExecutionLoop();
  renderRunEvidence();
  renderDeliveryChain();
  renderEventIntegrity();
  renderRunFeedback();
  renderReadiness();
  renderCustomerSuccess();
  renderSolutionPacks();
  renderWorkspaceSkills();
  loadReadiness();
  loadCustomerSuccess();
  loadSolutionPacks();
  loadWorkspaceSkills();
  // Durable Thread history is owned by chat-controller.js.
}

function switchWorkbenchView(viewName) {
  const allowedViews = ["run", "inspect", "admin"];
  const activeView = allowedViews.includes(viewName) ? viewName : "run";
  state.activeWorkbenchView = activeView;
  localStorage.setItem("taroai.activeWorkbenchView", activeView);
  elements.workbenchViews.forEach((view) => {
    const viewName = view.getAttribute("data-workbench-view");
    view.hidden = viewName !== activeView;
    view.classList.toggle("is-active", viewName === activeView);
  });
  elements.workbenchViewToggles.forEach((button) => {
    const isActive = button.dataset.workbenchViewToggle === activeView;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

function setChatState(chatState) {
  const activeState = chatState === "thread" ? "thread" : "empty";
  elements.shell.dataset.chatState = activeState;
}

function routeFromHash() {
  const routeName = window.location.hash.replace(/^#/, "").trim().toLowerCase();
  return Object.hasOwn(ROUTE_DEFINITIONS, routeName) ? routeName : "chat";
}

function renderAppRoute(routeName, updateHash = false) {
  const activeRoute = Object.hasOwn(ROUTE_DEFINITIONS, routeName)
    ? routeName
    : "chat";
  const definition = ROUTE_DEFINITIONS[activeRoute];
  state.appRoute = activeRoute;
  elements.shell.dataset.appRoute = activeRoute;
  elements.routeSurface.hidden = activeRoute === "chat";
  elements.routeLinks.forEach((link) => {
    link.classList.toggle("is-active", link.dataset.appRoute === activeRoute);
    if (link.tagName === "A") {
      link.setAttribute(
        "aria-current",
        link.dataset.appRoute === activeRoute ? "page" : "false",
      );
    }
  });

  if (activeRoute !== "chat") {
    closeActivePopover(false);
    setArtifactPanelOpen(false);
    setOperationsOpen(false);
    elements.routeEyebrow.textContent = definition.eyebrow;
    elements.routeTitle.textContent = definition.title;
    elements.routeDescription.textContent = definition.description;
    elements.routeSearchShell.hidden = activeRoute !== "search";
    renderRouteCards(definition.cards || []);
    if (activeRoute === "search") {
      renderRouteSearchResults(elements.routeSearch.value);
      window.requestAnimationFrame(() => elements.routeSearch.focus());
    }
  }

  if (updateHash && window.location.hash !== `#${activeRoute}`) {
    window.location.hash = activeRoute;
  }
}

function renderRouteCards(cards) {
  elements.routeCards.replaceChildren();
  for (const card of cards) {
    const article = document.createElement("article");
    article.className = "product-route-card";
    const meta = document.createElement("span");
    meta.className = "product-route-card-meta";
    meta.textContent = card.meta;
    const title = document.createElement("h2");
    title.textContent = card.title;
    const description = document.createElement("p");
    description.textContent = card.description;
    const action = document.createElement("button");
    action.type = "button";
    action.dataset.routeAction = card.action;
    action.textContent = card.actionLabel;
    article.append(meta, title, description, action);
    elements.routeCards.append(article);
  }
}

function renderRouteSearchResults(searchTerm = "") {
  const normalized = searchTerm.trim().toLowerCase();
  const routeItems = Object.entries(ROUTE_DEFINITIONS)
    .filter(([routeName]) => !["chat", "search"].includes(routeName))
    .map(([routeName, definition]) => ({
      title: definition.title,
      description: definition.description,
      action: `route:${routeName}`,
    }));
  const runItems = state.runHistory.map((run) => ({
    title: run.message || run.id,
    description: `Run · ${run.status || "created"}`,
    action: `run:${run.id}`,
  }));
  const results = [...routeItems, ...runItems].filter((item) => {
    if (!normalized) {
      return true;
    }
    return `${item.title} ${item.description}`.toLowerCase().includes(normalized);
  });
  elements.routeSearchResults.replaceChildren();

  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "product-route-search-empty";
    empty.textContent = "No matching routes or loaded runs.";
    elements.routeSearchResults.append(empty);
    return;
  }

  for (const result of results.slice(0, 12)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "product-route-search-result";
    button.dataset.routeAction = result.action;
    const title = document.createElement("strong");
    title.textContent = result.title;
    const description = document.createElement("small");
    description.textContent = result.description;
    button.append(title, description);
    elements.routeSearchResults.append(button);
  }
}

function prefillChatMessage(message) {
  startNewChat();
  elements.input.value = message;
  fitComposer();
  syncComposerState();
  elements.input.focus();
}

function prefillAgentRun(agentName) {
  prefillChatMessage(`Run the "${agentName}" agent`);
}

function handleRouteAction(action) {
  if (!action) {
    return;
  }
  if (action === "chat") {
    startNewChat();
    return;
  }
  if (action === "files") {
    openFilesDialog();
    return;
  }
  if (action === "operations") {
    setOperationsOpen(true);
    return;
  }
  if (action === "inspect") {
    switchWorkbenchView("inspect");
    setOperationsOpen(true);
    return;
  }
  if (action.startsWith("route:")) {
    renderAppRoute(action.slice("route:".length), true);
    return;
  }
  if (action.startsWith("agent:")) {
    prefillAgentRun(action.slice("agent:".length));
    return;
  }
  if (action.startsWith("prompt:")) {
    prefillChatMessage(action.slice("prompt:".length));
    return;
  }
  if (action.startsWith("run:")) {
    renderAppRoute("chat", true);
    selectRunFromHistory(action.slice("run:".length));
  }
}

function setSidebarCollapsed(collapsed) {
  state.sidebarCollapsed = Boolean(collapsed);
  elements.shell.classList.toggle("is-sidebar-collapsed", state.sidebarCollapsed);
  elements.sidebarCollapse.setAttribute(
    "aria-label",
    state.sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar",
  );
  localStorage.setItem("taroai.sidebarCollapsed", String(state.sidebarCollapsed));
}

function setActivePopover(popoverName, trigger = null) {
  const allowed = ["model", "add"];
  const activePopover = allowed.includes(popoverName) ? popoverName : null;
  state.activePopover = activePopover;
  if (activePopover && trigger) {
    state.returnFocus = trigger;
  }

  const modelOpen = activePopover === "model";
  const addOpen = activePopover === "add";
  elements.modelSelectorMenu.hidden = !modelOpen;
  elements.modelSelectorButton.setAttribute("aria-expanded", String(modelOpen));
  elements.composerAddMenu.hidden = !addOpen;
  elements.composerAddButton.setAttribute("aria-expanded", String(addOpen));

  const firstItem = activePopover
    ? (activePopover === "model" ? elements.modelSelectorMenu : elements.composerAddMenu)
        .querySelector('[role^="menuitem"]')
    : null;
  if (firstItem) {
    window.requestAnimationFrame(() => firstItem.focus());
  }
}

function closeActivePopover(returnFocus = false) {
  const focusTarget = state.returnFocus;
  setActivePopover(null);
  state.returnFocus = null;
  if (returnFocus && focusTarget) {
    window.requestAnimationFrame(() => focusTarget.focus());
  }
}

function setSelectedModel(modelName) {
  const available = Array.from(elements.modelOptions).map(
    (button) => button.dataset.modelOption,
  );
  const selectedModel = available.includes(modelName) ? modelName : available[0];
  state.selectedModel = selectedModel;
  elements.selectedModel.textContent = selectedModel;
  elements.modelOptions.forEach((button) => {
    const isSelected = button.dataset.modelOption === selectedModel;
    button.classList.toggle("is-selected", isSelected);
    button.setAttribute("aria-checked", String(isSelected));
  });
  localStorage.setItem("taroai.selectedModel", selectedModel);
}

function setArtifactPanelOpen(open) {
  state.artifactPanelOpen = Boolean(open);
  if (state.artifactPanelOpen) {
    state.operationsOpen = false;
  }
  elements.sidecar.classList.toggle("is-artifact-open", state.artifactPanelOpen);
  elements.sidecar.classList.toggle("is-operations-open", state.operationsOpen);
  if (!state.artifactPanelOpen) {
    elements.sidecar.classList.remove("is-chat-sidecar-open");
  }
}

function setOperationsOpen(open) {
  state.operationsOpen = Boolean(open);
  if (state.operationsOpen) {
    state.artifactPanelOpen = false;
    elements.sidecar.classList.remove("is-chat-sidecar-open");
  }
  elements.sidecar.classList.toggle("is-operations-open", state.operationsOpen);
  elements.sidecar.classList.toggle("is-artifact-open", state.artifactPanelOpen);
}

function renderAttachmentChips() {
  elements.attachmentChips.replaceChildren();
  for (const attachment of state.selectedAttachments) {
    const chip = document.createElement("span");
    chip.className = "attachment-chip";
    const name = document.createElement("span");
    name.textContent = attachment.filename || attachment.name || attachment.id;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${name.textContent}`);
    remove.dataset.removeAttachmentId = attachment.id;
    remove.textContent = "×";
    chip.append(name, remove);
    elements.attachmentChips.append(chip);
  }
}

function renderFilesDialog() {
  const searchTerm = (elements.filesSearch.value || "").trim().toLowerCase();
  const allFiles = [...state.workspaceFiles, ...state.storageObjects].filter(
    (storageObject, index, values) => values.findIndex((item) => item.id === storageObject.id) === index,
  );
  const candidates = allFiles.filter((storageObject) => {
    const filename = (storageObject.filename || storageObject.id || "").toLowerCase();
    return !searchTerm || filename.includes(searchTerm);
  });
  elements.filesList.replaceChildren();

  if (!candidates.length) {
    const empty = document.createElement("div");
    empty.className = "files-empty-state";
    empty.setAttribute("data-files-empty", "");
    const icon = document.createElement("span");
    icon.className = "files-empty-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "▧";
    const title = document.createElement("p");
    title.textContent = "No files yet";
    const copy = document.createElement("small");
    copy.textContent = "Files generated or uploaded will appear here.";
    empty.append(icon, title, copy);
    elements.filesList.append(empty);
  } else {
    for (const storageObject of candidates) {
      const label = document.createElement("label");
      label.className = "files-list-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = storageObject.id;
      checkbox.checked = state.filesDialogSelection.has(storageObject.id);
      const copy = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = storageObject.filename || storageObject.id;
      const meta = document.createElement("small");
      meta.textContent = `${storageObject.purpose || "file"} · ${storageObject.size_bytes || 0} bytes`;
      copy.append(name, meta);
      const type = document.createElement("small");
      type.textContent = storageObject.content_type || "file";
      label.append(checkbox, copy, type);
      elements.filesList.append(label);
    }
  }
  updateFilesSelectionStatus();
}

function updateFilesSelectionStatus() {
  const count = state.filesDialogSelection.size;
  elements.filesSelectionStatus.textContent = count
    ? `${count} file${count === 1 ? "" : "s"} selected`
    : "No files selected";
  elements.filesConfirm.disabled = count === 0;
  elements.filesConfirm.closest(".files-dialog-footer").hidden = count === 0;
}

async function openFilesDialog() {
  closeActivePopover(false);
  try {
    const payload = await apiFetch(`/api/workspaces/${encodeURIComponent(state.workspaceId)}/files`);
    state.workspaceFiles = payload.files || payload.items || payload || [];
  } catch {
    state.workspaceFiles = [];
  }
  state.filesDialogSelection = new Set(
    state.selectedAttachments.map((attachment) => attachment.id),
  );
  renderFilesDialog();
  if (typeof elements.filesDialog.showModal === "function") {
    elements.filesDialog.showModal();
  } else {
    elements.filesDialog.setAttribute("open", "");
  }
}

function closeFilesDialog() {
  if (typeof elements.filesDialog.close === "function") {
    elements.filesDialog.close();
  } else {
    elements.filesDialog.removeAttribute("open");
  }
}

function confirmFilesSelection() {
  const selected = [...state.workspaceFiles, ...state.storageObjects].filter((storageObject, index, values) => {
    if (values.findIndex((item) => item.id === storageObject.id) !== index) return false;
    return state.filesDialogSelection.has(storageObject.id);
  });
  state.selectedAttachments = selected;
  renderAttachmentChips();
  closeFilesDialog();
  syncComposerState();
}

function handleAddCommand(command) {
  closeActivePopover(false);
  if (command === "files") {
    openFilesDialog();
    return;
  }
  if (command === "drive") {
    openFilesDialog();
    elements.filesSelectionStatus.textContent =
      "Google Drive requires a connector; choose an existing workspace file for now.";
    return;
  }
  const commandText = {
    image: "/image ",
    video: "/video ",
    voice: "/voice ",
    connectors: "@",
    browser: "/browser ",
    workflow: "/workflow ",
    slides: "/slides ",
  }[command];
  if (commandText) {
    elements.input.value = commandText;
    fitComposer();
    syncComposerState();
    elements.input.focus();
  }
}

function startNewChat() {
  renderAppRoute("chat", true);
  stopRunPolling();
  state.currentRunId = null;
  state.selectedRunHistoryId = null;
  state.lastSequence = 0;
  state.events = [];
  state.eventStreamIntegrityIssues = [];
  state.artifacts = [];
  state.storageObjects = [];
  state.selectedAttachments = [];
  renderAttachmentChips();
  resetConversation();
  renderRunHistory();
  renderArtifacts();
  setStatus("idle");
  setArtifactPanelOpen(false);
  elements.input.value = "";
  fitComposer();
  syncComposerState();
  elements.input.focus();
}

function syncSettings() {
  state.apiBase = elements.apiBase.value.trim().replace(/\/$/, "");
  state.tenantId = elements.tenantId.value.trim();
  state.userId = elements.userId.value.trim();
  state.workspaceId = elements.workspaceId.value.trim();
  state.tenantSlug = elements.tenantSlug.value.trim();
  state.ownerDisplayName = elements.ownerDisplayName.value.trim();
  state.authEmail = elements.loginEmail.value.trim();
  localStorage.setItem("taroai.apiBase", state.apiBase);
  localStorage.setItem("taroai.tenantId", state.tenantId);
  localStorage.setItem("taroai.userId", state.userId);
  localStorage.setItem("taroai.workspaceId", state.workspaceId);
  localStorage.setItem("taroai.tenantSlug", state.tenantSlug);
  localStorage.setItem("taroai.ownerDisplayName", state.ownerDisplayName);
  localStorage.setItem("taroai.authEmail", state.authEmail);
}

function requestHeaders() {
  const headers = {
    "Content-Type": "application/json",
    "X-Tenant-ID": state.tenantId,
    "X-User-ID": state.userId,
  };
  if (state.accessToken) {
    const bearerPrefix = "Bearer ";
    headers["Authorization"] = `${bearerPrefix}${state.accessToken}`;
  }
  return headers;
}

async function apiFetch(path, options = {}) {
  syncSettings();
  const response = await fetch(`${state.apiBase}${path}`, {
    ...options,
    headers: {
      ...requestHeaders(),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  const body = parseResponseBody(text);
  if (!response.ok) {
    handleAuthExpired(response.status);
    const detail = body.detail || body.message || response.statusText;
    throw new Error(`${response.status} ${detail}`);
  }
  return body;
}

async function apiText(path) {
  syncSettings();
  const response = await fetch(`${state.apiBase}${path}`, {
    headers: requestHeaders(),
  });
  const text = await response.text();
  if (!response.ok) {
    handleAuthExpired(response.status);
    throw new Error(`${response.status} ${text || response.statusText}`);
  }
  return text;
}

function parseResponseBody(text) {
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

async function raiseStorageFetchError(response) {
  handleAuthExpired(response.status);
  const message = await response.text();
  throw new Error(`${response.status} ${message || response.statusText}`);
}

function appendMessage(kind, text) {
  setChatState("thread");
  const message = document.createElement("article");
  message.className = `message message-${kind}`;
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  message.append(paragraph);
  elements.conversation.append(message);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
}

function resetConversation() {
  setChatState("empty");
  const message = document.createElement("article");
  message.className = "message message-agent";
  const paragraph = document.createElement("p");
  paragraph.textContent =
    "Start a governed run. I will stream timeline events, terminal output, approvals, and artifacts from the Taroai backend.";
  message.append(paragraph);
  elements.conversation.replaceChildren(message);
}

function setStatus(status) {
  state.runStatus = status;
  elements.status.textContent = status;
  elements.status.className = "status-pill";
  if (ACTIVE_RUN_STATUSES.includes(status)) {
    elements.status.classList.add("running");
  }
  if (status === "succeeded") {
    elements.status.classList.add("succeeded");
  }
  if (RETRYABLE_RUN_STATUSES.includes(status)) {
    elements.status.classList.add("failed");
  }
  renderRunControls();
}

function fitComposer() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 180)}px`;
}

function syncComposerState() {
  elements.send.disabled = !elements.input.value.trim();
}

function renderAuth(status) {
  const hasToken = Boolean(state.accessToken);
  elements.authStatus.textContent = status || (hasToken ? "Bearer" : "No token");
  elements.logoutButton.disabled = !hasToken;
}

function renderBootstrap(status = "Not bootstrapped") {
  elements.bootstrapStatus.textContent = status;
}

function handleAuthExpired(status) {
  if ((status === 401 || status === 403) && state.accessToken) {
    stopRunPolling();
    state.accessToken = "";
    sessionStorage.removeItem("taroai.accessToken");
    clearAuthenticatedWorkspaceState("Authentication expired.");
    renderAuth("Auth required");
    appendMessage("agent", "Authentication expired. Please sign in again.");
    return true;
  }
  return false;
}

async function loadReadiness() {
  renderReadiness({ status: "checking" });
  try {
    const readiness = await apiFetch("/readyz");
    state.readiness = readiness;
    renderReadiness(readiness);
  } catch (error) {
    state.readiness = null;
    renderReadiness(null, error);
  }
}

function renderReadiness(readiness = state.readiness, error = null) {
  if (error) {
    elements.readinessStatus.textContent = "Preflight unavailable";
    elements.readinessModel.textContent = error.message;
    elements.readinessSandbox.textContent = "Sandbox unchecked";
    return;
  }
  if (!readiness) {
    elements.readinessStatus.textContent = "Preflight unchecked";
    elements.readinessModel.textContent = "Model unchecked";
    elements.readinessSandbox.textContent = "Sandbox unchecked";
    return;
  }
  if (readiness.status === "checking") {
    elements.readinessStatus.textContent = "Checking preflight";
    elements.readinessModel.textContent = "Model checking";
    elements.readinessSandbox.textContent = "Sandbox checking";
    return;
  }

  const checks = readiness.checks || {};
  const modelGateway = checks.model_gateway || {};
  const sandbox = checks.sandbox || {};
  elements.readinessModel.textContent = describeModelReadiness(modelGateway);
  elements.readinessSandbox.textContent = describeSandboxReadiness(sandbox);
  elements.readinessStatus.textContent =
    modelGateway.configured && sandbox.configured ? "Preflight ready" : "Preflight needs config";
}

function renderRunControls() {
  const hasRun = Boolean(state.currentRunId);
  const cancellable = hasRun && ACTIVE_RUN_STATUSES.includes(state.runStatus);
  const retryable = hasRun && RETRYABLE_RUN_STATUSES.includes(state.runStatus);
  elements.cancelRun.disabled = !cancellable;
  elements.retryRun.disabled = !retryable;
  elements.runControlStatus.textContent = hasRun
    ? `${state.currentRunId} · ${state.runStatus}`
    : "No active run";
  renderExecutionLoop();
  renderRunEvidence();
  renderDeliveryChain();
  renderEventIntegrity();
}

function renderExecutionLoop() {
  const stages = executionLoopStages();
  setExecutionLoopStage(elements.executionLoopRun, stages.run);
  setExecutionLoopStage(elements.executionLoopPlan, stages.plan);
  setExecutionLoopStage(elements.executionLoopSandbox, stages.sandbox);
  setExecutionLoopStage(elements.executionLoopBrowser, stages.browser);
  setExecutionLoopStage(elements.executionLoopArtifact, stages.artifact);
  const executionLoopSummary = executionLoopSummaryLabel(stages);
  elements.executionLoopSummary.textContent = executionLoopSummary;
  elements.executionModelRoute.textContent = modelRouteLabel();
}

function setExecutionLoopStage(element, stage) {
  element.textContent = stage.label;
  const shell = element.closest("[data-execution-stage]");
  if (shell) {
    shell.dataset.state = stage.state;
  }
}

function executionLoopStages() {
  const runtime =
    state.runtimeState && !state.runtimeState.error ? state.runtimeState : null;
  const plannedSteps = runtime && Array.isArray(runtime.plan) ? runtime.plan : [];
  const completedSteps =
    runtime && Array.isArray(runtime.completed_step_ids)
      ? runtime.completed_step_ids
      : [];
  const promotedPaths =
    runtime && Array.isArray(runtime.promoted_sandbox_artifact_paths)
      ? runtime.promoted_sandbox_artifact_paths
      : [];
  return {
    run: runExecutionLoopStage(),
    plan: planExecutionLoopStage(runtime, plannedSteps, completedSteps),
    sandbox: sandboxExecutionLoopStage(runtime),
    browser: browserExecutionLoopStage(runtime),
    artifact: artifactExecutionLoopStage(promotedPaths),
  };
}

function runExecutionLoopStage() {
  if (!state.currentRunId) {
    return executionLoopStageLabel("Idle", "waiting");
  }
  if (RETRYABLE_RUN_STATUSES.includes(state.runStatus)) {
    return executionLoopStageLabel(state.runStatus, "failed");
  }
  if (isRunTerminalStatus(state.runStatus)) {
    return executionLoopStageLabel(state.runStatus, "done");
  }
  return executionLoopStageLabel(state.runStatus || "created", "active");
}

function planExecutionLoopStage(runtime, plannedSteps, completedSteps) {
  if (!state.currentRunId) {
    return executionLoopStageLabel("Waiting", "waiting");
  }
  if (runtime && plannedSteps.length) {
    const label = `${completedSteps.length}/${plannedSteps.length}`;
    const complete = completedSteps.length >= plannedSteps.length;
    return executionLoopStageLabel(label, complete ? "done" : "active");
  }
  if (runtime && runtime.current_step_id) {
    return executionLoopStageLabel("Active", "active");
  }
  if (hasEventType("plan.created") || hasEventType("model.plan.created")) {
    return executionLoopStageLabel("Created", "done");
  }
  return executionLoopStageLabel("Waiting", "waiting");
}

function latestPlanCreatedEvent() {
  return [...state.events].reverse().find((event) => {
    return (
      (event.type === "plan.created" || event.type === "model.plan.created") &&
      event.payload
    );
  });
}

function modelRouteLabel() {
  if (!state.currentRunId) {
    return "No model route";
  }
  const event = latestPlanCreatedEvent();
  if (!event) {
    return "Model route pending";
  }
  const payload = event.payload || {};
  const parts = [];
  parts.push(payload.provider || "provider unknown");
  if (payload.model) {
    parts.push(payload.model);
  }
  const usage = payload.usage || {};
  if (usage.total_tokens !== undefined && usage.total_tokens !== null) {
    parts.push(`${usage.total_tokens} tokens`);
  }
  const attempts = Array.isArray(payload.provider_attempts)
    ? payload.provider_attempts.length
    : 0;
  if (attempts > 1) {
    parts.push(`${attempts} attempts`);
  }
  return parts.join(" · ");
}

function sandboxExecutionLoopStage(runtime) {
  if (!state.currentRunId) {
    return executionLoopStageLabel("Waiting", "waiting");
  }
  if (hasEventType("sandbox.artifact.promoted")) {
    return executionLoopStageLabel("Promoted", "done");
  }
  if (hasEventType("sandbox.session.destroyed")) {
    return executionLoopStageLabel("Closed", "done");
  }
  if (hasEventType("sandbox.command.executed")) {
    return executionLoopStageLabel("Command", "active");
  }
  if (
    (runtime && runtime.sandbox_session_id) ||
    hasEventType("sandbox.session.created")
  ) {
    return executionLoopStageLabel("Active", "active");
  }
  return executionLoopStageLabel("Waiting", "waiting");
}

function browserExecutionLoopStage(runtime) {
  if (!state.currentRunId) {
    return executionLoopStageLabel("Waiting", "waiting");
  }
  if (hasEventType("browser.session.destroyed")) {
    return executionLoopStageLabel("Closed", "done");
  }
  if (hasEventType("browser.action.performed")) {
    return executionLoopStageLabel("Observed", "active");
  }
  if (
    (runtime && runtime.browser_session_id) ||
    hasEventType("browser.session.created")
  ) {
    return executionLoopStageLabel("Active", "active");
  }
  return executionLoopStageLabel("Waiting", "waiting");
}

function artifactExecutionLoopStage(promotedPaths) {
  if (!state.currentRunId) {
    return executionLoopStageLabel("Waiting", "waiting");
  }
  if (state.artifacts.length) {
    return executionLoopStageLabel(`${state.artifacts.length} ready`, "done");
  }
  if (promotedPaths.length) {
    return executionLoopStageLabel(`${promotedPaths.length} promoted`, "done");
  }
  if (
    hasEventType("artifact.created") ||
    hasEventType("sandbox.artifact.promoted")
  ) {
    return executionLoopStageLabel("Created", "done");
  }
  return executionLoopStageLabel("Waiting", "waiting");
}

function executionLoopStageLabel(label, stageState) {
  return { label, state: stageState };
}

function hasEventType(type) {
  return state.events.some((event) => event.type === type);
}

function executionLoopSummaryLabel(stages) {
  if (!state.currentRunId) {
    return "No active run";
  }
  if (stages.artifact.state === "done") {
    return "Artifact ready";
  }
  if (stages.sandbox.state === "active") {
    return "Sandbox active";
  }
  if (stages.browser.state === "active") {
    return "Browser active";
  }
  if (stages.plan.state === "active") {
    return "Plan active";
  }
  if (stages.run.state === "failed") {
    return "Needs review";
  }
  return stages.run.label;
}

function renderRunEvidence() {
  const evidence = buildRunEvidenceItems();
  setEvidenceRow(elements.evidencePlan, evidence.plan);
  setEvidenceRow(elements.evidenceSandbox, evidence.sandbox);
  setEvidenceRow(elements.evidenceArtifact, evidence.artifact);
  setEvidenceRow(elements.evidenceBrowser, evidence.browser);
  setEvidenceRow(elements.evidenceTerminal, evidence.terminal);
  elements.evidenceSummary.textContent = runEvidenceSummaryLabel(evidence);
}

function renderDeliveryChain() {
  const chain = buildDeliveryChainEvidence();
  elements.deliveryChainStatus.textContent = chain.statusLabel;
  elements.deliveryChainStatus.dataset.deliveryChainState = chain.status;
  setDeliveryChainValue(elements.deliveryChainRun, chain.runId);
  setDeliveryChainValue(elements.deliveryChainSandbox, chain.sandboxSessionId);
  setDeliveryChainValue(elements.deliveryChainArtifactStorage, chain.artifactStorageIds);
  setDeliveryChainValue(elements.deliveryChainTerminalStorage, chain.terminalStorageId);
  setDeliveryChainValue(elements.deliveryChainBrowserStorage, chain.browserStorageId);
}

function buildDeliveryChainEvidence() {
  const runtime =
    state.runtimeState && !state.runtimeState.error ? state.runtimeState : null;
  const commandEvent = latestSandboxCommandEvent();
  const commandOutput = commandEvent ? resolveTerminalOutput(commandEvent) : null;
  const terminalStorageObject = commandOutput
    ? storageObjectForTerminalOutputUri(commandOutput.output_uri)
    : null;
  const browserEvent = latestBrowserEvent();
  const browserStorageObject = browserEvent
    ? storageObjectForBrowserCapture(browserEvent.payload || {})
    : null;
  const artifactStorageIds = readyStorageBackedArtifacts()
    .map((item) => item.storageObject.id)
    .filter(Boolean);
  const sandboxSessionId =
    (runtime && runtime.sandbox_session_id) ||
    (commandOutput && commandOutput.session_id) ||
    "--";
  const chain = {
    runId: state.currentRunId || "--",
    sandboxSessionId,
    artifactStorageIds,
    terminalStorageId: terminalStorageObject ? terminalStorageObject.id : "--",
    browserStorageId: browserStorageObject ? browserStorageObject.id : "--",
  };
  const hasRequiredDelivery =
    Boolean(state.currentRunId) &&
    chain.sandboxSessionId !== "--" &&
    chain.artifactStorageIds.length > 0 &&
    chain.terminalStorageId !== "--";
  if (!state.currentRunId) {
    return { ...chain, statusLabel: "No delivery chain", status: "waiting" };
  }
  if (RETRYABLE_RUN_STATUSES.includes(state.runStatus)) {
    return { ...chain, statusLabel: "Delivery incomplete", status: "failed" };
  }
  if (hasRequiredDelivery) {
    return { ...chain, statusLabel: "Delivery chain complete", status: "ready" };
  }
  return { ...chain, statusLabel: "Collecting delivery evidence", status: "active" };
}

function setDeliveryChainValue(element, value) {
  const text = Array.isArray(value)
    ? value.length
      ? value.slice(0, 2).join(", ") + (value.length > 2 ? ` +${value.length - 2}` : "")
      : "--"
    : value || "--";
  element.textContent = text;
  element.dataset.deliveryChainValue = text === "--" ? "" : text;
}

function renderEventIntegrity() {
  const integrity = buildEventIntegrityEvidence();
  elements.eventIntegrityStatus.textContent = integrity.statusLabel;
  elements.eventIntegrityStatus.dataset.eventIntegrityState = integrity.status;
  setEventIntegrityValue(elements.eventIntegrityCount, integrity.countLabel);
  setEventIntegrityValue(elements.eventIntegritySequence, integrity.sequenceLabel);
  setEventIntegrityValue(elements.eventIntegrityClosure, integrity.closureLabel);
}

function buildEventIntegrityEvidence() {
  if (!state.currentRunId || !state.events.length) {
    return {
      status: "waiting",
      statusLabel: "No event stream",
      countLabel: "--",
      sequenceLabel: "--",
      closureLabel: "--",
    };
  }

  const eventSequences = state.events
    .map((event) => eventSequence(event))
    .filter((sequence) => sequence !== null);
  const eventsMissingSequence = state.events.length !== eventSequences.length;
  const duplicateSequenceCount =
    eventSequences.length - new Set(eventSequences).size;
  const sequenceViolation = sequenceListHasViolation(eventSequences);
  const planIndex = firstEventTypeIndexOf(["plan.created", "model.plan.created"]);
  const skillIndex = firstEventTypeIndex("skill.workflow_invoked");
  const browserIndex = firstEventTypeIndex("browser.action.performed");
  const commandIndex = firstEventTypeIndex("sandbox.command.executed");
  const artifactIndex = firstEventTypeIndex("sandbox.artifact.promoted");
  const succeededIndex = firstEventTypeIndex("run.succeeded");
  const closureComplete =
    commandIndex !== -1 &&
    artifactIndex !== -1 &&
    succeededIndex !== -1 &&
    (planIndex === -1 || planIndex < commandIndex) &&
    (skillIndex === -1 || skillIndex < commandIndex) &&
    (browserIndex === -1 || browserIndex < succeededIndex) &&
    commandIndex < artifactIndex &&
    artifactIndex < succeededIndex;

  if (
    state.eventStreamIntegrityIssues.length ||
    eventsMissingSequence ||
    duplicateSequenceCount > 0 ||
    sequenceViolation
  ) {
    return {
      status: "failed",
      statusLabel: "Event stream needs review",
      countLabel: `${state.events.length} events`,
      sequenceLabel:
        state.eventStreamIntegrityIssues[0] ||
        (eventsMissingSequence ? "event stream sequence is missing" : null) ||
        "event stream sequence is not monotonic",
      closureLabel: describeEventClosure(
        planIndex,
        skillIndex,
        browserIndex,
        commandIndex,
        artifactIndex,
        succeededIndex,
      ),
    };
  }

  if (closureComplete) {
    return {
      status: "ready",
      statusLabel: "Event stream verified",
      countLabel: `${state.events.length} events`,
      sequenceLabel: describeEventSequenceRange(eventSequences),
      closureLabel: describeEventClosure(
        planIndex,
        skillIndex,
        browserIndex,
        commandIndex,
        artifactIndex,
        succeededIndex,
      ),
    };
  }

  return {
    status: "active",
    statusLabel: "Collecting event stream",
    countLabel: `${state.events.length} events`,
    sequenceLabel: describeEventSequenceRange(eventSequences),
    closureLabel: describeEventClosure(
      planIndex,
      skillIndex,
      browserIndex,
      commandIndex,
      artifactIndex,
      succeededIndex,
    ),
  };
}

function firstEventTypeIndex(type) {
  return state.events.findIndex((event) => event.type === type);
}

function firstEventTypeIndexOf(types) {
  return state.events.findIndex((event) => types.includes(event.type));
}

function describeEventSequenceRange(eventSequences) {
  if (!eventSequences.length) {
    return "No sequence";
  }
  const firstSequence = eventSequences[0];
  const lastSequence = eventSequences[eventSequences.length - 1];
  return firstSequence === lastSequence
    ? `#${firstSequence} monotonic`
    : `#${firstSequence}-#${lastSequence} monotonic`;
}

function describeEventClosure(
  planIndex,
  skillIndex,
  browserIndex,
  commandIndex,
  artifactIndex,
  succeededIndex,
) {
  if (commandIndex === -1) {
    return "Waiting for command";
  }
  if (artifactIndex === -1) {
    return "Waiting for artifact";
  }
  if (succeededIndex === -1) {
    return "Waiting for success";
  }
  if (!(commandIndex < artifactIndex && artifactIndex < succeededIndex)) {
    return "Closure out of order";
  }
  if (planIndex !== -1) {
    if (!(planIndex < commandIndex)) {
      return "Closure out of order";
    }
  }
  if (skillIndex !== -1 && !(skillIndex < commandIndex)) {
    return "Closure out of order";
  }
  if (browserIndex !== -1 && !(browserIndex < succeededIndex)) {
    return "Closure out of order";
  }
  return eventClosureStages(
    planIndex,
    skillIndex,
    browserIndex,
    commandIndex,
    artifactIndex,
    succeededIndex,
  );
}

function eventClosureStages(
  planIndex,
  skillIndex,
  browserIndex,
  commandIndex,
  artifactIndex,
  succeededIndex,
) {
  return [
    planIndex !== -1 ? { index: planIndex, label: "plan" } : null,
    skillIndex !== -1 ? { index: skillIndex, label: "skill" } : null,
    { index: commandIndex, label: "command" },
    browserIndex !== -1 ? { index: browserIndex, label: "browser" } : null,
    { index: artifactIndex, label: "artifact" },
    { index: succeededIndex, label: "succeeded" },
  ]
    .filter(Boolean)
    .sort((left, right) => left.index - right.index)
    .map((stage) => stage.label)
    .join(" -> ");
}

function setEventIntegrityValue(element, value) {
  const text = value || "--";
  element.textContent = text;
  element.dataset.eventIntegrityValue = text === "--" ? "" : text;
}

function buildRunEvidenceItems() {
  const runtime =
    state.runtimeState && !state.runtimeState.error ? state.runtimeState : null;
  const plannedSteps = runtime && Array.isArray(runtime.plan) ? runtime.plan : [];
  const completedSteps =
    runtime && Array.isArray(runtime.completed_step_ids)
      ? runtime.completed_step_ids
      : [];
  const commandEvent = latestSandboxCommandEvent();
  const commandOutput = commandEvent ? resolveTerminalOutput(commandEvent) : null;
  const artifactStorageCount = state.artifacts.filter((artifact) =>
    storageObjectForArtifact(artifact)
  ).length;
  const browserEvent = latestBrowserEvent();
  const browserStorageObject = browserEvent
    ? storageObjectForBrowserCapture(browserEvent.payload || {})
    : null;
  return {
    plan: planEvidenceItem(runtime, plannedSteps, completedSteps),
    sandbox: sandboxEvidenceItem(commandOutput),
    artifact: artifactEvidenceItem(artifactStorageCount),
    browser: browserEvidenceItem(browserEvent, browserStorageObject),
    terminal: terminalEvidenceItem(commandOutput),
  };
}

function planEvidenceItem(runtime, plannedSteps, completedSteps) {
  if (!state.currentRunId) {
    return evidenceItem("Waiting", "waiting");
  }
  if (plannedSteps.length) {
    return evidenceItem(
      `${completedSteps.length}/${plannedSteps.length} steps`,
      completedSteps.length >= plannedSteps.length ? "done" : "active"
    );
  }
  if (runtime && runtime.current_step_id) {
    return evidenceItem("Planning", "active");
  }
  if (hasEventType("plan.created") || hasEventType("model.plan.created")) {
    return evidenceItem("Created", "done");
  }
  return evidenceItem("Waiting", "waiting");
}

function sandboxEvidenceItem(commandOutput) {
  if (!state.currentRunId) {
    return evidenceItem("Waiting", "waiting");
  }
  if (!commandOutput) {
    if (hasEventType("sandbox.session.created")) {
      return evidenceItem("Session ready", "active");
    }
    return evidenceItem("Waiting", "waiting");
  }
  if (commandOutput.exit_code === 0) {
    return evidenceItem("Command passed", "done");
  }
  return evidenceItem(`Exit ${commandOutput.exit_code}`, "failed");
}

function artifactEvidenceItem(artifactStorageCount) {
  if (!state.currentRunId) {
    return evidenceItem("Waiting", "waiting");
  }
  if (artifactStorageCount > 0) {
    return evidenceItem(`${artifactStorageCount} downloadable`, "done");
  }
  if (state.artifacts.length) {
    return evidenceItem("Created", "active");
  }
  if (hasEventType("sandbox.artifact.promoted")) {
    return evidenceItem("Promoted", "done");
  }
  return evidenceItem("Waiting", "waiting");
}

function browserEvidenceItem(browserEvent, browserStorageObject) {
  if (!state.currentRunId) {
    return evidenceItem("Waiting", "waiting");
  }
  if (browserStorageObject) {
    return evidenceItem("Capture stored", "done");
  }
  if (browserEvent) {
    const action = browserEvent.payload && browserEvent.payload.action_type;
    return evidenceItem(action || "Observed", "active");
  }
  if (hasEventType("browser.session.created")) {
    return evidenceItem("Session ready", "active");
  }
  return evidenceItem("Optional", "waiting");
}

function terminalEvidenceItem(commandOutput) {
  if (!state.currentRunId) {
    return evidenceItem("Waiting", "waiting");
  }
  if (!commandOutput) {
    return evidenceItem("Waiting", "waiting");
  }
  if (commandOutput.stdout || commandOutput.stderr) {
    return evidenceItem("Raw output", "failed");
  }
  if (
    commandOutput.stdout_length !== undefined ||
    commandOutput.stderr_length !== undefined ||
    commandOutput.output_uri
  ) {
    return evidenceItem("Safe summary", "done");
  }
  return evidenceItem("Exit summary", "active");
}

function setEvidenceRow(row, item) {
  row.setAttribute("data-evidence-status", item.status);
  const label = row.querySelector("strong");
  if (label) {
    label.textContent = item.label;
  }
}

function evidenceItem(label, status) {
  return { label, status };
}

function runEvidenceSummaryLabel(evidence) {
  if (!state.currentRunId) {
    return "No run evidence";
  }
  const required = [evidence.plan, evidence.sandbox, evidence.artifact, evidence.terminal];
  if (required.some((item) => item.status === "failed")) {
    return "Evidence needs review";
  }
  if (required.every((item) => item.status === "done")) {
    return "Artifact delivery proven";
  }
  if (required.some((item) => item.status === "active")) {
    return "Collecting evidence";
  }
  return "Waiting for evidence";
}

function describeModelReadiness(modelGateway) {
  if (modelGateway.configured) {
    return "Model ready";
  }
  const missing = modelGateway.missing || [];
  return missing.length
    ? `Model missing: ${missing.join(", ")}`
    : "Model not configured";
}

function describeSandboxReadiness(sandbox) {
  const provider = sandbox.provider || "sandbox";
  if (sandbox.configured) {
    if (sandbox.capabilities_checked) {
      const isolated =
        sandbox.network_isolation_declared &&
        sandbox.filesystem_isolation_declared &&
        sandbox.resource_limits_declared;
      return isolated
        ? `Sandbox isolated: ${provider}`
        : `Sandbox PoC: ${provider}`;
    }
    return `Sandbox ready: ${provider}`;
  }
  if (sandbox.controller_required && !sandbox.controller_configured) {
    return `Sandbox ${provider}: controller missing`;
  }
  const missing = sandbox.missing || [];
  return missing.length
    ? `Sandbox ${provider} missing: ${missing.join(", ")}`
    : `Sandbox ${provider} not configured`;
}

async function bootstrapTenant() {
  syncSettings();
  const bootstrapToken = elements.bootstrapToken.value.trim();
  const email = elements.loginEmail.value.trim();
  if (!bootstrapToken || !state.tenantSlug || !email || !elements.loginPassword.value) {
    renderBootstrap("Missing");
    return;
  }
  renderBootstrap("Bootstrapping");
  try {
    const bootstrapPath = "/api/tenants/bootstrap";
    const response = await fetch(`${state.apiBase}${bootstrapPath}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bootstrap-Token": bootstrapToken,
      },
      body: JSON.stringify({
        tenant_slug: state.tenantSlug,
        owner_email: email,
        owner_display_name: state.ownerDisplayName || "Owner",
        owner_password: elements.loginPassword.value,
      }),
    });
    const text = await response.text();
    const result = parseResponseBody(text);
    if (!response.ok) {
      const detail = result.detail || result.message || response.statusText;
      throw new Error(`${response.status} ${detail}`);
    }
    if (result.tenant_id) {
      state.tenantId = result.tenant_id;
      elements.tenantId.value = result.tenant_id;
      localStorage.setItem("taroai.tenantId", state.tenantId);
    }
    if (result.owner_user_id) {
      state.userId = result.owner_user_id;
      elements.userId.value = result.owner_user_id;
      localStorage.setItem("taroai.userId", state.userId);
    }
    if (result.starter_workspace_id) {
      state.workspaceId = result.starter_workspace_id;
      elements.workspaceId.value = result.starter_workspace_id;
      localStorage.setItem("taroai.workspaceId", state.workspaceId);
    }
    elements.bootstrapToken.value = "";
    renderBootstrap("Tenant ready");
    await login();
  } catch (error) {
    elements.bootstrapToken.value = "";
    renderBootstrap("Bootstrap failed");
    appendMessage("agent", error.message);
  }
}

async function login() {
  syncSettings();
  const email = elements.loginEmail.value.trim();
  const password = elements.loginPassword.value;
  if (!email || !password) {
    renderAuth("Missing");
    return;
  }
  renderAuth("Signing in");
  try {
    const result = await apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: state.tenantId,
        email,
        password,
      }),
    });
    state.accessToken = result.access_token || "";
    if (state.accessToken) {
      sessionStorage.setItem("taroai.accessToken", state.accessToken);
    }
    if (result.tenant_id) {
      state.tenantId = result.tenant_id;
      elements.tenantId.value = result.tenant_id;
      localStorage.setItem("taroai.tenantId", state.tenantId);
    }
    if (result.user_id) {
      state.userId = result.user_id;
      elements.userId.value = result.user_id;
      localStorage.setItem("taroai.userId", state.userId);
    }
    elements.loginPassword.value = "";
    renderAuth("Bearer");
    await loadReadiness();
    await loadCustomerSuccess();
    await loadSolutionPacks();
    await loadWorkspaceSkills();
    await loadRunHistory();
    window.dispatchEvent(new CustomEvent("taroai:auth-changed", { detail: { authenticated: true } }));
  } catch (error) {
    state.accessToken = "";
    sessionStorage.removeItem("taroai.accessToken");
    clearAuthenticatedWorkspaceState("Authentication failed.");
    renderAuth("Auth failed");
    appendMessage("agent", error.message);
  }
}

async function logout() {
  if (!state.accessToken) {
    return;
  }
  try {
    await apiFetch(`/api/auth/logout`, { method: "POST" });
  } finally {
    stopRunPolling();
    state.accessToken = "";
    sessionStorage.removeItem("taroai.accessToken");
    clearAuthenticatedWorkspaceState();
    renderAuth();
    window.dispatchEvent(new CustomEvent("taroai:auth-changed", { detail: { authenticated: false } }));
  }
}

function clearAuthenticatedWorkspaceState(terminalMessage = "Signed out.") {
  state.currentRunId = null;
  state.lastSequence = 0;
  state.events = [];
  state.eventStreamIntegrityIssues = [];
  state.artifacts = [];
  state.storageObjects = [];
  state.runHistory = [];
  state.runTrace = null;
  state.runtimeState = null;
  state.selectedRunHistoryId = null;
  state.deliveredRunIds.clear();
  state.previewedRunIds.clear();
  state.feedbackSubmittedRunIds.clear();
  state.pendingApprovalId = null;
  state.customerSuccess = null;
  state.solutionPacks = [];
  state.selectedSolutionPackId = null;
  state.workspaceSkills = [];
  state.selectedSkillId = null;
  state.runStatus = "idle";
  clearBrowserPreview();
  renderCustomerSuccess();
  renderSolutionPacks();
  renderWorkspaceSkills();
  renderRunHistory();
  renderBrowser();
  renderArtifacts();
  renderRunTrace();
  renderRuntimeState();
  renderExecutionLoop();
  renderRunEvidence();
  renderDeliveryChain();
  renderApproval();
  renderRunControls();
  resetConversation();
  setStatus("idle");
  renderTerminalOutputStorageObject(null);
  renderTerminal(terminalMessage);
}

async function loadCustomerSuccess() {
  renderCustomerSuccess({ status: "loading" });
  try {
    const summary = await apiFetch("/api/customer-success/summary");
    const feedback = await apiFetch("/api/customer-success/feedback").catch(() => []);
    const evaluationCandidates = await apiFetch(
      "/api/customer-success/evaluation-candidates"
    ).catch(() => []);
    const solutionPackCandidates = await apiFetch(
      "/api/customer-success/solution-pack-candidates"
    ).catch(() => []);
    const publicationDrafts = await apiFetch(
      "/api/customer-success/solution-pack-drafts"
    ).catch(() => []);
    state.customerSuccess = {
      summary,
      feedback,
      evaluationCandidates,
      solutionPackCandidates,
      publicationDrafts,
    };
    renderCustomerSuccess(state.customerSuccess);
  } catch (error) {
    state.customerSuccess = null;
    renderCustomerSuccess(null, error);
  }
}

async function loadSolutionPacks() {
  renderSolutionPacks({ status: "loading" });
  try {
    const result = await apiFetch("/api/solution-packs");
    const packs = Array.isArray(result) ? result : result.items || [];
    state.solutionPacks = packs;
    if (
      state.selectedSolutionPackId &&
      !state.solutionPacks.some((pack) => pack.manifest?.id === state.selectedSolutionPackId)
    ) {
      state.selectedSolutionPackId = null;
    }
    if (!state.selectedSolutionPackId) {
      const publishedPack = state.solutionPacks.find((pack) => pack.status === "published");
      const firstPack = publishedPack || state.solutionPacks[0] || null;
      state.selectedSolutionPackId = firstPack?.manifest?.id || null;
    }
    renderSolutionPacks();
  } catch (error) {
    state.solutionPacks = [];
    state.selectedSolutionPackId = null;
    renderSolutionPacks(null, error);
  }
}

async function loadWorkspaceSkills() {
  renderWorkspaceSkills({ status: "loading" });
  try {
    const skills = await apiFetch(
      `/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills`
    );
    state.workspaceSkills = Array.isArray(skills) ? skills : [];
    if (
      state.selectedSkillId &&
      !state.workspaceSkills.some((skill) => skill.skill_id === state.selectedSkillId)
    ) {
      state.selectedSkillId = null;
    }
    if (!state.selectedSkillId) {
      const readySkill = state.workspaceSkills.find((skill) => skill.invocation_ready);
      state.selectedSkillId = readySkill ? readySkill.skill_id : null;
    }
    renderWorkspaceSkills();
  } catch (error) {
    state.workspaceSkills = [];
    state.selectedSkillId = null;
    renderWorkspaceSkills(null, error);
  }
}

async function loadRunHistory() {
  if (window.__taroaiThreadChat) {
    return window.taroaiChat?.loadThreads?.();
  }
  renderRunHistory({ status: "loading" });
  try {
    const result = await apiFetch(
      `/api/runs?workspace_id=${encodeURIComponent(state.workspaceId)}&limit=20`
    );
    state.runHistory = result.items || [];
    renderRunHistory();
  } catch (error) {
    state.runHistory = [];
    renderRunHistory(null, error);
  }
}

function renderSolutionPacks(data = state.solutionPacks, error = null) {
  elements.solutionPackList.replaceChildren();
  if (error) {
    elements.solutionPackStatus.textContent = "Unavailable";
    elements.solutionPackInstallStatus.textContent = error.message;
    elements.solutionPackInstallButton.disabled = true;
    appendSolutionPackEmpty(error.message);
    return;
  }
  if (data && data.status === "loading") {
    elements.solutionPackStatus.textContent = "Loading packs";
    elements.solutionPackInstallStatus.textContent = "Loading";
    elements.solutionPackInstallButton.disabled = true;
    appendSolutionPackEmpty("Loading solution packs.");
    return;
  }

  const packs = Array.isArray(data) ? data : [];
  const selectedPack = selectedSolutionPack();
  elements.solutionPackStatus.textContent = packs.length
    ? `${packs.length} available`
    : "No packs loaded";
  if (!packs.length) {
    elements.solutionPackInstallButton.disabled = true;
    elements.solutionPackInstallStatus.textContent = "No solution packs";
    appendSolutionPackEmpty("No solution packs.");
    return;
  }

  for (const pack of packs) {
    const manifest = pack.manifest || {};
    if (!manifest.id) {
      continue;
    }
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.solutionPackId = manifest.id;
    button.setAttribute("data-solution-pack-id", manifest.id);
    button.classList.toggle("is-selected", manifest.id === state.selectedSolutionPackId);
    const title = document.createElement("span");
    title.textContent = manifest.name || manifest.id;
    const meta = document.createElement("small");
    const skillCount = Array.isArray(manifest.skills) ? manifest.skills.length : 0;
    meta.textContent = `${pack.status || "draft"} · v${manifest.version || "--"} · ${skillCount} skills`;
    button.append(title, meta);
    item.append(button);
    elements.solutionPackList.append(item);
  }

  const canInstall = Boolean(selectedPack && selectedPack.status === "published");
  elements.solutionPackInstallButton.disabled = !canInstall;
  if (!selectedPack) {
    elements.solutionPackInstallStatus.textContent = "Select a published pack";
  } else if (!canInstall) {
    elements.solutionPackInstallStatus.textContent = "Publish pack before install";
  } else {
    elements.solutionPackInstallStatus.textContent =
      `Ready: ${selectedPack.manifest.name || selectedPack.manifest.id}`;
  }
}

function appendSolutionPackEmpty(message) {
  const empty = document.createElement("li");
  empty.textContent = message;
  elements.solutionPackList.append(empty);
}

function selectedSolutionPack() {
  return state.solutionPacks.find((pack) => {
    return pack.manifest && pack.manifest.id === state.selectedSolutionPackId;
  });
}

function selectSolutionPack(packId) {
  state.selectedSolutionPackId = packId;
  renderSolutionPacks();
}

async function installSelectedSolutionPack() {
  const pack = selectedSolutionPack();
  if (!pack || pack.status !== "published") {
    renderSolutionPacks();
    return;
  }
  elements.solutionPackInstallButton.disabled = true;
  elements.solutionPackInstallStatus.textContent = "Installing pack";
  let installedMessage = "";
  try {
    const installation = await apiFetch(
      `/api/solution-packs/${encodeURIComponent(pack.manifest.id)}/install`,
      {
        method: "POST",
        body: JSON.stringify({ workspace_ids: [state.workspaceId] }),
      }
    );
    const installedCount = Array.isArray(installation.installed_skill_ids)
      ? installation.installed_skill_ids.length
      : 0;
    installedMessage = `Solution pack installed: ${installedCount} skills`;
    elements.solutionPackInstallStatus.textContent = installedMessage;
    await loadWorkspaceSkills();
  } catch (error) {
    elements.solutionPackInstallStatus.textContent = error.message;
  } finally {
    renderSolutionPacks();
    if (installedMessage) {
      elements.solutionPackInstallStatus.textContent = installedMessage;
    }
  }
}

function renderRunHistory(data = state.runHistory, error = null) {
  elements.runHistoryList.replaceChildren();
  if (error) {
    elements.runHistoryStatus.textContent = "Unavailable";
    appendRunHistoryEmpty("No recent chats.");
    return;
  }
  if (data && data.status === "loading") {
    elements.runHistoryStatus.textContent = "Loading runs";
    appendRunHistoryEmpty("No recent chats.");
    return;
  }

  const runs = Array.isArray(data) ? data : [];
  elements.runHistoryStatus.textContent = runs.length
    ? `${runs.length} recent runs`
    : "No runs loaded";
  if (!runs.length) {
    appendRunHistoryEmpty("No runs.");
    return;
  }

  for (const run of runs) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.runHistoryId = run.id;
    button.setAttribute("data-run-history-id", run.id);
    if (run.id === state.selectedRunHistoryId || run.id === state.currentRunId) {
      button.classList.add("is-selected");
    }
    const title = document.createElement("span");
    title.textContent = run.message || run.id;
    const meta = document.createElement("small");
    meta.textContent = `${run.status} · ${shortDateTime(run.created_at)}`;
    button.append(title, meta);
    item.append(button);
    elements.runHistoryList.append(item);
  }
}

function appendRunHistoryEmpty(message) {
  const empty = document.createElement("li");
  empty.textContent = message;
  elements.runHistoryList.append(empty);
}

function shortDateTime(value) {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderConversationForRun(run) {
  setChatState("thread");
  elements.conversation.replaceChildren();
  appendMessage("user", run.message || "Run this agent task.");

  if (state.events.length) {
    const trace = document.createElement("div");
    trace.className = "tool-trace";
    trace.setAttribute("aria-label", "Run activity");
    for (const event of state.events.slice(-6)) {
      const row = document.createElement("div");
      row.className = "tool-trace-row";
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = event.type?.includes("command") ? "⌁" : "⌘";
      const label = document.createElement("span");
      label.textContent = event.type || "Run event";
      row.append(icon, label);
      trace.append(row);
    }
    elements.conversation.append(trace);
  }

  const status = state.runStatus || run.status || "created";
  appendMessage(
    "agent",
    `Run status: ${status}. Open Operations for the full timeline, terminal, approvals, and evidence.`,
  );
  const actions = document.createElement("div");
  actions.className = "message-actions";
  for (const label of ["Copy", "Useful", "Needs work", "More"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    actions.append(button);
  }
  elements.conversation.append(actions);
}

async function selectRunFromHistory(runId) {
  const run = state.runHistory.find((item) => item.id === runId);
  if (!run) {
    return;
  }
  stopRunPolling();
  state.currentRunId = run.id;
  state.selectedRunHistoryId = run.id;
  state.lastSequence = 0;
  state.events = [];
  state.eventStreamIntegrityIssues = [];
  state.artifacts = [];
  state.storageObjects = [];
  state.runTrace = null;
  state.runtimeState = null;
  renderConversationForRun(run);
  setStatus(run.status || "created");
  renderTimeline();
  renderRunTrace();
  renderRuntimeState();
  renderBrowser();
  renderExecutionLoop();
  renderRunEvidence();
  renderDeliveryChain();
  renderArtifacts();
  renderApproval();
  renderTerminal("Loading selected run...");
  renderRunHistory();
  await refreshRun();
  renderConversationForRun({ ...run, status: state.runStatus });
  if (ACTIVE_RUN_STATUSES.includes(state.runStatus)) {
    startRunPolling();
  }
}

function renderWorkspaceSkills(data = state.workspaceSkills, error = null) {
  elements.workspaceSkillsList.replaceChildren();
  if (error) {
    elements.workspaceSkillsStatus.textContent = "Unavailable";
    elements.skillInvokeStatus.textContent = error.message;
    elements.skillInvokeButton.disabled = true;
    appendWorkspaceSkillEmpty(error.message);
    return;
  }
  if (data && data.status === "loading") {
    elements.workspaceSkillsStatus.textContent = "Loading skills";
    elements.skillInvokeStatus.textContent = "Loading";
    elements.skillInvokeButton.disabled = true;
    appendWorkspaceSkillEmpty("Loading skills.");
    return;
  }

  const skills = Array.isArray(data) ? data : [];
  const selectedSkill = selectedWorkspaceSkill();
  elements.workspaceSkillsStatus.textContent = skills.length
    ? `${skills.length} installed`
    : "No skills loaded";
  if (!skills.length) {
    elements.skillInvokeButton.disabled = true;
    elements.skillInvokeStatus.textContent = "No installed skills";
    appendWorkspaceSkillEmpty("No installed skills.");
    return;
  }

  for (const skill of skills) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.workspaceSkillId = skill.skill_id;
    button.setAttribute("data-workspace-skill-id", skill.skill_id);
    button.classList.toggle("is-selected", skill.skill_id === state.selectedSkillId);
    const title = document.createElement("span");
    title.textContent = skill.skill_id;
    const meta = document.createElement("small");
    const missingScopes = skill.missing_required_scopes || [];
    const readyLabel = skill.invocation_ready ? "ready" : "blocked";
    const mode = skill.invocation_mode || "unavailable";
    meta.textContent = missingScopes.length
      ? `${mode} · missing ${missingScopes.join(", ")}`
      : `${mode} · ${readyLabel}`;
    button.append(title, meta);
    item.append(button);
    elements.workspaceSkillsList.append(item);
  }

  elements.skillInvokeButton.disabled = !selectedSkill || !selectedSkill.invocation_ready;
  if (!selectedSkill) {
    elements.skillInvokeStatus.textContent = "Select a ready skill";
  } else if (!selectedSkill.invocation_ready) {
    const missingScopes = selectedSkill.missing_required_scopes || [];
    elements.skillInvokeStatus.textContent = missingScopes.length
      ? `Missing scopes: ${missingScopes.join(", ")}`
      : "Skill is not ready";
  } else {
    elements.skillInvokeStatus.textContent = `Ready: ${selectedSkill.skill_id}`;
  }
}

function appendWorkspaceSkillEmpty(message) {
  const empty = document.createElement("li");
  empty.textContent = message;
  elements.workspaceSkillsList.append(empty);
}

function selectedWorkspaceSkill() {
  return state.workspaceSkills.find((skill) => skill.skill_id === state.selectedSkillId);
}

function selectWorkspaceSkill(skillId) {
  state.selectedSkillId = skillId;
  renderWorkspaceSkills();
}

async function invokeSelectedWorkspaceSkill() {
  const skill = selectedWorkspaceSkill();
  if (!skill || !skill.invocation_ready) {
    renderWorkspaceSkills();
    return;
  }
  let input;
  try {
    input = JSON.parse(elements.skillInvokeInput.value || "{}");
  } catch {
    elements.skillInvokeStatus.textContent = "Input JSON is invalid";
    return;
  }
  elements.skillInvokeButton.disabled = true;
  elements.skillInvokeStatus.textContent = "Invoking skill";
  let invokedRunId = "";
  try {
    const result = await apiFetch(
      `/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills/${encodeURIComponent(skill.skill_id)}/invoke`,
      {
        method: "POST",
        body: JSON.stringify({ input }),
      }
    );
    state.currentRunId = result.run_id || result.output?.run_id;
    if (state.currentRunId) {
      invokedRunId = state.currentRunId;
      state.selectedRunHistoryId = state.currentRunId;
      state.lastSequence = 0;
      state.events = [];
      state.eventStreamIntegrityIssues = [];
      state.artifacts = [];
      state.storageObjects = [];
      setStatus(result.status || result.output?.status || "running");
      elements.skillInvokeStatus.textContent = `Run ${state.currentRunId}`;
      switchWorkbenchView("run");
      await refreshRun();
      loadRunHistory();
      if (ACTIVE_RUN_STATUSES.includes(state.runStatus)) {
        startRunPolling();
      }
    } else {
      elements.skillInvokeStatus.textContent = "Skill invoked";
    }
  } catch (error) {
    elements.skillInvokeStatus.textContent = error.message;
  } finally {
    renderWorkspaceSkills();
    if (invokedRunId) {
      elements.skillInvokeStatus.textContent = `Run ${invokedRunId}`;
    }
  }
}

function renderCustomerSuccess(data = state.customerSuccess, error = null) {
  if (error) {
    elements.customerSuccessStatus.textContent = "Unavailable";
    elements.customerSuccessHealth.textContent = "--";
    elements.customerSuccessRuns.textContent = "--";
    elements.customerSuccessFeedback.textContent = error.message;
    elements.customerSuccessEvalCandidates.textContent = "--";
    elements.customerSuccessPackCandidates.textContent = "--";
    setCandidateActionStatus(error.message);
    renderEvaluationCandidateReview({ evaluationCandidates: [] });
    renderSolutionPackCandidateReview({ solutionPackCandidates: [] });
    renderSolutionPackDrafts({ publicationDrafts: [] }, error.message);
    return;
  }
  if (!data) {
    elements.customerSuccessStatus.textContent = "Not loaded";
    elements.customerSuccessHealth.textContent = "--";
    elements.customerSuccessRuns.textContent = "--";
    elements.customerSuccessFeedback.textContent = "--";
    elements.customerSuccessEvalCandidates.textContent = "--";
    elements.customerSuccessPackCandidates.textContent = "--";
    setCandidateActionStatus("Candidate actions idle");
    renderEvaluationCandidateReview({ evaluationCandidates: [] });
    renderSolutionPackCandidateReview({ solutionPackCandidates: [] });
    renderSolutionPackDrafts({ publicationDrafts: [] });
    return;
  }
  if (data.status === "loading") {
    elements.customerSuccessStatus.textContent = "Loading";
    elements.customerSuccessHealth.textContent = "--";
    elements.customerSuccessRuns.textContent = "--";
    elements.customerSuccessFeedback.textContent = "--";
    elements.customerSuccessEvalCandidates.textContent = "--";
    elements.customerSuccessPackCandidates.textContent = "--";
    setCandidateActionStatus("Loading candidates");
    renderEvaluationCandidateReview({ evaluationCandidates: [] }, "Loading eval candidates");
    renderSolutionPackCandidateReview(
      { solutionPackCandidates: [] },
      "Loading pack candidates"
    );
    renderSolutionPackDrafts({ publicationDrafts: [] }, "Loading drafts");
    return;
  }

  const summary = data.summary || {};
  const adoption = summary.adoption || {};
  const health = summary.health || {};
  const evaluationCandidates = data.evaluationCandidates || [];
  const solutionPackCandidates = data.solutionPackCandidates || [];
  elements.customerSuccessStatus.textContent = "Loaded";
  elements.customerSuccessHealth.textContent = health.band || "--";
  elements.customerSuccessRuns.textContent =
    adoption.runs_created === undefined
      ? "--"
      : `${adoption.runs_completed || 0}/${adoption.runs_created}`;
  elements.customerSuccessFeedback.textContent = String(
    adoption.feedback_submitted ?? (data.feedback || []).length
  );
  elements.customerSuccessEvalCandidates.textContent =
    candidateQueueLabel(evaluationCandidates);
  elements.customerSuccessPackCandidates.textContent =
    candidateQueueLabel(solutionPackCandidates);
  setCandidateActionStatus();
  renderEvaluationCandidateReview(data);
  renderSolutionPackCandidateReview(data);
  renderSolutionPackDrafts(data);
}

function setCandidateActionStatus(message = "") {
  elements.customerSuccessCandidateStatus.textContent =
    message || "Candidate actions idle";
}

function setMissingSkillStatus(message = "") {
  elements.customerSuccessMissingSkillStatus.textContent =
    message || "Request idle";
}

function candidateQueueLabel(candidates) {
  const pending = candidates.filter((candidate) => {
    return candidate.status === "pending_review";
  }).length;
  return `${pending}/${candidates.length}`;
}

async function submitMissingSkillFeedback() {
  const missingSkillName = elements.customerSuccessMissingSkillName.value.trim();
  const solutionPackId =
    elements.customerSuccessMissingSkillSolutionPack.value.trim() || "sales.renewal_ops";
  const comment = elements.customerSuccessMissingSkillComment.value.trim();
  if (!missingSkillName) {
    setMissingSkillStatus("Skill name required");
    return;
  }

  setMissingSkillStatus("Recording request");
  elements.customerSuccessSubmitMissingSkill.disabled = true;
  try {
    await apiFetch("/api/customer-success/feedback", {
      method: "POST",
      body: JSON.stringify({
        submitted_by_user_id: state.userId,
        feedback_type: "missing_skill",
        target_type: "solution_pack",
        target_id: solutionPackId,
        solution_pack_id: solutionPackId,
        missing_skill_name: missingSkillName,
        comment: comment || null,
        metadata: {
          source: "workspace_skill_request",
        },
      }),
    });
    elements.customerSuccessMissingSkillName.value = "";
    elements.customerSuccessMissingSkillComment.value = "";
    setMissingSkillStatus("Skill request recorded");
    appendMessage("agent", "Skill request recorded.");
    await loadCustomerSuccess();
  } catch (error) {
    setMissingSkillStatus(error.message);
    appendMessage("agent", error.message);
  } finally {
    elements.customerSuccessSubmitMissingSkill.disabled = false;
  }
}

async function createCustomerSuccessEvaluationCandidates() {
  setCandidateActionStatus("Generating eval candidates");
  elements.customerSuccessCreateEvalCandidates.disabled = true;
  try {
    const candidates = await apiFetch("/api/customer-success/evaluation-candidates", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadCustomerSuccess();
    setCandidateActionStatus(`Eval candidates generated: ${candidates.length}`);
  } catch (error) {
    setCandidateActionStatus(error.message);
    appendMessage("agent", error.message);
  } finally {
    elements.customerSuccessCreateEvalCandidates.disabled = false;
  }
}

async function createCustomerSuccessSolutionPackCandidates() {
  setCandidateActionStatus("Generating pack candidates");
  elements.customerSuccessCreatePackCandidates.disabled = true;
  try {
    const candidates = await apiFetch("/api/customer-success/solution-pack-candidates", {
      method: "POST",
      body: JSON.stringify({ minimum_repeated_feedback: 3 }),
    });
    await loadCustomerSuccess();
    setCandidateActionStatus(`Pack candidates generated: ${candidates.length}`);
  } catch (error) {
    setCandidateActionStatus(error.message);
    appendMessage("agent", error.message);
  } finally {
    elements.customerSuccessCreatePackCandidates.disabled = false;
  }
}

function selectedEvaluationCandidate(data = state.customerSuccess) {
  const candidates = data ? data.evaluationCandidates || [] : [];
  return (
    candidates.find((candidate) => candidate.status === "pending_review") ||
    candidates[0] ||
    null
  );
}

function renderEvaluationCandidateReview(
  data = state.customerSuccess,
  statusMessage = ""
) {
  const candidate = selectedEvaluationCandidate(data);
  const reviewable = Boolean(candidate && candidate.status === "pending_review");
  elements.customerSuccessEvalAccept.disabled = !reviewable;
  elements.customerSuccessEvalReject.disabled = !reviewable;

  if (statusMessage) {
    elements.customerSuccessEvalSelected.textContent = statusMessage;
    return;
  }
  if (!candidate) {
    elements.customerSuccessEvalSelected.textContent = "No eval candidate selected";
    return;
  }
  const caseLabel = candidate.evaluation_case_id
    ? ` case ${candidate.evaluation_case_id}`
    : "";
  elements.customerSuccessEvalSelected.textContent =
    `${candidate.status}: ${candidate.proposed_eval_name || candidate.id}${caseLabel}`;
}

async function reviewSelectedEvaluationCandidate(status) {
  const candidate = selectedEvaluationCandidate();
  if (!candidate) {
    return;
  }
  renderEvaluationCandidateReview(state.customerSuccess, `Marking eval ${status}`);
  try {
    const updated = await apiFetch(
      `/api/customer-success/evaluation-candidates/${candidate.id}/review`,
      {
        method: "POST",
        body: JSON.stringify(evaluationCandidateReviewPayload(status)),
      }
    );
    await loadCustomerSuccess();
    const caseLabel = updated.evaluation_case_id
      ? `, case ${updated.evaluation_case_id}`
      : "";
    const statusLabel =
      status === "accepted" ? "Eval candidate accepted" : "Eval candidate rejected";
    setCandidateActionStatus(`${statusLabel}${caseLabel}`);
  } catch (error) {
    setCandidateActionStatus(error.message);
    appendMessage("agent", error.message);
  }
}

function evaluationCandidateReviewPayload(status) {
  if (status === "accepted") {
    return {
      status: "accepted",
      review_note: "Create eval case from workspace feedback.",
    };
  }
  return {
    status: "rejected",
    review_note: "Reject eval candidate from workspace review.",
  };
}

function selectedSolutionPackCandidate(data = state.customerSuccess) {
  const candidates = data ? data.solutionPackCandidates || [] : [];
  return (
    candidates.find((candidate) => candidate.status === "pending_review") ||
    candidates[0] ||
    null
  );
}

function renderSolutionPackCandidateReview(
  data = state.customerSuccess,
  statusMessage = ""
) {
  const candidate = selectedSolutionPackCandidate(data);
  const reviewable = Boolean(candidate && candidate.status === "pending_review");
  elements.customerSuccessPackAccept.disabled = !reviewable;
  elements.customerSuccessPackReject.disabled = !reviewable;

  if (statusMessage) {
    elements.customerSuccessPackSelected.textContent = statusMessage;
    return;
  }
  if (!candidate) {
    elements.customerSuccessPackSelected.textContent = "No pack candidate selected";
    return;
  }
  const draftLabel = candidate.publication_draft_id
    ? ` draft ${candidate.publication_draft_id}`
    : "";
  elements.customerSuccessPackSelected.textContent =
    `${candidate.status}: ${candidate.requested_skill_name || candidate.id}${draftLabel}`;
}

async function reviewSelectedSolutionPackCandidate(status) {
  const candidate = selectedSolutionPackCandidate();
  if (!candidate) {
    return;
  }
  renderSolutionPackCandidateReview(state.customerSuccess, `Marking pack ${status}`);
  try {
    const updated = await apiFetch(
      `/api/customer-success/solution-pack-candidates/${candidate.id}/review`,
      {
        method: "POST",
        body: JSON.stringify(solutionPackCandidateReviewPayload(status)),
      }
    );
    if (updated.publication_draft_id) {
      state.selectedSolutionPackDraftId = updated.publication_draft_id;
    }
    await loadCustomerSuccess();
    const draftLabel = updated.publication_draft_id
      ? `, draft ${updated.publication_draft_id}`
      : "";
    const statusLabel =
      status === "accepted" ? "Pack candidate accepted" : "Pack candidate rejected";
    setCandidateActionStatus(`${statusLabel}${draftLabel}`);
  } catch (error) {
    setCandidateActionStatus(error.message);
    appendMessage("agent", error.message);
  }
}

function solutionPackCandidateReviewPayload(status) {
  if (status === "accepted") {
    return {
      status: "accepted",
      review_note: "Draft solution pack skill from workspace feedback.",
    };
  }
  return {
    status: "rejected",
    review_note: "Reject solution pack candidate from workspace review.",
  };
}

function renderSolutionPackDrafts(data, statusMessage = "") {
  const drafts = data.publicationDrafts || [];
  if (
    state.selectedSolutionPackDraftId &&
    !drafts.some((draft) => draft.id === state.selectedSolutionPackDraftId)
  ) {
    state.selectedSolutionPackDraftId = null;
  }
  if (!state.selectedSolutionPackDraftId && drafts.length) {
    state.selectedSolutionPackDraftId = drafts[0].id;
  }

  elements.customerSuccessDraftsList.replaceChildren();
  if (!drafts.length) {
    const empty = document.createElement("li");
    empty.textContent = "No drafts.";
    elements.customerSuccessDraftsList.append(empty);
  } else {
    for (const draft of drafts) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.solutionPackDraftId = draft.id;
      button.setAttribute("data-solution-pack-draft-id", draft.id);
      if (draft.id === state.selectedSolutionPackDraftId) {
        button.classList.add("is-selected");
      }
      const title = document.createElement("span");
      title.textContent = draft.requested_skill_name || "Untitled skill";
      const meta = document.createElement("small");
      meta.textContent = `${draft.status} · ${draft.solution_pack_id}`;
      button.append(title, meta);
      item.append(button);
      elements.customerSuccessDraftsList.append(item);
    }
  }

  const selected = selectedSolutionPackDraft();
  const editable = selected && ["draft", "rejected"].includes(selected.status);
  const reviewable = selected && selected.status === "in_review";
  const applicable =
    selected && selected.status === "approved" && !selected.production_change_applied;
  elements.customerSuccessDraftSelected.textContent = selected
    ? selected.requested_skill_name
    : "No draft selected";
  elements.customerSuccessDraftSkill.value = selected ? selected.requested_skill_name : "";
  elements.customerSuccessDraftSummary.value = selected
    ? selected.proposed_change_summary
    : "";
  elements.customerSuccessDraftPackVersion.value = selected
    ? selected.proposed_pack_version || ""
    : "";
  elements.customerSuccessDraftSkillManifest.value =
    selected && selected.proposed_skill_manifests && selected.proposed_skill_manifests.length
      ? JSON.stringify(selected.proposed_skill_manifests, null, 2)
      : selected && selected.proposed_skill_manifest
      ? JSON.stringify(selected.proposed_skill_manifest, null, 2)
      : "";
  elements.customerSuccessDraftSkill.disabled = !editable;
  elements.customerSuccessDraftSummary.disabled = !editable;
  elements.customerSuccessDraftPackVersion.disabled = !editable;
  elements.customerSuccessDraftSkillManifest.disabled = !editable;
  elements.customerSuccessDraftSave.disabled = !editable;
  elements.customerSuccessDraftSubmit.disabled = !editable;
  elements.customerSuccessDraftApprove.disabled = !reviewable;
  elements.customerSuccessDraftReject.disabled = !reviewable;
  elements.customerSuccessDraftApply.disabled = !applicable;
  elements.customerSuccessDraftStatus.textContent =
    statusMessage || (selected ? `Status: ${selected.status}` : "No draft selected");
}

function selectedSolutionPackDraft() {
  const drafts = state.customerSuccess ? state.customerSuccess.publicationDrafts || [] : [];
  return drafts.find((draft) => draft.id === state.selectedSolutionPackDraftId) || null;
}

function selectSolutionPackDraft(draftId) {
  state.selectedSolutionPackDraftId = draftId;
  renderSolutionPackDrafts(state.customerSuccess || { publicationDrafts: [] });
}

function replaceSolutionPackDraft(updatedDraft) {
  if (!state.customerSuccess) {
    state.customerSuccess = { publicationDrafts: [] };
  }
  const drafts = state.customerSuccess.publicationDrafts || [];
  const index = drafts.findIndex((draft) => draft.id === updatedDraft.id);
  if (index === -1) {
    drafts.unshift(updatedDraft);
  } else {
    drafts[index] = updatedDraft;
  }
  state.customerSuccess.publicationDrafts = drafts;
  state.selectedSolutionPackDraftId = updatedDraft.id;
  renderCustomerSuccess(state.customerSuccess);
}

async function saveSelectedSolutionPackDraft() {
  const draft = selectedSolutionPackDraft();
  if (!draft) {
    return;
  }
  renderSolutionPackDrafts(state.customerSuccess, "Saving draft");
  try {
    const proposedSkillManifest = parseDraftSkillManifest();
    if (proposedSkillManifest === false) {
      return;
    }
    const proposedPackVersion = elements.customerSuccessDraftPackVersion.value.trim();
    const updated = await apiFetch(
      `/api/customer-success/solution-pack-drafts/${draft.id}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          requested_skill_name: elements.customerSuccessDraftSkill.value.trim(),
          proposed_change_summary: elements.customerSuccessDraftSummary.value.trim(),
          ...(proposedPackVersion
            ? { proposed_pack_version: proposedPackVersion }
            : {}),
          ...(proposedSkillManifest
            ? manifestPayloadForDraft(proposedSkillManifest)
            : {}),
        }),
      }
    );
    replaceSolutionPackDraft(updated);
    elements.customerSuccessDraftStatus.textContent = "Draft saved";
  } catch (error) {
    elements.customerSuccessDraftStatus.textContent = error.message;
    appendMessage("agent", error.message);
  }
}

function parseDraftSkillManifest() {
  const raw = elements.customerSuccessDraftSkillManifest.value.trim();
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    elements.customerSuccessDraftStatus.textContent = "Skill manifest JSON is invalid";
    return false;
  }
}

function manifestPayloadForDraft(parsedManifest) {
  if (Array.isArray(parsedManifest)) {
    return { proposed_skill_manifests: parsedManifest };
  }
  return { proposed_skill_manifest: parsedManifest };
}

async function submitSelectedSolutionPackDraft() {
  const draft = selectedSolutionPackDraft();
  if (!draft) {
    return;
  }
  renderSolutionPackDrafts(state.customerSuccess, "Submitting draft");
  try {
    const updated = await apiFetch(
      `/api/customer-success/solution-pack-drafts/${draft.id}/submit`,
      { method: "POST" }
    );
    replaceSolutionPackDraft(updated);
    elements.customerSuccessDraftStatus.textContent = "Draft in review";
  } catch (error) {
    elements.customerSuccessDraftStatus.textContent = error.message;
    appendMessage("agent", error.message);
  }
}

async function reviewSelectedSolutionPackDraft(status) {
  const draft = selectedSolutionPackDraft();
  if (!draft) {
    return;
  }
  renderSolutionPackDrafts(state.customerSuccess, `Marking ${status}`);
  try {
    const updated = await apiFetch(
      `/api/customer-success/solution-pack-drafts/${draft.id}/review`,
      {
        method: "POST",
        body: JSON.stringify({ status }),
      }
    );
    replaceSolutionPackDraft(updated);
    elements.customerSuccessDraftStatus.textContent = `Draft ${status}`;
  } catch (error) {
    elements.customerSuccessDraftStatus.textContent = error.message;
    appendMessage("agent", error.message);
  }
}

async function applySelectedSolutionPackDraft() {
  const draft = selectedSolutionPackDraft();
  if (!draft) {
    return;
  }
  renderSolutionPackDrafts(state.customerSuccess, "Applying draft");
  try {
    const updated = await apiFetch(
      `/api/customer-success/solution-pack-drafts/${draft.id}/apply`,
      { method: "POST" }
    );
    replaceSolutionPackDraft(updated);
    elements.customerSuccessDraftStatus.textContent = "Draft applied";
  } catch (error) {
    elements.customerSuccessDraftStatus.textContent = error.message;
    appendMessage("agent", error.message);
  }
}

async function submitRun() {
  const message = elements.input.value.trim();
  if (!message) {
    return;
  }
  if (elements.shell.dataset.chatState === "empty") {
    elements.conversation.replaceChildren();
  }
  stopRunPolling();
  appendMessage("user", message);
  elements.input.value = "";
  fitComposer();
  syncComposerState();
  setStatus("creating");
  renderTerminal("Creating run...");

  try {
    const created = await apiFetch("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        workspace_id: state.workspaceId,
        agent_id: state.agentId,
        message,
        attachments: state.selectedAttachments.map((attachment) => attachment.id),
        mode: "autonomous",
      }),
    });
    state.currentRunId = created.run_id;
    state.selectedRunHistoryId = created.run_id;
    state.selectedAttachments = [];
    renderAttachmentChips();
    state.lastSequence = 0;
    state.events = [];
    state.eventStreamIntegrityIssues = [];
    state.artifacts = [];
    state.storageObjects = [];
    state.runTrace = null;
    state.runtimeState = null;
    state.runStatus = created.status;
    renderBrowser();
    renderArtifacts();
    renderRunTrace();
    renderRuntimeState();
    renderExecutionLoop();
    renderRunEvidence();
    renderDeliveryChain();
    renderApproval();
    renderRunControls();
    setStatus(created.status);
    appendMessage("agent", `Run ${created.run_id} created.`);
    startRunPolling();
    await loadRunHistory();
    await apiFetch(`/api/runs/${state.currentRunId}/execute`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshRun();
    await loadRunHistory();
  } catch (error) {
    stopRunPolling();
    setStatus("failed");
    appendMessage("agent", error.message);
    renderTerminal(error.message);
    if (state.currentRunId) {
      try {
        await refreshRun();
      } catch {
        return;
      }
    }
  }
}

async function cancelRun() {
  if (!state.currentRunId) {
    return;
  }
  elements.runControlStatus.textContent = "Cancelling run";
  try {
    await apiFetch(`/api/runs/${state.currentRunId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason_code: "operator_cancelled" }),
    });
    stopRunPolling();
    await refreshRun();
    await loadRunHistory();
  } catch (error) {
    elements.runControlStatus.textContent = error.message;
    appendMessage("agent", error.message);
  }
}

async function retryRun() {
  if (!state.currentRunId) {
    return;
  }
  elements.runControlStatus.textContent = "Retrying run";
  try {
    await apiFetch(`/api/runs/${state.currentRunId}/retry`, {
      method: "POST",
      body: JSON.stringify({ reason_code: "operator_retry" }),
    });
    state.lastSequence = 0;
    state.events = [];
    state.eventStreamIntegrityIssues = [];
    state.artifacts = [];
    state.storageObjects = [];
    state.runTrace = null;
    state.runtimeState = null;
    state.deliveredRunIds.delete(state.currentRunId);
    state.previewedRunIds.delete(state.currentRunId);
    state.feedbackSubmittedRunIds.delete(state.currentRunId);
    setStatus("retrying");
    renderRunEvidence();
    renderDeliveryChain();
    startRunPolling();
    await refreshRun();
    await loadRunHistory();
  } catch (error) {
    elements.runControlStatus.textContent = error.message;
    appendMessage("agent", error.message);
    renderRunControls();
  }
}

async function refreshRun() {
  if (!state.currentRunId) {
    return;
  }
  await loadRunStatus();
  await loadEvents();
  await loadArtifacts();
  await loadStorageObjects();
  await loadRunTrace();
  await loadRuntimeState();
  renderTimeline();
  renderRunTrace();
  renderRuntimeState();
  renderBrowser();
  renderArtifacts();
  renderExecutionLoop();
  renderRunEvidence();
  renderApproval();
  renderTerminalFromEvents();
  renderDeliveryChain();
  await announceRunDelivery();
  if (isRunTerminalStatus(state.runStatus)) {
    stopRunPolling();
  }
}

async function loadRunStatus() {
  const run = await apiFetch(`/api/runs/${state.currentRunId}`);
  if (run.status) {
    setStatus(run.status);
  }
  return run;
}

function startRunPolling() {
  if (state.pollTimer) {
    return;
  }
  state.pollTimer = window.setInterval(async () => {
    if (!state.currentRunId || state.pollingInFlight) {
      return;
    }
    state.pollingInFlight = true;
    try {
      await refreshRun();
    } catch (error) {
      appendMessage("agent", error.message);
      stopRunPolling();
    } finally {
      state.pollingInFlight = false;
    }
  }, state.pollIntervalMs);
}

function stopRunPolling() {
  if (!state.pollTimer) {
    return;
  }
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function isRunTerminalStatus(status) {
  return ["succeeded", "failed", "cancelled", "timed_out"].includes(status);
}

async function loadEvents() {
  let eventsPath = `/api/runs/${state.currentRunId}/events`;
  if (state.lastSequence) {
    eventsPath += `?after_sequence=${state.lastSequence}`;
  }
  const sseText = await apiText(eventsPath);
  const events = parseServerSentEvents(sseText);
  const newEvents = events.filter((event) => !eventAlreadyLoaded(event));
  recordEventStreamIntegrityIssues(newEvents);
  for (const event of newEvents) {
    if (!eventAlreadyLoaded(event)) {
      state.events.push(event);
    }
  }
  state.events.sort(compareEventsBySequence);
  state.lastSequence = lastFiniteEventSequence(state.events);
  const terminalEvent = [...state.events].reverse().find((event) =>
    ["run.status_changed", "run.succeeded", "run.failed"].includes(event.type)
  );
  if (terminalEvent && terminalEvent.payload && terminalEvent.payload.status) {
    setStatus(terminalEvent.payload.status);
  }
}

function parseServerSentEvents(text) {
  return text
    .split("\n\n")
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      const lines = block.split("\n");
      const eventLineType = lines
        .find((line) => line.startsWith("event:"))
        ?.slice(6)
        .trim();
      const eventLineId = lines
        .find((line) => line.startsWith("id:"))
        ?.slice(3)
        .trim();
      const dataLines = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim());
      if (!dataLines.length) {
        return null;
      }
      const parsed = JSON.parse(dataLines.join("\n"));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        parsed.type = parsed.type || eventLineType;
        parsed.id = parsed.id || eventLineId;
      }
      return parsed;
    })
    .filter(Boolean);
}

function recordEventStreamIntegrityIssues(events) {
  if (events.some((event) => eventSequence(event) === null)) {
    addEventStreamIntegrityIssue("event stream sequence is missing");
  }
  const incomingSequences = events
    .map((event) => eventSequence(event))
    .filter((sequence) => sequence !== null);
  if (sequenceListHasViolation(incomingSequences)) {
    addEventStreamIntegrityIssue(
      "incoming event stream sequence is not monotonic"
    );
  }
  const mergedSequences = [...state.events, ...events]
    .map((event) => eventSequence(event))
    .filter((sequence) => sequence !== null);
  if (mergedSequences.length !== new Set(mergedSequences).size) {
    addEventStreamIntegrityIssue("event stream sequence is duplicated");
  }
}

function addEventStreamIntegrityIssue(issue) {
  if (!state.eventStreamIntegrityIssues.includes(issue)) {
    state.eventStreamIntegrityIssues.push(issue);
  }
}

function sequenceListHasViolation(sequences) {
  return sequences.some((currentSequence, index) => {
    if (index === 0) {
      return false;
    }
    const previousSequence = sequences[index - 1];
    return currentSequence <= previousSequence;
  });
}

function eventAlreadyLoaded(event) {
  const identity = eventIdentity(event);
  return state.events.some((existing) => eventIdentity(existing) === identity);
}

function eventIdentity(event) {
  if (event.id) {
    return `id:${event.id}`;
  }
  const sequence = eventSequence(event);
  if (sequence !== null) {
    return `sequence:${sequence}`;
  }
  const payload = event.payload || {};
  const createdAt = event.created_at || payload.created_at || "";
  return `${event.type || "event"}:${createdAt}:${JSON.stringify(payload)}`;
}

function compareEventsBySequence(left, right) {
  const leftSequence = eventSequence(left);
  const rightSequence = eventSequence(right);
  if (leftSequence !== null && rightSequence !== null) {
    return leftSequence - rightSequence;
  }
  if (leftSequence !== null) {
    return -1;
  }
  if (rightSequence !== null) {
    return 1;
  }
  return eventIdentity(left).localeCompare(eventIdentity(right));
}

function lastFiniteEventSequence(events) {
  const sequences = events
    .map((event) => eventSequence(event))
    .filter((sequence) => sequence !== null);
  return sequences.length ? Math.max(...sequences) : 0;
}

function eventSequence(event) {
  if (event.sequence === undefined || event.sequence === null || event.sequence === "") {
    return null;
  }
  const sequence = Number(event.sequence);
  return Number.isFinite(sequence) ? sequence : null;
}

async function loadArtifacts() {
  state.artifacts = await apiFetch(`/api/runs/${state.currentRunId}/artifacts`);
}

async function loadStorageObjects() {
  try {
    state.storageObjects = await apiFetch(
      `/api/runs/${state.currentRunId}/storage-objects`
    );
  } catch (error) {
    state.storageObjects = [];
  }
}

function renderTimeline() {
  elements.timeline.replaceChildren();
  if (!state.events.length) {
    const empty = document.createElement("li");
    empty.className = "timeline-empty";
    empty.textContent = "No events yet.";
    elements.timeline.append(empty);
    renderEventIntegrity();
    return;
  }
  for (const event of state.events.slice(-12).reverse()) {
    const item = document.createElement("li");
    const type = document.createElement("span");
    type.className = "event-type";
    type.textContent = event.type;
    const meta = document.createElement("span");
    meta.className = "event-meta";
    meta.textContent = compactEventMeta(event);
    item.append(type, meta);
    elements.timeline.append(item);
  }
  renderEventIntegrity();
}

async function loadRunTrace() {
  try {
    state.runTrace = await apiFetch(`/api/runs/${state.currentRunId}/trace`);
  } catch (error) {
    state.runTrace = { error: error.message };
  }
}

function renderRunTrace(trace = state.runTrace) {
  elements.traceList.replaceChildren();
  if (!trace) {
    elements.traceStatus.textContent = "Not loaded";
    elements.traceSpanCount.textContent = "--";
    elements.traceEventCount.textContent = "--";
    elements.traceBillingCount.textContent = "--";
    elements.traceAuditCount.textContent = "--";
    elements.traceErrorClassification.textContent = "No error";
    appendTraceEmpty("No trace loaded.");
    return;
  }
  if (trace.error) {
    elements.traceStatus.textContent = "Unavailable";
    elements.traceSpanCount.textContent = "--";
    elements.traceEventCount.textContent = "--";
    elements.traceBillingCount.textContent = "--";
    elements.traceAuditCount.textContent = "--";
    elements.traceErrorClassification.textContent = trace.error;
    appendTraceEmpty("Trace requires audit access.");
    return;
  }

  const spans = trace.spans || [];
  const traceEvents = trace.trace_events || [];
  const billingMeters = trace.billing_meters || [];
  const auditEvents = trace.audit_events || [];
  elements.traceStatus.textContent = "Loaded";
  elements.traceSpanCount.textContent = String(spans.length);
  elements.traceEventCount.textContent = String(traceEvents.length);
  elements.traceBillingCount.textContent = String(billingMeters.length);
  elements.traceAuditCount.textContent = String(auditEvents.length);
  elements.traceErrorClassification.textContent = describeTraceError(trace);

  const visibleEvents = traceEvents.slice(-5).reverse();
  if (!visibleEvents.length) {
    appendTraceEmpty("No trace events.");
    return;
  }
  for (const event of visibleEvents) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.className = "trace-event-name";
    name.textContent = event.name;
    const meta = document.createElement("small");
    meta.textContent = `${event.source} · ${shortDateTime(event.occurred_at)}`;
    item.append(name, meta);
    elements.traceList.append(item);
  }
}

function appendTraceEmpty(message) {
  const empty = document.createElement("li");
  empty.textContent = message;
  elements.traceList.append(empty);
}

function describeTraceError(trace) {
  const classification = trace.error_classification;
  if (!classification) {
    return "No error";
  }
  const parts = [classification.category || "unknown"];
  if (classification.source_event_type) {
    parts.push(classification.source_event_type);
  }
  return parts.join(" · ");
}

async function loadRuntimeState() {
  try {
    state.runtimeState = await apiFetch(`/api/runs/${state.currentRunId}/state`);
  } catch (error) {
    state.runtimeState = { error: error.message };
  }
}

function renderRuntimeState(runtime = state.runtimeState) {
  if (!runtime) {
    elements.runtimeStateStatus.textContent = "Not loaded";
    elements.runtimeCurrentStep.textContent = "--";
    elements.runtimeCompletedCount.textContent = "--";
    elements.runtimeSandboxSession.textContent = "--";
    elements.runtimeBrowserSession.textContent = "--";
    elements.runtimeArtifactCount.textContent = "No promoted artifacts";
    return;
  }
  if (runtime.error) {
    elements.runtimeStateStatus.textContent = "Unavailable";
    elements.runtimeCurrentStep.textContent = "--";
    elements.runtimeCompletedCount.textContent = "--";
    elements.runtimeSandboxSession.textContent = "--";
    elements.runtimeBrowserSession.textContent = "--";
    elements.runtimeArtifactCount.textContent = runtime.error;
    return;
  }

  const completedSteps = runtime.completed_step_ids || [];
  const promotedArtifacts = runtime.promoted_sandbox_artifact_paths || [];
  elements.runtimeStateStatus.textContent = runtime.status || "Loaded";
  elements.runtimeCurrentStep.textContent = runtime.current_step_id || "--";
  elements.runtimeCompletedCount.textContent = String(completedSteps.length);
  elements.runtimeSandboxSession.textContent = runtime.sandbox_session_id || "--";
  elements.runtimeBrowserSession.textContent = runtime.browser_session_id || "--";
  elements.runtimeArtifactCount.textContent = promotedArtifacts.length
    ? `${promotedArtifacts.length} promoted artifact paths`
    : "No promoted artifacts";
}

function compactEventMeta(event) {
  const payload = event.payload || {};
  const parts = [];
  if (payload.step_id) {
    parts.push(payload.step_id);
  }
  if (payload.tool_name) {
    parts.push(payload.tool_name);
  }
  if (payload.action_type) {
    parts.push(payload.action_type);
  }
  if (payload.exit_code !== undefined) {
    parts.push(`exit ${payload.exit_code}`);
  }
  if (payload.current_url) {
    parts.push(payload.current_url);
  }
  if (payload.screenshot_uri) {
    parts.push("capture");
  }
  if (payload.artifact_name) {
    parts.push(payload.artifact_name);
  }
  return parts.join(" · ") || `#${event.sequence}`;
}

function latestBrowserEvent() {
  return [...state.events].reverse().find((event) => {
    return event.type === "browser.action.performed" && event.payload;
  });
}

function latestSandboxCommandEvent() {
  return [...state.events].reverse().find((event) => {
    return (
      event.type === "sandbox.command.executed" ||
      extractSafeSandboxToolOutput(event)
    );
  });
}

function renderBrowser() {
  const event = latestBrowserEvent();
  if (!event) {
    elements.browserStatus.textContent = "Waiting";
    elements.browserSession.textContent = "--";
    elements.browserAction.textContent = "--";
    elements.browserUrl.textContent = "--";
    elements.browserStorageObject.textContent = "--";
    clearBrowserCapture("No browser actions.");
    return;
  }

  const payload = event.payload || {};
  const actionType = payload.action_type || "observed";
  const currentUrl = payload.current_url || "about:blank";
  const screenshotUri = payload.screenshot_uri || "";
  elements.browserStatus.textContent = actionType;
  elements.browserSession.textContent = payload.session_id || "--";
  elements.browserAction.textContent = actionType;
  elements.browserUrl.textContent = currentUrl;
  elements.browserStorageObject.textContent =
    payload.storage_object_id || payload.screenshot_storage_object_id || "--";

  if (!screenshotUri) {
    clearBrowserCapture("No capture.");
    return;
  }

  elements.browserEmpty.hidden = true;
  elements.browserScreenshot.hidden = false;
  const storageObject = storageObjectForBrowserCapture(payload);
  if (storageObject) {
    elements.browserStorageObject.textContent = storageObject.id;
    elements.browserScreenshot.href = "#";
    elements.browserScreenshot.textContent = "Download capture";
    elements.browserScreenshot.dataset.browserStorageObjectId = storageObject.id;
    elements.browserScreenshot.setAttribute(
      "data-browser-storage-object-id",
      storageObject.id
    );
    previewBrowserCapture(storageObject);
    return;
  }
  delete elements.browserScreenshot.dataset.browserStorageObjectId;
  elements.browserScreenshot.removeAttribute("data-browser-storage-object-id");
  elements.browserScreenshot.href = screenshotUri;
  elements.browserScreenshot.textContent = screenshotUri;
  if (isPreviewableScreenshotUri(screenshotUri)) {
    setBrowserPreviewSource(screenshotUri);
    return;
  }
  clearBrowserPreview();
}

function clearBrowserCapture(message) {
  elements.browserEmpty.hidden = false;
  elements.browserEmpty.textContent = message;
  clearBrowserPreview();
  elements.browserScreenshot.hidden = true;
  elements.browserScreenshot.removeAttribute("href");
  elements.browserScreenshot.textContent = "Capture";
  delete elements.browserScreenshot.dataset.browserStorageObjectId;
  elements.browserScreenshot.removeAttribute("data-browser-storage-object-id");
}

function clearBrowserPreview() {
  if (state.browserPreviewObjectUrl) {
    URL.revokeObjectURL(state.browserPreviewObjectUrl);
    state.browserPreviewObjectUrl = null;
  }
  state.browserPreviewStorageObjectId = null;
  renderBrowserPreviewStorageObject("");
  elements.browserScreenshotPreview.hidden = true;
  elements.browserScreenshotPreview.removeAttribute("src");
}

function renderBrowserPreviewStorageObject(storageObjectId) {
  elements.browserPreviewStorageObject.textContent = storageObjectId || "--";
  elements.browserPreviewStorageObject.dataset.browserPreviewStorageObjectId =
    storageObjectId || "";
}

function setBrowserPreviewSource(src) {
  if (state.browserPreviewObjectUrl) {
    URL.revokeObjectURL(state.browserPreviewObjectUrl);
    state.browserPreviewObjectUrl = null;
  }
  elements.browserScreenshotPreview.src = src;
  elements.browserScreenshotPreview.hidden = false;
}

async function previewBrowserCapture(storageObject) {
  if (
    state.browserPreviewStorageObjectId === storageObject.id &&
    !elements.browserScreenshotPreview.hidden
  ) {
    return;
  }
  state.browserPreviewStorageObjectId = storageObject.id;
  renderBrowserPreviewStorageObject(storageObject.id);
  try {
    const contentPath = `/api/storage/objects/${storageObject.id}/content`;
    const response = await fetch(`${state.apiBase}${contentPath}`, {
      headers: requestHeaders(),
    });
    if (!response.ok) {
      await raiseStorageFetchError(response);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    setBrowserPreviewSource(objectUrl);
    state.browserPreviewObjectUrl = objectUrl;
    state.browserPreviewStorageObjectId = storageObject.id;
  } catch (error) {
    if (state.browserPreviewStorageObjectId === storageObject.id) {
      clearBrowserPreview();
    }
  }
}

function isPreviewableScreenshotUri(screenshotUri) {
  return (
    screenshotUri.startsWith("data:image/") ||
    screenshotUri.startsWith("blob:") ||
    screenshotUri.startsWith("http://") ||
    screenshotUri.startsWith("https://")
  );
}

function renderTerminalFromEvents() {
  const commandEvents = state.events.filter((event) => {
    return (
      event.type === "sandbox.command.executed" ||
      extractSafeSandboxToolOutput(event)
    );
  });
  if (!commandEvents.length) {
    return;
  }
  const latestEvent = commandEvents[commandEvents.length - 1];
  const latest = resolveTerminalOutput(latestEvent);
  const exitCode = latest.exit_code;
  elements.terminalStatus.textContent =
    exitCode === undefined ? "Summary" : exitCode === 0 ? "Passed" : `Exit ${exitCode}`;
  const lines = [];
  if (exitCode !== undefined) {
    lines.push(`exit ${exitCode}`);
  }
  if (latest.stdout_length !== undefined) {
    lines.push(`stdout ${latest.stdout_length} bytes`);
  }
  if (latest.stderr_length !== undefined) {
    lines.push(`stderr ${latest.stderr_length} bytes`);
  }
  if (latest.output_uri) {
    lines.push(latest.output_uri);
  }
  const outputStorageObject = storageObjectForTerminalOutputUri(latest.output_uri);
  renderTerminalOutputStorageObject(outputStorageObject);
  if (!lines.length) {
    lines.push("sandbox command summary unavailable");
  }
  renderTerminal(lines.join("\n"));
}

function storageObjectForTerminalOutputUri(outputUri) {
  if (!outputUri) {
    return null;
  }
  return state.storageObjects.find((storageObject) => {
    const uri = `s3://${storageObject.bucket}/${storageObject.key}`;
    return storageObject.purpose === "sandbox-command-outputs" && uri === outputUri;
  });
}

function renderTerminalOutputStorageObject(storageObject) {
  const storageObjectId = storageObject ? storageObject.id : "";
  elements.terminalOutputStorageObject.textContent = storageObjectId || "--";
  elements.terminalOutputStorageObject.dataset.terminalStorageObjectId =
    storageObjectId;
}

function extractSafeSandboxToolOutput(event) {
  const payload = event.payload || {};
  const result = payload.result || {};
  if (event.type !== "tool_call.completed" || result.tool_name !== "sandbox.command") {
    return null;
  }
  return result.output || null;
}

function resolveTerminalOutput(event) {
  if (event.type !== "sandbox.command.executed") {
    return safeTerminalOutput(extractSafeSandboxToolOutput(event) || {});
  }
  const payload = event.payload || {};
  const safeSummary = findSandboxToolSummary(payload.step_id);
  return safeTerminalOutput(Object.assign({}, payload, safeSummary));
}

function safeTerminalOutput(output) {
  const safeOutput = {};
  for (const key of [
    "exit_code",
    "stdout_length",
    "stderr_length",
    "output_uri",
    "session_id",
  ]) {
    if (output[key] !== undefined) {
      safeOutput[key] = output[key];
    }
  }
  return safeOutput;
}

function findSandboxToolSummary(stepId) {
  for (let index = state.events.length - 1; index >= 0; index -= 1) {
    const event = state.events[index];
    const payload = event.payload || {};
    if (stepId && payload.step_id !== stepId) {
      continue;
    }
    const output = extractSafeSandboxToolOutput(event);
    if (output) {
      return output;
    }
  }
  return {};
}

function renderTerminal(text) {
  elements.terminal.textContent = text;
}

function renderArtifacts() {
  elements.artifactCount.textContent = String(state.artifacts.length);
  renderDeliverySummary();
  renderRunFeedback();
  elements.artifacts.replaceChildren();
  if (!state.artifacts.length) {
    const empty = document.createElement("li");
    empty.textContent = "No artifacts.";
    elements.artifacts.append(empty);
    clearArtifactPreview();
    clearArtifactDownloadStatus();
    return;
  }
  for (const artifact of state.artifacts) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.className = "artifact-name";
    name.textContent = artifact.name;
    const uri = document.createElement("span");
    uri.className = "artifact-uri";
    uri.textContent = artifact.uri;
    item.append(name, uri);
    const storageObject = storageObjectForArtifact(artifact);
    if (storageObject) {
      const actions = document.createElement("div");
      actions.className = "artifact-actions";
      const download = document.createElement("button");
      download.type = "button";
      download.textContent = "Download";
      download.dataset.storageObjectId = storageObject.id;
      download.setAttribute("data-storage-object-id", storageObject.id);
      const preview = document.createElement("button");
      preview.type = "button";
      preview.textContent = "Preview";
      preview.dataset.previewStorageObjectId = storageObject.id;
      preview.setAttribute("data-preview-storage-object-id", storageObject.id);
      actions.append(preview, download);
      item.append(actions);
    }
    elements.artifacts.append(item);
  }
}

function renderRunFeedback(statusMessage = "") {
  const readyArtifacts = downloadableArtifacts();
  const ready =
    Boolean(state.currentRunId) &&
    state.runStatus === "succeeded" &&
    readyArtifacts.length > 0;
  const submitted =
    Boolean(state.currentRunId) &&
    state.feedbackSubmittedRunIds.has(state.currentRunId);
  const disabled = !ready || submitted;
  elements.runFeedbackPositive.disabled = disabled;
  elements.runFeedbackNegative.disabled = disabled;

  if (statusMessage) {
    elements.runFeedbackStatus.textContent = statusMessage;
    elements.runFeedbackStatus.dataset.runFeedbackState = submitted ? "submitted" : "active";
    return;
  }
  if (submitted) {
    elements.runFeedbackStatus.textContent = "Feedback recorded";
    elements.runFeedbackStatus.dataset.runFeedbackState = "submitted";
    return;
  }
  if (ready) {
    elements.runFeedbackStatus.textContent = "Feedback ready";
    elements.runFeedbackStatus.dataset.runFeedbackState = "ready";
    return;
  }
  elements.runFeedbackStatus.textContent = "Feedback unavailable";
  elements.runFeedbackStatus.dataset.runFeedbackState = "waiting";
}

async function submitRunFeedback(rating) {
  if (!state.currentRunId || state.runStatus !== "succeeded") {
    return;
  }
  const readyArtifacts = downloadableArtifacts();
  if (!readyArtifacts.length) {
    renderRunFeedback("Feedback unavailable");
    return;
  }

  renderRunFeedback("Recording feedback");
  elements.runFeedbackPositive.disabled = true;
  elements.runFeedbackNegative.disabled = true;
  try {
    await apiFetch("/api/customer-success/feedback", {
      method: "POST",
      body: JSON.stringify({
        submitted_by_user_id: state.userId,
        feedback_type: "thumbs_rating",
        target_type: "run",
        target_id: state.currentRunId,
        run_id: state.currentRunId,
        rating,
        metadata: {
          artifact_count: readyArtifacts.length,
          browser_action_observed: hasEventType("browser.action.performed"),
        },
      }),
    });
    state.feedbackSubmittedRunIds.add(state.currentRunId);
    renderRunFeedback("Feedback recorded");
    appendMessage("agent", "Feedback recorded.");
    await loadCustomerSuccess();
  } catch (error) {
    renderRunFeedback(error.message);
    appendMessage("agent", error.message);
  }
}

function renderDeliverySummary() {
  if (!state.artifacts.length) {
    elements.deliverySummary.textContent = "No artifacts delivered";
    elements.deliverySummary.dataset.deliveryState = "waiting";
    return;
  }
  const readyArtifacts = downloadableArtifacts();
  if (!readyArtifacts.length) {
    elements.deliverySummary.textContent = "Waiting for artifact storage";
    elements.deliverySummary.dataset.deliveryState = "pending";
    return;
  }

  const names = readyArtifacts
    .map((item) => item.artifact.name || item.storageObject.filename)
    .filter(Boolean);
  const visibleNames = names.length ? names.slice(0, 2).join(", ") : "artifact output";
  const hiddenCount = Math.max(readyArtifacts.length - 2, 0);
  const extra = hiddenCount ? ` and ${hiddenCount} more` : "";
  elements.deliverySummary.textContent = `Ready to download: ${visibleNames}${extra}`;
  elements.deliverySummary.dataset.deliveryState = "ready";
}

function storageObjectForArtifact(artifact) {
  return state.storageObjects.find((storageObject) => {
    const uri = `s3://${storageObject.bucket}/${storageObject.key}`;
    return (
      artifact.uri === uri ||
      (storageObject.purpose === "artifacts" && storageObject.filename === artifact.name)
    );
  });
}

function downloadableArtifacts() {
  return state.artifacts
    .map((artifact) => {
      return { artifact, storageObject: storageObjectForArtifact(artifact) };
    })
    .filter((item) => item.storageObject);
}

function readyStorageBackedArtifacts() {
  return downloadableArtifacts();
}

async function announceRunDelivery() {
  if (!state.currentRunId || state.runStatus !== "succeeded") {
    return;
  }
  const deliveryAnnounced = state.deliveredRunIds.has(state.currentRunId);
  const previewComplete = state.previewedRunIds.has(state.currentRunId);
  if (deliveryAnnounced && previewComplete) {
    return;
  }
  const readyArtifacts = downloadableArtifacts();
  if (!readyArtifacts.length) {
    return;
  }

  if (!deliveryAnnounced || !previewComplete) {
    switchWorkbenchView("run");
  }
  await autoPreviewFirstDeliveredArtifact(readyArtifacts);
  if (deliveryAnnounced) {
    return;
  }
  state.deliveredRunIds.add(state.currentRunId);
  const names = readyArtifacts
    .map((item) => item.artifact.name || item.storageObject.filename)
    .filter(Boolean);
  const visibleNames = names.length ? names.slice(0, 3).join(", ") : "artifact output";
  const hiddenCount = Math.max(readyArtifacts.length - 3, 0);
  const extra = hiddenCount ? ` and ${hiddenCount} more` : "";
  const plural = readyArtifacts.length === 1 ? "artifact" : "artifacts";
  appendMessage(
    "agent",
    `Run ${state.currentRunId} delivered ${readyArtifacts.length} ${plural}: ${visibleNames}${extra}.`
  );
}

async function autoPreviewFirstDeliveredArtifact(readyArtifacts) {
  if (!state.currentRunId || state.previewedRunIds.has(state.currentRunId)) {
    return;
  }
  const first = readyArtifacts[0];
  if (!first || !first.storageObject) {
    return;
  }
  const previewed = await previewArtifact(first.storageObject.id);
  if (previewed) {
    state.previewedRunIds.add(state.currentRunId);
  }
}

async function downloadArtifact(storageObjectId) {
  const storageObject = state.storageObjects.find((item) => {
    return item.id === storageObjectId;
  });
  if (!storageObject) {
    renderArtifactDownloadStatus("Artifact download unavailable", "failed");
    return;
  }
  const filename = storageObject.filename || "artifact";
  renderArtifactDownloadStatus(`Downloading ${filename}`, "pending");
  const downloaded = await downloadStorageObject(storageObject, "artifact");
  if (downloaded) {
    renderArtifactDownloadStatus(`Downloaded ${filename}`, "ready", storageObject.id);
  } else {
    renderArtifactDownloadStatus(`Download failed: ${filename}`, "failed");
  }
}

async function previewArtifact(storageObjectId) {
  const storageObject = state.storageObjects.find((item) => {
    return item.id === storageObjectId;
  });
  if (!storageObject) {
    return false;
  }
  renderArtifactPreview(storageObject, "Loading preview", "");
  try {
    const contentPath = `/api/storage/objects/${storageObject.id}/content`;
    const response = await fetch(`${state.apiBase}${contentPath}`, {
      headers: requestHeaders(),
    });
    if (!response.ok) {
      await raiseStorageFetchError(response);
    }
    const text = await response.text();
    const previewText = text.slice(0, ARTIFACT_PREVIEW_MAX_CHARACTERS);
    const status =
      text.length > ARTIFACT_PREVIEW_MAX_CHARACTERS
        ? `Preview truncated at ${ARTIFACT_PREVIEW_MAX_CHARACTERS} characters`
        : "Preview loaded";
    renderArtifactPreview(storageObject, status, previewText || "(empty artifact)");
    setArtifactPanelOpen(true);
    return true;
  } catch (error) {
    renderArtifactPreview(storageObject, error.message, "");
    appendMessage("agent", error.message);
    return false;
  }
}

function renderArtifactPreview(storageObject, status, content) {
  elements.artifactPreviewTitle.textContent = storageObject.filename || storageObject.id;
  elements.artifactPreviewStatus.textContent = status;
  elements.artifactPreviewStorageObject.textContent = storageObject.id;
  elements.artifactPreviewStorageObject.dataset.previewStorageObjectId =
    storageObject.id;
  elements.artifactPreviewContent.textContent = content;
}

function clearArtifactPreview() {
  elements.artifactPreviewTitle.textContent = "No artifact selected";
  elements.artifactPreviewStatus.textContent = "Preview idle";
  elements.artifactPreviewStorageObject.textContent = "--";
  elements.artifactPreviewStorageObject.dataset.previewStorageObjectId = "";
  elements.artifactPreviewContent.textContent = "Select an artifact preview.";
}

function renderArtifactDownloadStatus(message, status, storageObjectId = "") {
  elements.artifactDownloadStatus.textContent = message;
  elements.artifactDownloadStatus.dataset.downloadState = status;
  elements.artifactDownloadedStorageObject.textContent = storageObjectId || "--";
  elements.artifactDownloadedStorageObject.dataset.downloadStorageObjectId =
    storageObjectId;
}

function clearArtifactDownloadStatus() {
  renderArtifactDownloadStatus("No artifact downloaded", "idle");
}

async function downloadBrowserCapture(storageObjectId) {
  const storageObject = state.storageObjects.find((item) => {
    return item.id === storageObjectId;
  });
  if (!storageObject) {
    return;
  }
  await downloadStorageObject(storageObject, "browser-capture.png");
}

async function downloadStorageObject(storageObject, fallbackFilename) {
  syncSettings();
  try {
    const contentPath = `/api/storage/objects/${storageObject.id}/content`;
    const response = await fetch(`${state.apiBase}${contentPath}`, {
      headers: requestHeaders(),
    });
    if (!response.ok) {
      await raiseStorageFetchError(response);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = storageObject.filename || fallbackFilename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return true;
  } catch (error) {
    appendMessage("agent", error.message);
    return false;
  }
}

function storageObjectForBrowserCapture(payload) {
  const storageObjectId =
    payload.storage_object_id || payload.screenshot_storage_object_id;
  if (storageObjectId) {
    return state.storageObjects.find((storageObject) => {
      return storageObject.id === storageObjectId;
    });
  }
  const screenshotUri = payload.screenshot_uri || "";
  if (!screenshotUri) {
    return null;
  }
  return state.storageObjects.find((storageObject) => {
    const uri = `s3://${storageObject.bucket}/${storageObject.key}`;
    return storageObject.purpose === "browser" && uri === screenshotUri;
  });
}

function latestApprovalEvent() {
  return [...state.events].reverse().find((event) => {
    return (
      event.type === "approval.requested" ||
      event.type === "approval.resolved" ||
      event.type === "approval.rejected"
    );
  });
}

function renderApprovalResolution(event) {
  if (!event || event.type === "approval.requested") {
    elements.approvalResolution.textContent = "No approval decision yet.";
    elements.approvalResolution.dataset.resolutionState = "idle";
    return;
  }
  const payload = event.payload || {};
  const approved = event.type === "approval.resolved";
  elements.approvalResolution.textContent = approvalResolutionParts(
    approved ? "Approved" : "Rejected",
    payload,
  ).join(" · ");
  elements.approvalResolution.dataset.resolutionState = approved
    ? "approved"
    : "rejected";
}

function approvalResolutionParts(statusLabel, payload) {
  const parts = [statusLabel];
  if (payload.approval_id) {
    parts.push(payload.approval_id);
  }
  if (payload.resolved_by_user_id) {
    parts.push(payload.resolved_by_user_id);
  }
  return parts;
}

function renderApproval() {
  const approvalEvent = latestApprovalEvent();
  const hasApproval = Boolean(
    approvalEvent &&
      approvalEvent.type === "approval.requested" &&
      approvalEvent.payload,
  );
  state.pendingApprovalId = hasApproval ? approvalEvent.payload.approval_id : null;
  elements.approvalStatus.textContent = hasApproval ? "Pending" : "Clear";
  elements.approvalCopy.textContent = hasApproval
    ? approvalEvent.payload.reason || "Approval required."
    : "No pending approval.";
  renderApprovalResolution(approvalEvent);
  elements.approve.disabled = !hasApproval;
  elements.reject.disabled = !hasApproval;
}

async function approveRun() {
  if (!state.currentRunId || !state.pendingApprovalId) {
    return;
  }
  await apiFetch(`/api/runs/${state.currentRunId}/approvals`, {
    method: "POST",
    body: JSON.stringify({ approval_id: state.pendingApprovalId }),
  });
  startRunPolling();
  await refreshRun();
}

async function rejectRun() {
  if (!state.currentRunId || !state.pendingApprovalId) {
    return;
  }
  await apiFetch(`/api/runs/${state.currentRunId}/approvals/reject`, {
    method: "POST",
    body: JSON.stringify({ approval_id: state.pendingApprovalId }),
  });
  stopRunPolling();
  await refreshRun();
}

elements.routeLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    if (link === elements.newChat) {
      return;
    }
    event.preventDefault();
    renderAppRoute(link.dataset.appRoute, true);
  });
});

window.addEventListener("hashchange", () => {
  renderAppRoute(routeFromHash(), false);
});

elements.routeSearch.addEventListener("input", () => {
  renderRouteSearchResults(elements.routeSearch.value);
});

elements.routeSurface.addEventListener("click", (event) => {
  const button = event.target?.closest?.("[data-route-action]");
  if (button) {
    handleRouteAction(button.dataset.routeAction);
  }
});

elements.agentRunButtons.forEach((button) => {
  button.addEventListener("click", () => {
    prefillAgentRun(button.dataset.agentName || "selected");
  });
});

elements.agentCarouselNext.addEventListener("click", () => {
  const firstCard = elements.agentCardRail.firstElementChild;
  if (firstCard) {
    elements.agentCardRail.append(firstCard);
  }
});

elements.createAgent.addEventListener("click", () => {
  renderAppRoute("agents", true);
});

elements.createAgentPrompt.addEventListener("click", () => {
  renderAppRoute("agents", true);
});

elements.exploreAgents.addEventListener("click", () => {
  renderAppRoute("discover", true);
});

elements.modelSelectorButton.addEventListener("click", () => {
  const next = state.activePopover === "model" ? null : "model";
  setActivePopover(next, elements.modelSelectorButton);
});

elements.modelOptions.forEach((button) => {
  button.addEventListener("click", () => {
    setSelectedModel(button.dataset.modelOption);
    closeActivePopover(true);
  });
});

elements.composerAddButton.addEventListener("click", () => {
  const next = state.activePopover === "add" ? null : "add";
  setActivePopover(next, elements.composerAddButton);
});

elements.addCommands.forEach((button) => {
  button.addEventListener("click", () => handleAddCommand(button.dataset.addCommand));
});

elements.filesDialogOpeners.forEach((button) => {
  button.addEventListener("click", () => openFilesDialog());
});

elements.filesSearch.addEventListener("input", () => renderFilesDialog());
elements.filesList.addEventListener("change", (event) => {
  const target = event.target;
  if (!target || target.type !== "checkbox") {
    return;
  }
  if (target.checked) {
    state.filesDialogSelection.add(target.value);
  } else {
    state.filesDialogSelection.delete(target.value);
  }
  updateFilesSelectionStatus();
});
elements.filesConfirm.addEventListener("click", () => confirmFilesSelection());
elements.filesDialog.addEventListener("close", () => {
  state.filesDialogSelection = new Set();
});

elements.composerFileInput.addEventListener("change", () => {
  const count = elements.composerFileInput.files?.length || 0;
  openFilesDialog();
  if (count) {
    elements.filesSelectionStatus.textContent =
      `${count} local file${count === 1 ? "" : "s"} selected. Direct upload requires the upcoming Conversation attachment API.`;
  }
  elements.composerFileInput.value = "";
});

elements.attachmentChips.addEventListener("click", (event) => {
  const target = event.target;
  const button = target?.closest?.("[data-remove-attachment-id]");
  if (!button) {
    return;
  }
  state.selectedAttachments = state.selectedAttachments.filter(
    (attachment) => attachment.id !== button.dataset.removeAttachmentId,
  );
  renderAttachmentChips();
});

elements.artifactPanelClose.addEventListener("click", () => {
  setArtifactPanelOpen(false);
});
elements.operationsOpeners.forEach((button) => {
  button.addEventListener("click", () => setOperationsOpen(true));
});
elements.operationsClose.addEventListener("click", () => setOperationsOpen(false));
elements.sidebarCollapse.addEventListener("click", () => {
  setSidebarCollapsed(!state.sidebarCollapsed);
});
elements.newChat.addEventListener("click", () => startNewChat());

document.addEventListener("click", (event) => {
  if (!state.activePopover) {
    return;
  }
  const target = event.target;
  const activeMenu =
    state.activePopover === "model" ? elements.modelSelectorMenu : elements.composerAddMenu;
  const activeButton =
    state.activePopover === "model" ? elements.modelSelectorButton : elements.composerAddButton;
  if (activeMenu.contains(target) || activeButton.contains(target)) {
    return;
  }
  closeActivePopover(false);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.activePopover) {
    event.preventDefault();
    closeActivePopover(true);
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.input.value = button.dataset.prompt || "";
    fitComposer();
    elements.input.focus();
  });
});

elements.workbenchViewToggles.forEach((button) => {
  button.addEventListener("click", () => {
    switchWorkbenchView(button.dataset.workbenchViewToggle);
  });
});

elements.input.addEventListener("input", () => {
  fitComposer();
  syncComposerState();
});
elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submitRun();
  }
});
elements.send.addEventListener("click", () => submitRun());
elements.refresh.addEventListener("click", () => refreshRun());
elements.cancelRun.addEventListener("click", () => cancelRun());
elements.retryRun.addEventListener("click", () => retryRun());
elements.apiBase.addEventListener("change", () => {
  loadReadiness();
  loadCustomerSuccess();
  loadSolutionPacks();
  loadWorkspaceSkills();
});
elements.workspaceId.addEventListener("change", () => {
  loadWorkspaceSkills();
});
elements.customerSuccessRefresh.addEventListener("click", () => loadCustomerSuccess());
elements.solutionPackRefresh.addEventListener("click", () => loadSolutionPacks());
elements.solutionPackList.addEventListener("click", (event) => {
  const target = event.target;
  const button =
    target && target.closest ? target.closest("[data-solution-pack-id]") : null;
  if (button) {
    selectSolutionPack(button.dataset.solutionPackId);
  }
});
elements.solutionPackInstallButton.addEventListener("click", () => {
  installSelectedSolutionPack();
});
elements.workspaceSkillsRefresh.addEventListener("click", () => loadWorkspaceSkills());
elements.workspaceSkillsList.addEventListener("click", (event) => {
  const target = event.target;
  const button =
    target && target.closest ? target.closest("[data-workspace-skill-id]") : null;
  if (button) {
    selectWorkspaceSkill(button.dataset.workspaceSkillId);
  }
});
elements.skillInvokeButton.addEventListener("click", () => {
  invokeSelectedWorkspaceSkill();
});
elements.customerSuccessSubmitMissingSkill.addEventListener("click", () => {
  submitMissingSkillFeedback();
});
elements.customerSuccessCreateEvalCandidates.addEventListener("click", () => {
  createCustomerSuccessEvaluationCandidates();
});
elements.customerSuccessCreatePackCandidates.addEventListener("click", () => {
  createCustomerSuccessSolutionPackCandidates();
});
elements.customerSuccessEvalAccept.addEventListener("click", () => {
  reviewSelectedEvaluationCandidate("accepted");
});
elements.customerSuccessEvalReject.addEventListener("click", () => {
  reviewSelectedEvaluationCandidate("rejected");
});
elements.customerSuccessPackAccept.addEventListener("click", () => {
  reviewSelectedSolutionPackCandidate("accepted");
});
elements.customerSuccessPackReject.addEventListener("click", () => {
  reviewSelectedSolutionPackCandidate("rejected");
});
elements.runHistoryRefresh.addEventListener("click", () => loadRunHistory());
elements.runHistoryList.addEventListener("click", (event) => {
  const target = event.target;
  const button =
    target && target.closest ? target.closest("[data-run-history-id]") : null;
  if (button) {
    selectRunFromHistory(button.dataset.runHistoryId);
  }
});
elements.customerSuccessDraftsList.addEventListener("click", (event) => {
  const target = event.target;
  const button =
    target && target.closest
      ? target.closest("[data-solution-pack-draft-id]")
      : null;
  if (button) {
    selectSolutionPackDraft(button.dataset.solutionPackDraftId);
  }
});
elements.customerSuccessDraftSave.addEventListener("click", () => {
  saveSelectedSolutionPackDraft();
});
elements.customerSuccessDraftSubmit.addEventListener("click", () => {
  submitSelectedSolutionPackDraft();
});
elements.customerSuccessDraftApprove.addEventListener("click", () => {
  reviewSelectedSolutionPackDraft("approved");
});
elements.customerSuccessDraftReject.addEventListener("click", () => {
  reviewSelectedSolutionPackDraft("rejected");
});
elements.customerSuccessDraftApply.addEventListener("click", () => {
  applySelectedSolutionPackDraft();
});
elements.runFeedbackPositive.addEventListener("click", () => {
  submitRunFeedback(1);
});
elements.runFeedbackNegative.addEventListener("click", () => {
  submitRunFeedback(-1);
});
elements.approve.addEventListener("click", () => approveRun());
elements.reject.addEventListener("click", () => rejectRun());
elements.bootstrapLoginButton.addEventListener("click", () => bootstrapTenant());
elements.loginButton.addEventListener("click", () => login());
elements.logoutButton.addEventListener("click", () => logout());
elements.artifacts.addEventListener("click", (event) => {
  const target = event.target;
  const previewButton =
    target && target.closest
      ? target.closest("[data-preview-storage-object-id]")
      : null;
  if (previewButton) {
    previewArtifact(previewButton.dataset.previewStorageObjectId);
    return;
  }
  const downloadButton =
    target && target.closest ? target.closest("[data-storage-object-id]") : null;
  if (downloadButton) {
    downloadArtifact(downloadButton.dataset.storageObjectId);
  }
});
elements.browserScreenshot.addEventListener("click", (event) => {
  const storageObjectId = elements.browserScreenshot.dataset.browserStorageObjectId;
  if (!storageObjectId) {
    return;
  }
  event.preventDefault();
  downloadBrowserCapture(storageObjectId);
});

initializeControls();
fitComposer();
createChatController();
createSkillsUI();
createAgentsUI();
createArtifactsUI();
createSpeechUI();
createAgentBrainUI();
createFilesUI();

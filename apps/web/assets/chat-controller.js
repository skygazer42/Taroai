import { chatApi } from "./chat-api.js";
import {
  filterMentionCandidates,
  insertMention,
  mentionQuery,
  normalizeCapabilities,
  resourceReference,
} from "./mentions.js";

export const chatState = {
  currentThreadId: null,
  currentRunId: null,
  lastThreadSequence: 0,
  threads: [],
  messages: [],
  queue: [],
  events: [],
  artifacts: [],
  modelCatalog: [],
  selectedModel: null,
  capabilities: [],
  resourceRefs: [],
  uploads: [],
  thread: null,
  running: false,
  loading: false,
  streamAbort: null,
  streamRetry: null,
  reconnectAttempt: 0,
  mentionContext: null,
  activeSidecar: "artifacts",
  share: null,
  suggestions: [],
  promotingManual: false,
};

const ACTIVE_RUN_STATES = new Set([
  "created",
  "queued",
  "planning",
  "running",
  "executing",
  "repairing",
  "replanning",
  "verifying",
  "waiting_for_approval",
  "waiting_for_user",
]);

const TERMINAL_EVENT_WORDS = ["completed", "succeeded", "failed", "cancelled", "stopped"];

function query(selector, root = document) {
  return root.querySelector(selector);
}

function queryAll(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

function arrayFrom(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
    if (Array.isArray(payload?.[key]?.items)) return payload[key].items;
  }
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function setText(element, value) {
  if (element) element.textContent = value;
}

function safeTime(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
}

function threadIdFromHash() {
  const match = window.location.hash.match(/^#chat\/([^/?#]+)/i);
  return match ? decodeURIComponent(match[1]) : null;
}

function updateThreadHash(threadId, replace = false) {
  const hash = threadId ? `#chat/${encodeURIComponent(threadId)}` : "#chat";
  if (window.location.hash === hash) return;
  if (replace) history.replaceState({}, "", hash);
  else history.pushState({}, "", hash);
}

function modelKey(model) {
  return `${model.provider_id || model.provider || "provider"}:${model.model_id || model.id || model.name}`;
}

function normalizedModel(model, providerFallback = "") {
  const providerId = model.provider_id || model.provider || providerFallback || "default";
  const modelId = model.model_id || model.id || model.slug || model.name;
  const efforts = model.reasoning_efforts || model.efforts || model.supported_efforts || ["none"];
  return {
    ...model,
    provider_id: providerId,
    model_id: modelId,
    display_name: model.display_name || model.label || model.name || modelId,
    description: model.description || model.summary || "Available for this workspace",
    reasoning_efforts: Array.isArray(efforts) && efforts.length ? efforts : ["none"],
    reasoning_effort: model.reasoning_effort || model.default_reasoning_effort || efforts?.[0] || "none",
    enabled: model.enabled !== false && model.allowed !== false,
  };
}

function currentWorkspaceId() {
  return chatApi.settings().workspaceId;
}

function eventType(event) {
  return String(event.type || event.event_type || event.name || event.event || "event").toLowerCase();
}

function eventPayload(event) {
  return event.payload || event.data || event.detail || {};
}

function eventSequence(event) {
  return Number(event.thread_sequence || event.sequence || event.id || 0);
}

function dispatchStatus(message) {
  return String(message.dispatch_status || message.status || "completed").toLowerCase();
}

function messageContent(message) {
  return message.content || message.message || message.text || "";
}

function isAssistant(message) {
  return ["assistant", "agent", "system"].includes(String(message.role || message.kind || "").toLowerCase());
}

function escapeFilename(value) {
  return String(value || "artifact").replace(/[\\/:*?"<>|]+/g, "-");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export class ChatController {
  constructor(api = chatApi) {
    this.api = api;
    this.refs = {};
    this.initialized = false;
    this.boundClick = (event) => {
      Promise.resolve(this.onClick(event)).catch((error) => {
        this.network(error?.message || "The action could not be completed", "error");
      });
    };
    this.boundInput = (event) => this.onInput(event);
    this.boundKeydown = (event) => this.onKeydown(event);
    this.boundChange = (event) => this.onChange(event);
    this.boundHash = () => this.restoreFromHash();
    this.boundAuth = (event) => this.onAuthChanged(event);
    this.boundWindowMessage = (event) => this.onWindowMessage(event);
    this.boundDragOver = (event) => this.onDragOver(event);
    this.boundDrop = (event) => this.onDrop(event);
  }

  captureRefs() {
    this.refs = {
      shell: query("[data-app='taroai-workspace']"),
      conversation: query("[data-thread-conversation]"),
      chatScroll: query(".chat-scroll-region"),
      emptyState: query("[data-testid='chat-empty-state']"),
      input: query("#composer-input"),
      send: query("#send-button"),
      stop: query("[data-thread-stop]"),
      newChat: query("[data-new-chat]"),
      threadList: query("[data-thread-list]"),
      threadSearch: query("[data-thread-search]"),
      threadPresence: query("[data-thread-presence]"),
      modelButton: query("#model-selector-button"),
      modelMenu: query("#model-selector-menu"),
      selectedModel: query("[data-selected-model]"),
      fileInput: query("#composer-file-input"),
      dropzone: query("[data-chat-dropzone]"),
      uploadList: query("[data-upload-list]"),
      resourceChips: query("[data-resource-chips]"),
      mentionMenu: query("[data-mention-menu]"),
      mentionResults: query("[data-mention-results]"),
      deliveryMode: query("[data-delivery-mode]"),
      networkState: query("[data-chat-network-state]"),
      queueCount: query("[data-queue-count]"),
      sidecarQueueCount: query("[data-sidecar-queue-count]"),
      queue: query("[data-message-queue]"),
      sidecar: query("[data-workspace-sidecar]"),
      chatSidecar: query("[data-chat-sidecar]"),
      artifactEmpty: query("[data-thread-artifacts-empty]"),
      sidecarTitle: query("#artifact-panel-title"),
      artifactList: query("[data-thread-artifacts]"),
      artifactStage: query("[data-artifact-stage]"),
      artifactStageTitle: query("[data-artifact-stage-title]"),
      artifactStageContent: query("[data-artifact-stage-content]"),
      detailId: query("[data-thread-detail-id]"),
      detailModel: query("[data-thread-detail-model]"),
      detailRun: query("[data-thread-detail-run]"),
      detailStream: query("[data-thread-detail-stream]"),
      actionsMenu: query("[data-thread-actions-menu]"),
      moreButton: query("[data-thread-more]"),
      shareButton: query("[data-thread-share]"),
      createAgentButtons: queryAll("[data-create-agent], [data-create-agent-prompt], [data-thread-create-agent]"),
    };
  }

  async init() {
    if (this.initialized) return;
    this.initialized = true;
    this.captureRefs();
    document.addEventListener("click", this.boundClick, true);
    document.addEventListener("input", this.boundInput, true);
    document.addEventListener("keydown", this.boundKeydown, true);
    document.addEventListener("change", this.boundChange, true);
    window.addEventListener("hashchange", this.boundHash);
    window.addEventListener("taroai:auth-changed", this.boundAuth);
    window.addEventListener("message", this.boundWindowMessage);
    this.refs.dropzone?.addEventListener("dragover", this.boundDragOver);
    this.refs.dropzone?.addEventListener("drop", this.boundDrop);
    this.refs.dropzone?.addEventListener("dragleave", () => this.refs.dropzone?.classList.remove("is-dragging"));
    this.restoreDraft();
    this.renderAll();
    await Promise.allSettled([this.loadModelCatalog(), this.loadThreads(), this.loadCapabilities()]);
    await this.restoreFromHash();
  }

  stopOwnedEvent(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  ownedTarget(target) {
    return target?.closest?.(
      "[data-new-chat], #send-button, #model-selector-button, [data-chat-model], [data-model-effort], " +
        "[data-run-history-refresh], [data-thread-id], [data-thread-action], #composer-add-button, [data-add-command], " +
        "[data-open-queue], [data-open-artifacts], [data-sidecar-tab], [data-queue-action], [data-queue-dispatch], " +
        "[data-thread-stop], [data-thread-share], [data-thread-more], [data-thread-rename], [data-thread-pin], " +
        "[data-thread-archive], [data-thread-delete], [data-remove-upload], [data-remove-resource], [data-mention-id], " +
        "[data-delivery-mode], [data-voice-input], [data-thread-create-agent], [data-create-agent], " +
        "[data-create-agent-prompt], [data-thread-artifact], [data-artifact-copy], [data-artifact-download], " +
        "[data-message-copy], [data-message-retry], [data-message-speak], [data-message-summarize], [data-suggestion]",
    );
  }

  onClick(event) {
    const control = this.ownedTarget(event.target);
    if (!control) {
      if (!event.target?.closest?.("#model-selector-menu, #model-selector-button")) this.closeModelMenu();
      if (!event.target?.closest?.("[data-thread-actions-menu], [data-thread-more]")) this.closeThreadMenu();
      if (!event.target?.closest?.("#composer-add-menu, #composer-add-button")) this.closeAddMenu();
      return;
    }
    this.stopOwnedEvent(event);

    if (control.matches("[data-new-chat]")) return this.startNewChat();
    if (control.matches("#send-button")) return this.sendThreadMessage();
    if (control.matches("#model-selector-button")) return this.toggleModelMenu();
    if (control.matches("[data-chat-model]")) return this.selectModel(control.dataset.chatModel);
    if (control.matches("[data-model-effort]")) return this.selectModelEffort(control.dataset.modelEffort, control.dataset.modelKey);
    if (control.matches("[data-run-history-refresh]")) return this.loadThreads();
    if (control.matches("[data-thread-id]")) return this.loadThread(control.dataset.threadId, true);
    if (control.matches("[data-thread-action]")) return this.handleThreadItemAction(control);
    if (control.matches("#composer-add-button")) return this.toggleAddMenu();
    if (control.matches("[data-add-command]")) return this.handleAddCommand(control.dataset.addCommand);
    if (control.matches("[data-open-queue]")) return this.openSidecar("queue");
    if (control.matches("[data-open-artifacts]")) return this.openSidecar("artifacts");
    if (control.matches("[data-sidecar-tab]")) return this.openSidecar(control.dataset.sidecarTab);
    if (control.matches("[data-queue-action]")) return this.handleQueueAction(control);
    if (control.matches("[data-queue-dispatch]")) return this.dispatchQueue();
    if (control.matches("[data-thread-stop]")) return this.stopThread();
    if (control.matches("[data-thread-share]")) return this.shareThread();
    if (control.matches("[data-thread-more]")) return this.toggleThreadMenu();
    if (control.matches("[data-thread-rename]")) return this.renameCurrentThread();
    if (control.matches("[data-thread-pin]")) return this.pinCurrentThread();
    if (control.matches("[data-thread-archive]")) return this.archiveCurrentThread();
    if (control.matches("[data-thread-delete]")) return this.deleteCurrentThread();
    if (control.matches("[data-remove-upload]")) return this.removeUpload(control.dataset.removeUpload);
    if (control.matches("[data-remove-resource]")) return this.removeResource(control.dataset.removeResource);
    if (control.matches("[data-mention-id]")) return this.chooseMention(control.dataset.mentionId);
    if (control.matches("[data-delivery-mode]")) return this.toggleDeliveryMode();
    if (control.matches("[data-voice-input]")) return this.startVoiceInput(control);
    if (control.matches("[data-thread-create-agent], [data-create-agent], [data-create-agent-prompt]")) return this.openCreateAgentDialog();
    if (control.matches("[data-thread-artifact]")) return this.openArtifact(control.dataset.threadArtifact);
    if (control.matches("[data-artifact-copy]")) return this.copyArtifact();
    if (control.matches("[data-artifact-download]")) return this.downloadArtifact();
    if (control.matches("[data-message-copy]")) return this.copyMessage(control.dataset.messageCopy);
    if (control.matches("[data-message-retry]")) return this.retryMessage(control.dataset.messageRetry);
    if (control.matches("[data-message-speak]")) return this.speakMessage(control.dataset.messageSpeak, control);
    if (control.matches("[data-message-summarize]")) return this.summarizeMessage(control.dataset.messageSummarize);
    if (control.matches("[data-suggestion]")) return this.applySuggestion(control.dataset.suggestion);
  }

  onInput(event) {
    if (event.target === this.refs.threadSearch) {
      this.renderThreads();
      return;
    }
    if (event.target !== this.refs.input) return;
    this.saveDraft();
    this.syncComposer();
    this.updateMentionMenu();
  }

  onKeydown(event) {
    if (event.key === "Escape") {
      this.closeModelMenu();
      this.closeAddMenu();
      this.closeThreadMenu();
    }
    if (event.target !== this.refs.input) return;
    if (!this.refs.mentionMenu?.hidden) {
      if (event.key === "Escape") {
        event.preventDefault();
        this.closeMentionMenu();
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        const active = query("[data-mention-id].is-active", this.refs.mentionMenu) || query("[data-mention-id]", this.refs.mentionMenu);
        if (active) {
          this.stopOwnedEvent(event);
          this.chooseMention(active.dataset.mentionId);
          return;
        }
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      this.stopOwnedEvent(event);
      this.sendThreadMessage();
    }
  }

  onChange(event) {
    if (event.target === this.refs.fileInput) {
      event.stopImmediatePropagation();
      this.queueUploads(Array.from(event.target.files || []));
      event.target.value = "";
      return;
    }
    if (event.target?.matches?.("#api-base, #tenant-id, #user-id, #workspace-id")) {
      Promise.allSettled([this.loadThreads(), this.loadModelCatalog(), this.loadCapabilities()]);
    }
  }

  onDragOver(event) {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    this.refs.dropzone?.classList.add("is-dragging");
  }

  onDrop(event) {
    event.preventDefault();
    this.refs.dropzone?.classList.remove("is-dragging");
    this.queueUploads(Array.from(event.dataTransfer?.files || []));
  }

  async onAuthChanged(event) {
    if (!event.detail?.authenticated) {
      chatState.threads = [];
      this.startNewChat();
      this.renderThreadListNotice("Sign in to load threads", "Your local draft is still available.");
      return;
    }
    await Promise.allSettled([this.loadThreads(), this.loadModelCatalog(), this.loadCapabilities()]);
    const threadId = threadIdFromHash();
    if (threadId) await this.loadThread(threadId, false);
  }

  onWindowMessage(event) {
    const apiOrigin = new URL(this.api.settings().apiBase, window.location.href).origin;
    if (![window.location.origin, apiOrigin].includes(event.origin)) return;
    if (event.data?.type !== "taroai.connector.oauth.completed") return;
    this.network("Connector reconnected; resuming the paused action", "success");
    if (chatState.currentThreadId) {
      Promise.resolve(this.loadThread(chatState.currentThreadId, false)).catch(() => {});
    }
  }

  network(message, tone = "idle") {
    setText(this.refs.networkState, message);
    if (this.refs.networkState) this.refs.networkState.dataset.state = tone;
  }

  async loadThreads() {
    const queryString = new URLSearchParams({ workspace_id: currentWorkspaceId(), include_archived: "false" });
    try {
      const payload = await this.api.get(`/api/threads?${queryString}`);
      chatState.threads = arrayFrom(payload, "threads", "data");
      this.renderThreads();
    } catch (error) {
      this.renderThreadListNotice("Threads are unavailable", error.message);
    }
  }

  renderThreadListNotice(title, detail = "") {
    if (!this.refs.threadList) return;
    this.refs.threadList.replaceChildren();
    const item = document.createElement("li");
    item.className = "thread-list-notice";
    const strong = document.createElement("strong");
    strong.textContent = title;
    const small = document.createElement("small");
    small.textContent = detail;
    item.append(strong, small);
    this.refs.threadList.append(item);
  }

  renderThreads() {
    if (!this.refs.threadList) return;
    const search = (this.refs.threadSearch?.value || "").trim().toLowerCase();
    const threads = chatState.threads
      .filter((thread) => !["archived", "deleted"].includes(String(thread.status || "").toLowerCase()))
      .filter((thread) => !search || `${thread.title || ""} ${thread.last_message || ""}`.toLowerCase().includes(search))
      .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    this.refs.threadList.replaceChildren();
    if (!threads.length) {
      const empty = document.createElement("li");
      empty.className = "thread-list-notice";
      empty.innerHTML = `<strong>${search ? "No matching threads" : "No threads yet"}</strong><small>${search ? "Try a shorter search." : "Your conversations will stay here."}</small>`;
      this.refs.threadList.append(empty);
      return;
    }
    for (const thread of threads) {
      const item = document.createElement("li");
      item.className = "thread-list-item";
      item.classList.toggle("is-active", thread.id === chatState.currentThreadId);
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.threadId = thread.id;
      const title = document.createElement("strong");
      title.textContent = `${thread.pinned ? "• " : ""}${thread.title || "Untitled thread"}`;
      const meta = document.createElement("small");
      meta.textContent = thread.running || ACTIVE_RUN_STATES.has(String(thread.run_status || "").toLowerCase())
        ? "Working…"
        : safeTime(thread.updated_at || thread.created_at) || "Ready";
      button.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "thread-item-actions";
      for (const [action, label] of [["rename", "Rename"], ["archive", "Archive"], ["delete", "Delete"]]) {
        const actionButton = document.createElement("button");
        actionButton.type = "button";
        actionButton.dataset.threadAction = action;
        actionButton.dataset.threadActionId = thread.id;
        actionButton.title = label;
        actionButton.setAttribute("aria-label", `${label} ${thread.title || "thread"}`);
        actionButton.textContent = action === "rename" ? "✎" : action === "archive" ? "□" : "×";
        actions.append(actionButton);
      }
      item.append(button, actions);
      this.refs.threadList.append(item);
    }
  }

  async createThread() {
    const model = chatState.selectedModel;
    const body = {
      workspace_id: currentWorkspaceId(),
      title: "New thread",
      provider_id: model?.provider_id || null,
      model_id: model?.model_id || null,
      reasoning_effort: model?.reasoning_effort || null,
    };
    const created = await this.api.post("/api/threads", body, { scope: "thread-create" });
    const thread = created.thread || created;
    chatState.currentThreadId = thread.id;
    chatState.thread = thread;
    chatState.threads = [thread, ...chatState.threads.filter((item) => item.id !== thread.id)];
    updateThreadHash(thread.id, true);
    this.renderThreads();
    this.renderDetails();
    return thread;
  }

  async loadThread(threadId, updateHash = false) {
    if (!threadId) return this.startNewChat();
    this.abortStream();
    chatState.loading = true;
    chatState.currentThreadId = threadId;
    chatState.messages = [];
    chatState.events = [];
    chatState.queue = [];
    this.network("Opening thread…", "loading");
    this.renderConversation();
    try {
      const [payload, messagesPayload] = await Promise.all([
        this.api.get(`/api/threads/${encodeURIComponent(threadId)}`),
        this.api.get(`/api/threads/${encodeURIComponent(threadId)}/messages`),
      ]);
      const thread = payload.thread || payload;
      if (thread.id && thread.id !== threadId) chatState.currentThreadId = thread.id;
      chatState.thread = thread;
      chatState.currentRunId = thread.current_run_id || thread.active_run_id || payload.current_run_id || null;
      chatState.messages = arrayFrom(messagesPayload, "messages", "chat_messages");
      const hydratedEvents = arrayFrom(payload, "events", "timeline");
      const cachedEvents = this.restoreEventCache(threadId);
      chatState.events = hydratedEvents.length ? hydratedEvents : cachedEvents;
      const latestEvent = chatState.events.at(-1);
      chatState.currentRunId = chatState.currentRunId || latestEvent?.run_id || null;
      chatState.queue = arrayFrom(payload, "queue", "queued_messages").length
        ? arrayFrom(payload, "queue", "queued_messages")
        : chatState.messages.filter((message) => ["queued", "steering", "ready"].includes(dispatchStatus(message)));
      chatState.artifacts = arrayFrom(payload, "artifacts", "outputs").length
        ? arrayFrom(payload, "artifacts", "outputs")
        : arrayFrom(thread, "artifacts", "outputs");
      chatState.running =
        Boolean(thread.running) ||
        ACTIVE_RUN_STATES.has(String(thread.run_status || thread.current_run_status || "").toLowerCase()) ||
        chatState.messages.some((message) => dispatchStatus(message) === "inflight");
      if (chatState.running && chatState.currentRunId) {
        const streamedText = chatState.events
          .filter((event) => event.run_id === chatState.currentRunId && eventType(event) === "assistant.delta")
          .map((event) => eventPayload(event).delta || "")
          .join("");
        if (streamedText) {
          chatState.messages.push({
            id: `stream:${chatState.currentRunId}`,
            role: "assistant",
            content: streamedText,
            dispatch_status: "streaming",
            created_at: latestEvent?.created_at || new Date().toISOString(),
          });
        }
      }
      const restoredSequence = chatState.events.length
        ? Number(localStorage.getItem(`taroai.threadSequence.${threadId}`) || 0)
        : 0;
      const hydratedSequence = chatState.events.reduce((highest, item) => Math.max(highest, eventSequence(item)), 0);
      chatState.lastThreadSequence = Math.max(restoredSequence, hydratedSequence);
      const threadModel = this.findModel(thread.provider_id, thread.model_id);
      if (threadModel) {
        chatState.selectedModel = { ...threadModel, reasoning_effort: thread.reasoning_effort || threadModel.reasoning_effort };
      }
      chatState.threads = [thread, ...chatState.threads.filter((item) => item.id !== thread.id)];
      if (updateHash) updateThreadHash(thread.id);
      this.restoreDraft();
      this.renderAll();
      await this.maybePromoteManualMessage();
      this.network(chatState.running ? "Agent is working" : "Thread ready", chatState.running ? "active" : "idle");
      this.startEventStream();
    } catch (error) {
      this.network(error.status === 404 ? "Thread not found" : "Could not open thread", "error");
      this.renderInlineNotice("Thread unavailable", error.message, "failure");
    } finally {
      chatState.loading = false;
    }
  }

  async restoreFromHash() {
    const threadId = threadIdFromHash();
    if (threadId && threadId !== chatState.currentThreadId) return this.loadThread(threadId, false);
    if (!threadId && chatState.currentThreadId) this.startNewChat(false);
  }

  startNewChat(updateHash = true) {
    this.abortStream();
    chatState.currentThreadId = null;
    chatState.currentRunId = null;
    chatState.thread = null;
    chatState.messages = [];
    chatState.queue = [];
    chatState.events = [];
    chatState.artifacts = [];
    chatState.resourceRefs = [];
    chatState.uploads = [];
    chatState.running = false;
    chatState.suggestions = [];
    chatState.lastThreadSequence = 0;
    if (updateHash) updateThreadHash(null);
    this.refs.input.value = localStorage.getItem("taroai.threadDraft.new") || "";
    this.network("Ready", "idle");
    this.closeThreadMenu();
    this.closeModelMenu();
    this.renderAll();
    this.refs.input?.focus();
  }

  async updateThread(threadId, changes) {
    const updated = await this.api.patch(`/api/threads/${encodeURIComponent(threadId)}`, changes, { scope: "thread-update" });
    const thread = updated.thread || updated;
    chatState.threads = chatState.threads.map((item) => (item.id === threadId ? { ...item, ...thread, ...changes } : item));
    if (chatState.currentThreadId === threadId) chatState.thread = { ...chatState.thread, ...thread, ...changes };
    this.renderThreads();
    this.renderDetails();
    return thread;
  }

  async handleThreadItemAction(control) {
    const threadId = control.dataset.threadActionId;
    const action = control.dataset.threadAction;
    const thread = chatState.threads.find((item) => item.id === threadId);
    if (!thread) return;
    if (action === "rename") {
      const title = window.prompt("Rename thread", thread.title || "Untitled thread")?.trim();
      if (title) await this.updateThread(threadId, { title });
    }
    if (action === "archive") {
      await this.archiveThread(threadId);
      if (threadId === chatState.currentThreadId) this.startNewChat();
      else this.renderThreads();
    }
    if (action === "delete") {
      if (!window.confirm(`Delete “${thread.title || "Untitled thread"}”?`)) return;
      await this.api.delete(`/api/threads/${encodeURIComponent(threadId)}`, { scope: "thread-delete" });
      chatState.threads = chatState.threads.filter((item) => item.id !== threadId);
      if (threadId === chatState.currentThreadId) this.startNewChat();
      else this.renderThreads();
    }
  }

  async renameCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    const title = window.prompt("Rename thread", chatState.thread?.title || "Untitled thread")?.trim();
    if (title) await this.updateThread(chatState.currentThreadId, { title });
  }

  async pinCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    await this.updateThread(chatState.currentThreadId, { pinned: !chatState.thread?.pinned });
  }

  async archiveCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    await this.archiveThread(chatState.currentThreadId);
    this.startNewChat();
  }

  async archiveThread(threadId) {
    try {
      return await this.updateThread(threadId, { status: "archived" });
    } catch (error) {
      if (![400, 404, 405, 422].includes(error.status)) throw error;
      chatState.threads = chatState.threads.map((thread) =>
        thread.id === threadId ? { ...thread, status: "archived" } : thread,
      );
      if (chatState.currentThreadId === threadId) chatState.thread = { ...chatState.thread, status: "archived" };
      this.network("Archived in this view; server archive support is not ready", "warning");
      this.renderThreads();
      return chatState.thread;
    }
  }

  async deleteCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId || !window.confirm("Delete this thread and its messages?")) return;
    await this.api.delete(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}`, { scope: "thread-delete" });
    chatState.threads = chatState.threads.filter((thread) => thread.id !== chatState.currentThreadId);
    this.startNewChat();
  }

  toggleThreadMenu() {
    if (!this.refs.actionsMenu) return;
    this.closeModelMenu();
    this.closeAddMenu();
    const open = this.refs.actionsMenu.hidden;
    this.refs.actionsMenu.hidden = !open;
    this.refs.moreButton?.setAttribute("aria-expanded", String(open));
  }

  closeThreadMenu() {
    if (this.refs.actionsMenu) this.refs.actionsMenu.hidden = true;
    this.refs.moreButton?.setAttribute("aria-expanded", "false");
  }

  async loadModelCatalog() {
    const queryString = new URLSearchParams({ workspace_id: currentWorkspaceId() });
    try {
      const payload = await this.api.get(`/api/model-catalog?${queryString}`);
      let models = arrayFrom(payload, "models", "items");
      if (!models.length && Array.isArray(payload.providers)) {
        models = payload.providers.flatMap((provider) =>
          arrayFrom(provider, "models").map((model) => ({
            ...model,
            provider_id: model.provider_id || provider.id || provider.provider_id || provider.name,
          })),
        );
      } else if (!models.length && payload.providers) {
        models = Object.entries(payload.providers).flatMap(([provider, providerModels]) =>
          arrayFrom(providerModels, "models").map((model) => ({ ...model, provider_id: provider })),
        );
      }
      chatState.modelCatalog = models.map((model) => normalizedModel(model)).filter((model) => model.enabled && model.model_id);
    } catch (error) {
      const existing = queryAll("[data-model-option]").map((button) =>
        normalizedModel({
          provider_id: button.closest(".model-menu")?.dataset.provider || "configured",
          model_id: button.dataset.modelOption,
          display_name: button.dataset.modelOption,
          description: query("small", button)?.textContent || "Configured model",
        }),
      );
      chatState.modelCatalog = existing;
      this.network("Using saved model catalog", "warning");
    }
    const stored = localStorage.getItem("taroai.chatModel");
    const fallback = chatState.modelCatalog[0] || null;
    chatState.selectedModel = chatState.selectedModel || chatState.modelCatalog.find((model) => modelKey(model) === stored) || fallback;
    this.renderModelMenu();
    this.renderModelButton();
  }

  findModel(providerId, modelId) {
    return chatState.modelCatalog.find((model) => model.provider_id === providerId && model.model_id === modelId) || null;
  }

  renderModelMenu() {
    if (!this.refs.modelMenu) return;
    this.refs.modelMenu.replaceChildren();
    if (!chatState.modelCatalog.length) {
      const empty = document.createElement("p");
      empty.className = "model-menu-empty";
      empty.textContent = "No models are available for this workspace.";
      this.refs.modelMenu.append(empty);
      return;
    }
    const groups = new Map();
    for (const model of chatState.modelCatalog) {
      if (!groups.has(model.provider_id)) groups.set(model.provider_id, []);
      groups.get(model.provider_id).push(model);
    }
    for (const [provider, models] of groups) {
      const label = document.createElement("p");
      label.className = "menu-group-label";
      label.textContent = provider;
      this.refs.modelMenu.append(label);
      for (const model of models) {
        const row = document.createElement("div");
        row.className = "model-option chat-model-option";
        row.classList.toggle("is-selected", modelKey(model) === modelKey(chatState.selectedModel || {}));
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chat-model-main";
        button.dataset.chatModel = modelKey(model);
        const mark = document.createElement("span");
        mark.className = "model-mark";
        mark.textContent = provider.slice(0, 1).toUpperCase();
        const copy = document.createElement("span");
        const strong = document.createElement("strong");
        strong.textContent = model.display_name;
        const small = document.createElement("small");
        small.textContent = model.description;
        copy.append(strong, small);
        button.append(mark, copy);
        const effort = document.createElement("button");
        effort.type = "button";
        effort.className = "effort-chip";
        effort.dataset.modelEffort = model.reasoning_effort || model.reasoning_efforts[0];
        effort.dataset.modelKey = modelKey(model);
        effort.textContent = `${model.reasoning_effort || model.reasoning_efforts[0]} ›`;
        effort.title = "Cycle reasoning effort";
        row.append(button, effort);
        this.refs.modelMenu.append(row);
      }
    }
  }

  renderModelButton() {
    if (!chatState.selectedModel) {
      setText(this.refs.selectedModel, "Choose model");
      return;
    }
    setText(this.refs.selectedModel, `${chatState.selectedModel.display_name} · ${chatState.selectedModel.reasoning_effort}`);
    setText(this.refs.detailModel, `${chatState.selectedModel.provider_id} / ${chatState.selectedModel.model_id} / ${chatState.selectedModel.reasoning_effort}`);
  }

  toggleModelMenu() {
    if (!this.refs.modelMenu) return;
    this.closeAddMenu();
    this.closeThreadMenu();
    const open = this.refs.modelMenu.hidden;
    this.refs.modelMenu.hidden = !open;
    this.refs.modelButton?.setAttribute("aria-expanded", String(open));
  }

  closeModelMenu() {
    if (this.refs.modelMenu) this.refs.modelMenu.hidden = true;
    this.refs.modelButton?.setAttribute("aria-expanded", "false");
  }

  toggleAddMenu() {
    const menu = query("#composer-add-menu");
    const button = query("#composer-add-button");
    if (!menu) return;
    this.closeModelMenu();
    this.closeThreadMenu();
    const open = menu.hidden;
    menu.hidden = !open;
    button?.setAttribute("aria-expanded", String(open));
  }

  closeAddMenu() {
    const menu = query("#composer-add-menu");
    if (menu) menu.hidden = true;
    query("#composer-add-button")?.setAttribute("aria-expanded", "false");
  }

  handleAddCommand(command) {
    this.closeAddMenu();
    if (command === "files") {
      this.refs.fileInput?.click();
      return;
    }
    const commandText = {
      drive: "@Google-Drive ",
      image: "/image ",
      video: "/video ",
      voice: "/voice ",
      connectors: "@",
      browser: "/browser ",
      workflow: "/workflow ",
      slides: "/slides ",
    }[command];
    if (!commandText) return;
    this.refs.input.value = commandText;
    this.syncComposer();
    this.updateMentionMenu();
    this.refs.input.focus();
  }

  async selectModel(key) {
    const model = chatState.modelCatalog.find((item) => modelKey(item) === key);
    if (!model) return;
    chatState.selectedModel = { ...model };
    localStorage.setItem("taroai.chatModel", key);
    this.closeModelMenu();
    this.renderModelMenu();
    this.renderModelButton();
    if (chatState.currentThreadId) {
      try {
        await this.updateThread(chatState.currentThreadId, {
          provider_id: model.provider_id,
          model_id: model.model_id,
          reasoning_effort: model.reasoning_effort,
        });
        this.network("Model updated", "success");
      } catch (error) {
        this.network(`Model update failed: ${error.message}`, "error");
      }
    }
  }

  async selectModelEffort(currentEffort, key) {
    const model = chatState.modelCatalog.find((item) => modelKey(item) === key);
    if (!model) return;
    const currentIndex = Math.max(0, model.reasoning_efforts.indexOf(currentEffort));
    model.reasoning_effort = model.reasoning_efforts[(currentIndex + 1) % model.reasoning_efforts.length];
    chatState.selectedModel = { ...model };
    this.renderModelMenu();
    this.renderModelButton();
    if (chatState.currentThreadId) {
      try {
        await this.updateThread(chatState.currentThreadId, { reasoning_effort: model.reasoning_effort });
      } catch (error) {
        this.network(`Effort update failed: ${error.message}`, "error");
      }
    }
  }

  async loadCapabilities() {
    try {
      const payload = await this.api.get(`/api/workspaces/${encodeURIComponent(currentWorkspaceId())}/capabilities`);
      chatState.capabilities = normalizeCapabilities(payload);
    } catch {
      chatState.capabilities = [];
    }
  }

  updateMentionMenu() {
    const input = this.refs.input;
    const context = mentionQuery(input.value, input.selectionStart || input.value.length);
    chatState.mentionContext = context;
    if (!context) return this.closeMentionMenu();
    const candidates = filterMentionCandidates(chatState.capabilities, context.query);
    this.refs.mentionResults?.replaceChildren();
    if (!candidates.length) {
      const empty = document.createElement("p");
      empty.className = "mention-empty";
      empty.textContent = chatState.capabilities.length ? "No matching resources" : "No workspace resources available";
      this.refs.mentionResults?.append(empty);
    }
    candidates.forEach((candidate, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "option";
      button.className = index === 0 ? "is-active" : "";
      button.dataset.mentionId = `${candidate.type}:${candidate.id}`;
      const icon = document.createElement("span");
      icon.className = `mention-icon mention-icon-${candidate.type}`;
      icon.textContent = candidate.icon;
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = candidate.name;
      const small = document.createElement("small");
      small.textContent = `${candidate.type}${candidate.description ? ` · ${candidate.description}` : ""}`;
      copy.append(strong, small);
      button.append(icon, copy);
      this.refs.mentionResults?.append(button);
    });
    if (this.refs.mentionMenu) this.refs.mentionMenu.hidden = false;
  }

  closeMentionMenu() {
    if (this.refs.mentionMenu) this.refs.mentionMenu.hidden = true;
    chatState.mentionContext = null;
  }

  chooseMention(compoundId) {
    const candidate = chatState.capabilities.find((item) => `${item.type}:${item.id}` === compoundId);
    if (!candidate || !chatState.mentionContext) return;
    const inserted = insertMention(this.refs.input.value, this.refs.input.selectionStart, chatState.mentionContext, candidate);
    this.refs.input.value = inserted.text;
    if (!chatState.resourceRefs.some((item) => item.type === candidate.type && item.id === candidate.id)) {
      chatState.resourceRefs.push({ ...resourceReference(candidate), name: candidate.name });
    }
    this.closeMentionMenu();
    this.renderResourceChips();
    this.syncComposer();
    this.refs.input.focus();
    this.refs.input.setSelectionRange(inserted.cursor, inserted.cursor);
  }

  removeResource(compoundId) {
    chatState.resourceRefs = chatState.resourceRefs.filter((item) => `${item.type}:${item.id}` !== compoundId);
    this.renderResourceChips();
  }

  renderResourceChips() {
    if (!this.refs.resourceChips) return;
    this.refs.resourceChips.replaceChildren();
    for (const resource of chatState.resourceRefs) {
      const chip = document.createElement("span");
      chip.className = `resource-chip resource-chip-${resource.type}`;
      const kind = document.createElement("small");
      kind.textContent = resource.type;
      const name = document.createElement("strong");
      name.textContent = `@${resource.name || resource.id}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.removeResource = `${resource.type}:${resource.id}`;
      remove.setAttribute("aria-label", `Remove ${resource.name || resource.id}`);
      remove.textContent = "×";
      chip.append(kind, name, remove);
      this.refs.resourceChips.append(chip);
    }
  }

  async queueUploads(files) {
    for (const file of files) {
      const localId = `upload:${Date.now()}:${Math.random().toString(16).slice(2)}`;
      const upload = { id: localId, file, filename: file.name, size_bytes: file.size, progress: 0, status: "Reading" };
      chatState.uploads.push(upload);
      this.renderUploads();
      try {
        const result = await this.api.upload(file, (progress, status) => {
          upload.progress = progress;
          upload.status = status;
          this.renderUploads();
        });
        Object.assign(upload, result, { local_id: localId, id: result.id || result.storage_object_id || localId, progress: 1, status: "Ready", file: null });
      } catch (error) {
        upload.progress = 1;
        upload.status = "Failed";
        upload.error = error.message;
      }
      this.renderUploads();
      this.syncComposer();
    }
  }

  removeUpload(uploadId) {
    chatState.uploads = chatState.uploads.filter((upload) => upload.id !== uploadId && upload.local_id !== uploadId);
    this.renderUploads();
    this.syncComposer();
  }

  renderUploads() {
    if (!this.refs.uploadList) return;
    this.refs.uploadList.replaceChildren();
    for (const upload of chatState.uploads) {
      const chip = document.createElement("div");
      chip.className = "upload-chip";
      chip.dataset.status = upload.status.toLowerCase();
      const icon = document.createElement("span");
      icon.className = "upload-file-icon";
      icon.textContent = "□";
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = upload.filename || upload.name || "Upload";
      const small = document.createElement("small");
      small.textContent = upload.error || upload.status;
      const meter = document.createElement("i");
      meter.style.setProperty("--upload-progress", `${Math.round((upload.progress || 0) * 100)}%`);
      copy.append(strong, small, meter);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.removeUpload = upload.id;
      remove.setAttribute("aria-label", `Remove ${strong.textContent}`);
      remove.textContent = "×";
      chip.append(icon, copy, remove);
      this.refs.uploadList.append(chip);
    }
  }

  toggleDeliveryMode() {
    if (!this.refs.deliveryMode) return;
    const current = this.refs.deliveryMode.value || "queue";
    const next = current === "queue" ? "manual" : current === "manual" ? "steer" : "queue";
    this.refs.deliveryMode.value = next;
    this.refs.deliveryMode.textContent = next === "queue" ? "Queue automatic" : next === "manual" ? "Queue for review" : "Steer now";
    this.refs.deliveryMode.classList.toggle("is-steer", next === "steer");
    this.refs.deliveryMode.classList.toggle("is-manual", next === "manual");
  }

  saveDraft() {
    const key = chatState.currentThreadId || "new";
    localStorage.setItem(`taroai.threadDraft.${key}`, this.refs.input?.value || "");
  }

  restoreDraft() {
    if (!this.refs.input) return;
    const key = chatState.currentThreadId || "new";
    this.refs.input.value = localStorage.getItem(`taroai.threadDraft.${key}`) || "";
    this.syncComposer();
  }

  clearDraft() {
    const key = chatState.currentThreadId || "new";
    localStorage.removeItem(`taroai.threadDraft.${key}`);
    if (this.refs.input) this.refs.input.value = "";
  }

  syncComposer() {
    if (!this.refs.input || !this.refs.send) return;
    this.refs.input.style.height = "auto";
    this.refs.input.style.height = `${Math.min(this.refs.input.scrollHeight, 180)}px`;
    const uploadPending = chatState.uploads.some((upload) => !["Ready", "Failed"].includes(upload.status));
    this.refs.send.disabled = uploadPending || (!this.refs.input.value.trim() && !chatState.uploads.some((upload) => upload.status === "Ready"));
    if (this.refs.stop) this.refs.stop.hidden = !chatState.running;
    this.refs.send.hidden = chatState.running && !this.refs.input.value.trim();
    if (this.refs.deliveryMode) this.refs.deliveryMode.hidden = !chatState.running;
  }

  async sendThreadMessage(contentOverride = null, deliveryOverride = null) {
    const content = (contentOverride ?? this.refs.input?.value ?? "").trim();
    const attachments = chatState.uploads.filter((upload) => upload.status === "Ready").map((upload) => upload.id || upload.storage_object_id);
    if (!content && !attachments.length) return;
    const submittedContent = content || "Review the attached files.";
    if (!chatState.currentThreadId) {
      try {
        await this.createThread();
      } catch (error) {
        this.network(`Could not create thread: ${error.message}`, "error");
        return;
      }
    }
    const deliveryMode = deliveryOverride || (chatState.running ? this.refs.deliveryMode?.value || "queue" : "auto");
    const optimisticId = `client:${Date.now()}`;
    const optimistic = {
      id: optimisticId,
      role: "user",
      content: submittedContent,
      dispatch_status: deliveryMode === "auto" ? "sending" : deliveryMode === "steer" ? "steering" : deliveryMode === "manual" ? "ready" : "queued",
      kind: deliveryMode === "manual" ? "manual_queue" : "text",
      created_at: new Date().toISOString(),
      attachments,
      resource_refs: chatState.resourceRefs,
      optimistic: true,
    };
    chatState.messages.push(optimistic);
    this.clearDraft();
    chatState.resourceRefs = [];
    chatState.uploads = [];
    this.renderAll();
    this.network(deliveryMode === "auto" ? "Starting agent…" : deliveryMode === "steer" ? "Steering requested" : deliveryMode === "manual" ? "Queued for review after this turn" : "Message queued automatically", "loading");
    try {
      const result = await this.api.post(
        `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages`,
        {
          content: submittedContent,
          delivery_mode: deliveryMode,
          resource_refs: optimistic.resource_refs.map(({ type, id, version }) => ({ type, id, version: version ?? null })),
          attachments,
        },
        { scope: "thread-message" },
      );
      const message = result.message || result.chat_message || result;
      const persistedMessage = {
        ...optimistic,
        ...message,
        id: message.id || result.message_id || optimisticId,
        dispatch_status: message.dispatch_status || result.dispatch_status || optimistic.dispatch_status,
        optimistic: false,
      };
      chatState.messages = chatState.messages.map((item) => (item.id === optimisticId ? persistedMessage : item));
      chatState.currentRunId = result.run_id || result.current_run_id || chatState.currentRunId;
      const status = dispatchStatus(persistedMessage);
      if (["queued", "steering", "ready"].includes(status)) {
        chatState.queue = [persistedMessage, ...chatState.queue.filter((item) => item.id !== persistedMessage.id)];
      } else {
        chatState.running = true;
      }
      if (!chatState.thread?.title || chatState.thread.title === "New thread") {
        const suggestedTitle = submittedContent.replace(/\s+/g, " ").slice(0, 72).trim();
        if (suggestedTitle) {
          try {
            await this.updateThread(chatState.currentThreadId, { title: suggestedTitle });
          } catch {
            // A title is convenience state; the accepted message remains authoritative.
          }
        }
      }
      this.updateThreadPreview(submittedContent);
      this.renderAll();
      this.startEventStream();
    } catch (error) {
      chatState.messages = chatState.messages.map((item) => (item.id === optimisticId ? { ...item, dispatch_status: "failed", error: error.message } : item));
      this.network(`Message failed: ${error.message}`, "error");
      this.renderConversation();
    }
  }

  updateThreadPreview(content) {
    chatState.thread = { ...chatState.thread, last_message: content, updated_at: new Date().toISOString() };
    chatState.threads = chatState.threads.map((thread) => (thread.id === chatState.currentThreadId ? { ...thread, ...chatState.thread } : thread));
    this.renderThreads();
  }

  async editQueuedMessage(messageId) {
    const message = chatState.queue.find((item) => item.id === messageId);
    if (!message) return;
    const content = window.prompt("Edit queued message", messageContent(message))?.trim();
    if (!content) return;
    const updated = await this.api.patch(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}`,
      { content },
      { scope: "queue-edit" },
    );
    chatState.queue = chatState.queue.map((item) => (item.id === messageId ? { ...item, ...(updated.message || updated), content } : item));
    chatState.messages = chatState.messages.map((item) => (item.id === messageId ? { ...item, ...(updated.message || updated), content } : item));
    this.renderAll();
  }

  async deleteQueuedMessage(messageId) {
    await this.api.delete(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}`,
      { scope: "queue-delete" },
    );
    chatState.queue = chatState.queue.filter((item) => item.id !== messageId);
    chatState.messages = chatState.messages.filter((item) => item.id !== messageId);
    this.renderAll();
  }

  async steerQueuedMessage(messageId) {
    const queued = chatState.queue.find((item) => item.id === messageId);
    if (!queued) return;
    let result;
    try {
      result = await this.api.post(
        `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}/steer`,
        {},
        { scope: "queue-steer" },
      );
    } catch (error) {
      if (![404, 405].includes(error.status)) throw error;
      result = await this.api.post(
        `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/steer`,
        {
          content: messageContent(queued),
          attachments: queued.attachments || [],
          resource_refs: queued.resource_refs || [],
        },
        { scope: "queue-steer-fallback" },
      );
      await this.deleteQueuedMessage(messageId);
    }
    const status = result.dispatch_status || result.message?.dispatch_status || "steering";
    const nextMessageId = result.message_id || result.message?.id || messageId;
    const replacement = { ...queued, ...result.message, id: nextMessageId, dispatch_status: status };
    chatState.queue = [replacement, ...chatState.queue.filter((item) => item.id !== messageId && item.id !== nextMessageId)];
    chatState.messages = [
      ...chatState.messages.filter((item) => item.id !== messageId && item.id !== nextMessageId),
      replacement,
    ];
    this.network(status === "queued" ? "Steering unavailable; kept in queue" : "Will steer after the current action", status === "queued" ? "warning" : "success");
    this.renderAll();
  }

  addExistingAttachment(storageObject) {
    const id = storageObject?.storage_object_id || storageObject?.id;
    if (!id || chatState.uploads.some((upload) => (upload.id || upload.storage_object_id) === id)) return;
    chatState.uploads.push({
      ...storageObject,
      id,
      filename: storageObject.filename || storageObject.logical_path || id,
      status: "Ready",
      progress: 1,
    });
    this.renderAll();
    this.syncComposer();
    this.refs.input?.focus();
    this.network(`${storageObject.filename || "Workspace file"} attached`, "idle");
  }

  async promoteManualMessage(messageId) {
    const queued = chatState.queue.find((item) => item.id === messageId);
    if (!queued || queued.kind !== "manual_queue") return;
    const promoted = await this.api.post(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}/promote`,
      {},
      { scope: "queue-promote" },
    );
    chatState.queue = chatState.queue.filter((item) => item.id !== messageId);
    chatState.messages = chatState.messages.filter((item) => item.id !== messageId);
    chatState.resourceRefs = arrayFrom(promoted.resource_refs || [], "items");
    chatState.uploads = arrayFrom(promoted.attachments || [], "items").map((attachment) => ({
      id: typeof attachment === "string" ? attachment : attachment.id || attachment.storage_object_id,
      filename: typeof attachment === "string" ? attachment : attachment.filename || attachment.name || attachment.id,
      status: "Ready",
      progress: 1,
    }));
    if (this.refs.input) this.refs.input.value = messageContent(promoted);
    this.saveDraft();
    this.renderAll();
    this.syncComposer();
    this.refs.input?.focus();
    this.network("Manual queued message moved to the composer", "idle");
  }

  async maybePromoteManualMessage() {
    if (chatState.running || chatState.promotingManual || this.refs.input?.value.trim()) return;
    const pending = chatState.queue.find(
      (message) => message.kind === "manual_queue" && dispatchStatus(message) === "ready",
    );
    if (!pending) return;
    chatState.promotingManual = true;
    try {
      await this.promoteManualMessage(pending.id);
    } catch (error) {
      this.network(`Could not restore manual queue: ${error.message}`, "error");
    } finally {
      chatState.promotingManual = false;
    }
  }

  handleQueueAction(control) {
    const id = control.dataset.queueMessageId;
    if (control.dataset.queueAction === "promote") return this.promoteManualMessage(id);
    if (control.dataset.queueAction === "edit") return this.editQueuedMessage(id);
    if (control.dataset.queueAction === "delete") return this.deleteQueuedMessage(id);
    if (control.dataset.queueAction === "steer") return this.steerQueuedMessage(id);
  }

  async dispatchQueue() {
    if (!chatState.currentThreadId || !chatState.queue.length) return;
    try {
      let result;
      try {
        result = await this.api.post(
          `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/queue/dispatch`,
          {},
          { scope: "queue-dispatch" },
        );
      } catch (error) {
        if (![404, 405].includes(error.status)) throw error;
        result = await this.api.post(
          `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/continue`,
          {},
          { scope: "queue-continue-fallback" },
        );
      }
      chatState.currentRunId = result.run_id || chatState.currentRunId;
      chatState.running = true;
      this.network("Queued message started", "active");
      await this.loadThread(chatState.currentThreadId, false);
    } catch (error) {
      this.network(`Could not start queue: ${error.message}`, "error");
    }
  }

  renderQueue() {
    const queue = chatState.queue.filter((message) => ["queued", "steering", "ready"].includes(dispatchStatus(message)));
    setText(this.refs.queueCount, String(queue.length));
    setText(this.refs.sidecarQueueCount, String(queue.length));
    if (!this.refs.queue) return;
    this.refs.queue.replaceChildren();
    if (!queue.length) {
      const empty = document.createElement("li");
      empty.className = "queue-empty";
      empty.textContent = "Nothing queued.";
      this.refs.queue.append(empty);
      return;
    }
    queue.forEach((message, index) => {
      const item = document.createElement("li");
      item.className = "queue-item";
      item.dataset.status = dispatchStatus(message);
      item.dataset.kind = message.kind || "text";
      const order = document.createElement("span");
      order.className = "queue-order";
      order.textContent = String(index + 1).padStart(2, "0");
      const copy = document.createElement("div");
      const status = document.createElement("small");
      status.textContent = message.kind === "manual_queue"
        ? "Manual - review before sending"
        : dispatchStatus(message) === "steering"
          ? "Steer after current action"
          : "Automatic queue";
      const content = document.createElement("p");
      content.textContent = messageContent(message);
      copy.append(status, content);
      const actions = document.createElement("div");
      actions.className = "queue-actions";
      const availableActions = message.kind === "manual_queue"
        ? [["promote", "Move to composer"], ["steer", "Steer now"], ["edit", "Edit"], ["delete", "Delete"]]
        : [["steer", "Steer now"], ["edit", "Edit"], ["delete", "Delete"]];
      for (const [action, label] of availableActions) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.queueAction = action;
        button.dataset.queueMessageId = message.id;
        button.textContent = label;
        actions.append(button);
      }
      item.append(order, copy, actions);
      this.refs.queue.append(item);
    });
  }

  async stopThread() {
    if (!chatState.currentThreadId) return;
    if (this.refs.stop) this.refs.stop.disabled = true;
    try {
      try {
        await this.api.post(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/stop`, {}, { scope: "thread-stop" });
      } catch (error) {
        if (![404, 405].includes(error.status) || !chatState.currentRunId) throw error;
        await this.api.post(
          `/api/runs/${encodeURIComponent(chatState.currentRunId)}/cancel`,
          { reason_code: "user_requested" },
          { scope: "thread-run-cancel-fallback" },
        );
      }
      chatState.running = false;
      this.network("Agent stopped", "warning");
      this.renderAll();
    } catch (error) {
      this.network(`Stop failed: ${error.message}`, "error");
    } finally {
      if (this.refs.stop) this.refs.stop.disabled = false;
    }
  }

  abortStream() {
    chatState.streamAbort?.abort();
    chatState.streamAbort = null;
    if (chatState.streamRetry) clearTimeout(chatState.streamRetry);
    chatState.streamRetry = null;
    setText(this.refs.detailStream, "Disconnected");
  }

  restoreEventCache(threadId) {
    try {
      const cached = JSON.parse(sessionStorage.getItem(`taroai.threadEvents.${threadId}`) || "[]");
      return Array.isArray(cached) ? cached : [];
    } catch {
      return [];
    }
  }

  persistEventCache() {
    if (!chatState.currentThreadId) return;
    try {
      sessionStorage.setItem(
        `taroai.threadEvents.${chatState.currentThreadId}`,
        JSON.stringify(chatState.events.slice(-240)),
      );
    } catch {
      // The server stream remains authoritative if the browser storage quota is full.
    }
  }

  startEventStream() {
    if (!chatState.currentThreadId || chatState.streamAbort) return;
    const controller = new AbortController();
    chatState.streamAbort = controller;
    const threadId = chatState.currentThreadId;
    this.api
      .streamThreadEvents(threadId, {
        afterSequence: chatState.lastThreadSequence,
        signal: controller.signal,
        onStatus: (status) => {
          if (threadId !== chatState.currentThreadId) return;
          setText(this.refs.detailStream, status === "connected" ? "Live" : "Reconnecting");
          if (status === "connected") {
            chatState.reconnectAttempt = 0;
            this.network(chatState.running ? "Live · agent is working" : "Live", chatState.running ? "active" : "success");
          }
        },
        onEvent: (frame) => this.applyStreamEvent(frame),
      })
      .catch((error) => {
        if (controller.signal.aborted || threadId !== chatState.currentThreadId) return;
        this.network(`Connection interrupted · retrying`, "warning");
        this.renderReconnectCard(error.message);
      })
      .finally(() => {
        if (chatState.streamAbort === controller) chatState.streamAbort = null;
        if (!controller.signal.aborted && threadId === chatState.currentThreadId) {
          chatState.reconnectAttempt += 1;
          const delay = Math.min(30000, 1000 * 2 ** Math.min(chatState.reconnectAttempt, 5));
          chatState.streamRetry = window.setTimeout(() => {
            chatState.streamRetry = null;
            this.startEventStream();
          }, delay);
        }
      });
  }

  applyStreamEvent(frame) {
    const payload = frame.data || {};
    const event = payload.event || payload;
    chatState.currentRunId = event.run_id || eventPayload(event).run_id || chatState.currentRunId;
    const sequence = Number(frame.id || eventSequence(event));
    if (sequence && sequence <= chatState.lastThreadSequence) return;
    if (sequence) {
      chatState.lastThreadSequence = sequence;
      localStorage.setItem(`taroai.threadSequence.${chatState.currentThreadId}`, String(sequence));
    }
    if (frame.event === "heartbeat" || eventType(event) === "heartbeat") {
      this.network(chatState.running ? "Live · agent is working" : "Live", chatState.running ? "active" : "success");
      return;
    }
    const type = eventType(event);
    const payloadDetail = eventPayload(event);
    if (type.includes("assistant.delta") || type.includes("text.delta") || type.includes("message.delta")) {
      const detail = payloadDetail;
      const messageId = detail.message_id || event.message_id || `stream:${chatState.currentRunId || chatState.currentThreadId}`;
      const delta = detail.delta || detail.text || detail.content || "";
      const existing = chatState.messages.find((item) => item.id === messageId);
      if (existing) existing.content = `${existing.content || ""}${delta}`;
      else chatState.messages.push({ id: messageId, role: "assistant", content: delta, status: "streaming", created_at: new Date().toISOString() });
    }
    if (type === "assistant.message.completed") {
      const finalId = payloadDetail.message_id || `assistant:${chatState.currentRunId || Date.now()}`;
      const streamId = `stream:${chatState.currentRunId || chatState.currentThreadId}`;
      const streamed = chatState.messages.find((item) => item.id === streamId);
      const completed = chatState.messages.find((item) => item.id === finalId);
      const finalMessage = {
        ...(streamed || completed || {}),
        id: finalId,
        role: "assistant",
        content: payloadDetail.content || streamed?.content || completed?.content || "",
        dispatch_status: "completed",
        delivery_status: "delivered",
        created_at: streamed?.created_at || completed?.created_at || event.created_at || new Date().toISOString(),
      };
      chatState.messages = [...chatState.messages.filter((item) => ![streamId, finalId].includes(item.id)), finalMessage];
    }
    const streamedMessage = event.message || eventPayload(event).message || eventPayload(event).chat_message;
    if (type.includes("message") && streamedMessage && typeof streamedMessage === "object") {
      const message = streamedMessage;
      chatState.messages = [...chatState.messages.filter((item) => item.id !== message.id), message];
      const status = dispatchStatus(message);
      if (["queued", "steering", "ready"].includes(status)) {
        chatState.queue = [...chatState.queue.filter((item) => item.id !== message.id), message];
      } else {
        chatState.queue = chatState.queue.filter((item) => item.id !== message.id);
      }
    }
    if (type.includes("artifact")) this.captureArtifactFromEvent(event);
    if (!chatState.events.some((item) => eventSequence(item) && eventSequence(item) === sequence)) {
      chatState.events.push({ ...event, thread_sequence: sequence || eventSequence(event) });
      this.persistEventCache();
    }
    if (type.includes("run.started") || type.includes("cycle.started") || type.includes("action.requested")) chatState.running = true;
    if (type === "run.status_changed") {
      const status = String(payloadDetail.status || "").toLowerCase();
      chatState.running = ACTIVE_RUN_STATES.has(status);
      if (["succeeded", "failed", "cancelled", "timed_out"].includes(status)) {
        this.network(
          status === "succeeded" ? "Agent finished" : `Run ${status.replaceAll("_", " ")}`,
          status === "succeeded" ? "success" : status === "cancelled" ? "warning" : "error",
        );
      }
    }
    if (type === "agent.loop.completed") {
      chatState.running = false;
      const outcome = String(payloadDetail.outcome || "complete").toLowerCase();
      this.network(outcome === "complete" ? "Agent finished" : `Agent finished · ${outcome}`, outcome === "complete" ? "success" : "warning");
      if (chatState.currentThreadId) {
        this.loadSuggestions();
        this.api
          .get(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages`)
          .then((messages) => {
            chatState.messages = arrayFrom(messages, "messages", "chat_messages");
            chatState.queue = chatState.messages.filter((message) => ["queued", "steering", "ready"].includes(dispatchStatus(message)));
            this.renderAll();
            this.maybePromoteManualMessage();
          })
          .catch(() => {});
      }
    }
    if (TERMINAL_EVENT_WORDS.some((word) => type.includes(word)) && (type.includes("run") || type.includes("loop"))) {
      chatState.running = false;
      this.network(type.includes("fail") ? "Agent finished with issues" : "Agent finished", type.includes("fail") ? "error" : "success");
    }
    this.renderAll();
  }

  captureArtifactFromEvent(event) {
    const payload = eventPayload(event);
    const artifact = payload.artifact || payload;
    const id = artifact.id || artifact.artifact_id || artifact.storage_object_id;
    if (!id) return;
    chatState.artifacts = [{ ...artifact, id }, ...chatState.artifacts.filter((item) => item.id !== id)];
  }

  eventCardKind(type) {
    if (type.includes("approval")) return "approval";
    if (type.includes("verification") || type.includes("verifier") || type.includes("verified")) return "verifier";
    if (type.includes("replan")) return "replan";
    if (type.includes("repair")) return "repair";
    if (type.includes("observation") || type.includes("result")) return "observation";
    if (type.includes("skill")) return "skill";
    if (type.includes("tool") || type.includes("action")) return "tool";
    if (type.includes("decision") || type.includes("plan")) return "decision";
    if (type.includes("cycle") || type.includes("thinking") || type.includes("model")) return "thinking";
    if (type.includes("fail") || type.includes("error") || type.includes("lost")) return "failure";
    if (type.includes("run") || type.includes("loop")) return "thinking";
    return null;
  }

  eventCardTitle(kind, type, payload) {
    const explicit = payload.title || payload.label || payload.name || payload.skill_name || payload.tool_name;
    if (explicit) return explicit;
    return {
      thinking: type.includes("completed") ? "Thinking complete" : "Thinking",
      decision: "Decision",
      skill: "Skill",
      tool: "Tool",
      observation: "Observation",
      repair: "Repairing",
      replan: "Replanning",
      verifier: "Verifier",
      approval: "Approval required",
      failure: "Execution issue",
    }[kind];
  }

  eventCardSummary(kind, payload, type) {
    return (
      payload.summary ||
      payload.message ||
      payload.reason ||
      payload.description ||
      payload.observation ||
      payload.output_summary ||
      payload.status ||
      (type.includes("completed") ? "Completed" : kind === "thinking" ? "Working through the next step" : "Execution evidence")
    );
  }

  renderExecutionCard(event) {
    const type = eventType(event);
    const kind = this.eventCardKind(type);
    if (!kind) return null;
    const payload = eventPayload(event);
    const details = document.createElement("details");
    details.className = `agent-trace-card trace-${kind}`;
    details.open = ["approval", "failure"].includes(kind);
    const summary = document.createElement("summary");
    const marker = document.createElement("span");
    marker.className = "trace-marker";
    marker.textContent = { thinking: "·", decision: "→", skill: "S", tool: "T", observation: "↳", repair: "↻", replan: "⌁", verifier: "✓", approval: "!", failure: "×" }[kind];
    const copy = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = this.eventCardTitle(kind, type, payload);
    const small = document.createElement("small");
    small.textContent = text(this.eventCardSummary(kind, payload, type));
    copy.append(strong, small);
    const status = document.createElement("span");
    status.className = "trace-status";
    status.textContent = type.includes("completed") || type.includes("succeeded") ? "Done" : payload.status || (chatState.running ? "Live" : "Recorded");
    summary.append(marker, copy, status);
    const body = document.createElement("div");
    body.className = "trace-detail";
    const redacted = { ...payload };
    for (const key of ["chain_of_thought", "reasoning", "hidden_reasoning", "secret", "token"]) delete redacted[key];
    const pre = document.createElement("pre");
    pre.textContent = text(redacted, "No additional detail");
    body.append(pre);
    if (kind === "approval" && payload.approval_id) {
      const actions = document.createElement("div");
      actions.className = "trace-actions";
      for (const decision of ["approve", "reject"]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = decision === "approve" ? "Approve" : "Reject";
        button.addEventListener("click", (clickEvent) => {
          clickEvent.preventDefault();
          this.resolveApproval(payload.approval_id, decision);
        });
        actions.append(button);
      }
      body.append(actions);
    }
    if (kind === "failure" && (payload.connector_id || payload.connection_id)) {
      const reconnect = document.createElement("button");
      reconnect.type = "button";
      reconnect.className = "trace-reconnect";
      reconnect.textContent = "Reconnect and resume";
      reconnect.addEventListener("click", (clickEvent) => {
        clickEvent.preventDefault();
        this.reconnectConnector(payload.connector_id || payload.connection_id, payload.action_id);
      });
      body.append(reconnect);
    }
    details.append(summary, body);
    return details;
  }

  async resolveApproval(approvalId, decision) {
    try {
      await this.api.post(
        `/api/runs/${encodeURIComponent(chatState.currentRunId)}/approvals/${decision}`,
        { approval_id: approvalId },
        { scope: `approval-${decision}` },
      );
      this.network(decision === "approve" ? "Approved" : "Rejected", decision === "approve" ? "success" : "warning");
    } catch (error) {
      this.network(`Approval failed: ${error.message}`, "error");
    }
  }

  renderMessage(message) {
    const article = document.createElement("article");
    article.className = `message ${isAssistant(message) ? "message-agent" : "message-user"}`;
    article.dataset.messageId = message.id || "";
    const content = document.createElement("p");
    content.textContent = messageContent(message);
    article.append(content);
    const refs = arrayFrom(message.resource_refs || [], "items");
    const attachments = arrayFrom(message.attachments || [], "items");
    if (refs.length || attachments.length) {
      const evidence = document.createElement("div");
      evidence.className = "message-evidence-chips";
      refs.forEach((item) => {
        const chip = document.createElement("span");
        chip.textContent = `@${item.name || item.id}`;
        evidence.append(chip);
      });
      attachments.forEach((item) => {
        const chip = document.createElement("span");
        chip.textContent = `□ ${item.filename || item.name || item.id || item}`;
        evidence.append(chip);
      });
      article.append(evidence);
    }
    const meta = document.createElement("footer");
    meta.className = "message-meta";
    const time = document.createElement("time");
    time.textContent = safeTime(message.created_at);
    meta.append(time);
    const statusValue = dispatchStatus(message);
    if (!["completed", "sent", "succeeded"].includes(statusValue)) {
      const status = document.createElement("span");
      status.className = `message-dispatch status-${statusValue}`;
      status.textContent = statusValue === "steering" ? "Steering" : statusValue;
      meta.append(status);
    }
    if (isAssistant(message)) {
      for (const [action, label] of [["copy", "Copy"], ["summarize", "Summarize"], ["speak", "Read aloud"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset[action === "copy" ? "messageCopy" : action === "summarize" ? "messageSummarize" : "messageSpeak"] = message.id;
        button.textContent = label;
        meta.append(button);
      }
    }
    if (statusValue === "failed") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.dataset.messageRetry = message.id;
      retry.textContent = "Retry";
      meta.append(retry);
      if (message.error) meta.title = message.error;
    }
    article.append(meta);
    return article;
  }

  renderConversation() {
    if (!this.refs.conversation) return;
    this.refs.conversation.replaceChildren();
    const hasContent = chatState.messages.length || chatState.events.length;
    this.refs.shell.dataset.chatState = hasContent || chatState.currentThreadId ? "thread" : "empty";
    if (this.refs.emptyState) this.refs.emptyState.hidden = Boolean(hasContent || chatState.currentThreadId);
    if (!hasContent) {
      const intro = document.createElement("article");
      intro.className = "message message-agent chat-intro-message";
      const title = document.createElement("strong");
      title.textContent = chatState.loading ? "Opening your thread…" : "Tell me what you want to accomplish.";
      const body = document.createElement("p");
      body.textContent = chatState.loading
        ? "Restoring messages, queue, and execution evidence."
        : "I’ll keep the plan, tools, verification, and artifacts together in this thread.";
      intro.append(title, body);
      this.refs.conversation.append(intro);
      this.renderSuggestions([
        "Review a document and produce an evidence-backed brief",
        "Research a decision and compare the best options",
        "Build a reusable workflow from this task",
      ]);
      return;
    }

    const timeline = [
      ...chatState.messages.map((item, index) => ({ kind: "message", item, time: item.created_at || item.updated_at, order: index * 10 })),
      ...chatState.events.map((item, index) => ({ kind: "event", item, time: item.created_at || eventPayload(item).created_at, order: eventSequence(item) || 100000 + index })),
    ].sort((left, right) => {
      const leftTime = left.time ? new Date(left.time).valueOf() : Number.NaN;
      const rightTime = right.time ? new Date(right.time).valueOf() : Number.NaN;
      if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) return leftTime - rightTime;
      return left.order - right.order;
    });
    let traceGroup = null;
    for (const entry of timeline) {
      if (entry.kind === "message") {
        traceGroup = null;
        this.refs.conversation.append(this.renderMessage(entry.item));
        continue;
      }
      const card = this.renderExecutionCard(entry.item);
      if (!card) continue;
      if (!traceGroup) {
        traceGroup = document.createElement("section");
        traceGroup.className = "agent-trace-stack";
        this.refs.conversation.append(traceGroup);
      }
      traceGroup.append(card);
    }
    if (!chatState.running && chatState.messages.length) {
      this.renderSuggestions(chatState.suggestions.length ? chatState.suggestions : ["Summarize the result", "Turn this into an agent", "What should I do next?"]);
    }
    requestAnimationFrame(() => {
      if (this.refs.chatScroll) this.refs.chatScroll.scrollTop = this.refs.chatScroll.scrollHeight;
    });
  }

  renderSuggestions(suggestions) {
    const row = document.createElement("div");
    row.className = "quick-row thread-suggestions";
    row.setAttribute("aria-label", "Suggested follow-ups");
    for (const suggestion of suggestions) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.suggestion = suggestion;
      button.textContent = suggestion;
      row.append(button);
    }
    this.refs.conversation.append(row);
  }

  renderInlineNotice(title, detail, kind = "notice") {
    if (!this.refs.conversation) return;
    const card = document.createElement("article");
    card.className = `inline-system-card inline-system-${kind}`;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const body = document.createElement("p");
    body.textContent = detail;
    card.append(strong, body);
    this.refs.conversation.append(card);
  }

  renderReconnectCard(detail) {
    const existing = query("[data-reconnect-card]", this.refs.conversation);
    if (existing) {
      setText(query("p", existing), detail);
      return;
    }
    const card = document.createElement("article");
    card.className = "inline-system-card reconnect-card";
    card.dataset.reconnectCard = "true";
    const pulse = document.createElement("span");
    pulse.className = "reconnect-pulse";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = "Reconnecting to this thread";
    const body = document.createElement("p");
    body.textContent = detail;
    copy.append(title, body);
    card.append(pulse, copy);
    this.refs.conversation?.append(card);
  }

  renderArtifacts() {
    if (!this.refs.artifactList) return;
    this.refs.artifactList.replaceChildren();
    if (this.refs.artifactEmpty) this.refs.artifactEmpty.hidden = Boolean(chatState.artifacts.length);
    for (const artifact of chatState.artifacts) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "thread-artifact-item";
      button.dataset.threadArtifact = artifact.id || artifact.artifact_id;
      const icon = document.createElement("span");
      icon.textContent = artifact.kind === "dashboard" ? "▦" : "□";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = artifact.name || artifact.title || artifact.filename || "Artifact";
      const meta = document.createElement("small");
      meta.textContent = artifact.media_type || artifact.kind || artifact.status || "Output";
      copy.append(title, meta);
      button.append(icon, copy);
      this.refs.artifactList.append(button);
    }
  }

  async openArtifact(artifactId) {
    let artifact = chatState.artifacts.find((item) => (item.id || item.artifact_id) === artifactId);
    if (!artifact) return;
    this.openSidecar("artifacts");
    if (!artifact.content && !artifact.text && artifactId) {
      try {
        const loaded = await this.api.get(`/api/artifacts/${encodeURIComponent(artifactId)}`);
        artifact = { ...artifact, ...(loaded.artifact || loaded) };
        chatState.artifacts = chatState.artifacts.map((item) => ((item.id || item.artifact_id) === artifactId ? artifact : item));
      } catch (error) {
        artifact = { ...artifact, content: `Preview unavailable\n\n${error.message}` };
      }
    }
    chatState.activeArtifact = artifact;
    if (window.taroaiArtifacts?.open) {
      await window.taroaiArtifacts.open(artifact);
      return;
    }
    if (this.refs.artifactStage) this.refs.artifactStage.hidden = false;
    setText(this.refs.artifactStageTitle, artifact.name || artifact.title || artifact.filename || "Artifact");
    setText(this.refs.artifactStageContent, artifact.content || artifact.text || artifact.markdown || text(artifact.data || artifact));
  }

  async copyArtifact() {
    const content = this.refs.artifactStageContent?.textContent || "";
    if (!content) return;
    await navigator.clipboard?.writeText(content);
    this.network("Artifact copied", "success");
  }

  downloadArtifact() {
    const artifact = chatState.activeArtifact;
    if (!artifact) return;
    if (artifact.download_url || artifact.url) {
      window.open(artifact.download_url || artifact.url, "_blank", "noopener");
      return;
    }
    const blob = new Blob([this.refs.artifactStageContent?.textContent || ""], { type: artifact.media_type || "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = escapeFilename(artifact.filename || artifact.name || "artifact.txt");
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  openSidecar(view = "artifacts") {
    chatState.activeSidecar = view;
    this.refs.sidecar?.classList.remove("is-operations-open");
    this.refs.sidecar?.classList.add("is-artifact-open", "is-chat-sidecar-open");
    queryAll("[data-sidecar-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sidecarTab === view);
    });
    queryAll("[data-sidecar-view]").forEach((section) => {
      const active = section.dataset.sidecarView === view;
      section.hidden = !active;
      section.classList.toggle("is-active", active);
    });
    setText(this.refs.sidecarTitle, { artifacts: "Artifacts", queue: "Queue", details: "Thread details" }[view] || "Thread");
    query("[data-open-queue]")?.setAttribute("aria-expanded", String(view === "queue"));
  }

  renderDetails() {
    setText(this.refs.detailId, chatState.currentThreadId || "Not started");
    setText(this.refs.detailRun, chatState.currentRunId || (chatState.running ? "Starting" : "Idle"));
    this.renderModelButton();
    setText(this.refs.threadPresence, chatState.running ? "Working" : chatState.currentThreadId ? "Saved" : "Ready");
    this.refs.threadPresence?.classList.toggle("running", chatState.running);
    if (this.refs.shareButton) this.refs.shareButton.disabled = !chatState.currentThreadId;
    this.refs.createAgentButtons.forEach((button) => {
      button.disabled = !chatState.currentThreadId || chatState.running;
      button.title = button.disabled ? "Finish a thread before creating an agent" : "Create a reusable agent from this thread";
    });
  }

  renderAll() {
    this.renderThreads();
    this.renderConversation();
    this.renderQueue();
    this.renderArtifacts();
    this.renderResourceChips();
    this.renderUploads();
    this.renderDetails();
    this.syncComposer();
  }

  async shareThread() {
    if (!chatState.currentThreadId) return;
    try {
      const share = await this.api.post(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/share`, {}, { scope: "thread-share" });
      chatState.share = share;
      this.openShareDialog(share);
    } catch (error) {
      this.network(`Could not share: ${error.message}`, "error");
    }
  }

  async loadSuggestions() {
    if (!chatState.currentThreadId) return;
    try {
      const payload = await this.api.get(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/suggestions`);
      chatState.suggestions = arrayFrom(payload, "suggestions", "items").map((item) => typeof item === "string" ? item : item.label || item.prompt).filter(Boolean);
      this.renderConversation();
    } catch {
      chatState.suggestions = [];
    }
  }

  async reconnectConnector(connectionId, actionId = null) {
    try {
      const result = await this.api.post(`/api/connectors/${encodeURIComponent(connectionId)}/reconnect`, { thread_id: chatState.currentThreadId, run_id: chatState.currentRunId, action_id: actionId }, { scope: "connector-reconnect" });
      if (!result.authorization_url) throw new Error("Authorization URL was not returned");
      const popup = window.open(result.authorization_url, "connector-reconnect", "width=620,height=760");
      if (!popup) throw new Error("Allow popups to reconnect this connector");
      this.network("Complete authorization in the popup; this action will resume automatically", "loading");
    } catch (error) { this.network(`Reconnect failed: ${error.message}`, "error"); }
  }

  openShareDialog(share) {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog share-dialog";
    const shareUrl = share.url || share.share_url || `${location.origin}${location.pathname}#shared/${share.token || share.id}`;
    dialog.innerHTML = `
      <form method="dialog" class="chat-dialog-card">
        <header><div><small>Read-only link</small><h2>Share this thread</h2></div><button value="close" aria-label="Close">×</button></header>
        <p>Anyone with this link can view the published conversation and artifacts. Private Operations data stays hidden.</p>
        <div class="share-link-row"><input value="${escapeHtml(shareUrl)}" readonly /><button type="button" data-share-copy>Copy link</button></div>
        <footer><button type="button" class="danger-text" data-share-revoke>Revoke link</button><button value="close">Done</button></footer>
      </form>`;
    document.body.append(dialog);
    query("[data-share-copy]", dialog).addEventListener("click", async () => {
      await navigator.clipboard?.writeText(shareUrl);
      setText(query("[data-share-copy]", dialog), "Copied");
    });
    query("[data-share-revoke]", dialog).addEventListener("click", async () => {
      await this.api.delete(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/share`, { scope: "share-revoke" });
      chatState.share = null;
      dialog.close();
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  openCreateAgentDialog() {
    if (!chatState.currentThreadId) {
      this.network("Start a thread before creating an agent", "warning");
      return;
    }
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-draft-dialog";
    const suggestedName = chatState.thread?.title && chatState.thread.title !== "New thread" ? chatState.thread.title : "Reusable agent";
    dialog.innerHTML = `
      <form class="chat-dialog-card" data-agent-draft-form>
        <header><div><small>From successful thread</small><h2>Create an agent</h2></div><button type="button" data-dialog-close aria-label="Close">×</button></header>
        <p>Review what should become reusable. The draft keeps this thread's model, skills, references, and output contract.</p>
        <label><span>Name</span><input name="name" value="${escapeHtml(suggestedName)}" required /></label>
        <label><span>Description</span><textarea name="description" rows="2" placeholder="What this agent reliably accomplishes"></textarea></label>
        <label><span>Instructions</span><textarea name="instructions" rows="5" placeholder="The repeatable approach, constraints, and verification expectations"></textarea></label>
        <label><span>Output format</span><input name="output_format" placeholder="Report, spreadsheet, artifact set…" /></label>
        <div class="agent-draft-bindings"><span>${chatState.resourceRefs.length || arrayFrom(chatState.thread?.resource_refs || []).length} references</span><span>${chatState.artifacts.length} artifacts</span><span>${chatState.selectedModel?.display_name || "Default model"}</span></div>
        <footer><button type="button" data-dialog-close>Cancel</button><button type="submit" class="primary">Create draft</button></footer>
      </form>`;
    document.body.append(dialog);
    queryAll("[data-dialog-close]", dialog).forEach((button) => button.addEventListener("click", () => dialog.close()));
    query("[data-agent-draft-form]", dialog).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const submit = query("[type='submit']", dialog);
      submit.disabled = true;
      submit.textContent = "Creating…";
      try {
        const draft = await this.api.post(
          `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/agent-drafts`,
          {
            name: form.get("name"),
            description: form.get("description"),
            instructions: form.get("instructions"),
            output_format: form.get("output_format"),
          },
          { scope: "agent-draft" },
        );
        dialog.close();
        this.renderInlineNotice("Agent draft created", `${draft.name || form.get("name")} is ready for review in Agents.`, "success");
        this.network("Agent draft created", "success");
      } catch (error) {
        submit.disabled = false;
        submit.textContent = "Create draft";
        this.network(`Could not create agent: ${error.message}`, "error");
      }
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  startVoiceInput(control) {
    if (window.taroaiSpeech?.toggleRecording) return window.taroaiSpeech.toggleRecording(control);
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      this.network("Voice input is not supported by this browser", "warning");
      return;
    }
    const recognition = new Recognition();
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.onstart = () => {
      control.classList.add("is-recording");
      this.network("Listening… click the microphone to finish", "active");
    };
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results).map((result) => result[0].transcript).join("");
      this.refs.input.value = `${this.refs.input.value}${this.refs.input.value ? " " : ""}${transcript}`;
      this.syncComposer();
    };
    recognition.onerror = (event) => this.network(`Voice input stopped: ${event.error}`, "warning");
    recognition.onend = () => {
      control.classList.remove("is-recording");
      this.network("Transcript ready to edit", "success");
      this.refs.input.focus();
    };
    recognition.start();
  }

  async copyMessage(messageId) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    await navigator.clipboard?.writeText(messageContent(message));
    this.network("Message copied", "success");
  }

  retryMessage(messageId) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    chatState.messages = chatState.messages.filter((item) => item.id !== messageId);
    this.sendThreadMessage(messageContent(message), chatState.running ? "queue" : "auto");
  }

  speakMessage(messageId, control) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    if (window.taroaiSpeech?.toggleReadAloud) return window.taroaiSpeech.toggleReadAloud(message, control);
    if (!window.speechSynthesis) return;
    if (speechSynthesis.speaking) {
      speechSynthesis.cancel();
      control.textContent = "Read aloud";
      return;
    }
    const utterance = new SpeechSynthesisUtterance(messageContent(message));
    utterance.onend = () => { control.textContent = "Read aloud"; };
    control.textContent = "Stop audio";
    speechSynthesis.speak(utterance);
  }

  summarizeMessage(messageId) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    if (window.taroaiSpeech?.summarizeMessage) return window.taroaiSpeech.summarizeMessage(message);
  }

  applySuggestion(suggestion) {
    if (suggestion === "Turn this into an agent") return this.openCreateAgentDialog();
    this.refs.input.value = suggestion;
    this.syncComposer();
    this.refs.input.focus();
  }
}

let singleton = null;

export function createChatController() {
  if (singleton) return singleton;
  singleton = new ChatController();
  singleton.init();
  window.taroaiChat = singleton;
  return singleton;
}

export async function sendThreadMessage(content, deliveryMode = "auto", resourceRefs = [], attachments = []) {
  if (!singleton) createChatController();
  chatState.resourceRefs = resourceRefs;
  chatState.uploads = attachments.map((attachment) => ({ ...attachment, id: attachment.id || attachment, status: "Ready", progress: 1 }));
  return singleton.sendThreadMessage(content, deliveryMode);
}

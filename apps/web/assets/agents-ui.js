import { chatApi } from "./chat-api.js";
import { chatState } from "./chat-controller.js";

function list(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return Array.isArray(value?.items) ? value.items : [];
}

function schemaFields(schema = {}) {
  return Object.entries(schema.properties || {}).map(([name, definition]) => ({
    name,
    type: definition.type || "string",
    title: definition.title || name.replaceAll("_", " "),
    description: definition.description || "",
    required: (schema.required || []).includes(name),
    options: definition.enum || null,
    defaultValue: definition.default ?? "",
  }));
}

export class AgentsUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.agents = [];
    this.selected = null;
    this.detail = null;
    this.sessions = [];
  }

  init() {
    window.addEventListener("hashchange", () => this.route());
    this.root?.addEventListener("click", (event) => this.click(event));
    this.root?.addEventListener("submit", (event) => this.submit(event));
    this.root?.addEventListener("input", (event) => {
      if (event.target.matches("[data-agent-search]")) this.renderCards(event.target.value);
    });
    this.route();
  }

  route() {
    const active = window.location.hash.replace(/^#/, "").split("/")[0] === "agents";
    if (!active) {
      if (this.root?.dataset.owner === "agents") {
        this.root.hidden = true;
        this.root.replaceChildren();
        delete this.root.dataset.owner;
        document.querySelector("[data-app='taroai-workspace']")?.removeAttribute("data-rich-route");
      }
      return;
    }
    this.root.dataset.owner = "agents";
    this.root.hidden = false;
    document.querySelector("[data-app='taroai-workspace']")?.setAttribute("data-rich-route", "agents");
    this.renderShell();
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page agents-page">
        <header class="capability-page-header"><div><p>Reusable work</p><h1>Agents</h1><span>Versioned applications built from successful execution patterns.</span></div><div class="capability-header-actions"><button type="button" data-agent-import>Import</button><button class="primary" type="button" data-agent-create>Create agent</button></div></header>
        <div class="capability-toolbar"><label><span>⌕</span><input data-agent-search type="search" placeholder="Search agents" /></label><div class="agent-view-meta" data-agent-count>0 agents</div><button data-agents-refresh>Refresh</button></div>
        <div class="agent-product-layout"><section class="agent-library" data-agent-library><div class="route-loading">Loading agents…</div></section><aside class="agent-inspector" data-agent-inspector><div class="route-empty"><span>A</span><strong>Select an agent</strong><p>Review inputs, pinned context, versions, and recent sessions.</p></div></aside></div>
        <div class="route-toast" data-agent-toast hidden></div>
      </section>`;
  }

  async load() {
    try {
      const payload = await this.api.get(`/api/agents?workspace_id=${encodeURIComponent(this.api.settings().workspaceId)}`);
      this.agents = list(payload, "agents", "definitions");
      this.selected = this.agents.find((agent) => (agent.id || agent.agent_id) === (this.selected?.id || this.selected?.agent_id)) || this.agents[0] || null;
      this.renderCards();
      if (this.selected) await this.select(this.selected.id || this.selected.agent_id);
      else this.renderInspector();
    } catch (error) {
      this.agents = [];
      this.renderCards();
      this.toast(error.message, "error");
    }
  }

  renderCards(searchValue = "") {
    const library = this.root.querySelector("[data-agent-library]");
    if (!library) return;
    const search = searchValue.trim().toLowerCase();
    const agents = this.agents.filter((agent) => !search || `${agent.name || ""} ${agent.description || ""}`.toLowerCase().includes(search));
    this.root.querySelector("[data-agent-count]").textContent = `${agents.length} agent${agents.length === 1 ? "" : "s"}`;
    library.replaceChildren();
    if (!agents.length) {
      library.innerHTML = `<div class="route-empty"><span>A</span><strong>No agents yet</strong><p>Create one from a successful Chat or start a new draft here.</p><button data-agent-create>Create agent</button></div>`;
      return;
    }
    for (const agent of agents) {
      const id = agent.id || agent.agent_id;
      const card = document.createElement("article");
      card.className = "real-agent-card";
      card.classList.toggle("is-active", id === (this.selected?.id || this.selected?.agent_id));
      card.innerHTML = `<button class="agent-card-main" data-agent-id="${id}"><span class="agent-monogram">${(agent.name || "A").slice(0, 1).toUpperCase()}</span><span><small>${agent.status || "Published"} · v${agent.version || agent.latest_version || "1"}</small><strong></strong><p></p></span></button><footer><span>${list(agent.skills, "items").length} skills</span><span>${agent.run_count || 0} runs</span><button data-agent-run-card="${id}">Run</button></footer>`;
      card.querySelector("strong").textContent = agent.name || "Untitled agent";
      card.querySelector("p").textContent = agent.description || "Reusable agent";
      library.append(card);
    }
  }

  async select(id) {
    this.selected = this.agents.find((agent) => (agent.id || agent.agent_id) === id) || this.selected;
    this.renderCards(this.root.querySelector("[data-agent-search]")?.value || "");
    this.renderInspector(true);
    try {
      const [detail, sessions] = await Promise.allSettled([
        this.api.get(`/api/agents/${encodeURIComponent(id)}`),
        this.api.get(`/api/agents/${encodeURIComponent(id)}/sessions`),
      ]);
      this.detail = detail.status === "fulfilled" ? detail.value.agent || detail.value : this.selected;
      this.sessions = sessions.status === "fulfilled" ? list(sessions.value, "sessions", "runs") : [];
    } catch {
      this.detail = this.selected;
      this.sessions = [];
    }
    this.renderInspector();
  }

  renderInspector(loading = false) {
    const root = this.root.querySelector("[data-agent-inspector]");
    if (!root) return;
    if (!this.selected) {
      root.innerHTML = `<div class="route-empty"><span>A</span><strong>Select an agent</strong><p>Review its contract, pinned resources, and history.</p></div>`;
      return;
    }
    const agent = this.detail || this.selected;
    const schema = agent.input_schema || agent.input_contract || { type: "object", properties: { request: { type: "string", title: "Request" } }, required: ["request"] };
    const fields = schemaFields(schema);
    const versions = list(agent.versions, "items").length ? list(agent.versions, "items") : [{ version: agent.version || agent.latest_version || "1", status: agent.status || "published", created_at: agent.updated_at }];
    root.innerHTML = `
      <header class="agent-inspector-header"><div><span class="agent-monogram large">${(agent.name || "A").slice(0, 1).toUpperCase()}</span><div><small>${agent.status || "Published"}</small><h2></h2><p></p></div></div><button data-agent-edit>Edit draft</button></header>
      <nav class="agent-inspector-tabs"><button class="is-active" data-agent-tab="run">Run</button><button data-agent-tab="configuration">Configuration</button><button data-agent-tab="versions">Versions</button><button data-agent-tab="sessions">Sessions</button></nav>
      <section data-agent-panel="run"><form class="agent-run-form" data-agent-run-form>${fields.map((field) => this.fieldMarkup(field)).join("")}<button class="primary" type="submit" ${loading ? "disabled" : ""}>Run agent</button></form></section>
      <section data-agent-panel="configuration" hidden><div class="agent-config-block"><small>Instructions</small><p></p></div><div class="agent-binding-grid"><div><small>Pinned skills</small><strong>${list(agent.skills, "items").length || list(agent.skill_bindings, "items").length}</strong></div><div><small>Reference files</small><strong>${list(agent.files, "items").length || list(agent.reference_files, "items").length}</strong></div><div><small>Runtime</small><strong>${agent.runtime_profile?.name || agent.runtime || "Workspace default"}</strong></div><div><small>Output</small><strong>${agent.output_format || agent.output_contract?.type || "Structured result"}</strong></div></div></section>
      <section data-agent-panel="versions" hidden><ol class="agent-version-list">${versions.map((version) => `<li><span><strong>v${version.version || version.number}</strong><small>${version.status || "published"} · ${version.created_at ? new Date(version.created_at).toLocaleDateString() : "current"}</small></span><button data-agent-restore="${version.version || version.number}">Restore</button></li>`).join("")}</ol></section>
      <section data-agent-panel="sessions" hidden><ol class="agent-session-list">${this.sessions.length ? this.sessions.map((session) => `<li><span><strong>${session.title || session.input_summary || "Agent session"}</strong><small>${session.status || "completed"} · ${session.created_at ? new Date(session.created_at).toLocaleString() : ""}</small></span><button data-agent-session="${session.thread_id || session.id}">Open</button></li>`).join("") : "<li class='route-note'>No sessions yet.</li>"}</ol></section>`;
    root.querySelector("h2").textContent = agent.name || "Untitled agent";
    root.querySelector(".agent-inspector-header p").textContent = agent.description || "Reusable agent";
    root.querySelector(".agent-config-block p").textContent = agent.instructions || "No additional instructions were published.";
  }

  fieldMarkup(field) {
    const required = field.required ? "required" : "";
    if (field.type === "boolean") return `<label class="agent-checkbox"><input type="checkbox" name="${field.name}"/><span>${field.title}</span></label>`;
    if (field.options) return `<label><span>${field.title}${field.required ? " *" : ""}</span><select name="${field.name}" ${required}>${field.options.map((option) => `<option value="${option}">${option}</option>`).join("")}</select><small>${field.description}</small></label>`;
    if (["object", "array"].includes(field.type)) return `<label><span>${field.title}${field.required ? " *" : ""}</span><textarea name="${field.name}" data-json-field rows="3" ${required}>${JSON.stringify(field.defaultValue || (field.type === "array" ? [] : {}), null, 2)}</textarea><small>${field.description || "JSON value"}</small></label>`;
    return `<label><span>${field.title}${field.required ? " *" : ""}</span><input name="${field.name}" type="${field.type === "number" || field.type === "integer" ? "number" : "text"}" value="${field.defaultValue}" ${required}/><small>${field.description}</small></label>`;
  }

  click(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.agentId) return this.select(button.dataset.agentId);
    if (button.dataset.agentRunCard) return this.select(button.dataset.agentRunCard).then(() => this.root.querySelector("[data-agent-run-form] input, [data-agent-run-form] textarea")?.focus());
    if (button.matches("[data-agents-refresh]")) return this.load();
    if (button.matches("[data-agent-create]")) return this.openDraft();
    if (button.matches("[data-agent-edit]")) return this.openDraft(this.detail || this.selected);
    if (button.dataset.agentTab) return this.switchTab(button.dataset.agentTab);
    if (button.dataset.agentRestore) return this.restore(button.dataset.agentRestore);
    if (button.dataset.agentSession) { window.location.hash = `chat/${encodeURIComponent(button.dataset.agentSession)}`; return; }
    if (button.matches("[data-agent-import]")) return this.toast("Agent import is available through the versioned Agent API.", "idle");
  }

  submit(event) {
    if (event.target.matches("[data-agent-run-form]")) { event.preventDefault(); return this.run(event.target); }
    if (event.target.matches("[data-agent-draft-form]")) { event.preventDefault(); return this.saveDraft(event.target); }
  }

  switchTab(tab) {
    this.root.querySelectorAll("[data-agent-tab]").forEach((button) => button.classList.toggle("is-active", button.dataset.agentTab === tab));
    this.root.querySelectorAll("[data-agent-panel]").forEach((panel) => { panel.hidden = panel.dataset.agentPanel !== tab; });
  }

  async run(form) {
    if (!this.selected) return;
    const values = {};
    for (const [key, value] of new FormData(form)) {
      const field = form.elements[key];
      if (field?.matches?.("[data-json-field]")) {
        try { values[key] = JSON.parse(value); } catch { return this.toast(`${key} must be valid JSON`, "error"); }
      } else values[key] = value;
    }
    form.querySelector("[type='submit']").disabled = true;
    try {
      const id = this.selected.id || this.selected.agent_id;
      const result = await this.api.post(`/api/agents/${encodeURIComponent(id)}/runs`, { workspace_id: this.api.settings().workspaceId, input: values }, { scope: "agent-run" });
      this.toast("Agent session started", "success");
      window.location.hash = `chat/${encodeURIComponent(result.thread_id || result.thread?.id || result.run_id)}`;
    } catch (error) { this.toast(error.message, "error"); form.querySelector("[type='submit']").disabled = false; }
  }

  openDraft(agent = {}) {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-editor-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card" data-agent-draft-form><header><div><small>Review before publish</small><h2>${agent.id ? "Edit agent draft" : "Create agent"}</h2></div><button type="button" data-close>×</button></header><label><span>Name</span><input name="name" required /></label><label><span>Description</span><textarea name="description" rows="2"></textarea></label><label><span>Instructions</span><textarea name="instructions" rows="6" required></textarea></label><label><span>Output format</span><input name="output_format" placeholder="Report, table, artifact set…" /></label><label><span>Input JSON schema</span><textarea name="input_schema" data-json-field rows="6">${JSON.stringify(agent.input_schema || { type: "object", properties: { request: { type: "string" } }, required: ["request"] }, null, 2)}</textarea></label><div class="agent-draft-bindings"><span>${list(agent.skills, "items").length} pinned skills</span><span>${list(agent.files, "items").length} files</span><span>${agent.runtime || "Default runtime"}</span></div><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save & publish</button></footer></form>`;
    document.body.append(dialog);
    dialog.querySelector("[name='name']").value = agent.name || "";
    dialog.querySelector("[name='description']").value = agent.description || "";
    dialog.querySelector("[name='instructions']").value = agent.instructions || "";
    dialog.querySelector("[name='output_format']").value = agent.output_format || "";
    dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    dialog.querySelector("form").addEventListener("submit", (event) => { event.preventDefault(); this.saveDraft(event.target, dialog, agent); });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async saveDraft(form, dialog, agent = {}) {
    const data = new FormData(form);
    let input_schema;
    try { input_schema = JSON.parse(data.get("input_schema")); } catch { return this.toast("Input schema must be valid JSON", "error"); }
    const body = { name: data.get("name"), description: data.get("description"), instructions: data.get("instructions"), output_format: data.get("output_format"), input_schema, workspace_id: this.api.settings().workspaceId, thread_id: chatState.currentThreadId || null };
    const submit = form.querySelector("[type='submit']"); submit.disabled = true;
    try {
      const draft = await this.api.post(agent.id ? `/api/agents/${encodeURIComponent(agent.id)}/drafts` : "/api/agent-drafts", body, { scope: "agent-draft-save" });
      await this.api.post(`/api/agent-drafts/${encodeURIComponent(draft.id || draft.draft_id)}/publish`, {}, { scope: "agent-publish" });
      dialog?.close(); this.toast("Agent published", "success"); await this.load();
    } catch (error) { submit.disabled = false; this.toast(error.message, "error"); }
  }

  async restore(version) {
    if (!this.selected || !window.confirm(`Restore version ${version}? A new version will be created.`)) return;
    try {
      const id = this.selected.id || this.selected.agent_id;
      await this.api.post(`/api/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/restore`, {}, { scope: "agent-restore" });
      this.toast(`Version ${version} restored`, "success"); await this.select(id);
    } catch (error) { this.toast(error.message, "error"); }
  }

  toast(message, state = "idle") {
    const toast = this.root.querySelector("[data-agent-toast]");
    if (!toast) return; toast.hidden = false; toast.dataset.state = state; toast.textContent = message;
    clearTimeout(this.timer); this.timer = setTimeout(() => { toast.hidden = true; }, state === "error" ? 6000 : 3000);
  }
}

let singleton;
export function createAgentsUI() { if (!singleton) { singleton = new AgentsUI(); singleton.init(); window.taroaiAgents = singleton; } return singleton; }

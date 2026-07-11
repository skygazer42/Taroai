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
    this.root?.addEventListener("change", (event) => this.change(event));
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
        <header class="capability-page-header"><div><p>Reusable work</p><h1>Agents</h1><span>Versioned applications built from successful execution patterns.</span></div><div class="capability-header-actions"><button type="button" data-agent-import>Import</button><input type="file" accept="application/json,.json" hidden data-agent-import-input /><button class="primary" type="button" data-agent-create>Create agent</button></div></header>
        <div class="capability-toolbar"><label><span>⌕</span><input data-agent-search type="search" placeholder="Search agents" /></label><div class="agent-view-meta" data-agent-count>0 agents</div><button data-agents-refresh>Refresh</button></div>
        <div class="agent-product-layout"><section class="agent-library" data-agent-library><div class="route-loading">Loading agents…</div></section><aside class="agent-inspector" data-agent-inspector><div class="route-empty"><span>A</span><strong>Select an agent</strong><p>Review inputs, pinned context, versions, and recent sessions.</p></div></aside></div>
        <div class="route-toast" data-agent-toast hidden></div>
      </section>`;
  }

  async load() {
    try {
      const workspace = encodeURIComponent(this.api.settings().workspaceId);
      const [payload, browserProfiles] = await Promise.all([
        this.api.get(`/api/agents?workspace_id=${workspace}`),
        this.api.get(`/api/browser/profiles?workspace_id=${workspace}`).catch(() => ({ profiles: [] })),
      ]);
      this.agents = list(payload, "agents", "definitions");
      this.draftBrowserProfiles = list(browserProfiles, "profiles");
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
      if (detail.status === "fulfilled") {
        const definition = detail.value.agent || detail.value;
        const versions = list(detail.value, "versions");
        const activeVersion = versions.find((version) => version.version === definition.published_version)
          || versions.find((version) => version.version === definition.latest_version)
          || versions.at(-1);
        this.detail = { ...definition, ...(activeVersion?.spec || {}), version: activeVersion?.version, versions };
      } else this.detail = this.selected;
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
      <header class="agent-inspector-header"><div><span class="agent-monogram large">${(agent.name || "A").slice(0, 1).toUpperCase()}</span><div><small>${agent.status || "Published"}</small><h2></h2><p></p></div></div><div class="agent-inspector-actions"><button data-agent-export>Export</button><button data-agent-edit>Edit draft</button></div></header>
      <nav class="agent-inspector-tabs"><button class="is-active" data-agent-tab="run">Run</button><button data-agent-tab="configuration">Configuration</button><button data-agent-tab="versions">Versions</button><button data-agent-tab="sessions">Sessions</button></nav>
      <section data-agent-panel="run"><form class="agent-run-form" data-agent-run-form>${fields.map((field) => this.fieldMarkup(field)).join("")}<button class="primary" type="submit" ${loading ? "disabled" : ""}>Run agent</button></form></section>
      <section data-agent-panel="configuration" hidden><div class="agent-config-block"><small>Instructions</small><p></p></div><div class="agent-binding-grid"><div><small>Pinned skills</small><strong>${list(agent.skills, "items").length || list(agent.skill_bindings, "items").length}</strong></div><div><small>Reference files</small><strong>${list(agent.files, "items").length || list(agent.reference_files, "items").length}</strong></div><div><small>Runtime</small><strong>${agent.runtime_snapshot?.image || "Workspace default"}</strong></div><div><small>Autonomy</small><strong>${agent.runtime_snapshot?.autonomy_mode || "workflow"}</strong></div><div><small>Browser profile</small><strong data-agent-browser-profile></strong></div><div><small>Restored files</small><strong>${list(agent.runtime_snapshot?.files, "items").length}</strong></div><div><small>Output</small><strong>${agent.output_format || agent.output_contract?.type || "Structured result"}</strong></div></div></section>
      <section data-agent-panel="versions" hidden><ol class="agent-version-list">${versions.map((version) => `<li><span><strong>v${version.version || version.number}</strong><small>${version.status || "published"} · ${version.created_at ? new Date(version.created_at).toLocaleDateString() : "current"}</small></span><button data-agent-restore="${version.version || version.number}">Restore</button></li>`).join("")}</ol></section>
      <section data-agent-panel="sessions" hidden><ol class="agent-session-list">${this.sessions.length ? this.sessions.map((session) => `<li><span><strong>${session.title || session.input_summary || "Agent session"}</strong><small>${session.status || "completed"} · ${session.created_at ? new Date(session.created_at).toLocaleString() : ""}</small></span><button data-agent-session="${session.thread_id || session.id}">Open</button></li>`).join("") : "<li class='route-note'>No sessions yet.</li>"}</ol></section>`;
    root.querySelector("h2").textContent = agent.name || "Untitled agent";
    root.querySelector(".agent-inspector-header p").textContent = agent.description || "Reusable agent";
    root.querySelector(".agent-config-block p").textContent = agent.instructions || "No additional instructions were published.";
    const profile = (this.draftBrowserProfiles || []).find((item) => item.id === agent.runtime_snapshot?.browser_profile_id);
    root.querySelector("[data-agent-browser-profile]").textContent = profile?.name || (agent.runtime_snapshot?.browser_profile_id ? "Pinned profile" : "Workspace default");
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
    if (button.matches("[data-agent-import]")) return this.root.querySelector("[data-agent-import-input]")?.click();
    if (button.matches("[data-agent-export]")) return this.exportAgent();
  }

  async change(event) {
    if (!event.target.matches("[data-agent-import-input]") || !event.target.files?.[0]) return;
    try {
      const bundle = JSON.parse(await event.target.files[0].text());
      const result = await this.api.post("/api/agents/import", {
        workspace_id: this.api.settings().workspaceId,
        bundle,
        publish: true,
      }, { scope: "agent-import" });
      this.toast(`Imported ${result.agent?.name || "Agent"}`, "success");
      await this.load();
    } catch (error) {
      this.toast(error.message, "error");
    } finally {
      event.target.value = "";
    }
  }

  async exportAgent() {
    if (!this.selected) return;
    try {
      const id = this.selected.id || this.selected.agent_id;
      const bundle = await this.api.get(`/api/agents/${encodeURIComponent(id)}/export`);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${(bundle.metadata?.name || "agent").replace(/[^A-Za-z0-9._-]+/g, "-")}.agent.json`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      this.toast("Agent bundle exported", "success");
    } catch (error) {
      this.toast(error.message, "error");
    }
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
      const result = await this.api.post(`/api/agents/${encodeURIComponent(id)}/runs`, { input: values }, { scope: "agent-run" });
      this.toast("Agent session started", "success");
      window.location.hash = `chat/${encodeURIComponent(result.thread_id || result.thread?.id || result.run_id)}`;
    } catch (error) { this.toast(error.message, "error"); form.querySelector("[type='submit']").disabled = false; }
  }

  async openDraft(agent = {}) {
    const workspace = encodeURIComponent(this.api.settings().workspaceId);
    const [files, browserProfiles] = await Promise.allSettled([
      this.api.get(`/api/workspaces/${workspace}/files`),
      this.api.get(`/api/browser/profiles?workspace_id=${workspace}`),
    ]);
    this.draftFiles = files.status === "fulfilled" ? list(files.value, "files") : [];
    this.draftBrowserProfiles = browserProfiles.status === "fulfilled" ? list(browserProfiles.value, "profiles").filter((item) => item.status === "active") : [];
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-editor-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card" data-agent-draft-form><header><div><small>Review before publish</small><h2>${agent.id ? "Edit agent draft" : "Create agent"}</h2></div><button type="button" data-close>×</button></header><label><span>Name</span><input name="name" required /></label><label><span>Description</span><textarea name="description" rows="2"></textarea></label><label><span>Instructions</span><textarea name="instructions" rows="6" required></textarea></label><label><span>Output format</span><input name="output_format" placeholder="Report, table, artifact set…" /></label><label><span>Input JSON schema</span><textarea name="input_schema" data-json-field rows="6">${JSON.stringify(agent.input_schema || { type: "object", properties: { request: { type: "string" } }, required: ["request"] }, null, 2)}</textarea></label><div class="agent-runtime-editor"><label><span>Autonomy</span><select name="autonomy_mode"><option value="workflow">Guarded workflow</option><option value="autonomous">Autonomous</option></select></label><label><span>Network policy</span><select name="network_mode"><option value="disabled">Disabled</option><option value="allowlist">Allowlist</option><option value="open">Open</option></select></label><label><span>Browser profile</span><select name="browser_profile_id" data-agent-browser-profile-options><option value="">Workspace default</option></select></label><label><span>Runtime image</span><input name="runtime_image" placeholder="python:3.12-slim" /></label><label><span>Timeout seconds</span><input name="timeout_seconds" type="number" min="1" max="86400" /></label></div><fieldset class="agent-reference-picker"><legend>Reference files <small>Materialized into every fresh run</small></legend><div data-agent-reference-options></div></fieldset><div class="agent-draft-bindings"><span>${list(agent.skill_bindings || agent.skills, "items").length} pinned skills</span><span data-agent-reference-count>${list(agent.reference_files || agent.files, "items").length} files</span><span>${agent.runtime_snapshot?.image || "Default runtime"}</span></div><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save & publish</button></footer></form>`;
    document.body.append(dialog);
    dialog.querySelector("[name='name']").value = agent.name || "";
    dialog.querySelector("[name='description']").value = agent.description || "";
    dialog.querySelector("[name='instructions']").value = agent.instructions || "";
    dialog.querySelector("[name='output_format']").value = agent.output_contract?.format || agent.output_format || "";
    dialog.querySelector("[name='autonomy_mode']").value = agent.runtime_snapshot?.autonomy_mode || "workflow";
    dialog.querySelector("[name='network_mode']").value = agent.runtime_snapshot?.network_mode || "disabled";
    dialog.querySelector("[name='runtime_image']").value = agent.runtime_snapshot?.image || "";
    dialog.querySelector("[name='timeout_seconds']").value = agent.runtime_snapshot?.timeout_seconds || 300;
    const profileSelect = dialog.querySelector("[data-agent-browser-profile-options]");
    for (const profile of this.draftBrowserProfiles) {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = `${profile.name}${profile.is_default ? " · default" : ""}`;
      profileSelect.append(option);
    }
    profileSelect.value = agent.runtime_snapshot?.browser_profile_id || "";
    const selectedFiles = new Set(list(agent.reference_files || agent.files, "items").map((item) => item.storage_object_id || item.id));
    const options = dialog.querySelector("[data-agent-reference-options]");
    if (!this.draftFiles.length) {
      const empty = document.createElement("p");
      empty.textContent = "No persistent workspace files are available.";
      options.append(empty);
    }
    for (const file of this.draftFiles) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      const small = document.createElement("small");
      checkbox.type = "checkbox";
      checkbox.name = "reference_file";
      checkbox.value = file.storage_object_id || file.id;
      checkbox.checked = selectedFiles.has(checkbox.value);
      strong.textContent = file.logical_path || file.filename || file.id;
      small.textContent = `${file.content_type || "file"} · ${file.size_bytes || 0} bytes`;
      copy.append(strong, small);
      label.append(checkbox, copy);
      options.append(label);
    }
    options.addEventListener("change", () => {
      const count = options.querySelectorAll("input:checked").length;
      dialog.querySelector("[data-agent-reference-count]").textContent = `${count} files`;
    });
    dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    dialog.querySelector("form").addEventListener("submit", (event) => { event.preventDefault(); this.saveDraft(event.target, dialog, agent); });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async saveDraft(form, dialog, agent = {}) {
    const data = new FormData(form);
    let input_schema;
    try { input_schema = JSON.parse(data.get("input_schema")); } catch { return this.toast("Input schema must be valid JSON", "error"); }
    const reference_files = data.getAll("reference_file").map((storageObjectId) => {
      const file = (this.draftFiles || []).find((item) => (item.storage_object_id || item.id) === storageObjectId) || {};
      return {
        storage_object_id: storageObjectId,
        filename: file.logical_path || file.filename || storageObjectId,
        content_type: file.content_type || "application/octet-stream",
        size_bytes: file.size_bytes || 0,
      };
    });
    const version = {
      input_schema,
      output_contract: { type: "string", format: data.get("output_format") || "markdown" },
      instructions: data.get("instructions"),
      skill_bindings: list(agent.skill_bindings || agent.skills, "items"),
      connector_bindings: list(agent.connector_bindings || agent.connectors, "items"),
      knowledge_bindings: list(agent.knowledge_bindings || agent.knowledge, "items"),
      reference_files,
      model_policy: agent.model_policy || {},
      runtime_snapshot: {
        ...(agent.runtime_snapshot || {}),
        autonomy_mode: data.get("autonomy_mode") || "workflow",
        network_mode: data.get("network_mode") || "disabled",
        image: data.get("runtime_image") || undefined,
        timeout_seconds: Number(data.get("timeout_seconds") || 300),
        browser_profile_id: data.get("browser_profile_id") || undefined,
      },
      source_thread_id: agent.source_thread_id || chatState.currentThreadId || null,
      source_run_id: agent.source_run_id || null,
      change_note: agent.id ? "Updated from Agent editor" : "Created from Agent editor",
    };
    const submit = form.querySelector("[type='submit']"); submit.disabled = true;
    try {
      let agentId = agent.id;
      let versionNumber;
      if (agentId) {
        await this.api.patch(`/api/agents/${encodeURIComponent(agentId)}`, {
          name: data.get("name"),
          description: data.get("description"),
        }, { scope: "agent-definition-update" });
        const createdVersion = await this.api.post(`/api/agents/${encodeURIComponent(agentId)}/versions`, { version }, { scope: "agent-version-create" });
        versionNumber = createdVersion.version;
      } else {
        const created = await this.api.post("/api/agents", {
          workspace_id: this.api.settings().workspaceId,
          name: data.get("name"),
          description: data.get("description"),
          version,
        }, { scope: "agent-create" });
        agentId = created.agent.id;
        versionNumber = created.version.version;
      }
      await this.api.post(`/api/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionNumber)}/publish`, {}, { scope: "agent-publish" });
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

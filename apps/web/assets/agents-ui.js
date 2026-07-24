import { chatApi } from "./chat-api.js?v=20260722-flow115";
import { chatState, queueAgentRunHandoff } from "./chat-controller.js?v=20260724-design4";
import { icon } from "./icons.js?v=20260724-icons2";

function list(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return Array.isArray(value?.items) ? value.items : [];
}

function skillId(value = {}) {
  return value.id || value.skill_id || value.manifest?.id;
}

function sandboxNetworkModes(readiness = {}) {
  return readiness.configured && String(readiness.provider || "disabled").toLowerCase() === "e2b"
    ? ["disabled", "open"]
    : ["disabled"];
}

function schemaFields(schema = {}) {
  let properties = schema.properties || {};
  if (!Object.keys(properties).length && schema.additionalProperties !== false) {
    properties = { request: { type: "string", title: "Request", description: "What should this Agent do?" } };
    schema = { ...schema, required: ["request"] };
  }
  return Object.entries(properties).map(([name, definition]) => ({
    name,
    type: definition.type || "string",
    title: definition.title || name.replaceAll("_", " "),
    description: definition.description || "",
    required: (schema.required || []).includes(name),
    options: definition.enum || null,
    defaultValue: definition.default ?? "",
  }));
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function describeSchedule(trigger) {
  const cron = trigger.schedule?.cron_expression || "";
  const [minute, hour, day, month, weekday] = cron.split(" ");
  const time = /^\d+$/.test(hour) && /^\d+$/.test(minute)
    ? `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`
    : null;
  if (time && day === "*" && month === "*" && weekday === "*") return `Daily at ${time}`;
  if (time && day === "*" && month === "*" && weekday === "1-5") return `Weekdays at ${time}`;
  if (time && day === "*" && month === "*" && weekday === "1") return `Mondays at ${time}`;
  return cron || "Schedule";
}

function schemaExample(schema = {}) {
  if (Array.isArray(schema.enum) && schema.enum.length) return schema.enum[0];
  if (schema.type === "boolean") return false;
  if (schema.type === "integer" || schema.type === "number") return 0;
  if (schema.type === "array") return [];
  if (schema.type === "object" || schema.properties) {
    return Object.fromEntries(
      Object.entries(schema.properties || {}).map(([name, definition]) => [name, schemaExample(definition)]),
    );
  }
  return "value";
}

export class AgentsUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.agents = [];
    this.selected = null;
    this.detail = null;
    this.sessions = [];
    this.triggers = [];
    this.evaluationRuns = [];
    this.activity = [];
    this.files = [];
    this.memories = [];
    this.memoryCandidates = [];
    this.apiKeys = [];
    this.apiKeysError = "";
    this.apiKeysLoading = false;
    this.draftSkills = [];
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
        <header class="capability-page-header"><div><p>Reusable work</p><h1>Agents</h1><span>Versioned applications built from successful execution patterns.</span></div><div class="capability-header-actions"><button type="button" data-agent-import>${icon("upload")}<span>Import</span></button><input type="file" accept="application/json,.json" hidden data-agent-import-input /><button class="primary" type="button" data-agent-create>${icon("plus")}<span>Create agent</span></button></div></header>
        <div class="capability-toolbar"><label><span aria-hidden="true">${icon("search")}</span><input data-agent-search type="search" placeholder="Search agents" /></label><div class="agent-view-meta" data-agent-count>0 agents</div><button data-agents-refresh aria-label="Refresh">${icon("refresh-cw")}<span>Refresh</span></button></div>
        <div class="agent-product-layout"><section class="agent-library" data-agent-library><div class="route-loading">Loading agents…</div></section><aside class="agent-inspector" data-agent-inspector><div class="route-empty"><span>${icon("bot")}</span><strong>Select an agent</strong><p>Review inputs, pinned context, versions, and recent sessions.</p></div></aside></div>
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
      const routeId = window.location.hash.replace(/^#/, "").split("/")[1];
      const requestedId = routeId ? decodeURIComponent(routeId) : null;
      this.selected = this.agents.find((agent) => (agent.id || agent.agent_id) === requestedId)
        || this.agents.find((agent) => (agent.id || agent.agent_id) === (this.selected?.id || this.selected?.agent_id))
        || this.agents[0]
        || null;
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
      library.innerHTML = `<div class="route-empty"><span>${icon("bot")}</span><strong>No agents yet</strong><p>Create one from a successful Chat or start a new draft here.</p><button data-agent-create>Create agent</button></div>`;
      return;
    }
    for (const agent of agents) {
      const id = agent.id || agent.agent_id;
      const version = agent.version || agent.latest_version || "1";
      const status = agent.latest_version !== agent.published_version ? "draft" : (agent.status || "published");
      const card = document.createElement("article");
      card.className = "real-agent-card";
      card.classList.toggle("is-active", id === (this.selected?.id || this.selected?.agent_id));
      card.innerHTML = `<button class="agent-card-main" data-agent-id="${id}"><span class="agent-monogram">${icon("bot")}</span><span><small>${status} · v${version}</small><strong></strong><p></p></span></button><footer><span>${list(agent.skill_bindings, "items").length} skills</span><span>${agent.run_count || 0} runs</span><button data-agent-run-card="${id}">${icon("play")}<span>Run</span></button></footer>`;
      card.querySelector("strong").textContent = agent.name || "Untitled agent";
      card.querySelector("p").textContent = agent.description || "Reusable agent";
      library.append(card);
    }
  }

  async select(id) {
    this.selected = this.agents.find((agent) => (agent.id || agent.agent_id) === id) || this.selected;
    const apiEnabled = Boolean(this.selected?.published_version || this.selected?.status === "published");
    this.apiKeys = [];
    this.apiKeysError = "";
    this.apiKeysLoading = apiEnabled;
    this.renderCards(this.root.querySelector("[data-agent-search]")?.value || "");
    this.renderInspector(true);
    try {
      const [detail, sessions, evaluations, triggers, activity, files, memories, memoryCandidates, apiKeys] = await Promise.allSettled([
        this.api.get(`/api/agents/${encodeURIComponent(id)}`),
        this.api.get(`/api/agents/${encodeURIComponent(id)}/sessions`),
        this.api.get(`/api/evaluations/runs?target_id=${encodeURIComponent(id)}&target_kind=agent`),
        this.api.get("/api/triggers"),
        this.api.get(`/api/agents/${encodeURIComponent(id)}/activity`),
        this.api.get(`/api/agents/${encodeURIComponent(id)}/files?version=${encodeURIComponent(this.selected.latest_version)}`),
        this.api.get(`/api/memory?scope_type=agent&scope_id=${encodeURIComponent(id)}`),
        this.api.get(`/api/memory?scope_type=agent&scope_id=${encodeURIComponent(id)}&status=candidate`),
        apiEnabled ? this.api.get(`/api/api-keys?agent_id=${encodeURIComponent(id)}`) : Promise.resolve({ items: [] }),
      ]);
      if (detail.status === "fulfilled") {
        const definition = detail.value.agent || detail.value;
        const versions = list(detail.value, "versions");
        const activeVersion = versions.find((version) => version.version === definition.latest_version)
          || versions.find((version) => version.version === definition.published_version)
          || versions.at(-1);
        this.detail = {
          ...definition,
          ...(activeVersion?.spec || {}),
          status: activeVersion?.status || definition.status,
          version: activeVersion?.version,
          versions,
        };
      } else this.detail = this.selected;
      this.sessions = sessions.status === "fulfilled" ? list(sessions.value, "sessions", "runs") : [];
      this.evaluationRuns = evaluations.status === "fulfilled" ? list(evaluations.value, "runs") : [];
      this.triggers = triggers.status === "fulfilled"
        ? list(triggers.value, "triggers").filter((trigger) => trigger.type === "schedule" && trigger.agent_id === id)
        : [];
      this.activity = activity.status === "fulfilled" ? list(activity.value, "activity") : [];
      this.files = files.status === "fulfilled" ? list(files.value, "files") : [];
      this.memories = memories.status === "fulfilled" ? list(memories.value, "memories") : [];
      this.memoryCandidates = memoryCandidates.status === "fulfilled" ? list(memoryCandidates.value, "memories") : [];
      this.apiKeys = apiKeys.status === "fulfilled" ? list(apiKeys.value, "items", "api_keys", "keys") : [];
      this.apiKeysError = apiKeys.status === "rejected" ? apiKeys.reason?.message || "API keys unavailable" : "";
    } catch {
      this.detail = this.selected;
      this.sessions = [];
      this.triggers = [];
      this.evaluationRuns = [];
      this.activity = [];
      this.files = [];
      this.memories = [];
      this.memoryCandidates = [];
      this.apiKeys = [];
      this.apiKeysError = "API keys unavailable";
    }
    this.apiKeysLoading = false;
    this.renderInspector();
  }

  renderInspector(loading = false) {
    const root = this.root.querySelector("[data-agent-inspector]");
    if (!root) return;
    if (!this.selected) {
      root.innerHTML = `<div class="route-empty"><span>${icon("bot")}</span><strong>Select an agent</strong><p>Review its contract, pinned resources, and history.</p></div>`;
      return;
    }
    const agent = this.detail || this.selected;
    const schema = agent.input_schema || agent.input_contract || { type: "object", properties: { request: { type: "string", title: "Request" } }, required: ["request"] };
    const fields = schemaFields(schema);
    const versions = list(agent.versions, "items").length ? list(agent.versions, "items") : [{ version: agent.version || agent.latest_version || "1", status: agent.status || "published", created_at: agent.updated_at }];
    const publishedSchema = versions.find((version) => version.version === agent.published_version)?.spec?.input_schema || schema;
    const mountedFiles = this.files.length
      ? this.files.map((file) => `<details><summary><span>${escapeHtml(file.path)}</span><small>${escapeHtml(file.content_type || "file")}</small></summary>${file.content == null ? "<p>Stored file reference</p>" : `<pre>${escapeHtml(file.content)}</pre>`}</details>`).join("")
      : '<p class="route-note">No mounted files available.</p>';
    root.innerHTML = `
      <header class="agent-inspector-header"><div><span class="agent-monogram large">${icon("bot")}</span><div><small>${agent.status || "Published"}</small><h2></h2><p></p></div></div><div class="agent-inspector-actions"><button data-agent-export>Export</button><button data-agent-edit>Edit draft</button>${agent.status === "draft" ? `<button class="primary" data-agent-publish-version="${agent.version}">Publish v${agent.version}</button>` : ""}</div></header>
      <nav class="agent-inspector-tabs"><button class="is-active" data-agent-tab="run">Run</button><button data-agent-tab="api">API</button><button data-agent-tab="schedule">Schedule</button><button data-agent-tab="configuration">Configuration</button><button data-agent-tab="memory">Memory</button><button data-agent-tab="evaluation">Evaluation</button><button data-agent-tab="versions">Versions</button><button data-agent-tab="sessions">Sessions</button><button data-agent-tab="activity">Activity</button></nav>
      <section data-agent-panel="run"><form class="agent-run-form" data-agent-run-form novalidate>${fields.map((field) => this.fieldMarkup(field)).join("")}<button class="primary" type="submit" ${loading ? "disabled" : ""}>Run agent</button></form></section>
      <section data-agent-panel="api" hidden>${this.apiMarkup(agent, publishedSchema)}</section>
      <section data-agent-panel="schedule" hidden>${this.scheduleMarkup(agent, loading)}</section>
      <section data-agent-panel="configuration" hidden><div class="agent-config-block"><small>Instructions</small><p></p></div><div class="agent-binding-grid"><div><small>Pinned skills</small><strong>${list(agent.skills, "items").length || list(agent.skill_bindings, "items").length}</strong></div><div><small>Connectors</small><strong>${list(agent.connector_bindings || agent.connectors, "items").length}</strong></div><div><small>Knowledge bases</small><strong>${list(agent.knowledge_bindings || agent.knowledge, "items").length}</strong></div><div><small>Reference files</small><strong>${list(agent.files, "items").length || list(agent.reference_files, "items").length}</strong></div><div><small>Runtime</small><strong>${agent.runtime_snapshot?.image || "Workspace default"}</strong></div><div><small>App type</small><strong>${agent.app_kind === "workflow" ? "Workflow app" : "Agent"}</strong></div><div><small>Browser profile</small><strong data-agent-browser-profile></strong></div><div><small>Write actions</small><strong>${agent.write_autonomy === "full_auto" ? "Full auto" : "Require approval"}</strong></div><div><small>Output</small><strong>${agent.output_format || agent.output_contract?.type || "Structured result"}</strong></div></div><div class="agent-mounted-files"><header><small>Mounted app files</small><span>${this.files.length}</span></header>${mountedFiles}</div></section>
      <section data-agent-panel="memory" hidden>${this.memoryMarkup()}</section>
      <section data-agent-panel="evaluation" hidden><div class="agent-evaluation-head"><div><small>Release gate</small><strong>${agent.runtime_snapshot?.evaluation_suite_id ? `${agent.runtime_snapshot.evaluation_suite_id} · ${agent.runtime_snapshot.evaluation_suite_version}` : "No suite bound"}</strong></div>${agent.runtime_snapshot?.evaluation_suite_id ? `<button data-agent-evaluate-version="${agent.version}">Run evaluation</button>` : ""}</div><ol class="agent-evaluation-list">${this.evaluationRuns.length ? this.evaluationRuns.map((run) => `<li><span><strong>${Math.round((run.metrics?.weighted_score || 0) * 100)}% · ${run.status}</strong><small>${run.suite_id} ${run.suite_version} · ${run.completed_at ? new Date(run.completed_at).toLocaleString() : ""}</small></span><span class="evaluation-run-actions">${run.promotion_gate?.allowed ? `<button data-evaluation-baseline="${run.id}">Set baseline</button>` : ""}<button data-evaluation-evidence="${run.id}">Evidence</button></span></li>`).join("") : "<li class='route-note'>No evaluation runs yet.</li>"}</ol></section>
      <section data-agent-panel="versions" hidden><ol class="agent-version-list">${versions.map((version) => `<li><span><strong>v${version.version || version.number}</strong><small>${version.status || "published"} · ${version.created_at ? new Date(version.created_at).toLocaleDateString() : "current"}</small></span><button data-agent-restore="${version.version || version.number}">Restore</button></li>`).join("")}</ol></section>
      <section data-agent-panel="sessions" hidden><ol class="agent-session-list">${this.sessions.length ? this.sessions.map((session) => `<li><span><strong>${escapeHtml(session.title || session.input_summary || "Agent session")}</strong><small>${escapeHtml(session.status || "completed")} · ${session.created_at ? new Date(session.created_at).toLocaleString() : ""}</small></span>${session.thread_id ? `<button data-agent-session="${escapeHtml(session.thread_id)}">Open</button>` : "<small>Background run</small>"}</li>`).join("") : "<li class='route-note'>No sessions yet.</li>"}</ol></section>
      <section data-agent-panel="activity" hidden><ol class="agent-session-list">${this.activity.length ? this.activity.map((item) => `<li><span><strong>${escapeHtml(item.type.replaceAll(".", " "))}</strong><small>${escapeHtml(item.execution_status || item.status || "recorded")} · ${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</small></span>${item.thread_id ? `<button data-agent-session="${escapeHtml(item.thread_id)}">Open</button>` : ""}</li>`).join("") : "<li class='route-note'>No activity yet.</li>"}</ol></section>`;
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

  apiMarkup(agent, schema) {
    const publishedVersion = agent.published_version || (agent.status === "published" ? agent.version : null);
    if (!publishedVersion) {
      return `<div class="agent-api-unpublished"><small>API Trigger</small><strong>Publish this Agent to enable API access</strong><p>API keys can only invoke a published version.</p><button type="button" disabled>Create API key</button></div>`;
    }
    const id = agent.id || agent.agent_id;
    const endpoint = `${this.api.settings().apiBase}/api/v1/apps/${encodeURIComponent(id)}/runs`;
    const request = { inputs: schemaExample(schema) };
    const curl = `curl --request POST "${endpoint}" \\
  --header "Authorization: Bearer $TAROAI_API_KEY" \\
  --header "Content-Type: application/json" \\
  --header "Idempotency-Key: your-request-id" \\
  --data-binary @- <<'JSON'\n${JSON.stringify(request, null, 2)}\nJSON`;
    const keys = this.apiKeys.map((key) => {
      const revoked = Boolean(key.revoked_at);
      const activity = revoked
        ? `Revoked ${new Date(key.revoked_at).toLocaleString()}`
        : key.last_used_at
          ? `Last used ${new Date(key.last_used_at).toLocaleString()}`
          : `Created ${new Date(key.created_at).toLocaleString()} · Never used`;
      return `<li><span><strong>${escapeHtml(key.name)}</strong><small><code>${escapeHtml(key.token_prefix)}</code> · ${escapeHtml(activity)}</small></span>${revoked ? "<em>Revoked</em>" : `<button type="button" data-agent-api-key-revoke="${escapeHtml(key.id)}" data-agent-api-key-name="${escapeHtml(key.name)}">Revoke</button>`}</li>`;
    }).join("");
    const keyList = this.apiKeysLoading
      ? '<p class="route-note">Loading API keys…</p>'
      : this.apiKeysError
        ? `<p class="route-note">${escapeHtml(this.apiKeysError)}</p>`
        : `<ol class="agent-api-key-list">${keys || '<li class="route-note">No API keys yet.</li>'}</ol>`;
    return `<div class="agent-api-panel">
      <header><div><small>API Trigger</small><strong>Published v${escapeHtml(publishedVersion)}</strong><p>Each key can invoke only this Agent. The raw token is shown once.</p></div><button type="button" data-agent-api-key-create>Create API key</button></header>
      <section><label for="agent-api-endpoint">Endpoint</label><div class="share-link-row"><input id="agent-api-endpoint" data-agent-api-endpoint value="${escapeHtml(endpoint)}" readonly /><button type="button" data-agent-api-copy="endpoint">Copy</button></div></section>
      <details open><summary>cURL</summary><div class="agent-api-code"><pre data-agent-api-curl>${escapeHtml(curl)}</pre><button type="button" data-agent-api-copy="curl">Copy cURL</button></div></details>
      <details><summary>Input JSON schema</summary><pre>${escapeHtml(JSON.stringify(schema, null, 2))}</pre></details>
      <section class="agent-api-keys"><div><small>Credentials</small><strong>${this.apiKeys.filter((key) => !key.revoked_at).length} active</strong></div>${keyList}</section>
    </div>`;
  }

  async copyApiValue(button) {
    const selector = button.dataset.agentApiCopy === "endpoint" ? "[data-agent-api-endpoint]" : "[data-agent-api-curl]";
    const source = button.closest("[data-agent-panel='api']")?.querySelector(selector);
    const value = source?.value || source?.textContent || "";
    if (!value) return;
    const label = button.textContent;
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = label; }, 1600);
    } catch (error) { this.toast(`Copy failed: ${error.message}`, "error"); }
  }

  async reloadApiKeys() {
    const id = this.selected?.id || this.selected?.agent_id;
    if (!id) return;
    this.apiKeysLoading = true;
    this.apiKeysError = "";
    try {
      const payload = await this.api.get(`/api/api-keys?agent_id=${encodeURIComponent(id)}`);
      this.apiKeys = list(payload, "items", "api_keys", "keys");
    } catch (error) {
      this.apiKeys = [];
      this.apiKeysError = error.message;
      throw error;
    } finally {
      this.apiKeysLoading = false;
      this.renderInspector();
      this.switchTab("api");
    }
  }

  openApiKeyDialog() {
    const agent = this.detail || this.selected;
    if (!agent?.published_version && agent?.status !== "published") return this.toast("Publish this Agent before creating an API key", "error");
    const id = agent.id || agent.agent_id;
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-api-key-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card"><header><div><small>Agent API</small><h2>Create API key</h2></div><button type="button" data-close aria-label="Close">${icon("x")}</button></header><p>This key can invoke only ${escapeHtml(agent.name || "this Agent")}. The raw token will be shown once.</p><label><span>Key name</span><input name="name" maxlength="120" value="${escapeHtml(`${agent.name || "Agent"} integration`)}" required /></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Create key</button></footer></form>`;
    document.body.append(dialog);
    const closeButtons = () => dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    closeButtons();
    dialog.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const name = String(new FormData(form).get("name") || "").trim();
      if (!name) return;
      const submit = form.querySelector("[type='submit']");
      submit.disabled = true;
      try {
        const result = await this.api.post("/api/api-keys", {
          name,
          agent_id: id,
        }, { scope: "agent-api-key-create" });
        if (!result.rawToken) throw new Error("API key token was not returned");
        form.innerHTML = `<header><div><small>Shown once</small><h2>API key created</h2></div><button type="button" data-close aria-label="Close">${icon("x")}</button></header><p>Copy this token now. It cannot be viewed again after this dialog closes.</p><label><span>API key</span><input data-agent-api-raw-token readonly /></label><div class="share-link-row"><span></span><button type="button" data-agent-api-raw-copy>Copy key</button></div><footer><button class="primary" type="button" data-close>Done</button></footer>`;
        const token = form.querySelector("[data-agent-api-raw-token]");
        token.value = result.rawToken;
        form.querySelector("[data-agent-api-raw-copy]").addEventListener("click", async (copy) => {
          const copyButton = copy.currentTarget;
          try {
            await navigator.clipboard.writeText(result.rawToken);
            copyButton.textContent = "Copied";
          } catch (error) {
            token.select();
            this.toast(`Copy failed: ${error.message}`, "error");
          }
        });
        closeButtons();
        this.reloadApiKeys().catch((error) => this.toast(`API key list could not refresh: ${error.message}`, "error"));
      } catch (error) {
        submit.disabled = false;
        this.toast(error.message, "error");
      }
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
    dialog.querySelector("input")?.focus();
  }

  async revokeApiKey(button) {
    if (!window.confirm(`Revoke ${button.dataset.agentApiKeyName || "this API key"}?`)) return;
    button.disabled = true;
    try {
      await this.api.delete(`/api/api-keys/${encodeURIComponent(button.dataset.agentApiKeyRevoke)}`, { scope: "agent-api-key-revoke" });
      this.toast("API key revoked", "success");
      await this.reloadApiKeys();
    } catch (error) {
      button.disabled = false;
      this.toast(error.message, "error");
    }
  }

  scheduleMarkup(agent, loading = false) {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const published = Boolean(agent.published_version || agent.status === "published");
    const activeCount = this.triggers.filter((trigger) => trigger.status === "enabled").length;
    const schedules = this.triggers.map((trigger) => {
      const ended = trigger.schedule?.ends_at && new Date(trigger.schedule.ends_at) < new Date();
      const nextRun = trigger.next_run_at
        ? new Date(trigger.next_run_at).toLocaleString()
        : ended ? "Schedule ended" : "Waiting for scheduler";
      const status = trigger.status || "disabled";
      return `<li class="agent-schedule-row"><span class="agent-schedule-state" data-state="${escapeHtml(status)}" aria-label="${escapeHtml(status)}"></span><span><strong>${escapeHtml(trigger.name)}</strong><small>${escapeHtml(describeSchedule(trigger))} · ${escapeHtml(trigger.schedule?.timezone || "UTC")}</small><em>${escapeHtml(nextRun)}</em></span><span class="agent-schedule-actions"><button type="button" data-trigger-toggle="${escapeHtml(trigger.id)}" data-trigger-status="${escapeHtml(status)}">${status === "enabled" ? "Pause" : "Enable"}</button>${status === "disabled" ? `<button class="agent-schedule-delete" type="button" data-trigger-delete="${escapeHtml(trigger.id)}" data-trigger-name="${escapeHtml(trigger.name)}">Delete</button>` : ""}</span></li>`;
    }).join("");
    const showEditor = published && this.triggers.length === 0;
    return `<div class="agent-schedule-panel">
      <header><div><small>Automatic runs</small><strong>${activeCount ? `${activeCount} enabled` : "No enabled schedules"}</strong><p>Runs use the published Agent version and appear in Sessions.</p></div><button type="button" data-agent-schedule-new aria-expanded="${showEditor}" ${published && !loading ? "" : "disabled"}>New schedule</button></header>
      ${published ? "" : '<p class="route-note">Publish this Agent before scheduling it.</p>'}
      <form class="agent-schedule-form" data-agent-schedule-form ${showEditor ? "" : "hidden"}>
        <label><span>Schedule name</span><input name="name" maxlength="160" placeholder="Daily workspace brief" /></label>
        <fieldset><legend>Repeat</legend><div class="agent-schedule-frequency" role="group" aria-label="Repeat frequency"><button class="is-active" type="button" data-schedule-frequency="daily" aria-pressed="true">Daily</button><button type="button" data-schedule-frequency="weekdays" aria-pressed="false">Weekdays</button><button type="button" data-schedule-frequency="weekly" aria-pressed="false">Mondays</button></div><input type="hidden" name="frequency" value="daily" /></fieldset>
        <div class="agent-schedule-fields"><label><span>Time</span><input name="time" value="09:00" inputmode="numeric" pattern="(?:[01]\\d|2[0-3]):[0-5]\\d" placeholder="09:00" required /></label><label><span>Timezone</span><input name="timezone" value="${escapeHtml(timezone)}" required /></label></div>
        <label><span>Run instruction</span><textarea name="message" rows="3" required>Run ${escapeHtml(agent.name || "this agent")} and return the configured result.</textarea></label>
        <footer><button type="button" data-agent-schedule-cancel>Cancel</button><button class="primary" type="submit">Create schedule</button></footer>
      </form>
      <ol class="agent-schedule-list">${schedules || '<li class="route-note">No schedules yet. Add one to run this Agent automatically.</li>'}</ol>
    </div>`;
  }

  click(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.agentId) return this.select(button.dataset.agentId);
    if (button.dataset.agentRunCard) return this.select(button.dataset.agentRunCard).then(() => this.root.querySelector("[data-agent-run-form] input, [data-agent-run-form] textarea")?.focus());
    if (button.matches("[data-agents-refresh]")) return this.load();
    if (button.matches("[data-agent-create]")) {
      window.location.hash = "chat";
      return window.taroaiChat?.openAgentBuilderDialog();
    }
    if (button.matches("[data-agent-edit]")) return this.openDraft(this.detail || this.selected);
    if (button.dataset.agentTab) return this.switchTab(button.dataset.agentTab);
    if (button.matches("[data-agent-api-key-create]")) return this.openApiKeyDialog();
    if (button.dataset.agentApiKeyRevoke) return this.revokeApiKey(button);
    if (button.dataset.agentApiCopy) return this.copyApiValue(button);
    if (button.matches("[data-agent-schedule-new]")) return this.toggleScheduleEditor(button);
    if (button.matches("[data-agent-schedule-cancel]")) return this.toggleScheduleEditor(button, false);
    if (button.dataset.scheduleFrequency) return this.selectScheduleFrequency(button);
    if (button.dataset.triggerToggle) return this.toggleTrigger(button);
    if (button.dataset.triggerDelete) return this.deleteTrigger(button);
    if (button.dataset.agentPublishVersion) return this.publishDraft(button.dataset.agentPublishVersion);
    if (button.dataset.agentRestore) return this.restore(button.dataset.agentRestore);
    if (button.dataset.agentSession) { window.location.hash = `chat/${encodeURIComponent(button.dataset.agentSession)}`; return; }
    if (button.matches("[data-agent-import]")) return this.root.querySelector("[data-agent-import-input]")?.click();
    if (button.matches("[data-agent-export]")) return this.exportAgent();
    if (button.dataset.agentEvaluateVersion) return this.evaluateVersion(button.dataset.agentEvaluateVersion);
    if (button.dataset.evaluationBaseline) return this.promoteBaseline(button.dataset.evaluationBaseline);
    if (button.dataset.evaluationEvidence) return this.openEvidence(button.dataset.evaluationEvidence);
    if (button.dataset.agentMemoryApprove) return this.reviewMemory(button.dataset.agentMemoryApprove, "approve");
    if (button.dataset.agentMemoryReject) return this.reviewMemory(button.dataset.agentMemoryReject, "reject");
  }

  memoryMarkup() {
    const rows = (records, candidate = false) => records.map((memory) => `
      <li><span><strong>${candidate ? "Session summary" : "Learned experience"}</strong><small>${escapeHtml(memory.content)}</small></span>${candidate ? `<span><button data-agent-memory-reject="${escapeHtml(memory.id)}">Reject</button><button data-agent-memory-approve="${escapeHtml(memory.id)}">Use memory</button></span>` : ""}</li>`).join("");
    return `<div class="agent-memory-panel"><header><strong>Agent memory</strong><p>Successful sessions become reviewable summaries. Only approved experience is recalled in later runs, with time decay.</p></header><h3>Needs review</h3><ol class="agent-session-list">${rows(this.memoryCandidates, true) || "<li class='route-note'>No session summaries need review.</li>"}</ol><h3>Approved</h3><ol class="agent-session-list">${rows(this.memories) || "<li class='route-note'>No approved experience yet.</li>"}</ol></div>`;
  }

  async reviewMemory(memoryId, action) {
    try {
      await this.api.post(`/api/memory/${encodeURIComponent(memoryId)}/${action}`, {}, { scope: "agent-memory" });
      this.toast(action === "approve" ? "Agent memory approved" : "Session summary rejected", "success");
      await this.select(this.selected.id || this.selected.agent_id);
      this.switchTab("memory");
    } catch (error) { this.toast(error.message, "error"); }
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
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
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
    if (event.target.matches("[data-agent-schedule-form]")) { event.preventDefault(); return this.createSchedule(event.target); }
    if (event.target.matches("[data-agent-draft-form]")) { event.preventDefault(); return this.saveDraft(event.target); }
  }

  switchTab(tab) {
    this.root.querySelectorAll("[data-agent-tab]").forEach((button) => button.classList.toggle("is-active", button.dataset.agentTab === tab));
    this.root.querySelectorAll("[data-agent-panel]").forEach((panel) => { panel.hidden = panel.dataset.agentPanel !== tab; });
  }

  toggleScheduleEditor(button, open) {
    const form = this.root.querySelector("[data-agent-schedule-form]");
    if (!form) return;
    const shouldOpen = open ?? form.hidden;
    form.hidden = !shouldOpen;
    this.root.querySelector("[data-agent-schedule-new]")?.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) form.elements.name?.focus();
  }

  selectScheduleFrequency(button) {
    const form = button.closest("form");
    if (!form) return;
    form.elements.frequency.value = button.dataset.scheduleFrequency;
    form.querySelectorAll("[data-schedule-frequency]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
  }

  async createSchedule(form) {
    if (!this.selected) return;
    const data = new FormData(form);
    const match = String(data.get("time") || "").trim().match(/^([01]\d|2[0-3]):([0-5]\d)$/);
    if (!match) return this.toast("Time must use 24-hour HH:MM format", "error");
    const [, hour, minute] = match;
    const frequency = data.get("frequency");
    const cron = {
      daily: `${Number(minute)} ${Number(hour)} * * *`,
      weekdays: `${Number(minute)} ${Number(hour)} * * 1-5`,
      weekly: `${Number(minute)} ${Number(hour)} * * 1`,
    }[frequency];
    const submit = form.querySelector("[type='submit']");
    submit.disabled = true;
    try {
      const agent = this.detail || this.selected;
      await this.api.post("/api/triggers", {
        workspace_id: this.api.settings().workspaceId,
        agent_id: this.selected.id || this.selected.agent_id,
        type: "schedule",
        name: String(data.get("name") || "").trim() || `${agent.name} · ${frequency}`,
        input_template: { message: String(data.get("message") || "").trim() },
        schedule: {
          cron_expression: cron,
          timezone: String(data.get("timezone") || "UTC").trim(),
        },
      }, { scope: "agent-schedule-create" });
      this.toast("Schedule created", "success");
      await this.select(this.selected.id || this.selected.agent_id);
      this.switchTab("schedule");
    } catch (error) {
      submit.disabled = false;
      this.toast(error.message, "error");
    }
  }

  async toggleTrigger(button) {
    button.disabled = true;
    try {
      const action = button.dataset.triggerStatus === "enabled" ? "disable" : "enable";
      await this.api.post(`/api/triggers/${encodeURIComponent(button.dataset.triggerToggle)}/${action}`, {}, { scope: `agent-schedule-${action}` });
      this.toast(action === "disable" ? "Schedule paused" : "Schedule enabled", "success");
      await this.select(this.selected.id || this.selected.agent_id);
      this.switchTab("schedule");
    } catch (error) {
      button.disabled = false;
      this.toast(error.message, "error");
    }
  }

  async deleteTrigger(button) {
    if (!window.confirm(`Delete ${button.dataset.triggerName || "this schedule"}?`)) return;
    button.disabled = true;
    try {
      await this.api.delete(`/api/triggers/${encodeURIComponent(button.dataset.triggerDelete)}`, { scope: "agent-schedule-delete" });
      this.toast("Schedule deleted", "success");
      await this.select(this.selected.id || this.selected.agent_id);
      this.switchTab("schedule");
    } catch (error) {
      button.disabled = false;
      this.toast(error.message, "error");
    }
  }

  async run(form) {
    if (!this.selected) return;
    if (!form.reportValidity()) {
      this.toast("Complete the required fields before running this Agent.", "error");
      return;
    }
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
      const version = this.detail?.version || this.selected.latest_version;
      const model = chatState.selectedModel;
      queueAgentRunHandoff({
        agent_id: id,
        input: values,
        ...(version ? { version: Number(version) } : {}),
        ...(model?.provider_id && model?.model_id ? {
          provider_id: model.provider_id,
          model_id: model.model_id,
          reasoning_effort: model.reasoning_effort || null,
        } : {}),
      });
      window.location.hash = "chat";
    } catch (error) { this.toast(error.message, "error"); form.querySelector("[type='submit']").disabled = false; }
  }

  async openDraft(agent = {}) {
    const workspace = encodeURIComponent(this.api.settings().workspaceId);
    const [files, browserProfiles, engineConnections, repositories, evaluationSuites, skills, connectors, knowledgeBases, readiness] = await Promise.allSettled([
      this.api.get(`/api/workspaces/${workspace}/files`),
      this.api.get(`/api/browser/profiles?workspace_id=${workspace}`),
      this.api.get(`/api/agent-engines/connections?workspace_id=${workspace}`),
      this.api.get(`/api/repositories?workspace_id=${workspace}`),
      this.api.get(`/api/evaluations/suites?target_kind=agent`),
      this.api.get(`/api/workspaces/${workspace}/skills`),
      this.api.get(`/api/connectors?workspace_id=${workspace}`),
      this.api.get(`/api/knowledge-bases?workspace_id=${workspace}`),
      this.api.get("/readyz"),
    ]);
    this.draftFiles = files.status === "fulfilled" ? list(files.value, "files") : [];
    this.draftBrowserProfiles = browserProfiles.status === "fulfilled" ? list(browserProfiles.value, "profiles").filter((item) => item.status === "active") : [];
    this.draftEngineConnections = engineConnections.status === "fulfilled" ? list(engineConnections.value, "connections").filter((item) => item.status === "active") : [];
    this.draftRepositories = repositories.status === "fulfilled" ? list(repositories.value, "repositories").filter((item) => item.status === "active") : [];
    this.draftEvaluationSuites = evaluationSuites.status === "fulfilled" ? list(evaluationSuites.value, "suites") : [];
    const pinnedSkills = list(agent.skill_bindings || agent.skills, "items");
    const pinnedById = new Map(pinnedSkills.map((item) => [skillId(item), item]));
    const availableSkills = skills.status === "fulfilled"
      ? list(skills.value, "skills", "installations").filter((item) => item.status === "enabled" && item.invocation_ready !== false)
      : [];
    this.draftSkills = Array.from(new Map([...availableSkills, ...pinnedSkills].map((item) => [skillId(item), { ...item, ...(pinnedById.get(skillId(item)) || {}) }])).values()).filter((item) => skillId(item));
    const pinnedConnectors = list(agent.connector_bindings || agent.connectors, "items");
    const pinnedConnectorIds = new Set(pinnedConnectors.map((item) => item.id || item.connector_id));
    this.draftConnectors = Array.from(new Map([
      ...(connectors.status === "fulfilled" ? list(connectors.value, "connectors").filter((item) => item.status === "enabled") : []),
      ...pinnedConnectors,
    ].map((item) => [item.id || item.connector_id, item])).values()).filter((item) => item.id || item.connector_id);
    const pinnedKnowledge = list(agent.knowledge_bindings || agent.knowledge, "items");
    const pinnedKnowledgeIds = new Set(pinnedKnowledge.map((item) => item.id || item.knowledge_id));
    this.draftKnowledgeBases = Array.from(new Map([
      ...(knowledgeBases.status === "fulfilled" ? list(knowledgeBases.value, "knowledge_bases", "bases") : []),
      ...pinnedKnowledge,
    ].map((item) => [item.id || item.knowledge_id, item])).values()).filter((item) => item.id || item.knowledge_id);
    const sandboxReadiness = readiness.status === "fulfilled" ? readiness.value?.checks?.sandbox || {} : {};
    const networkModes = sandboxNetworkModes(sandboxReadiness);
    const selectedNetworkMode = networkModes.includes(agent.runtime_snapshot?.network_mode)
      ? agent.runtime_snapshot.network_mode
      : "disabled";
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-editor-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card" data-agent-draft-form><header><div><small>Review before publish</small><h2>${agent.id ? "Edit agent draft" : "Create agent"}</h2></div><button type="button" data-close aria-label="Close">${icon("x")}</button></header><label><span>Name</span><input name="name" required /></label><label><span>Description</span><textarea name="description" rows="2"></textarea></label><div class="agent-runtime-editor"><label><span>App type</span><select name="app_kind"><option value="agent">Agent</option><option value="workflow">Workflow app</option></select></label><label><span>Write actions</span><select name="write_autonomy"><option value="approval_required">Require approval</option><option value="full_auto">Full auto</option></select></label></div><label><span>Instructions</span><textarea name="instructions" rows="6" required></textarea></label><label><span>Output format</span><input name="output_format" placeholder="Report, table, artifact set…" /></label><label><span>Input JSON schema</span><textarea name="input_schema" data-json-field rows="6">${JSON.stringify(agent.input_schema || { type: "object", properties: { request: { type: "string" } }, required: ["request"] }, null, 2)}</textarea></label><div class="agent-runtime-editor"><label><span>Run mode</span><select name="autonomy_mode"><option value="workflow">Guarded workflow</option><option value="autonomous">Autonomous</option></select></label><label><span>Network policy</span><select name="network_mode">${networkModes.map((mode) => `<option value="${mode}">${mode === "open" ? "Open" : "Disabled"}</option>`).join("")}</select></label><label><span>Browser profile</span><select name="browser_profile_id" data-agent-browser-profile-options><option value="">Workspace default</option></select></label><label><span>Runtime image</span><input name="runtime_image" placeholder="python:3.12-slim" /></label><label><span>Timeout seconds</span><input name="timeout_seconds" type="number" min="1" max="86400" /></label></div><fieldset class="agent-reference-picker"><legend>Tools <small>Only grant capabilities this Agent needs</small></legend><div><label><input type="checkbox" name="sandbox_enabled" /><span><strong>Code sandbox</strong><small>Network access follows the selected provider policy.</small></span></label></div></fieldset><fieldset class="agent-reference-picker"><legend>Skills <small>Pinned to the installed version</small></legend><div data-agent-skill-options></div></fieldset><fieldset class="agent-reference-picker"><legend>Connectors <small>Only enabled workspace connections</small></legend><div data-agent-connector-options></div></fieldset><fieldset class="agent-reference-picker"><legend>Knowledge <small>Limit retrieval to selected knowledge bases</small></legend><div data-agent-knowledge-options></div></fieldset><fieldset class="agent-reference-picker"><legend>Reference files <small>Materialized into every fresh run</small></legend><div data-agent-reference-options></div></fieldset><div class="agent-draft-bindings"><span data-agent-skill-count>${pinnedSkills.length} skills</span><span data-agent-connector-count>${pinnedConnectors.length} connectors</span><span data-agent-knowledge-count>${pinnedKnowledge.length} knowledge bases</span><span data-agent-reference-count>${list(agent.reference_files || agent.files, "items").length} files</span></div><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save & publish</button></footer></form>`;
    document.body.append(dialog);
    const engineLabel = document.createElement("label");
    engineLabel.innerHTML = `<span>Agent Engine</span><select name="engine_connection_id"><option value="">Taroai Native</option></select>`;
    const engineSelect = engineLabel.querySelector("select");
    for (const connection of this.draftEngineConnections) {
      const option = document.createElement("option");
      option.value = connection.id;
      option.dataset.engineType = connection.engine_type;
      option.textContent = `${connection.name} · ${connection.engine_type}`;
      engineSelect.append(option);
    }
    dialog.querySelector(".agent-runtime-editor").prepend(engineLabel);
    const repositoryLabel = document.createElement("label");
    repositoryLabel.innerHTML = `<span>Repository</span><select name="repository_id"><option value="">No repository</option></select>`;
    for (const repository of this.draftRepositories) {
      const option = document.createElement("option"); option.value = repository.id; option.textContent = `${repository.name} · ${repository.default_branch}`; repositoryLabel.querySelector("select").append(option);
    }
    const branchLabel = document.createElement("label"); branchLabel.innerHTML = `<span>Coding branch</span><input name="coding_branch" placeholder="Generated per Run" />`;
    dialog.querySelector(".agent-runtime-editor").prepend(repositoryLabel, branchLabel);
    const evaluationLabel = document.createElement("label");
    evaluationLabel.innerHTML = `<span>Release evaluation</span><select name="evaluation_suite"><option value="">No release gate</option></select>`;
    for (const record of this.draftEvaluationSuites) {
      const suite = record.suite || record;
      const option = document.createElement("option");
      option.value = `${suite.id}@@${suite.version}`;
      option.textContent = `${suite.id} · ${suite.version} · ${suite.cases?.length || 0} cases`;
      evaluationLabel.querySelector("select").append(option);
    }
    dialog.querySelector(".agent-runtime-editor").prepend(evaluationLabel);
    dialog.querySelector("[name='name']").value = agent.name || "";
    dialog.querySelector("[name='description']").value = agent.description || "";
    dialog.querySelector("[name='instructions']").value = agent.instructions || "";
    dialog.querySelector("[name='output_format']").value = agent.output_contract?.format || agent.output_format || "";
    dialog.querySelector("[name='app_kind']").value = agent.app_kind || "agent";
    dialog.querySelector("[name='write_autonomy']").value = agent.write_autonomy || "approval_required";
    dialog.querySelector("[name='autonomy_mode']").value = agent.runtime_snapshot?.autonomy_mode || "workflow";
    dialog.querySelector("[name='network_mode']").value = selectedNetworkMode;
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
    engineSelect.value = agent.runtime_snapshot?.engine_connection_id || "";
    repositoryLabel.querySelector("select").value = agent.runtime_snapshot?.repository_id || "";
    branchLabel.querySelector("input").value = agent.runtime_snapshot?.branch || "";
    evaluationLabel.querySelector("select").value = agent.runtime_snapshot?.evaluation_suite_id
      ? `${agent.runtime_snapshot.evaluation_suite_id}@@${agent.runtime_snapshot.evaluation_suite_version}`
      : "";
    const snapshot = agent.runtime_snapshot || {};
    dialog.querySelector("[name='sandbox_enabled']").checked = snapshot.sandbox_enabled ?? Boolean(
      snapshot.source_run_id || snapshot.image || snapshot.repository_id || snapshot.files?.length || agent.reference_files?.length
    );
    const selectedSkills = new Set(pinnedSkills.map(skillId));
    const skillOptions = dialog.querySelector("[data-agent-skill-options]");
    if (!this.draftSkills.length) {
      const empty = document.createElement("p");
      empty.textContent = "No enabled workspace skills are available.";
      skillOptions.append(empty);
    }
    for (const skill of this.draftSkills) {
      const id = skillId(skill);
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      const small = document.createElement("small");
      checkbox.type = "checkbox";
      checkbox.name = "skill_binding";
      checkbox.value = id;
      checkbox.checked = selectedSkills.has(id);
      strong.textContent = skill.name || skill.manifest?.name || id;
      small.textContent = `v${skill.version || skill.installed_version || skill.manifest?.version}`;
      copy.append(strong, small);
      label.append(checkbox, copy);
      skillOptions.append(label);
    }
    skillOptions.addEventListener("change", () => {
      const count = skillOptions.querySelectorAll("input:checked").length;
      dialog.querySelector("[data-agent-skill-count]").textContent = `${count} pinned skills`;
    });
    const connectorOptions = dialog.querySelector("[data-agent-connector-options]");
    if (!this.draftConnectors.length) connectorOptions.innerHTML = "<p>No enabled workspace connectors are available.</p>";
    for (const connector of this.draftConnectors) {
      const id = connector.id || connector.connector_id;
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      const small = document.createElement("small");
      checkbox.type = "checkbox";
      checkbox.name = "connector_binding";
      checkbox.value = id;
      checkbox.checked = pinnedConnectorIds.has(id);
      strong.textContent = connector.display_name || connector.name || id;
      small.textContent = `${connector.type || "connector"} · ${connector.status || "pinned"}`;
      copy.append(strong, small);
      label.append(checkbox, copy);
      connectorOptions.append(label);
    }
    connectorOptions.addEventListener("change", () => {
      dialog.querySelector("[data-agent-connector-count]").textContent = `${connectorOptions.querySelectorAll("input:checked").length} connectors`;
    });
    const knowledgeOptions = dialog.querySelector("[data-agent-knowledge-options]");
    if (!this.draftKnowledgeBases.length) knowledgeOptions.innerHTML = "<p>No workspace knowledge bases are available.</p>";
    for (const knowledge of this.draftKnowledgeBases) {
      const id = knowledge.id || knowledge.knowledge_id;
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      const small = document.createElement("small");
      checkbox.type = "checkbox";
      checkbox.name = "knowledge_binding";
      checkbox.value = id;
      checkbox.checked = pinnedKnowledgeIds.has(id);
      strong.textContent = knowledge.name || id;
      small.textContent = knowledge.description || "Workspace knowledge base";
      copy.append(strong, small);
      label.append(checkbox, copy);
      knowledgeOptions.append(label);
    }
    knowledgeOptions.addEventListener("change", () => {
      dialog.querySelector("[data-agent-knowledge-count]").textContent = `${knowledgeOptions.querySelectorAll("input:checked").length} knowledge bases`;
    });
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
    const skill_bindings = data.getAll("skill_binding").map((id) => {
      const skill = this.draftSkills.find((item) => skillId(item) === id);
      return {
        id,
        version: skill.version || skill.installed_version || skill.manifest?.version,
        package_digest: skill.package_digest,
        source_digest: skill.source_digest,
      };
    });
    const connector_bindings = data.getAll("connector_binding").map((id) => ({ id }));
    const knowledge_bindings = data.getAll("knowledge_binding").map((id) => ({ id }));
    const [evaluationSuiteId, evaluationSuiteVersion] = String(data.get("evaluation_suite") || "").split("@@");
    const version = {
      input_schema,
      output_contract: { type: "string", format: data.get("output_format") || "markdown" },
      instructions: data.get("instructions"),
      skill_bindings,
      connector_bindings,
      knowledge_bindings,
      reference_files,
      model_policy: {},
      runtime_snapshot: {
        ...(agent.runtime_snapshot || {}),
        autonomy_mode: data.get("autonomy_mode") || "workflow",
        network_mode: data.get("network_mode") || "disabled",
        sandbox_enabled: data.has("sandbox_enabled"),
        image: data.get("runtime_image") || undefined,
        timeout_seconds: Number(data.get("timeout_seconds") || 300),
        browser_profile_id: data.get("browser_profile_id") || undefined,
        engine_connection_id: data.get("engine_connection_id") || undefined,
        engine_type: form.elements.engine_connection_id?.selectedOptions?.[0]?.dataset.engineType || "native",
        repository_id: data.get("repository_id") || undefined,
        branch: data.get("coding_branch") || undefined,
        evaluation_suite_id: evaluationSuiteId || undefined,
        evaluation_suite_version: evaluationSuiteVersion || undefined,
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
          app_kind: data.get("app_kind"),
          write_autonomy: data.get("write_autonomy"),
        }, { scope: "agent-definition-update" });
        const createdVersion = await this.api.post(`/api/agents/${encodeURIComponent(agentId)}/versions`, { version }, { scope: "agent-version-create" });
        versionNumber = createdVersion.version;
      } else {
        const created = await this.api.post("/api/agents", {
          workspace_id: this.api.settings().workspaceId,
          name: data.get("name"),
          description: data.get("description"),
          app_kind: data.get("app_kind"),
          write_autonomy: data.get("write_autonomy"),
          version,
        }, { scope: "agent-create" });
        agentId = created.agent.id;
        versionNumber = created.version.version;
      }
      if (evaluationSuiteId && evaluationSuiteVersion) {
        const evaluation = await this.api.post(`/api/evaluations/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionNumber)}/run`, {
          suite_id: evaluationSuiteId,
          suite_version: evaluationSuiteVersion,
        }, { scope: "agent-evaluation" });
        if (!evaluation.promotion_gate?.allowed) throw new Error(`Evaluation blocked publication: ${(evaluation.promotion_gate?.reasons || []).join(", ")}`);
      }
      await this.api.post(`/api/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionNumber)}/publish`, {}, { scope: "agent-publish" });
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
      dialog?.close(); this.toast("Agent published", "success"); await this.load();
    } catch (error) { submit.disabled = false; this.toast(error.message, "error"); }
  }

  async restore(version) {
    if (!this.selected || !window.confirm(`Restore version ${version}? A new version will be created.`)) return;
    try {
      const id = this.selected.id || this.selected.agent_id;
      await this.api.post(`/api/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/restore`, {}, { scope: "agent-restore" });
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
      this.toast(`Version ${version} restored`, "success"); await this.select(id);
    } catch (error) { this.toast(error.message, "error"); }
  }

  async publishDraft(version) {
    if (!this.selected) return;
    try {
      const id = this.selected.id || this.selected.agent_id;
      await this.api.post(`/api/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/publish`, {}, { scope: "agent-publish" });
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
      this.toast(`Version ${version} published`, "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async evaluateVersion(version) {
    const agent = this.detail || this.selected;
    const suiteId = agent?.runtime_snapshot?.evaluation_suite_id;
    const suiteVersion = agent?.runtime_snapshot?.evaluation_suite_version;
    if (!suiteId || !suiteVersion) return this.toast("Bind an evaluation suite in the Agent draft first", "error");
    try {
      const id = this.selected.id || this.selected.agent_id;
      await this.api.post(`/api/evaluations/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/run`, { suite_id: suiteId, suite_version: suiteVersion }, { scope: "agent-evaluation" });
      this.toast("Evaluation completed", "success");
      await this.select(id);
      this.switchTab("evaluation");
    } catch (error) { this.toast(error.message, "error"); }
  }

  async promoteBaseline(runId) {
    try {
      await this.api.post(`/api/evaluations/runs/${encodeURIComponent(runId)}/baseline`, {}, { scope: "evaluation-baseline" });
      this.toast("Evaluation baseline promoted", "success");
    } catch (error) { this.toast(error.message, "error"); }
  }

  async openEvidence(runId) {
    try {
      const evidence = await this.api.get(`/api/evaluations/runs/${encodeURIComponent(runId)}/evidence`);
      const dialog = document.createElement("dialog");
      dialog.className = "chat-dialog agent-editor-dialog";
      dialog.innerHTML = `<div class="chat-dialog-card evaluation-evidence-dialog"><header><div><small>Redaction-safe record</small><h2>Evaluation evidence</h2></div><button type="button" data-close aria-label="Close">${icon("x")}</button></header><pre></pre><footer><button type="button" data-close>Close</button></footer></div>`;
      dialog.querySelector("pre").textContent = JSON.stringify(evidence, null, 2);
      dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
      dialog.addEventListener("close", () => dialog.remove());
      document.body.append(dialog); dialog.showModal();
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

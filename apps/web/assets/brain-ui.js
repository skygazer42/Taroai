import { chatApi } from "./chat-api.js?v=20260722-flow115";

function asArray(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return Array.isArray(value?.items) ? value.items : [];
}

export class AgentBrainUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.connectors = [];
    this.skills = [];
    this.memories = [];
    this.browserProfiles = [];
    this.browserSessions = [];
    this.engineConnections = [];
    this.engineSessions = [];
    this.repositories = [];
    this.codingWorkspaces = [];
    this.selectedConnectorId = null;
    this.selectedBrowserProfileId = null;
    this.selectedEngineConnectionId = null;
    this.selectedRepositoryId = null;
    this.tab = "connectors";
    this.boundRoute = () => this.route();
    this.boundMessage = (event) => this.oauthCompleted(event);
  }

  init() {
    window.addEventListener("hashchange", this.boundRoute);
    window.addEventListener("message", this.boundMessage);
    this.root?.addEventListener("click", (event) => this.click(event));
    this.route();
  }

  route() {
    const [route, requestedTab] = window.location.hash.replace(/^#/, "").split("/");
    const active = route === "brain";
    if (!active) {
      if (this.root?.dataset.owner === "brain") {
        this.root.hidden = true;
        this.root.replaceChildren();
        delete this.root.dataset.owner;
        document.querySelector("[data-app='taroai-workspace']")?.removeAttribute("data-rich-route");
      }
      return;
    }
    this.root.dataset.owner = "brain";
    this.root.hidden = false;
    document.querySelector("[data-app='taroai-workspace']")?.setAttribute("data-rich-route", "brain");
    this.renderShell();
    this.switchTab(["connectors", "skills", "memory", "secrets", "browser", "engines", "repositories"].includes(requestedTab) ? requestedTab : "connectors");
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page agent-brain-page">
        <header class="capability-page-header">
          <div><p>Workspace capabilities</p><h1>Agent Brain</h1><span>Control the skills and connected services available to every agent turn.</span></div>
          <div class="capability-header-actions"><button type="button" class="primary" data-mcp-create>Add MCP server</button><button type="button" data-brain-refresh>Refresh</button></div>
        </header>
        <nav class="skill-detail-tabs" aria-label="Agent Brain sections">
          <button class="is-active" data-brain-tab="connectors">Connectors</button>
          <button data-brain-tab="skills">Skills</button>
          <button data-brain-tab="memory">Memory</button>
          <button data-brain-tab="secrets">Secrets</button>
          <button data-brain-tab="browser">Browser</button>
          <button data-brain-tab="engines">Engines</button>
          <button data-brain-tab="repositories">Repositories</button>
        </nav>
        <section data-brain-panel="connectors" class="capability-split brain-connectors">
          <aside class="capability-list" data-connector-list><div class="route-loading">Loading connectors…</div></aside>
          <article class="capability-detail" data-connector-detail><div class="route-empty"><span>C</span><strong>Select a connector</strong><p>Inspect authorization, capabilities, and workspace availability.</p></div></article>
        </section>
        <section data-brain-panel="skills" hidden></section>
        <section data-brain-panel="memory" hidden></section>
        <section data-brain-panel="secrets" hidden></section>
        <section data-brain-panel="browser" hidden></section>
        <section data-brain-panel="engines" hidden></section>
        <section data-brain-panel="repositories" hidden></section>
        <div class="route-toast" data-brain-toast hidden></div>
      </section>`;
    this.renderStaticPanels();
  }

  async load() {
    const workspace = encodeURIComponent(this.api.settings().workspaceId);
    const user = encodeURIComponent(this.api.settings().userId);
    const [connectors, skills, browserProfiles, browserSessions, engineConnections, engineSessions, repositories, codingWorkspaces, memories] = await Promise.allSettled([
      this.api.get(`/api/connectors?workspace_id=${workspace}`),
      this.api.get(`/api/workspaces/${workspace}/skills`),
      this.api.get(`/api/browser/profiles?workspace_id=${workspace}`),
      this.api.get(`/api/browser/profile-sessions?workspace_id=${workspace}`),
      this.api.get(`/api/agent-engines/connections?workspace_id=${workspace}`),
      this.api.get(`/api/agent-engines/sessions?workspace_id=${workspace}`),
      this.api.get(`/api/repositories?workspace_id=${workspace}`),
      this.api.get(`/api/coding-workspaces?workspace_id=${workspace}`),
      this.api.get(`/api/memory?scope_type=user&scope_id=${user}`),
    ]);
    this.connectors = connectors.status === "fulfilled" ? asArray(connectors.value, "connectors") : [];
    this.skills = skills.status === "fulfilled" ? asArray(skills.value, "skills") : [];
    this.browserProfiles = browserProfiles.status === "fulfilled" ? asArray(browserProfiles.value, "profiles") : [];
    this.browserSessions = browserSessions.status === "fulfilled" ? asArray(browserSessions.value, "sessions") : [];
    this.engineConnections = engineConnections.status === "fulfilled" ? asArray(engineConnections.value, "connections") : [];
    this.engineSessions = engineSessions.status === "fulfilled" ? asArray(engineSessions.value, "sessions") : [];
    this.repositories = repositories.status === "fulfilled" ? asArray(repositories.value, "repositories") : [];
    this.codingWorkspaces = codingWorkspaces.status === "fulfilled" ? asArray(codingWorkspaces.value, "coding_workspaces") : [];
    this.memories = memories.status === "fulfilled" ? asArray(memories.value, "memories") : [];
    if (!this.selectedConnectorId || !this.connectors.some((item) => item.id === this.selectedConnectorId)) {
      this.selectedConnectorId = this.connectors[0]?.id || null;
    }
    if (!this.selectedBrowserProfileId || !this.browserProfiles.some((item) => item.id === this.selectedBrowserProfileId)) {
      this.selectedBrowserProfileId = this.browserProfiles.find((item) => item.is_default)?.id || this.browserProfiles[0]?.id || null;
    }
    if (!this.selectedEngineConnectionId || !this.engineConnections.some((item) => item.id === this.selectedEngineConnectionId)) {
      this.selectedEngineConnectionId = this.engineConnections[0]?.id || null;
    }
    if (!this.selectedRepositoryId || !this.repositories.some((item) => item.id === this.selectedRepositoryId)) this.selectedRepositoryId = this.repositories[0]?.id || null;
    this.renderConnectors();
    this.renderStaticPanels();
    if (connectors.status === "rejected") this.toast(connectors.reason?.message || "Connectors are unavailable", "error");
  }

  renderConnectors() {
    const list = this.root.querySelector("[data-connector-list]");
    const detail = this.root.querySelector("[data-connector-detail]");
    if (!list || !detail) return;
    list.replaceChildren();
    if (!this.connectors.length) {
      list.innerHTML = `<div class="route-empty compact"><span>C</span><strong>No connectors yet</strong><p>Add an MCP server to make its tools available to agents.</p></div>`;
      detail.innerHTML = `<div class="route-empty"><span>+</span><strong>Connect an MCP server</strong><p>Tool permissions are discovered before the connector is enabled.</p><button type="button" class="primary" data-mcp-create>Add MCP server</button></div>`;
      return;
    }
    for (const connector of this.connectors) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "capability-list-item";
      button.classList.toggle("is-active", connector.id === this.selectedConnectorId);
      button.dataset.connectorId = connector.id;
      button.innerHTML = `<span class="capability-glyph">C</span><span><strong></strong><small></small><em></em></span><i data-state="${connector.status === "enabled" ? "enabled" : "disabled"}"></i>`;
      button.querySelector("strong").textContent = connector.display_name;
      button.querySelector("small").textContent = connector.type.replaceAll("_", " ");
      button.querySelector("em").textContent = `${connector.auth_mode} · ${connector.status}`;
      list.append(button);
    }
    const connector = this.connectors.find((item) => item.id === this.selectedConnectorId);
    if (!connector) return;
    const capabilities = asArray(connector.capabilities);
    const oauth = connector.auth_mode === "oauth2";
    const enabled = connector.status === "enabled";
    detail.innerHTML = `
      <header class="capability-detail-header">
        <div><span class="capability-glyph large">C</span><div><small>${connector.type.replaceAll("_", " ")}</small><h2></h2><p>${connector.id}</p></div></div>
        <div>${oauth && !enabled ? `<button class="primary" data-connector-connect>${connector.status === "needs_reauth" ? "Reconnect" : "Connect"}</button>` : ""}<button data-connector-toggle>${enabled ? "Disable" : oauth ? "Enable after authorization" : "Enable"}</button></div>
      </header>
      <div class="skill-evidence-strip"><div><small>Status</small><strong>${connector.status}</strong></div><div><small>Authorization</small><strong>${connector.auth_mode}</strong></div><div><small>Capabilities</small><strong>${capabilities.length}</strong></div><div><small>Sensitivity</small><strong>${connector.sensitivity_level}</strong></div></div>
      <section class="connector-capability-grid"><header><h3>Available actions</h3><p>Only enabled capabilities are exposed to the Agent Loop.</p></header><div data-connector-capabilities></div></section>`;
    detail.querySelector("h2").textContent = connector.display_name;
    const capabilityList = detail.querySelector("[data-connector-capabilities]");
    for (const capability of capabilities) {
      const card = document.createElement("article");
      card.className = "connector-capability-card";
      const scopes = asArray(capability.required_scopes);
      card.innerHTML = `<header><strong></strong><span data-risk="${capability.risk_level}">${capability.risk_level}</span></header><p></p><small>${capability.approval_required ? "Approval required" : "No extra approval"}</small>`;
      card.querySelector("strong").textContent = capability.name;
      card.querySelector("p").textContent = scopes.length ? scopes.join(" · ") : "No additional scopes";
      capabilityList.append(card);
    }
  }

  renderStaticPanels() {
    const skills = this.root.querySelector("[data-brain-panel='skills']");
    const memory = this.root.querySelector("[data-brain-panel='memory']");
    const secrets = this.root.querySelector("[data-brain-panel='secrets']");
    const browser = this.root.querySelector("[data-brain-panel='browser']");
    const engines = this.root.querySelector("[data-brain-panel='engines']");
    const repositories = this.root.querySelector("[data-brain-panel='repositories']");
    if (skills) skills.innerHTML = `<div class="brain-summary-card"><span>S</span><div><h2>${this.skills.length} workspace skills</h2><p>Inspect SKILL.md, package files, evaluations, and pinned versions.</p><button data-open-skills>Manage skills</button></div></div>`;
    if (memory) this.renderMemory(memory);
    if (secrets) secrets.innerHTML = `<div class="brain-summary-card"><span>K</span><div><h2>Secrets</h2><p>Connector credentials remain in the Secret Vault and are issued to tools as short-lived leases.</p></div></div>`;
    if (browser) this.renderBrowser(browser);
    if (engines) this.renderEngines(engines);
    if (repositories) this.renderRepositories(repositories);
  }

  openMcpConnectorEditor() {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card">
      <header><div><small>Model Context Protocol</small><h2>Add MCP server</h2></div><button type="button" data-close aria-label="Close">×</button></header>
      <p>Taroai connects over Streamable HTTP and discovers the server's tools before enabling it.</p>
      <label><span>Name</span><input name="display_name" maxlength="160" autocomplete="off" placeholder="Company tools" required /></label>
      <label><span>Server URL</span><input name="url" type="url" inputmode="url" autocomplete="url" placeholder="https://mcp.example.com/mcp" required /></label>
      <label><span>Secret reference ID <small>Optional · sent as a Bearer token</small></span><input name="secret_ref_id" autocomplete="off" placeholder="Secret Vault reference" /></label>
      <footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Connect</button></footer>
    </form>`;
    document.body.append(dialog);
    dialog.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) dialog.close();
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const secretRefId = String(data.get("secret_ref_id") || "").trim();
      const submit = event.currentTarget.querySelector("[type='submit']");
      submit.disabled = true;
      submit.textContent = "Discovering tools…";
      let created = null;
      try {
        created = await this.api.post("/api/connectors", {
          workspace_id: this.api.settings().workspaceId,
          type: "mcp_server",
          display_name: String(data.get("display_name") || "").trim(),
          auth_mode: secretRefId ? "api_key" : "none",
          ...(secretRefId ? { credential: { secret_ref_id: secretRefId, required_actions: ["mcp.call"] } } : {}),
          metadata: { mcp: { url: String(data.get("url") || "").trim() } },
        }, { scope: "mcp-create" });
        this.selectedConnectorId = created.id;
        await this.api.post(`/api/connectors/${encodeURIComponent(created.id)}/enable`, {}, { scope: "mcp-enable" });
        dialog.close();
        await this.load();
        await window.taroaiChat?.loadCapabilities?.();
        this.toast("MCP server connected", "success");
      } catch (error) {
        if (created) {
          dialog.close();
          await this.load();
          this.toast(`MCP server saved as a draft: ${error.message}`, "warning");
          return;
        }
        submit.disabled = false;
        submit.textContent = "Connect";
        this.toast(error.message || "MCP server could not be added", "error");
      }
    });
    dialog.showModal();
    dialog.querySelector("input")?.focus();
  }

  renderMemory(root) {
    root.innerHTML = `
      <section class="brain-memory-ledger">
        <header>
          <div><small>Personal context</small><h2>Long-term memory</h2><p>Saved details can shape future replies. New memories always require your approval.</p></div>
          <button type="button" data-memory-chat>Ask Chat to remember</button>
        </header>
        <div data-memory-list></div>
      </section>`;
    const list = root.querySelector("[data-memory-list]");
    if (!this.memories.length) {
      list.innerHTML = `<div class="route-empty compact"><span>M</span><strong>No saved memories</strong><p>Ask Chat to remember a stable preference when you want it carried into future conversations.</p><button data-memory-chat>Save a preference</button></div>`;
      return;
    }
    for (const memory of [...this.memories].reverse()) {
      const row = document.createElement("article");
      row.className = "brain-memory-row";
      const heading = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = memory.metadata?.source === "explicit_agent_save" ? "Saved from Chat" : "Reviewed memory";
      const createdAt = new Date(memory.created_at || "");
      const date = Number.isNaN(createdAt.valueOf())
        ? "Date unavailable"
        : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(createdAt);
      const meta = document.createElement("small");
      meta.textContent = memory.expires_at ? `${date} · Expires ${new Date(memory.expires_at).toLocaleDateString()}` : `${date} · No expiry`;
      heading.append(title, meta);
      const content = document.createElement("p");
      content.textContent = memory.content;
      const forget = document.createElement("button");
      forget.type = "button";
      forget.className = "brain-memory-forget";
      forget.dataset.memoryForget = memory.id;
      forget.textContent = "Forget";
      forget.setAttribute("aria-label", `Forget memory: ${memory.content.slice(0, 80)}`);
      row.append(heading, content, forget);
      list.append(row);
    }
  }

  async forgetMemory(memoryId) {
    if (!window.confirm("Forget this memory? It will no longer shape future replies.")) return;
    const previous = this.memories;
    this.memories = this.memories.filter((memory) => memory.id !== memoryId);
    this.renderStaticPanels();
    try {
      await this.api.delete(`/api/memory/${encodeURIComponent(memoryId)}`);
      this.toast("Memory forgotten", "success");
    } catch (error) {
      this.memories = previous;
      this.renderStaticPanels();
      this.toast(error.message || "Memory could not be forgotten. Try again.", "error");
    }
  }

  click(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.brainTab) return this.switchTab(button.dataset.brainTab);
    if (button.dataset.connectorId) {
      this.selectedConnectorId = button.dataset.connectorId;
      return this.renderConnectors();
    }
    if (button.matches("[data-brain-refresh]")) return this.load();
    if (button.matches("[data-mcp-create]")) return this.openMcpConnectorEditor();
    if (button.matches("[data-connector-connect]")) return this.connect();
    if (button.matches("[data-connector-toggle]")) return this.toggle();
    if (button.matches("[data-open-skills]")) window.location.hash = "skills";
    if (button.matches("[data-memory-chat]")) this.prefill("请记住：");
    if (button.dataset.memoryForget) return this.forgetMemory(button.dataset.memoryForget);
    if (button.matches("[data-brain-start-browser]")) this.prefill("Use the browser to ");
    if (button.matches("[data-browser-profile-create]")) return this.openBrowserProfileEditor();
    if (button.dataset.browserProfileId) {
      this.selectedBrowserProfileId = button.dataset.browserProfileId;
      return this.renderStaticPanels();
    }
    if (button.matches("[data-browser-profile-edit]")) return this.openBrowserProfileEditor(this.selectedBrowserProfile());
    if (button.matches("[data-browser-profile-default]")) return this.updateBrowserProfile({ is_default: true });
    if (button.matches("[data-browser-profile-disable]")) return this.disableBrowserProfile();
    if (button.matches("[data-browser-session-open]")) return this.openBrowserSession();
    if (button.dataset.browserSessionClose) return this.closeBrowserSession(button.dataset.browserSessionClose);
    if (button.dataset.browserSessionNavigate) return this.browserSessionAction(button.dataset.browserSessionNavigate, "navigate");
    if (button.dataset.browserSessionScreenshot) return this.browserSessionAction(button.dataset.browserSessionScreenshot, "screenshot");
    if (button.matches("[data-engine-create]")) return this.openEngineEditor();
    if (button.dataset.engineConnectionId) { this.selectedEngineConnectionId = button.dataset.engineConnectionId; return this.renderStaticPanels(); }
    if (button.matches("[data-engine-disable]")) return this.updateEngineConnection({ status: "disabled" });
    if (button.matches("[data-engine-session-start]")) return this.startEngineSession();
    if (button.dataset.engineSessionCancel) return this.controlEngineSession(button.dataset.engineSessionCancel, "cancel");
    if (button.dataset.engineSessionResume) return this.controlEngineSession(button.dataset.engineSessionResume, "resume");
    if (button.dataset.engineSessionClose) return this.controlEngineSession(button.dataset.engineSessionClose, "close");
    if (button.dataset.engineSessionSteer) return this.steerEngineSession(button.dataset.engineSessionSteer);
    if (button.dataset.engineSessionEvents) return this.openEngineEvents(button.dataset.engineSessionEvents);
    if (button.dataset.engineApproval) return this.decideEngineApproval(button.dataset.engineSession, button.dataset.engineApproval, button.dataset.engineDecision);
    if (button.matches("[data-repository-create]")) return this.openRepositoryEditor();
    if (button.dataset.repositoryId) { this.selectedRepositoryId = button.dataset.repositoryId; return this.renderStaticPanels(); }
    if (button.matches("[data-repository-disable]")) return this.updateRepository({ status: "disabled" });
  }

  renderRepositories(root) {
    const selected = this.repositories.find((item) => item.id === this.selectedRepositoryId) || null;
    root.innerHTML = `<section class="repository-workspace"><aside><header><div><small>Source control</small><h2>Repositories</h2></div><button class="primary" data-repository-create>Connect</button></header><div data-repository-list></div></aside><article data-repository-detail></article></section>`;
    const list = root.querySelector("[data-repository-list]");
    if (!this.repositories.length) list.innerHTML = `<div class="route-empty compact"><span>R</span><strong>No repositories</strong><p>Connect a governed Git repository for coding Agents.</p></div>`;
    for (const repository of this.repositories) {
      const button = document.createElement("button"); button.type = "button"; button.className = "repository-row"; button.classList.toggle("is-active", repository.id === this.selectedRepositoryId); button.dataset.repositoryId = repository.id;
      button.innerHTML = `<span>R</span><div><strong></strong><small></small></div><i data-state="${repository.status}"></i>`; button.querySelector("strong").textContent = repository.name; button.querySelector("small").textContent = `${repository.provider} · ${repository.default_branch}`; list.append(button);
    }
    const detail = root.querySelector("[data-repository-detail]");
    if (!selected) { detail.innerHTML = `<div class="route-empty"><span>R</span><strong>Connect a repository</strong><p>Credentials stay in the selected Connector; Coding Workspaces receive a run-scoped checkout.</p><button data-repository-create>Connect repository</button></div>`; return; }
    const sessions = this.codingWorkspaces.filter((item) => item.repository_id === selected.id);
    detail.innerHTML = `<header class="repository-heading"><div><span>R</span><div><small>${selected.provider}</small><h2></h2><p></p></div></div><button class="danger" data-repository-disable ${selected.status !== "active" ? "disabled" : ""}>Disable</button></header><div class="engine-facts"><div><small>Default branch</small><strong>${selected.default_branch}</strong></div><div><small>Authentication</small><strong>${selected.connector_id ? "Connector" : "Public HTTPS"}</strong></div><div><small>Coding sessions</small><strong>${sessions.length}</strong></div></div><section class="repository-session-list"><header><h3>Recent worktrees</h3></header><div data-repository-sessions></div></section>`;
    detail.querySelector("h2").textContent = selected.name; detail.querySelector(".repository-heading p").textContent = selected.repository_url;
    const sessionList = detail.querySelector("[data-repository-sessions]");
    for (const item of sessions) { const row = document.createElement("article"); row.className = "coding-evidence-row"; const title = document.createElement("strong"); title.textContent = item.branch; const meta = document.createElement("small"); meta.textContent = `${item.status} · ${item.run_id}`; const body = document.createElement("p"); body.textContent = item.worktree_path; row.append(title, meta, body); sessionList.append(row); }
    if (!sessions.length) sessionList.innerHTML = `<p class="route-note">No Coding Workspaces have used this repository.</p>`;
  }

  openRepositoryEditor() {
    const dialog = document.createElement("dialog"); dialog.className = "chat-dialog repository-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card"><header><div><small>Governed source</small><h2>Connect repository</h2></div><button type="button" data-close>×</button></header><label><span>Name</span><input name="name" required /></label><label><span>Provider</span><select name="provider"><option value="github">GitHub</option><option value="gitlab">GitLab</option><option value="bitbucket">Bitbucket</option><option value="generic">Generic Git</option></select></label><label><span>HTTPS repository URL</span><input name="repository_url" type="url" required placeholder="https://github.com/org/repository" /></label><label><span>Default branch</span><input name="default_branch" value="main" required /></label><label><span>Connector ID</span><input name="connector_id" placeholder="Optional GitHub/GitLab Connector" /></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Connect</button></footer></form>`;
    document.body.append(dialog); dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    dialog.querySelector("form").addEventListener("submit", async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const submit = event.currentTarget.querySelector("[type='submit']"); submit.disabled = true; try { const created = await this.api.post("/api/repositories", { workspace_id: this.api.settings().workspaceId, name: data.get("name"), provider: data.get("provider"), repository_url: data.get("repository_url"), default_branch: data.get("default_branch"), connector_id: String(data.get("connector_id") || "").trim() || null }, { scope: "repository-connect" }); this.selectedRepositoryId = created.id; dialog.close(); this.toast("Repository connected", "success"); await this.load(); await window.taroaiChat?.loadCapabilities?.(); } catch (error) { submit.disabled = false; this.toast(error.message, "error"); } });
    dialog.addEventListener("close", () => dialog.remove()); dialog.showModal();
  }

  async updateRepository(patch) {
    if (!this.selectedRepositoryId) return;
    try { await this.api.patch(`/api/repositories/${encodeURIComponent(this.selectedRepositoryId)}`, patch, { scope: "repository-update" }); this.toast("Repository updated", "success"); await this.load(); await window.taroaiChat?.loadCapabilities?.(); }
    catch (error) { this.toast(error.message, "error"); }
  }

  selectedEngineConnection() {
    return this.engineConnections.find((item) => item.id === this.selectedEngineConnectionId) || null;
  }

  renderEngines(root) {
    const selected = this.selectedEngineConnection();
    root.innerHTML = `<section class="engine-workspace"><aside><header><div><small>Inner loops</small><h2>Agent Engines</h2></div><button class="primary" data-engine-create>Connect</button></header><div data-engine-list></div></aside><article><div data-engine-detail></div><section class="engine-session-list"><header><h3>Engine sessions</h3><span>${this.engineSessions.filter((item) => ["starting", "running", "waiting_approval"].includes(item.status)).length} active</span></header><div data-engine-sessions></div></section></article></section>`;
    const list = root.querySelector("[data-engine-list]");
    if (!this.engineConnections.length) list.innerHTML = `<div class="route-empty compact"><span>E</span><strong>No Engine connections</strong><p>Connect an OpenCode, Codex, or Claude runner. Native execution remains available.</p></div>`;
    for (const connection of this.engineConnections) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "engine-connection-row";
      button.classList.toggle("is-active", connection.id === this.selectedEngineConnectionId);
      button.dataset.engineConnectionId = connection.id;
      button.innerHTML = `<span>${connection.engine_type.slice(0, 1).toUpperCase()}</span><div><strong></strong><small></small></div><i data-state="${connection.status}"></i>`;
      button.querySelector("strong").textContent = connection.name;
      button.querySelector("small").textContent = `${connection.engine_type} · ${connection.capabilities?.length || 0} capabilities`;
      list.append(button);
    }
    const detail = root.querySelector("[data-engine-detail]");
    if (!selected) detail.innerHTML = `<div class="route-empty"><span>E</span><strong>Connect an Agent Engine</strong><p>Taroai governs the session while the selected runner owns its coding loop.</p><button data-engine-create>Connect Engine</button></div>`;
    else {
      detail.innerHTML = `<header class="engine-heading"><div><span>${selected.engine_type.slice(0, 1).toUpperCase()}</span><div><small>${selected.engine_type} runner</small><h2></h2><p></p></div></div><button class="danger" data-engine-disable ${selected.status !== "active" ? "disabled" : ""}>Disable</button></header><div class="engine-facts"><div><small>Status</small><strong>${selected.status}</strong></div><div><small>Authentication</small><strong>${selected.secret_ref_present ? "Secret Vault" : "None"}</strong></div><div><small>Capabilities</small><strong>${selected.capabilities?.length || 0}</strong></div></div><form class="engine-session-launch"><label><span>Task</span><textarea rows="3" data-engine-task placeholder="Implement the requested change and report diff, tests, and artifacts."></textarea></label><label><span>Working directory</span><input data-engine-cwd value="/workspace" /></label><button class="primary" type="button" data-engine-session-start ${selected.status !== "active" ? "disabled" : ""}>Start session</button></form>`;
      detail.querySelector("h2").textContent = selected.name;
      detail.querySelector(".engine-heading p").textContent = selected.endpoint_url || "Taroai native runtime";
    }
    const sessions = root.querySelector("[data-engine-sessions]");
    if (!this.engineSessions.length) sessions.innerHTML = `<p class="route-note">No Engine sessions yet.</p>`;
    for (const session of this.engineSessions) {
      const row = document.createElement("article");
      row.className = "engine-session-row";
      row.innerHTML = `<i data-state="${session.status}"></i><div><strong></strong><small></small></div><input placeholder="Steer this session" data-engine-steer-input="${session.id}" /><div><button data-engine-session-events="${session.id}">Events</button><button data-engine-session-steer="${session.id}" ${session.status !== "running" ? "disabled" : ""}>Steer</button><button data-engine-session-cancel="${session.id}" ${!["starting", "running", "waiting_approval"].includes(session.status) ? "disabled" : ""}>Cancel</button><button data-engine-session-resume="${session.id}" ${session.status !== "cancelled" ? "disabled" : ""}>Resume</button><button data-engine-session-close="${session.id}" ${session.status === "closed" ? "disabled" : ""}>Close</button></div>`;
      const connection = this.engineConnections.find((item) => item.id === session.connection_id);
      row.querySelector("strong").textContent = connection?.name || session.engine_type;
      row.querySelector("small").textContent = `${session.status} · ${session.external_session_id || session.id}`;
      sessions.append(row);
    }
  }

  openEngineEditor() {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog engine-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card"><header><div><small>Remote inner loop</small><h2>Connect Agent Engine</h2></div><button type="button" data-close>×</button></header><label><span>Name</span><input name="name" required /></label><label><span>Engine type</span><select name="engine_type"><option value="opencode">OpenCode Server</option><option value="codex">Codex app-server Runner</option><option value="claude">Claude Agent SDK Runner</option><option value="native">Taroai Native</option></select></label><label><span>Runner endpoint</span><input name="endpoint_url" type="url" placeholder="https://runner.example.com" /></label><label><span>Secret reference ID</span><input name="secret_ref_id" placeholder="Optional Secret Vault reference" /></label><label><span>Capabilities</span><input name="capabilities" placeholder="stream_events, approvals, steering, checkpoints" /></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Connect</button></footer></form>`;
    document.body.append(dialog);
    dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    dialog.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault(); const form = new FormData(event.currentTarget); const submit = event.currentTarget.querySelector("[type='submit']"); submit.disabled = true;
      try {
        const created = await this.api.post("/api/agent-engines/connections", { workspace_id: this.api.settings().workspaceId, name: form.get("name"), engine_type: form.get("engine_type"), endpoint_url: String(form.get("endpoint_url") || "").trim() || null, secret_ref_id: String(form.get("secret_ref_id") || "").trim() || null, capabilities: String(form.get("capabilities") || "").split(",").map((item) => item.trim()).filter(Boolean) }, { scope: "engine-connect" });
        this.selectedEngineConnectionId = created.id; dialog.close(); this.toast("Agent Engine connected", "success"); await this.load();
      } catch (error) { submit.disabled = false; this.toast(error.message, "error"); }
    });
    dialog.addEventListener("close", () => dialog.remove()); dialog.showModal();
  }

  async updateEngineConnection(patch) {
    const connection = this.selectedEngineConnection(); if (!connection) return;
    try { await this.api.patch(`/api/agent-engines/connections/${encodeURIComponent(connection.id)}`, patch, { scope: "engine-update" }); this.toast("Engine connection updated", "success"); await this.load(); }
    catch (error) { this.toast(error.message, "error"); }
  }

  async startEngineSession() {
    const connection = this.selectedEngineConnection(); if (!connection) return;
    const task = this.root.querySelector("[data-engine-task]")?.value.trim(); if (!task) return this.toast("Enter a task for the Engine", "error");
    try { await this.api.post("/api/agent-engines/sessions", { workspace_id: this.api.settings().workspaceId, connection_id: connection.id, task, cwd: this.root.querySelector("[data-engine-cwd]")?.value.trim() || "/workspace" }, { scope: "engine-session" }); this.toast("Engine session started", "success"); await this.load(); }
    catch (error) { this.toast(error.message, "error"); }
  }

  async steerEngineSession(sessionId) {
    const input = this.root.querySelector(`[data-engine-steer-input="${CSS.escape(sessionId)}"]`); const message = input?.value.trim(); if (!message) return;
    try { await this.api.post(`/api/agent-engines/sessions/${encodeURIComponent(sessionId)}/steer`, { message }, { scope: "engine-steer" }); input.value = ""; this.toast("Steering delivered", "success"); await this.load(); }
    catch (error) { this.toast(error.message, "error"); }
  }

  async controlEngineSession(sessionId, operation) {
    try { await this.api.post(`/api/agent-engines/sessions/${encodeURIComponent(sessionId)}/${operation}`, {}, { scope: `engine-${operation}` }); this.toast(`Engine session ${operation} accepted`, "success"); await this.load(); }
    catch (error) { this.toast(error.message, "error"); }
  }

  async openEngineEvents(sessionId) {
    try {
      const payload = await this.api.get(`/api/agent-engines/sessions/${encodeURIComponent(sessionId)}/events?refresh=true`);
      const events = asArray(payload, "events");
      const dialog = document.createElement("dialog"); dialog.className = "chat-dialog engine-events-dialog";
      dialog.innerHTML = `<div class="chat-dialog-card"><header><div><small>Normalized Runner stream</small><h2>Engine events</h2></div><button type="button" data-close>×</button></header><div data-engine-event-list></div><footer><button type="button" data-close>Done</button></footer></div>`;
      const list = dialog.querySelector("[data-engine-event-list]");
      for (const event of events) {
        const row = document.createElement("article"); row.className = "engine-event-row";
        const approvalId = event.payload?.approval_id || event.payload?.id;
        row.innerHTML = `<header><strong></strong><small></small></header><pre></pre>${event.event_type.includes("approval") && approvalId ? `<div><button data-engine-approval="${approvalId}" data-engine-session="${sessionId}" data-engine-decision="approve">Approve</button><button data-engine-approval="${approvalId}" data-engine-session="${sessionId}" data-engine-decision="reject">Reject</button></div>` : ""}`;
        row.querySelector("strong").textContent = event.event_type; row.querySelector("small").textContent = `#${event.sequence}`; row.querySelector("pre").textContent = JSON.stringify(event.payload || {}, null, 2); list.append(row);
      }
      if (!events.length) list.innerHTML = `<p class="route-note">The Runner has not emitted events yet.</p>`;
      document.body.append(dialog); dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close())); dialog.addEventListener("click", (event) => { const button = event.target.closest("[data-engine-approval]"); if (button) this.decideEngineApproval(button.dataset.engineSession, button.dataset.engineApproval, button.dataset.engineDecision); }); dialog.addEventListener("close", () => dialog.remove()); dialog.showModal();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async decideEngineApproval(sessionId, approvalId, decision) {
    try { await this.api.post(`/api/agent-engines/sessions/${encodeURIComponent(sessionId)}/approvals/${encodeURIComponent(approvalId)}`, { decision }, { scope: `engine-approval-${decision}` }); this.toast(`Engine action ${decision}d`, "success"); document.querySelector(".engine-events-dialog")?.close(); await this.load(); }
    catch (error) { this.toast(error.message, "error"); }
  }

  selectedBrowserProfile() {
    return this.browserProfiles.find((item) => item.id === this.selectedBrowserProfileId) || null;
  }

  renderBrowser(root) {
    const profile = this.selectedBrowserProfile();
    const activeSessions = this.browserSessions.filter((item) => item.status === "active");
    root.innerHTML = `
      <section class="browser-profile-workspace">
        <aside class="browser-profile-rail">
          <header><div><small>Persistent identity</small><h2>Browser profiles</h2></div><button class="primary" data-browser-profile-create>New</button></header>
          <div data-browser-profile-list></div>
        </aside>
        <article class="browser-profile-detail">
          <div data-browser-profile-detail></div>
          <section class="browser-session-ledger">
            <header><div><small>Live execution</small><h3>Browser sessions</h3></div><span>${activeSessions.length} active</span></header>
            <div data-browser-session-list></div>
          </section>
        </article>
      </section>`;
    const profileList = root.querySelector("[data-browser-profile-list]");
    if (!this.browserProfiles.length) {
      profileList.innerHTML = `<div class="route-empty compact"><span>B</span><strong>No profiles yet</strong><p>Create a governed browser identity with its own saved cookies and domain boundary.</p></div>`;
    }
    for (const item of this.browserProfiles) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "browser-profile-row";
      button.classList.toggle("is-active", item.id === this.selectedBrowserProfileId);
      button.dataset.browserProfileId = item.id;
      button.innerHTML = `<span class="browser-profile-mark">${item.is_default ? "D" : "B"}</span><span><strong></strong><small></small></span><i data-state="${item.status}"></i>`;
      button.querySelector("strong").textContent = item.name;
      button.querySelector("small").textContent = `${item.allowed_domains?.length || 0} domains · revision ${item.revision || 0}`;
      profileList.append(button);
    }
    const detail = root.querySelector("[data-browser-profile-detail]");
    if (!profile) {
      detail.innerHTML = `<div class="route-empty"><span>B</span><strong>Create a browser profile</strong><p>Profiles keep login state private while Agents receive only a scoped profile reference.</p><button data-browser-profile-create>Create profile</button></div>`;
    } else {
      detail.innerHTML = `
        <header class="browser-profile-heading"><div><span class="browser-profile-mark large">B</span><div><small>${profile.is_default ? "Workspace default" : "Browser profile"}</small><h2></h2><p></p></div></div><div><button data-browser-profile-edit>Edit</button><button data-browser-profile-default ${profile.is_default || profile.status !== "active" ? "disabled" : ""}>Make default</button><button class="danger" data-browser-profile-disable ${profile.status !== "active" ? "disabled" : ""}>Disable</button></div></header>
        <div class="browser-profile-facts"><div><small>State</small><strong>${profile.status}</strong></div><div><small>Saved state</small><strong>${profile.has_saved_state ? `Revision ${profile.revision}` : "Not captured"}</strong></div><div><small>Last used</small><strong>${profile.last_used_at ? new Date(profile.last_used_at).toLocaleString() : "Never"}</strong></div></div>
        <section class="browser-domain-boundary"><header><div><small>Network boundary</small><h3>Allowed domains</h3></div></header><div data-browser-domain-list></div></section>
        <form class="browser-session-launch" data-browser-session-launch><label><span>Start URL</span><input type="url" placeholder="https://example.com" data-browser-start-url /></label><button class="primary" type="button" data-browser-session-open ${profile.status !== "active" ? "disabled" : ""}>Open session</button><button type="button" data-brain-start-browser>Use in Chat</button></form>`;
      detail.querySelector("h2").textContent = profile.name;
      detail.querySelector(".browser-profile-heading p").textContent = profile.description || "Saved browser state for governed Agent work.";
      const domains = detail.querySelector("[data-browser-domain-list]");
      for (const domain of profile.allowed_domains || []) {
        const chip = document.createElement("span");
        chip.textContent = domain;
        domains.append(chip);
      }
      if (!profile.allowed_domains?.length) domains.innerHTML = `<small>All domains permitted by the workspace browser policy.</small>`;
    }
    const sessions = root.querySelector("[data-browser-session-list]");
    if (!this.browserSessions.length) sessions.innerHTML = `<p class="route-note">No browser sessions have been opened in this workspace.</p>`;
    for (const session of this.browserSessions) {
      const item = document.createElement("article");
      item.className = "browser-session-row";
      item.innerHTML = `<span data-browser-session-state="${session.status}"></span><div><strong></strong><small></small></div><label><input type="url" placeholder="Navigate to URL" data-browser-session-url="${session.session_id}" /></label><div><button data-browser-session-navigate="${session.session_id}" ${session.status !== "active" ? "disabled" : ""}>Go</button><button data-browser-session-screenshot="${session.session_id}" ${session.status !== "active" ? "disabled" : ""}>Capture</button><button data-browser-session-close="${session.session_id}" ${session.status !== "active" ? "disabled" : ""}>Close & save</button></div>`;
      const linkedProfile = this.browserProfiles.find((candidate) => candidate.id === session.profile_id);
      item.querySelector("strong").textContent = linkedProfile?.name || "Ephemeral browser";
      item.querySelector("small").textContent = session.current_url || `${session.status} · ${new Date(session.started_at).toLocaleString()}`;
      sessions.append(item);
    }
  }

  openBrowserProfileEditor(profile = null) {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog browser-profile-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card"><header><div><small>Private browser state</small><h2>${profile ? "Edit browser profile" : "Create browser profile"}</h2></div><button type="button" data-close>×</button></header><label><span>Name</span><input name="name" required maxlength="120" /></label><label><span>Description</span><textarea name="description" rows="3"></textarea></label><label><span>Allowed domains</span><textarea name="allowed_domains" rows="5" placeholder="github.com\n*.example.com"></textarea><small>One hostname per line. Leave empty to use the workspace browser policy.</small></label><label class="browser-default-check"><input name="is_default" type="checkbox" /><span>Use as the workspace default profile</span></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">${profile ? "Save changes" : "Create profile"}</button></footer></form>`;
    document.body.append(dialog);
    const form = dialog.querySelector("form");
    form.elements.name.value = profile?.name || "";
    form.elements.description.value = profile?.description || "";
    form.elements.allowed_domains.value = (profile?.allowed_domains || []).join("\n");
    form.elements.is_default.checked = Boolean(profile?.is_default);
    dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const payload = {
        name: String(data.get("name") || "").trim(),
        description: String(data.get("description") || "").trim(),
        allowed_domains: String(data.get("allowed_domains") || "").split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean),
        is_default: data.get("is_default") === "on",
      };
      const submit = form.querySelector("[type='submit']");
      submit.disabled = true;
      try {
        if (profile) await this.api.patch(`/api/browser/profiles/${encodeURIComponent(profile.id)}`, payload, { scope: "browser-profile-update" });
        else {
          const created = await this.api.post("/api/browser/profiles", { ...payload, workspace_id: this.api.settings().workspaceId }, { scope: "browser-profile-create" });
          this.selectedBrowserProfileId = created.id;
        }
        dialog.close();
        this.toast(profile ? "Browser profile updated" : "Browser profile created", "success");
        await this.load();
        await window.taroaiChat?.loadCapabilities?.();
      } catch (error) { submit.disabled = false; this.toast(error.message, "error"); }
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async updateBrowserProfile(patch) {
    const profile = this.selectedBrowserProfile();
    if (!profile) return;
    try {
      await this.api.patch(`/api/browser/profiles/${encodeURIComponent(profile.id)}`, patch, { scope: "browser-profile-update" });
      this.toast("Browser profile updated", "success");
      await this.load();
      await window.taroaiChat?.loadCapabilities?.();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async disableBrowserProfile() {
    const profile = this.selectedBrowserProfile();
    if (!profile) return;
    try {
      await this.api.delete(`/api/browser/profiles/${encodeURIComponent(profile.id)}`, { scope: "browser-profile-disable" });
      this.toast("Browser profile disabled", "success");
      await this.load();
      await window.taroaiChat?.loadCapabilities?.();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async openBrowserSession() {
    const profile = this.selectedBrowserProfile();
    if (!profile) return;
    const startUrl = this.root.querySelector("[data-browser-start-url]")?.value.trim() || null;
    try {
      await this.api.post(`/api/browser/profiles/${encodeURIComponent(profile.id)}/sessions`, { start_url: startUrl }, { scope: "browser-session-open" });
      this.toast("Browser session opened", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async browserSessionAction(sessionId, actionType) {
    const url = this.root.querySelector(`[data-browser-session-url="${CSS.escape(sessionId)}"]`)?.value.trim() || null;
    if (actionType === "navigate" && !url) return this.toast("Enter a URL to navigate", "error");
    try {
      const result = await this.api.post(`/api/browser/profile-sessions/${encodeURIComponent(sessionId)}/actions`, { action_type: actionType, url }, { scope: `browser-${actionType}` });
      this.toast(actionType === "screenshot" ? `Screenshot captured${result.storage_object_id ? ` · ${result.storage_object_id}` : ""}` : "Browser navigated", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async closeBrowserSession(sessionId) {
    try {
      await this.api.delete(`/api/browser/profile-sessions/${encodeURIComponent(sessionId)}`, { scope: "browser-session-close" });
      this.toast("Session state saved to its profile", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  switchTab(tab) {
    this.tab = tab;
    this.root.querySelectorAll("[data-brain-tab]").forEach((item) => item.classList.toggle("is-active", item.dataset.brainTab === tab));
    this.root.querySelectorAll("[data-brain-panel]").forEach((panel) => { panel.hidden = panel.dataset.brainPanel !== tab; });
  }

  async connect() {
    const connector = this.connectors.find((item) => item.id === this.selectedConnectorId);
    if (!connector) return;
    try {
      const result = await this.api.post(`/api/connectors/${encodeURIComponent(connector.id)}/oauth/authorize`, {}, { scope: "connector-authorize" });
      if (!result.authorization_url) throw new Error("Authorization URL was not returned");
      const popup = window.open(result.authorization_url, "connector-authorize", "width=620,height=760");
      if (!popup) throw new Error("Allow popups to connect this service");
      this.toast("Complete authorization in the popup", "loading");
    } catch (error) { this.toast(error.message, "error"); }
  }

  async toggle() {
    const connector = this.connectors.find((item) => item.id === this.selectedConnectorId);
    if (!connector) return;
    if (connector.auth_mode === "oauth2" && connector.status !== "enabled") {
      return this.connect();
    }
    const operation = connector.status === "enabled" ? "disable" : "enable";
    try {
      await this.api.post(`/api/connectors/${encodeURIComponent(connector.id)}/${operation}`, {}, { scope: `connector-${operation}` });
      this.toast(`Connector ${operation}d`, "success");
      await this.load();
      await window.taroaiChat?.loadCapabilities?.();
    } catch (error) { this.toast(error.message, "error"); }
  }

  oauthCompleted(event) {
    const apiOrigin = new URL(this.api.settings().apiBase, window.location.href).origin;
    if (![window.location.origin, apiOrigin].includes(event.origin)) return;
    if (event.data?.type !== "taroai.connector.oauth.completed") return;
    this.toast("Connector authorized", "success");
    this.load();
    window.taroaiChat?.loadCapabilities?.();
  }

  prefill(value) {
    window.location.hash = "chat";
    requestAnimationFrame(() => {
      const input = document.querySelector("#composer-input");
      if (!input) return;
      input.value = value;
      input.focus();
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  toast(message, state = "idle") {
    const toast = this.root.querySelector("[data-brain-toast]");
    if (!toast) return;
    toast.hidden = false;
    toast.dataset.state = state;
    toast.textContent = message;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { toast.hidden = true; }, state === "error" ? 6000 : 3500);
  }
}

let singleton;
export function createAgentBrainUI() {
  if (!singleton) {
    singleton = new AgentBrainUI();
    singleton.init();
    window.taroaiAgentBrain = singleton;
  }
  return singleton;
}

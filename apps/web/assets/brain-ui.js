import { chatApi } from "./chat-api.js";

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
    this.browserProfiles = [];
    this.browserSessions = [];
    this.selectedConnectorId = null;
    this.selectedBrowserProfileId = null;
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
    const active = window.location.hash.replace(/^#/, "").split("/")[0] === "brain";
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
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page agent-brain-page">
        <header class="capability-page-header">
          <div><p>Workspace capabilities</p><h1>Agent Brain</h1><span>Control the skills and connected services available to every agent turn.</span></div>
          <button type="button" data-brain-refresh>Refresh</button>
        </header>
        <nav class="skill-detail-tabs" aria-label="Agent Brain sections">
          <button class="is-active" data-brain-tab="connectors">Connectors</button>
          <button data-brain-tab="skills">Skills</button>
          <button data-brain-tab="memory">Memory</button>
          <button data-brain-tab="secrets">Secrets</button>
          <button data-brain-tab="browser">Browser</button>
        </nav>
        <section data-brain-panel="connectors" class="capability-split brain-connectors">
          <aside class="capability-list" data-connector-list><div class="route-loading">Loading connectors…</div></aside>
          <article class="capability-detail" data-connector-detail><div class="route-empty"><span>C</span><strong>Select a connector</strong><p>Inspect authorization, capabilities, and workspace availability.</p></div></article>
        </section>
        <section data-brain-panel="skills" hidden></section>
        <section data-brain-panel="memory" hidden></section>
        <section data-brain-panel="secrets" hidden></section>
        <section data-brain-panel="browser" hidden></section>
        <div class="route-toast" data-brain-toast hidden></div>
      </section>`;
    this.renderStaticPanels();
  }

  async load() {
    const workspace = encodeURIComponent(this.api.settings().workspaceId);
    const [connectors, skills, browserProfiles, browserSessions] = await Promise.allSettled([
      this.api.get(`/api/connectors?workspace_id=${workspace}`),
      this.api.get(`/api/workspaces/${workspace}/skills`),
      this.api.get(`/api/browser/profiles?workspace_id=${workspace}`),
      this.api.get(`/api/browser/profile-sessions?workspace_id=${workspace}`),
    ]);
    this.connectors = connectors.status === "fulfilled" ? asArray(connectors.value, "connectors") : [];
    this.skills = skills.status === "fulfilled" ? asArray(skills.value, "skills") : [];
    this.browserProfiles = browserProfiles.status === "fulfilled" ? asArray(browserProfiles.value, "profiles") : [];
    this.browserSessions = browserSessions.status === "fulfilled" ? asArray(browserSessions.value, "sessions") : [];
    if (!this.selectedConnectorId || !this.connectors.some((item) => item.id === this.selectedConnectorId)) {
      this.selectedConnectorId = this.connectors[0]?.id || null;
    }
    if (!this.selectedBrowserProfileId || !this.browserProfiles.some((item) => item.id === this.selectedBrowserProfileId)) {
      this.selectedBrowserProfileId = this.browserProfiles.find((item) => item.is_default)?.id || this.browserProfiles[0]?.id || null;
    }
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
      list.innerHTML = `<div class="route-empty compact"><span>C</span><strong>No connector definitions</strong><p>Ask a workspace administrator to add a governed connector definition.</p></div>`;
      detail.innerHTML = `<div class="route-empty"><span>+</span><strong>Connect external services</strong><p>Connector credentials stay in the Secret Vault and agent calls use scoped capabilities.</p></div>`;
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
    if (skills) skills.innerHTML = `<div class="brain-summary-card"><span>S</span><div><h2>${this.skills.length} workspace skills</h2><p>Inspect SKILL.md, package files, evaluations, and pinned versions.</p><button data-open-skills>Manage skills</button></div></div>`;
    if (memory) memory.innerHTML = `<div class="brain-summary-card"><span>M</span><div><h2>Memory</h2><p>Long-term facts and reviewed memories are governed by the workspace memory service.</p><button data-open-operations>Open memory operations</button></div></div>`;
    if (secrets) secrets.innerHTML = `<div class="brain-summary-card"><span>K</span><div><h2>Secrets</h2><p>Connector credentials remain in the Secret Vault and are issued to tools as short-lived leases.</p></div></div>`;
    if (browser) this.renderBrowser(browser);
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
    if (button.matches("[data-connector-connect]")) return this.connect();
    if (button.matches("[data-connector-toggle]")) return this.toggle();
    if (button.matches("[data-open-skills]")) window.location.hash = "skills";
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

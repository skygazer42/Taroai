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
    this.selectedConnectorId = null;
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
    const [connectors, skills] = await Promise.allSettled([
      this.api.get(`/api/connectors?workspace_id=${workspace}`),
      this.api.get(`/api/workspaces/${workspace}/skills`),
    ]);
    this.connectors = connectors.status === "fulfilled" ? asArray(connectors.value, "connectors") : [];
    this.skills = skills.status === "fulfilled" ? asArray(skills.value, "skills") : [];
    if (!this.selectedConnectorId || !this.connectors.some((item) => item.id === this.selectedConnectorId)) {
      this.selectedConnectorId = this.connectors[0]?.id || null;
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
    if (browser) browser.innerHTML = `<div class="brain-summary-card"><span>B</span><div><h2>Browser use</h2><p>Browser actions run through the governed browser controller with policy and audit events.</p><button data-brain-start-browser>Try in Chat</button></div></div>`;
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

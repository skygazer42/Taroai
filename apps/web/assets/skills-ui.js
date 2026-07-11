import { chatApi } from "./chat-api.js";
import { chatState } from "./chat-controller.js";

function items(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return Array.isArray(value?.items) ? value.items : [];
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(String(reader.result || "").split(",").pop() || "");
    reader.readAsDataURL(file);
  });
}

export class SkillsUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.skills = [];
    this.selected = null;
    this.files = [];
    this.activeFile = null;
    this.mode = "rendered";
    this.filter = "all";
    this.search = "";
    this.boundRoute = () => this.route();
  }

  init() {
    window.addEventListener("hashchange", this.boundRoute);
    this.root?.addEventListener("click", (event) => this.click(event));
    this.root?.addEventListener("input", (event) => this.input(event));
    this.root?.addEventListener("change", (event) => this.change(event));
    this.route();
  }

  route() {
    const active = window.location.hash.replace(/^#/, "").split("/")[0] === "skills";
    if (!active) {
      if (this.root?.dataset.owner === "skills") {
        this.root.hidden = true;
        this.root.replaceChildren();
        delete this.root.dataset.owner;
        document.querySelector("[data-app='taroai-workspace']")?.removeAttribute("data-rich-route");
      }
      return;
    }
    this.root.dataset.owner = "skills";
    this.root.hidden = false;
    document.querySelector("[data-app='taroai-workspace']")?.setAttribute("data-rich-route", "skills");
    this.renderShell();
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page skills-page">
        <header class="capability-page-header">
          <div><p>Workspace capabilities</p><h1>Skills</h1><span>Portable guidance, scripts, references, and evaluation evidence.</span></div>
          <div class="capability-header-actions"><button type="button" data-skill-import-github>Install from GitHub</button><button type="button" class="primary" data-skill-import-zip>Upload ZIP</button><input type="file" accept=".zip,application/zip" data-skill-zip hidden /></div>
        </header>
        <div class="capability-toolbar">
          <label><span aria-hidden="true">⌕</span><input type="search" data-skill-search placeholder="Search installed and available skills" /></label>
          <div role="tablist"><button class="is-active" data-skill-filter="all">All</button><button data-skill-filter="builtin">Built-in</button><button data-skill-filter="custom">Custom</button></div>
          <button type="button" data-skills-refresh>Refresh</button>
        </div>
        <div class="capability-split">
          <aside class="capability-list" data-skill-list><div class="route-loading">Loading skills…</div></aside>
          <article class="capability-detail" data-skill-detail><div class="route-empty"><span>S</span><strong>Select a skill</strong><p>Inspect instructions, supporting files, version state, and evaluation evidence.</p></div></article>
        </div>
        <div class="route-toast" data-skill-toast hidden></div>
      </section>`;
  }

  async load() {
    try {
      const workspace = encodeURIComponent(this.api.settings().workspaceId);
      const [installed, catalog] = await Promise.allSettled([
        this.api.get(`/api/workspaces/${workspace}/skills`),
        this.api.get(`/api/skills?workspace_id=${workspace}`),
      ]);
      const all = [
        ...(installed.status === "fulfilled" ? items(installed.value, "skills", "installations") : []),
        ...(catalog.status === "fulfilled" ? items(catalog.value, "skills", "catalog") : []),
      ];
      const byId = new Map();
      for (const raw of all) {
        const id = raw.id || raw.skill_id || raw.manifest?.id;
        if (!id) continue;
        const previous = byId.get(id) || {};
        byId.set(id, { ...previous, ...raw, id, name: raw.name || raw.manifest?.name || previous.name || id });
      }
      this.skills = Array.from(byId.values());
      this.selected = this.skills.find((skill) => skill.id === this.selected?.id) || this.skills[0] || null;
      this.renderList();
      if (this.selected) await this.select(this.selected.id);
      else this.renderDetail();
    } catch (error) {
      this.toast(error.message, "error");
      this.skills = [];
      this.renderList();
    }
  }

  filtered() {
    return this.skills.filter((skill) => {
      const type = String(skill.source_type || skill.type || skill.origin || "custom").toLowerCase();
      const typeMatch = this.filter === "all" || (this.filter === "builtin" ? type.includes("built") : !type.includes("built"));
      const searchMatch = !this.search || `${skill.name} ${skill.description || ""} ${skill.id}`.toLowerCase().includes(this.search);
      return typeMatch && searchMatch;
    });
  }

  renderList() {
    const list = this.root.querySelector("[data-skill-list]");
    if (!list) return;
    list.replaceChildren();
    const skills = this.filtered();
    if (!skills.length) {
      list.innerHTML = `<div class="route-empty compact"><span>S</span><strong>No matching skills</strong><p>Install a package or change the current search.</p></div>`;
      return;
    }
    for (const skill of skills) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "capability-list-item";
      button.classList.toggle("is-active", skill.id === this.selected?.id);
      button.dataset.skillId = skill.id;
      const enabled = skill.enabled !== false && skill.status !== "disabled";
      button.innerHTML = `<span class="capability-glyph">${skill.icon || "S"}</span><span><strong></strong><small></small><em></em></span><i data-state="${enabled ? "enabled" : "disabled"}"></i>`;
      button.querySelector("strong").textContent = skill.name;
      button.querySelector("small").textContent = skill.description || "Reusable execution guidance";
      button.querySelector("em").textContent = `${skill.source_type || skill.origin || "custom"} · v${skill.installed_version || skill.version || skill.manifest?.version || "1.0.0"}`;
      list.append(button);
    }
  }

  async select(id) {
    this.selected = this.skills.find((skill) => skill.id === id) || null;
    this.files = [];
    this.activeFile = null;
    this.renderList();
    this.renderDetail(true);
    if (!this.selected) return;
    const version = this.selected.installed_version || this.selected.version || this.selected.manifest?.version || "latest";
    try {
      const payload = await this.api.get(`/api/skills/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/files`);
      this.files = items(payload, "files", "entries");
      this.activeFile = this.files.find((file) => (file.path || file.name) === "SKILL.md") || this.files[0] || null;
    } catch {
      const markdown = this.selected.skill_md || this.selected.instructions || this.selected.manifest?.description || "# Skill\n\nPackage content is not available yet.";
      this.files = [{ path: "SKILL.md", content: markdown, media_type: "text/markdown" }];
      this.activeFile = this.files[0];
    }
    this.renderDetail();
  }

  renderDetail(loading = false) {
    const detail = this.root.querySelector("[data-skill-detail]");
    if (!detail) return;
    if (!this.selected) {
      detail.innerHTML = `<div class="route-empty"><span>S</span><strong>Select a skill</strong><p>Inspect instructions, files, versions, and evaluation evidence.</p></div>`;
      return;
    }
    const skill = this.selected;
    const enabled = skill.enabled !== false && skill.status !== "disabled";
    const version = skill.installed_version || skill.version || skill.manifest?.version || "1.0.0";
    detail.innerHTML = `
      <header class="capability-detail-header">
        <div><span class="capability-glyph large">${skill.icon || "S"}</span><div><small>${skill.source_type || skill.origin || "Custom skill"}</small><h2></h2><p></p></div></div>
        <div><label class="quiet-switch"><input type="checkbox" data-skill-toggle ${enabled ? "checked" : ""}/><span></span><em>${enabled ? "Enabled" : "Disabled"}</em></label><button type="button" class="primary" data-skill-try>Try in Chat</button></div>
      </header>
      <div class="skill-evidence-strip">
        <div><small>Installed</small><strong>v${version}</strong></div><div><small>Success</small><strong>${skill.success_rate != null ? `${Math.round(skill.success_rate * 100)}%` : "—"}</strong></div><div><small>Avg. cost</small><strong>${skill.average_cost != null ? `$${Number(skill.average_cost).toFixed(3)}` : "—"}</strong></div><div><small>Evaluation</small><strong>${skill.evaluation_status || skill.eval_status || "Not run"}</strong></div>
      </div>
      <nav class="skill-detail-tabs"><button class="is-active" data-skill-view="content">Content</button><button data-skill-view="versions">Versions</button><button data-skill-view="evaluation">Evaluation</button></nav>
      <section data-skill-view-panel="content" class="skill-content-grid">
        <aside class="skill-file-tree" data-skill-files>${loading ? "<span>Loading package…</span>" : ""}</aside>
        <div class="skill-source-panel"><header><strong data-skill-file-title>SKILL.md</strong><div><button data-skill-source-mode="rendered" class="is-active">Rendered</button><button data-skill-source-mode="raw">Raw</button></div></header><div data-skill-source></div></div>
      </section>
      <section data-skill-view-panel="versions" hidden><div class="skill-version-actions"><button data-skill-refresh-source>Refresh source</button><button data-skill-upgrade>Upgrade</button><button data-skill-rollback>Rollback</button></div><div class="route-note">Version changes are pinned to this workspace and remain visible in Run evidence.</div></section>
      <section data-skill-view-panel="evaluation" hidden><div class="evaluation-summary"><strong>${skill.evaluation_status || "Evaluation not run"}</strong><p>${skill.evaluation_summary || "Run the package evaluation suite before promotion."}</p><button data-skill-evaluate>Run evaluation</button></div></section>`;
    detail.querySelector("h2").textContent = skill.name;
    detail.querySelector(".capability-detail-header p").textContent = skill.description || "Reusable execution guidance for this workspace.";
    if (!loading) this.renderFiles();
  }

  renderFiles() {
    const tree = this.root.querySelector("[data-skill-files]");
    const source = this.root.querySelector("[data-skill-source]");
    if (!tree || !source) return;
    tree.replaceChildren();
    for (const file of this.files) {
      const path = file.path || file.name;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.skillFile = path;
      button.classList.toggle("is-active", path === (this.activeFile?.path || this.activeFile?.name));
      button.textContent = `${path.includes("/") ? "└ " : ""}${path}`;
      tree.append(button);
    }
    const content = this.activeFile?.content || this.activeFile?.text || "Select a package file.";
    this.root.querySelector("[data-skill-file-title]").textContent = this.activeFile?.path || this.activeFile?.name || "Package";
    source.replaceChildren();
    if (this.mode === "rendered" && String(this.activeFile?.path || "").toLowerCase().endsWith(".md")) {
      const article = document.createElement("article");
      article.className = "skill-markdown";
      for (const block of String(content).split(/\n{2,}/)) {
        const line = block.trim();
        if (!line) continue;
        const node = document.createElement(line.startsWith("# ") ? "h1" : line.startsWith("## ") ? "h2" : line.startsWith("### ") ? "h3" : "p");
        node.textContent = line.replace(/^#{1,3}\s+/, "");
        article.append(node);
      }
      source.append(article);
    } else {
      const pre = document.createElement("pre");
      pre.textContent = content;
      source.append(pre);
    }
  }

  input(event) {
    if (event.target.matches("[data-skill-search]")) {
      this.search = event.target.value.trim().toLowerCase();
      this.renderList();
    }
  }

  change(event) {
    if (event.target.matches("[data-skill-zip]") && event.target.files?.[0]) this.importZip(event.target.files[0]);
    if (event.target.matches("[data-skill-toggle]")) this.toggle(event.target.checked);
  }

  click(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.skillId) return this.select(button.dataset.skillId);
    if (button.dataset.skillFilter) {
      this.filter = button.dataset.skillFilter;
      this.root.querySelectorAll("[data-skill-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
      return this.renderList();
    }
    if (button.matches("[data-skills-refresh]")) return this.load();
    if (button.matches("[data-skill-import-zip]")) return this.root.querySelector("[data-skill-zip]")?.click();
    if (button.matches("[data-skill-import-github]")) return this.importGithub();
    if (button.dataset.skillFile) {
      this.activeFile = this.files.find((file) => (file.path || file.name) === button.dataset.skillFile);
      return this.renderFiles();
    }
    if (button.dataset.skillSourceMode) {
      this.mode = button.dataset.skillSourceMode;
      this.root.querySelectorAll("[data-skill-source-mode]").forEach((item) => item.classList.toggle("is-active", item === button));
      return this.renderFiles();
    }
    if (button.dataset.skillView) return this.switchView(button.dataset.skillView);
    if (button.matches("[data-skill-try]")) return this.tryInChat();
    if (button.matches("[data-skill-refresh-source]")) return this.action("refresh", "Source refreshed");
    if (button.matches("[data-skill-upgrade]")) return this.action("upgrade", "Skill upgraded");
    if (button.matches("[data-skill-rollback]")) return this.action("rollback", "Skill rolled back");
    if (button.matches("[data-skill-evaluate]")) return this.action("evaluations", "Evaluation started");
  }

  switchView(view) {
    this.root.querySelectorAll("[data-skill-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.skillView === view));
    this.root.querySelectorAll("[data-skill-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.skillViewPanel !== view; });
  }

  async toggle(enabled) {
    if (!this.selected) return;
    try {
      const workspace = encodeURIComponent(this.api.settings().workspaceId);
      await this.api.patch(`/api/workspaces/${workspace}/skills/${encodeURIComponent(this.selected.id)}`, { enabled }, { scope: "skill-toggle" });
      this.selected.enabled = enabled;
      this.toast(enabled ? "Skill enabled" : "Skill disabled", "success");
      this.renderList();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async action(action, success) {
    if (!this.selected) return;
    try {
      await this.api.post(`/api/skills/${encodeURIComponent(this.selected.id)}/${action}`, { workspace_id: this.api.settings().workspaceId }, { scope: `skill-${action}` });
      this.toast(success, "success");
      if (action !== "evaluations") await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async importGithub() {
    const source_url = window.prompt("Public GitHub repository URL");
    if (!source_url) return;
    try {
      await this.api.post("/api/skills/import/github", { source_url, workspace_id: this.api.settings().workspaceId }, { scope: "skill-github-import" });
      this.toast("GitHub skill installed", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async importZip(file) {
    try {
      this.toast("Reading and validating package…", "loading");
      const content_base64 = await fileBase64(file);
      await this.api.post("/api/skills/import/zip", { filename: file.name, content_base64, workspace_id: this.api.settings().workspaceId }, { scope: "skill-zip-import" });
      this.toast("Skill package installed", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  tryInChat() {
    if (!this.selected) return;
    const version = this.selected.installed_version || this.selected.version || null;
    chatState.resourceRefs = [{ type: "skill", id: this.selected.id, version, name: this.selected.name }];
    window.location.hash = "chat";
    requestAnimationFrame(() => {
      const input = document.querySelector("#composer-input");
      if (input) { input.value = `Use @${this.selected.name.replace(/\s+/g, "-")} to `; input.focus(); input.dispatchEvent(new Event("input", { bubbles: true })); }
      window.taroaiChat?.renderResourceChips?.();
    });
  }

  toast(message, state = "idle") {
    const toast = this.root.querySelector("[data-skill-toast]");
    if (!toast) return;
    toast.hidden = false;
    toast.dataset.state = state;
    toast.textContent = message;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { toast.hidden = true; }, state === "error" ? 6000 : 3000);
  }
}

let singleton;
export function createSkillsUI() {
  if (!singleton) { singleton = new SkillsUI(); singleton.init(); window.taroaiSkills = singleton; }
  return singleton;
}

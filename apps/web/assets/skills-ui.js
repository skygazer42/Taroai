import { chatApi } from "./chat-api.js?v=20260722-flow115";
import { chatState } from "./chat-controller.js?v=20260724-flow140";

function items(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return Array.isArray(value?.items) ? value.items : [];
}

function packageVersion(record) {
  return record?.package?.version || record?.package?.manifest?.version || null;
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(String(reader.result || "").split(",").pop() || "");
    reader.readAsDataURL(file);
  });
}

function parseGithubSkillUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "github.com") {
    throw new Error("Use a public https://github.com repository URL");
  }
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length < 2) throw new Error("GitHub URL must include owner and repository");
  const owner = parts[0];
  const repository = parts[1].replace(/\.git$/i, "");
  let ref = "main";
  let subdirectory = null;
  if (parts[2] === "tree") {
    if (!parts[3]) throw new Error("GitHub tree URL must include a branch or tag");
    ref = parts[3];
    subdirectory = parts.slice(4).join("/") || null;
  } else if (parts.length > 2) {
    throw new Error("Use a repository root or /tree/<ref>/<folder> URL");
  }
  return { owner, repository, ref, subdirectory };
}

export class SkillsUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.skills = [];
    this.selected = null;
    this.files = [];
    this.activeFile = null;
    this.packageRecord = null;
    this.packages = [];
    this.versionDiff = null;
    this.evaluations = [];
    this.mode = "rendered";
    this.filter = "all";
    this.search = "";
    this.installedSkillIds = new Set();
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
      const [installed, catalog, store] = await Promise.allSettled([
        this.api.get(`/api/workspaces/${workspace}/skills`),
        this.api.get(`/api/skills?workspace_id=${workspace}`),
        this.api.get("/api/store/items?kind=solution_pack"),
      ]);
      const installedItems = installed.status === "fulfilled" ? items(installed.value, "skills", "installations") : [];
      this.installedSkillIds = new Set(installedItems.map((item) => item.skill_id || item.id).filter(Boolean));
      const all = [
        ...installedItems.map((item) => ({ ...item, __installed: true })),
        ...(catalog.status === "fulfilled" ? items(catalog.value, "skills", "catalog").map((item) => ({ ...item, __catalog: true })) : []),
        ...(store.status === "fulfilled" ? items(store.value).map((item) => ({ ...item, __store: true, origin: "builtin" })) : []),
      ];
      const byId = new Map();
      for (const raw of all) {
        const id = raw.id || raw.skill_id || raw.manifest?.id;
        if (!id) continue;
        const previous = byId.get(id) || {};
        byId.set(id, {
          ...previous,
          ...raw,
          id,
          installed: Boolean(previous.installed || raw.__installed),
          enabled: raw.__installed ? raw.status !== "disabled" : previous.enabled,
          name: raw.name || raw.manifest?.name || previous.name || id,
          description: raw.description || raw.manifest?.description || previous.description || "",
        });
      }
      this.skills = Array.from(byId.values());
      const routeId = window.location.hash.replace(/^#/, "").split("/")[1];
      const requestedId = routeId ? decodeURIComponent(routeId) : null;
      this.selected = this.skills.find((skill) => skill.id === requestedId)
        || this.skills.find((skill) => skill.id === this.selected?.id)
        || this.skills[0]
        || null;
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
      const enabled = Boolean(skill.installed) && skill.enabled !== false && skill.status !== "disabled";
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
    this.packageRecord = null;
    this.packages = [];
    this.versionDiff = null;
    this.evaluations = [];
    this.renderList();
    this.renderDetail(true);
    if (!this.selected) return;
    if (this.selected.__store) {
      try {
        const detail = await this.api.get(`/api/store/items/${encodeURIComponent(id)}`);
        this.selected = { ...this.selected, ...detail, __store: true, origin: "builtin" };
      } catch (error) {
        this.toast(error.message, "error");
      }
      this.renderDetail();
      return;
    }
    let version = this.selected.installed_version || this.selected.version || this.selected.manifest?.version || null;
    try {
      const packages = items(await this.api.get(`/api/skills/${encodeURIComponent(id)}/packages`), "packages");
      this.packages = packages;
      this.packageRecord = packages.find((item) => packageVersion(item) === version) || packages[packages.length - 1] || null;
      version = packageVersion(this.packageRecord) || version;
      if (!version) throw new Error("No package version is available");
      const payload = await this.api.get(`/api/skills/${encodeURIComponent(id)}/packages/${encodeURIComponent(version)}/files`);
      this.files = items(payload, "files", "entries");
      this.activeFile = this.files.find((file) => (file.path || file.name) === "SKILL.md") || this.files[0] || null;
      if (this.activeFile) await this.loadFile(this.activeFile.path || this.activeFile.name);
      try {
        this.evaluations = items(await this.api.get(`/api/skills/${encodeURIComponent(id)}/packages/${encodeURIComponent(version)}/evaluations`), "evaluations");
      } catch { this.evaluations = []; }
    } catch {
      const markdown = this.selected.skill_md || this.selected.instructions || this.selected.manifest?.description || "# Skill\n\nPackage content is not available yet.";
      this.files = [{ path: "SKILL.md", content: markdown, media_type: "text/markdown" }];
      this.activeFile = this.files[0];
    }
    this.renderDetail();
  }

  async loadFile(path) {
    if (!this.selected || !path) return;
    const version = packageVersion(this.packageRecord) || this.selected.installed_version || this.selected.manifest?.version;
    if (!version) return;
    const payload = await this.api.get(`/api/skills/${encodeURIComponent(this.selected.id)}/packages/${encodeURIComponent(version)}/files/${path.split("/").map(encodeURIComponent).join("/")}`);
    const existing = this.files.find((file) => (file.path || file.name) === path);
    const hydrated = { ...existing, ...payload, path, content: payload.content ?? existing?.content ?? "" };
    this.files = this.files.map((file) => (file.path || file.name) === path ? hydrated : file);
    this.activeFile = hydrated;
  }

  renderDetail(loading = false) {
    const detail = this.root.querySelector("[data-skill-detail]");
    if (!detail) return;
    if (!this.selected) {
      detail.innerHTML = `<div class="route-empty"><span>S</span><strong>Select a skill</strong><p>Inspect instructions, files, versions, and evaluation evidence.</p></div>`;
      return;
    }
    const skill = this.selected;
    if (skill.__store) {
      this.renderStoreDetail(detail, loading);
      return;
    }
    const installed = Boolean(skill.installed);
    const enabled = installed && skill.enabled !== false && skill.status !== "disabled";
    const version = packageVersion(this.packageRecord) || skill.installed_version || skill.version || skill.manifest?.version || "1.0.0";
    const packageStatus = this.packageRecord?.status || skill.status || "draft";
    const latestEvaluation = this.evaluations[this.evaluations.length - 1] || null;
    const canInstall = packageStatus === "published";
    detail.innerHTML = `
      <header class="capability-detail-header">
        <div><span class="capability-glyph large">${skill.icon || "S"}</span><div><small>${skill.source_type || skill.origin || "Custom skill"}</small><h2></h2><p></p></div></div>
        <div>${installed ? `<label class="quiet-switch"><input type="checkbox" data-skill-toggle ${enabled ? "checked" : ""}/><span></span><em>${enabled ? "Enabled" : "Disabled"}</em></label><button type="button" class="primary" data-skill-try>Try in Chat</button>` : `<button type="button" class="primary" data-skill-install ${canInstall ? "" : "disabled"}>${canInstall ? "Install" : "Publish before install"}</button>`}</div>
      </header>
      <div class="skill-evidence-strip">
        <div><small>${installed ? "Installed" : "Package"}</small><strong>v${version}</strong></div><div><small>Status</small><strong>${packageStatus}</strong></div><div><small>Avg. cost</small><strong>${skill.average_cost != null ? `$${Number(skill.average_cost).toFixed(3)}` : "—"}</strong></div><div><small>Evaluation</small><strong>${latestEvaluation?.status || skill.evaluation_status || skill.eval_status || "Not run"}</strong></div>
      </div>
      <nav class="skill-detail-tabs"><button class="is-active" data-skill-view="content">Content</button><button data-skill-view="versions">Versions</button><button data-skill-view="evaluation">Evaluation</button></nav>
      <section data-skill-view-panel="content" class="skill-content-grid">
        <aside class="skill-file-tree" data-skill-files>${loading ? "<span>Loading package…</span>" : ""}</aside>
        <div class="skill-source-panel"><header><strong data-skill-file-title>SKILL.md</strong><div><button data-skill-source-mode="rendered" class="is-active">Rendered</button><button data-skill-source-mode="raw">Raw</button></div></header><div data-skill-source></div></div>
      </section>
      <section data-skill-view-panel="evaluation" hidden><div class="evaluation-summary"><strong>${latestEvaluation?.status || "Evaluation not run"}</strong><p>${latestEvaluation ? `Score ${latestEvaluation.score ?? "—"} · ${latestEvaluation.id}` : "Run the package evaluation suite before promotion."}</p><button data-skill-evaluate>Run evaluation</button>${packageStatus === "draft" ? `<button data-skill-publish ${latestEvaluation?.passed ? "" : "disabled"}>Publish evaluated version</button>` : ""}</div></section>`;
    detail.querySelector("h2").textContent = skill.name;
    detail.querySelector(".capability-detail-header p").textContent = skill.description || "Reusable execution guidance for this workspace.";
    if (!loading) {
      this.renderFiles();
      this.renderVersions();
    }
  }

  renderStoreDetail(detail, loading = false) {
    const item = this.selected;
    const packages = item.packages || [];
    const installed = packages.length > 0 && packages.every((pkg) => this.installedSkillIds.has(pkg.skill_id));
    detail.innerHTML = `
      <header class="capability-detail-header">
        <div><span class="capability-glyph large">S</span><div><small>Built-in · ${item.publisher || "Taroai"}</small><h2></h2><p></p></div></div>
        <div><button type="button" class="primary" data-skill-install ${installed || loading ? "disabled" : ""}>${installed ? "Installed" : loading ? "Loading…" : "Install pack"}</button></div>
      </header>
      <div class="skill-evidence-strip">
        <div><small>Version</small><strong>v${item.version || "1.0.0"}</strong></div><div><small>Skills</small><strong>${item.skill_count ?? packages.length}</strong></div><div><small>Risk</small><strong>${item.risk_level || "low"}</strong></div><div><small>License</small><strong>${item.license || "—"}</strong></div>
      </div>
      <section class="store-pack-detail">
        <div class="route-note"><strong>Verified built-in package</strong><p>Installed from immutable Taroai resources with an exact package digest. No external credentials are required.</p></div>
        <div class="store-package-list" data-store-package-list></div>
      </section>`;
    detail.querySelector("h2").textContent = item.name;
    detail.querySelector(".capability-detail-header p").textContent = item.description || "Reusable built-in workspace capability.";
    const list = detail.querySelector("[data-store-package-list]");
    for (const pkg of packages) {
      const row = document.createElement("article");
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = pkg.skill_id;
      const meta = document.createElement("small");
      meta.textContent = `v${pkg.version} · ${pkg.risk_level || "low"} risk`;
      copy.append(title, meta);
      const status = document.createElement("span");
      status.textContent = this.installedSkillIds.has(pkg.skill_id) ? "Installed" : "Included";
      row.append(copy, status);
      list.append(row);
    }
  }

  renderVersions() {
    const list = this.root.querySelector("[data-skill-version-list]");
    const form = this.root.querySelector("[data-skill-version-compare]");
    if (!list || !form) return;
    list.replaceChildren();
    for (const record of this.packages) {
      const pkg = record.package || {};
      const version = packageVersion(record);
      const card = document.createElement("button");
      card.type = "button";
      card.dataset.skillVersionOpen = version;
      card.className = "skill-version-card";
      card.classList.toggle("is-active", version === packageVersion(this.packageRecord));
      card.innerHTML = `<span><strong></strong><small></small></span><em></em>`;
      card.querySelector("strong").textContent = `v${version}`;
      card.querySelector("small").textContent = `${pkg.files?.length || 0} files · ${(pkg.package_digest || "").slice(0, 10)}`;
      card.querySelector("em").textContent = record.status || "draft";
      list.append(card);
    }
    for (const select of form.querySelectorAll("select")) select.replaceChildren();
    for (const record of this.packages) {
      const version = packageVersion(record);
      for (const select of form.querySelectorAll("select")) {
        const option = document.createElement("option");
        option.value = version;
        option.textContent = `v${version} · ${record.status}`;
        select.append(option);
      }
    }
    const current = packageVersion(this.packageRecord);
    form.elements.to_version.value = current || packageVersion(this.packages.at(-1)) || "";
    form.elements.from_version.value = packageVersion(this.packages.find((item) => packageVersion(item) !== form.elements.to_version.value)) || form.elements.to_version.value;
    form.addEventListener("submit", (event) => { event.preventDefault(); this.compareVersions(new FormData(form)); });
    if (this.versionDiff) this.renderVersionDiff();
  }

  renderVersionDiff() {
    const root = this.root.querySelector("[data-skill-version-diff]");
    if (!root || !this.versionDiff) return;
    const diff = this.versionDiff;
    root.replaceChildren();
    const summary = document.createElement("div");
    summary.className = "skill-version-diff-summary";
    const facts = [
      ["Files", `+${diff.files.added.length} · ~${diff.files.changed.length} · −${diff.files.removed.length}`],
      ["Permissions", `+${diff.required_scopes.added.length} · −${diff.required_scopes.removed.length}`],
      ["Dependencies", `+${diff.dependencies.added.length} · −${diff.dependencies.removed.length}`],
    ];
    for (const [label, value] of facts) {
      const fact = document.createElement("div");
      const small = document.createElement("small"); small.textContent = label;
      const strong = document.createElement("strong"); strong.textContent = value;
      fact.append(small, strong); summary.append(fact);
    }
    root.append(summary);
    if (diff.release_notes) {
      const notes = document.createElement("section"); notes.className = "skill-release-notes";
      const title = document.createElement("strong"); title.textContent = "Release notes";
      const text = document.createElement("p"); text.textContent = diff.release_notes;
      notes.append(title, text); root.append(notes);
    }
    for (const patch of diff.patches) {
      const section = document.createElement("section"); section.className = "skill-version-patch";
      const header = document.createElement("header"); header.textContent = patch.path;
      const pre = document.createElement("pre");
      pre.textContent = patch.binary ? `Binary file changed (${patch.before_size || 0} → ${patch.after_size || 0} bytes)` : patch.diff || "Metadata changed";
      section.append(header, pre); root.append(section);
    }
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
      return this.loadFile(button.dataset.skillFile)
        .then(() => this.renderFiles())
        .catch((error) => this.toast(error.message, "error"));
    }
    if (button.dataset.skillSourceMode) {
      this.mode = button.dataset.skillSourceMode;
      this.root.querySelectorAll("[data-skill-source-mode]").forEach((item) => item.classList.toggle("is-active", item === button));
      return this.renderFiles();
    }
    if (button.dataset.skillView) return this.switchView(button.dataset.skillView);
    if (button.matches("[data-skill-try]")) return this.tryInChat();
    if (button.matches("[data-skill-install]")) return this.install();
    if (button.matches("[data-skill-upgrade]")) return this.moveVersion("upgrade");
    if (button.matches("[data-skill-rollback]")) return this.moveVersion("rollback");
    if (button.matches("[data-skill-evaluate]")) return this.evaluate();
    if (button.matches("[data-skill-publish]")) return this.publish();
    if (button.dataset.skillVersionOpen) return this.openVersion(button.dataset.skillVersionOpen);
  }

  switchView(view) {
    this.root.querySelectorAll("[data-skill-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.skillView === view));
    this.root.querySelectorAll("[data-skill-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.skillViewPanel !== view; });
  }

  async toggle(enabled) {
    if (!this.selected) return;
    try {
      const workspace = encodeURIComponent(this.api.settings().workspaceId);
      await this.api.post(`/api/workspaces/${workspace}/skills/${encodeURIComponent(this.selected.id)}/${enabled ? "enable" : "disable"}`, {}, { scope: "skill-toggle" });
      this.selected.enabled = enabled;
      this.toast(enabled ? "Skill enabled" : "Skill disabled", "success");
      this.renderList();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async install() {
    if (!this.selected) return;
    if (this.selected.__store) {
      try {
        await this.api.post(
          `/api/store/items/${encodeURIComponent(this.selected.id)}/install`,
          { workspace_id: this.api.settings().workspaceId, expected_digest: this.selected.digest },
          { scope: "store-install" },
        );
        this.toast("Built-in pack installed", "success");
        await this.load();
      } catch (error) { this.toast(error.message, "error"); }
      return;
    }
    if (!this.packageRecord) return;
    const workspace = encodeURIComponent(this.api.settings().workspaceId);
    const pkg = this.packageRecord.package;
    try {
      await this.api.post(`/api/workspaces/${workspace}/skills/${encodeURIComponent(this.selected.id)}/install`, { version: packageVersion(this.packageRecord), package_digest: pkg.package_digest }, { scope: "skill-install" });
      this.toast("Skill installed in this workspace", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async evaluate() {
    if (!this.selected || !this.packageRecord) return;
    const version = packageVersion(this.packageRecord);
    try {
      const result = await this.api.post(`/api/skills/${encodeURIComponent(this.selected.id)}/packages/${encodeURIComponent(version)}/evaluate`, { workspace_id: this.api.settings().workspaceId }, { scope: "skill-evaluate" });
      this.evaluations.push(result);
      this.toast(result.passed ? "Evaluation passed" : "Evaluation completed with failures", result.passed ? "success" : "error");
      this.renderDetail();
      this.switchView("evaluation");
    } catch (error) { this.toast(error.message, "error"); }
  }

  async publish() {
    if (!this.selected || !this.packageRecord) return;
    const evaluation = [...this.evaluations].reverse().find((item) => item.passed);
    if (!evaluation) return this.toast("A passing evaluation is required before publish", "error");
    const version = packageVersion(this.packageRecord);
    try {
      this.packageRecord = await this.api.post(`/api/skills/${encodeURIComponent(this.selected.id)}/packages/${encodeURIComponent(version)}/publish`, { evaluation_run_id: evaluation.id }, { scope: "skill-publish" });
      this.toast("Skill version published", "success");
      await this.select(this.selected.id);
    } catch (error) { this.toast(error.message, "error"); }
  }

  async moveVersion(operation) {
    if (!this.selected || !this.selected.installed_version) return;
    try {
      const packages = items(await this.api.get(`/api/skills/${encodeURIComponent(this.selected.id)}/packages`), "packages")
        .filter((item) => item.status === "published" && packageVersion(item) !== this.selected.installed_version);
      if (!packages.length) throw new Error("No other published version is available");
      const suggested = packageVersion(packages[packages.length - 1]);
      const target = window.prompt(`${operation === "upgrade" ? "Upgrade" : "Rollback"} to version`, suggested);
      if (!target) return;
      await this.api.post(`/api/workspaces/${encodeURIComponent(this.api.settings().workspaceId)}/skills/${encodeURIComponent(this.selected.id)}/${operation}`, { target_version: target, expected_package_digest: this.selected.package_digest || null }, { scope: `skill-${operation}` });
      this.toast(`Skill ${operation} complete`, "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async openVersion(version) {
    if (!this.selected) return;
    const record = this.packages.find((item) => packageVersion(item) === version);
    if (!record) return;
    this.packageRecord = record;
    this.versionDiff = null;
    try {
      const payload = await this.api.get(`/api/skills/${encodeURIComponent(this.selected.id)}/packages/${encodeURIComponent(version)}/files`);
      this.files = items(payload, "files", "entries");
      this.activeFile = this.files.find((file) => (file.path || file.name) === "SKILL.md") || this.files[0] || null;
      if (this.activeFile) await this.loadFile(this.activeFile.path || this.activeFile.name);
      this.evaluations = items(await this.api.get(`/api/skills/${encodeURIComponent(this.selected.id)}/packages/${encodeURIComponent(version)}/evaluations`), "evaluations");
      this.renderDetail();
      this.switchView("versions");
    } catch (error) { this.toast(error.message, "error"); }
  }

  async compareVersions(data) {
    if (!this.selected) return;
    const from = String(data.get("from_version") || "");
    const to = String(data.get("to_version") || "");
    if (!from || !to || from === to) return this.toast("Choose two different versions", "error");
    try {
      this.versionDiff = await this.api.get(`/api/skills/${encodeURIComponent(this.selected.id)}/packages/${encodeURIComponent(to)}/diff?compare_to=${encodeURIComponent(from)}`);
      this.renderVersionDiff();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async importGithub() {
    const sourceUrl = window.prompt("Public GitHub repository URL");
    if (!sourceUrl) return;
    try {
      const source = parseGithubSkillUrl(sourceUrl);
      await this.api.post("/api/skills/import/github", { source, workspace_id: this.api.settings().workspaceId }, { scope: "skill-github-import" });
      this.toast("GitHub skill imported as a draft", "success");
      await this.load();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async importZip(file) {
    try {
      this.toast("Reading and validating package…", "loading");
      const archive_base64 = await fileBase64(file);
      await this.api.post("/api/skills/import/zip", { archive_base64, workspace_id: this.api.settings().workspaceId }, { scope: "skill-zip-import" });
      this.toast("Skill package imported as a draft", "success");
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

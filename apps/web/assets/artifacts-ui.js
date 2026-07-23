import { chatApi } from "./chat-api.js?v=20260722-flow115";

function contentOf(artifact) {
  return artifact.content ?? artifact.text ?? artifact.markdown ?? artifact.source ?? artifact.srcdoc ?? artifact.data ?? "";
}

function kindOf(artifact) {
  const type = String(artifact.kind || artifact.artifact_type || artifact.media_type || "text").toLowerCase();
  if (type.includes("dashboard") || artifact.widgets) return "dashboard";
  if (type.includes("html")) return "html";
  if (type.includes("svg")) return "svg";
  if (type.includes("pdf")) return "pdf";
  if (type.includes("image") || /\.(png|jpe?g|webp|gif)$/i.test(artifact.filename || artifact.name || "")) return "image";
  if (type.includes("code") || /\.(js|ts|py|json|yaml|yml|css|html|sql)$/i.test(artifact.filename || artifact.name || "")) return "code";
  return "text";
}

export class ArtifactsUI {
  constructor(api = chatApi) {
    this.api = api;
    this.artifact = null;
    this.mode = "preview";
    this.source = null;
    this.diff = null;
    this.previewUrl = null;
    this.objectUrls = [];
    this.openToken = 0;
  }

  init() {
    window.taroaiArtifacts = this;
  }

  async open(artifact) {
    const openToken = ++this.openToken;
    this.revokeUrls();
    if (artifact.id && !contentOf(artifact) && !artifact.download_url) {
      try {
        const preview = await this.api.get(`/api/artifacts/${encodeURIComponent(artifact.id)}/preview`);
        artifact = { ...artifact, ...preview };
      } catch { /* Source and download remain available when preview is unsupported. */ }
    }
    if (openToken !== this.openToken) return;
    this.artifact = artifact;
    this.source = null;
    this.diff = null;
    if (artifact.id && ["image", "pdf"].includes(kindOf(artifact))) {
      try {
        const blob = await this.api.blob(`/api/artifacts/${encodeURIComponent(artifact.id)}/download`);
        if (openToken !== this.openToken) return;
        this.previewUrl = URL.createObjectURL(blob);
        this.objectUrls.push(this.previewUrl);
      } catch { /* The preview shows an explicit unavailable state below. */ }
    }
    const stage = document.querySelector("[data-artifact-stage]");
    if (!stage) return;
    stage.hidden = false;
    stage.innerHTML = `
      <header class="rich-artifact-header"><div><small data-rich-artifact-type></small><strong data-artifact-stage-title></strong></div><div class="rich-artifact-actions"><button data-artifact-mode="preview" class="is-active">Preview</button><button data-artifact-mode="source">Source</button><button data-artifact-mode="diff">Diff</button><button data-rich-artifact-share>Share</button><button data-rich-artifact-copy>Copy</button><button data-rich-artifact-download>Download</button></div></header>
      <div class="artifact-metadata" data-artifact-metadata></div>
      <div class="rich-artifact-stage" data-rich-artifact-stage></div>`;
    stage.querySelector("[data-artifact-stage-title]").textContent = artifact.name || artifact.title || artifact.filename || "Artifact";
    stage.querySelector("[data-rich-artifact-type]").textContent = kindOf(artifact);
    stage.querySelector("[data-artifact-metadata]").textContent = [artifact.media_type, artifact.size_bytes ? `${artifact.size_bytes.toLocaleString()} bytes` : "", artifact.created_at ? new Date(artifact.created_at).toLocaleString() : ""].filter(Boolean).join(" · ");
    stage.addEventListener("click", (event) => this.click(event));
    this.mode = "preview";
    this.render();
  }

  async click(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.artifactMode) {
      this.mode = button.dataset.artifactMode;
      document.querySelectorAll("[data-artifact-mode]").forEach((item) => item.classList.toggle("is-active", item === button));
      if (this.mode === "source") await this.loadSource();
      if (this.mode === "diff") await this.loadDiff();
      this.render();
    }
    if (button.matches("[data-rich-artifact-copy]")) this.copy();
    if (button.matches("[data-rich-artifact-download]")) this.download();
    if (button.matches("[data-rich-artifact-share]")) this.share();
  }

  render() {
    const stage = document.querySelector("[data-rich-artifact-stage]");
    if (!stage || !this.artifact) return;
    stage.replaceChildren();
    const kind = kindOf(this.artifact);
    if (this.mode === "diff") return this.renderDiff(stage);
    if (this.mode === "source" || ["code", "text"].includes(kind)) return this.renderCode(stage);
    if (kind === "dashboard") return this.renderDashboard(stage);
    if (kind === "html") return this.renderHtml(stage);
    if (kind === "svg") return this.renderSvg(stage);
    if (kind === "pdf") return this.renderPdf(stage);
    if (kind === "image") return this.renderImage(stage);
    this.renderCode(stage);
  }

  renderCode(stage) {
    const pre = document.createElement("pre");
    pre.className = "rich-code-view";
    const code = document.createElement("code");
    const content = this.source?.source ?? contentOf(this.artifact);
    code.textContent = typeof content === "string" ? content : JSON.stringify(content, null, 2);
    pre.append(code); stage.append(pre);
  }

  renderDiff(stage) {
    const pre = document.createElement("pre");
    pre.className = "rich-code-view artifact-diff-view";
    const source = this.diff?.diff || "No previous version was found, or the artifact has no changes.";
    for (const line of source.split("\n")) {
      const row = document.createElement("span");
      row.textContent = `${line}\n`;
      if (line.startsWith("+") && !line.startsWith("+++")) row.dataset.diff = "added";
      else if (line.startsWith("-") && !line.startsWith("---")) row.dataset.diff = "removed";
      else if (line.startsWith("@@")) row.dataset.diff = "hunk";
      pre.append(row);
    }
    stage.append(pre);
  }

  async loadSource() {
    if (this.source || !this.artifact?.id) return;
    try {
      this.source = await this.api.get(`/api/artifacts/${encodeURIComponent(this.artifact.id)}/source`);
    } catch (error) {
      this.source = { source: `Unable to load artifact source.\n${error.message}` };
    }
  }

  async loadDiff() {
    if (this.diff || !this.artifact?.id) return;
    try {
      this.diff = await this.api.get(`/api/artifacts/${encodeURIComponent(this.artifact.id)}/diff`);
    } catch (error) {
      this.diff = { diff: `Unable to load artifact diff.\n${error.message}` };
    }
  }

  renderHtml(stage) {
    const frame = document.createElement("iframe");
    frame.className = "artifact-preview-frame";
    frame.setAttribute("sandbox", "allow-scripts allow-forms");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("title", this.artifact.name || "HTML artifact preview");
    const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">`;
    frame.srcdoc = `${csp}${String(contentOf(this.artifact))}`;
    stage.append(frame);
  }

  renderSvg(stage) {
    const frame = document.createElement("iframe");
    frame.className = "artifact-preview-frame";
    frame.setAttribute("sandbox", "");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.title = this.artifact.name || "SVG artifact preview";
    frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:"><style>body{margin:0;display:grid;place-items:center;min-height:100vh;background:#fff}svg{max-width:100%;height:auto}</style>${String(contentOf(this.artifact))}`;
    stage.append(frame);
  }

  renderPdf(stage) {
    const source = this.previewUrl || (!this.artifact.id && (this.artifact.preview_url || this.artifact.download_url || this.artifact.url));
    if (!source) return this.renderBinaryUnavailable(stage);
    const frame = document.createElement("iframe");
    frame.className = "artifact-preview-frame";
    frame.title = this.artifact.name || "PDF preview";
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.referrerPolicy = "no-referrer";
    frame.src = source || "about:blank";
    stage.append(frame);
  }

  renderImage(stage) {
    const source = this.previewUrl || (!this.artifact.id && (this.artifact.preview_url || this.artifact.download_url || this.artifact.url)) || String(contentOf(this.artifact));
    if (!source) return this.renderBinaryUnavailable(stage);
    const image = document.createElement("img");
    image.className = "artifact-image-preview";
    image.alt = this.artifact.name || "Artifact preview";
    image.src = source;
    stage.append(image);
  }

  renderBinaryUnavailable(stage) {
    const note = document.createElement("p");
    note.className = "route-note";
    note.textContent = "Preview unavailable. Try Download again.";
    stage.append(note);
  }

  renderDashboard(stage) {
    const dashboard = typeof contentOf(this.artifact) === "object" ? contentOf(this.artifact) : this.artifact.dashboard || this.artifact;
    const grid = document.createElement("div");
    grid.className = "typed-dashboard";
    const widgets = dashboard.widgets || [];
    for (const widget of widgets) {
      const card = document.createElement("section");
      card.className = `dashboard-widget widget-${widget.type || "kpi"}`;
      const heading = document.createElement("header");
      const title = document.createElement("strong"); title.textContent = widget.title || "Metric";
      const note = document.createElement("small"); note.textContent = widget.subtitle || "";
      heading.append(title, note); card.append(heading);
      this.renderWidget(card, widget); grid.append(card);
    }
    if (!widgets.length) grid.innerHTML = `<div class="route-note">This dashboard does not contain supported widgets.</div>`;
    stage.append(grid);
  }

  renderWidget(card, widget) {
    const type = widget.type || "kpi";
    if (type === "kpi") {
      const value = document.createElement("div"); value.className = "dashboard-kpi"; value.textContent = widget.value ?? "—";
      const delta = document.createElement("span"); delta.textContent = widget.delta || widget.description || ""; value.append(delta); card.append(value); return;
    }
    if (["bar", "line", "area", "chart"].includes(type)) {
      const chart = document.createElement("div"); chart.className = "dashboard-bars";
      const values = widget.values || widget.data || [];
      const max = Math.max(1, ...values.map((entry) => Number(entry.value ?? entry.y ?? 0)));
      values.forEach((entry) => { const row = document.createElement("div"); const label = document.createElement("span"); label.textContent = entry.label ?? entry.x ?? ""; const bar = document.createElement("i"); bar.style.setProperty("--bar-size", `${Math.max(2, Number(entry.value ?? entry.y ?? 0) / max * 100)}%`); const amount = document.createElement("em"); amount.textContent = entry.value ?? entry.y ?? 0; row.append(label, bar, amount); chart.append(row); });
      card.append(chart); return;
    }
    if (type === "table") {
      const table = document.createElement("table"); const columns = widget.columns || Object.keys(widget.rows?.[0] || {});
      const head = document.createElement("thead"); const hr = document.createElement("tr"); columns.forEach((column) => { const th = document.createElement("th"); th.textContent = column.label || column; hr.append(th); }); head.append(hr); table.append(head);
      const body = document.createElement("tbody"); (widget.rows || []).forEach((row) => { const tr = document.createElement("tr"); columns.forEach((column) => { const key = column.key || column; const td = document.createElement("td"); td.textContent = row[key] ?? ""; tr.append(td); }); body.append(tr); }); table.append(body); card.append(table); return;
    }
    if (type === "progress") {
      const progress = document.createElement("div"); progress.className = "dashboard-progress"; const bar = document.createElement("i"); bar.style.setProperty("--progress", `${Math.min(100, Number(widget.value || 0))}%`); progress.append(bar); const value = document.createElement("span"); value.textContent = `${widget.value || 0}%`; progress.append(value); card.append(progress); return;
    }
    const alert = document.createElement("p"); alert.className = "dashboard-alert"; alert.dataset.tone = widget.tone || "info"; alert.textContent = widget.message || widget.value || "Alert"; card.append(alert);
  }

  async copy() {
    if (this.mode === "source") await this.loadSource();
    if (this.mode === "diff") await this.loadDiff();
    const content = this.mode === "diff" ? this.diff?.diff : this.mode === "source" ? this.source?.source : contentOf(this.artifact);
    await navigator.clipboard?.writeText(typeof content === "string" ? content : JSON.stringify(content, null, 2));
  }

  async download() {
    if (this.artifact.id) {
      try {
        const blob = await this.api.blob(`/api/artifacts/${encodeURIComponent(this.artifact.id)}/download`);
        const url = URL.createObjectURL(blob); this.objectUrls.push(url); const link = document.createElement("a"); link.href = url; link.download = this.artifact.filename || this.artifact.name || "artifact"; link.click(); return;
      } catch (error) {
        const stage = document.querySelector("[data-rich-artifact-stage]");
        if (stage) {
          stage.replaceChildren();
          const note = document.createElement("p");
          note.className = "route-note";
          note.textContent = `Download failed. ${error.message}`;
          stage.append(note);
        }
        return;
      }
    }
    const direct = this.artifact.download_url || this.artifact.url;
    if (direct && !this.artifact.id) { window.open(direct, "_blank", "noopener"); return; }
    const content = contentOf(this.artifact); const blob = new Blob([typeof content === "string" ? content : JSON.stringify(content, null, 2)], { type: this.artifact.media_type || "text/plain" });
    const url = URL.createObjectURL(blob); this.objectUrls.push(url); const link = document.createElement("a"); link.href = url; link.download = this.artifact.filename || this.artifact.name || "artifact.txt"; link.click();
  }

  share() {
    if (!this.artifact?.id) return;
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog artifact-share-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card"><header><div><small>External artifact</small><h2>Create share link</h2></div><button type="button" data-close>×</button></header><p>The link opens this artifact only. Workspace conversations, files, and credentials remain private.</p><label><span>Expires after</span><select name="expires_in_hours"><option value="24">24 hours</option><option value="168" selected>7 days</option><option value="720">30 days</option></select></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Create link</button></footer></form>`;
    document.body.append(dialog);
    const form = dialog.querySelector("form");
    dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector("[type='submit']");
      submit.disabled = true;
      try {
        const result = await this.api.post(`/api/artifacts/${encodeURIComponent(this.artifact.id)}/share`, { expires_in_hours: Number(new FormData(form).get("expires_in_hours")) }, { scope: "artifact-share" });
        await navigator.clipboard?.writeText(result.url);
        form.innerHTML = `<header><div><small>Share link ready</small><h2>Link copied</h2></div><button type="button" data-close>×</button></header><label><span>Public URL</span><input data-artifact-share-url readonly /></label><p data-artifact-share-expiry></p><footer><button class="primary" type="button" data-close>Done</button></footer>`;
        form.querySelector("[data-artifact-share-url]").value = result.url;
        form.querySelector("[data-artifact-share-expiry]").textContent = `Expires ${new Date(result.expires_at).toLocaleString()}`;
        form.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
      } catch (error) {
        submit.disabled = false;
        form.querySelector("p").textContent = error.message;
      }
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  close() {
    this.openToken += 1;
    this.revokeUrls();
    this.artifact = null;
    this.source = null;
    this.diff = null;
    const stage = document.querySelector("[data-artifact-stage]");
    if (stage) { stage.replaceChildren(); stage.hidden = true; }
  }

  revokeUrls() {
    this.objectUrls.forEach((url) => URL.revokeObjectURL(url));
    this.objectUrls = [];
    this.previewUrl = null;
  }
}

let singleton;
export function createArtifactsUI() { if (!singleton) { singleton = new ArtifactsUI(); singleton.init(); } return singleton; }

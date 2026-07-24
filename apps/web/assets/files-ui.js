import { chatApi } from "./chat-api.js?v=20260722-flow115";
import { icon, iconElement } from "./icons.js?v=20260724-icons2";

function items(payload) {
  if (Array.isArray(payload)) return payload;
  return payload?.files || payload?.items || [];
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function fileKind(file) {
  const type = String(file.content_type || "").toLowerCase();
  if (type.startsWith("image/")) return "image";
  if (type === "application/pdf") return "pdf";
  if (
    type.startsWith("text/") ||
    type === "application/json" ||
    type.endsWith("+json") ||
    type === "application/xml" ||
    type.endsWith("+xml") ||
    type.includes("javascript")
  ) return "text";
  return "binary";
}

export class FilesUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.files = [];
    this.selected = null;
    this.search = "";
    this.includeRunFiles = false;
    this.objectUrl = null;
    this.uploading = false;
  }

  init() {
    window.addEventListener("hashchange", () => this.route());
    this.root?.addEventListener("click", (event) => this.click(event));
    this.root?.addEventListener("input", (event) => this.input(event));
    this.root?.addEventListener("change", (event) => this.change(event));
    this.route();
  }

  route() {
    const active = window.location.hash.replace(/^#/, "").split("/")[0] === "files";
    if (!active) {
      if (this.root?.dataset.owner === "files") {
        this.releasePreview();
        this.root.hidden = true;
        this.root.replaceChildren();
        delete this.root.dataset.owner;
        document.querySelector("[data-app='taroai-workspace']")?.removeAttribute("data-rich-route");
      }
      return;
    }
    this.root.dataset.owner = "files";
    this.root.hidden = false;
    document.querySelector("[data-app='taroai-workspace']")?.setAttribute("data-rich-route", "files");
    this.renderShell();
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page files-page">
        <header class="capability-page-header"><div><p>Workspace drive</p><h1>Files</h1><span>Persistent inputs and outputs that can be reopened, pinned to Agents, and materialized into fresh runs.</span></div><div class="capability-header-actions"><button type="button" data-files-refresh>${icon("refresh-cw")}<span>Refresh</span></button><button class="primary" type="button" data-files-upload>${icon("upload")}<span>Upload files</span></button><input type="file" multiple hidden data-files-upload-input /></div></header>
        <div class="capability-toolbar files-toolbar"><label><span aria-hidden="true">${icon("search")}</span><input data-files-route-search type="search" placeholder="Search workspace files" /></label><label class="files-run-toggle"><input type="checkbox" data-files-include-runs /> Include run outputs</label><div data-files-route-count>0 files</div></div>
        <div class="files-product-layout"><section class="workspace-file-list" data-workspace-file-list><div class="route-loading">Loading workspace files…</div></section><aside class="workspace-file-inspector" data-workspace-file-inspector><div class="route-empty"><span>${icon("file")}</span><strong>Select a file</strong><p>Preview content, inspect provenance, attach it to Chat, or move it into a folder.</p></div></aside></div>
        <div class="route-toast" data-files-toast hidden></div>
      </section>`;
  }

  async load() {
    const query = new URLSearchParams({ include_run_files: String(this.includeRunFiles) });
    if (this.search) query.set("query", this.search);
    try {
      const payload = await this.api.get(`/api/workspaces/${encodeURIComponent(this.api.settings().workspaceId)}/files?${query}`);
      this.files = items(payload);
      this.selected = this.files.find((file) => file.id === this.selected?.id) || this.files[0] || null;
      this.renderList();
      await this.renderInspector();
    } catch (error) {
      this.files = [];
      this.renderList(error.message);
      this.toast(error.message, "error");
    }
  }

  renderList(error = "") {
    const target = this.root.querySelector("[data-workspace-file-list]");
    const count = this.root.querySelector("[data-files-route-count]");
    if (!target) return;
    count.textContent = `${this.files.length} file${this.files.length === 1 ? "" : "s"}`;
    target.replaceChildren();
    if (!this.files.length) {
      const empty = document.createElement("div");
      empty.className = "route-empty workspace-files-empty";
      const glyph = document.createElement("span");
      const heading = document.createElement("strong");
      const message = document.createElement("p");
      glyph.append(iconElement("file"));
      heading.textContent = error ? "Files unavailable" : "No workspace files yet";
      message.textContent = error || "Upload a durable input here, or attach a file in Chat. It will remain available after the run ends.";
      empty.append(glyph, heading, message);
      target.append(empty);
      return;
    }
    const heading = document.createElement("div");
    heading.className = "workspace-file-row workspace-file-heading";
    heading.innerHTML = "<span>File name</span><span>Source</span><span>Size</span><span>Updated</span>";
    target.append(heading);
    for (const file of this.files) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "workspace-file-row";
      row.dataset.workspaceFileId = file.id;
      row.classList.toggle("is-selected", file.id === this.selected?.id);
      const name = document.createElement("span");
      name.className = "workspace-file-name";
      const glyph = document.createElement("i");
      glyph.append(iconElement({ image: "image", text: "file-code", pdf: "file" }[fileKind(file)] || "file"));
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = file.logical_path || file.filename || file.id;
      const small = document.createElement("small");
      small.textContent = file.content_type || "application/octet-stream";
      copy.append(strong, small);
      name.append(glyph, copy);
      const source = document.createElement("span");
      source.textContent = file.run_id ? "Run output" : "Workspace";
      const size = document.createElement("span");
      size.textContent = formatBytes(file.size_bytes);
      const time = document.createElement("span");
      time.textContent = file.created_at ? new Date(file.created_at).toLocaleDateString() : "—";
      row.append(name, source, size, time);
      target.append(row);
    }
  }

  async renderInspector() {
    const target = this.root.querySelector("[data-workspace-file-inspector]");
    if (!target) return;
    this.releasePreview();
    target.replaceChildren();
    if (!this.selected) {
      target.innerHTML = `<div class="route-empty"><span>${icon("file")}</span><strong>Select a file</strong><p>Preview content and reuse it in a new Agent run.</p></div>`;
      return;
    }
    const file = this.selected;
    const header = document.createElement("header");
    header.className = "workspace-file-detail-header";
    const title = document.createElement("div");
    const eyebrow = document.createElement("small");
    eyebrow.textContent = file.run_id ? "RUN OUTPUT" : "WORKSPACE FILE";
    const heading = document.createElement("h2");
    heading.textContent = file.logical_path || file.filename;
    title.append(eyebrow, heading);
    const actions = document.createElement("div");
    actions.innerHTML = `<button type="button" data-file-use>${icon("paperclip")}<span>Use in Chat</span></button><button type="button" data-file-download>${icon("download")}<span>Download</span></button>`;
    header.append(title, actions);
    const facts = document.createElement("dl");
    facts.className = "workspace-file-facts";
    const factValues = [
      ["Type", file.content_type || "Unknown"],
      ["Size", formatBytes(file.size_bytes)],
      ["Object", file.storage_object_id || file.id],
      ["Agent pins", String(file.pinned_reference || 0)],
    ];
    for (const [label, value] of factValues) {
      const wrapper = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      wrapper.append(dt, dd);
      facts.append(wrapper);
    }
    const preview = document.createElement("section");
    preview.className = "workspace-file-preview";
    preview.innerHTML = '<div class="route-loading">Loading preview…</div>';
    const management = document.createElement("footer");
    management.className = "workspace-file-management";
    management.innerHTML = `<button type="button" data-file-rename>${icon("folder")}<span>Rename or move</span></button><button class="danger" type="button" data-file-delete>${icon("trash-2")}<span>Delete</span></button>`;
    target.append(header, facts, preview, management);
    await this.loadPreview(preview, file);
  }

  async loadPreview(target, file) {
    try {
      const blob = await this.api.blob(`/api/storage/objects/${encodeURIComponent(file.id)}/content`);
      const kind = fileKind(file);
      target.replaceChildren();
      if (kind === "text") {
        const pre = document.createElement("pre");
        const content = await blob.text();
        pre.textContent = content.slice(0, 12000);
        target.append(pre);
        return;
      }
      if (kind === "image" || kind === "pdf") {
        this.objectUrl = URL.createObjectURL(blob);
        if (kind === "image") {
          const image = document.createElement("img");
          image.src = this.objectUrl;
          image.alt = file.filename || "File preview";
          target.append(image);
        } else {
          const frame = document.createElement("iframe");
          frame.src = this.objectUrl;
          frame.title = file.filename || "PDF preview";
          target.append(frame);
        }
        return;
      }
      target.innerHTML = `<div class="route-empty compact"><span>${icon("download")}</span><strong>Preview unavailable</strong><p>Download this binary file or attach it directly to Chat.</p></div>`;
    } catch (error) {
      target.innerHTML = `<div class="route-empty compact"><span>${icon("triangle-alert")}</span><strong>Preview unavailable</strong><p></p></div>`;
      target.querySelector("p").textContent = error.message;
    }
  }

  async click(event) {
    if (this.root.dataset.owner !== "files") return;
    const row = event.target.closest("[data-workspace-file-id]");
    if (row) {
      this.selected = this.files.find((file) => file.id === row.dataset.workspaceFileId) || null;
      this.renderList();
      await this.renderInspector();
      return;
    }
    if (event.target.closest("[data-files-upload]")) this.root.querySelector("[data-files-upload-input]")?.click();
    if (event.target.closest("[data-files-refresh]")) this.load();
    if (event.target.closest("[data-file-use]")) this.useInChat();
    if (event.target.closest("[data-file-download]")) this.download();
    if (event.target.closest("[data-file-rename]")) this.rename();
    if (event.target.closest("[data-file-delete]")) this.remove();
  }

  input(event) {
    if (!event.target.matches("[data-files-route-search]")) return;
    window.clearTimeout(this.searchTimer);
    this.searchTimer = window.setTimeout(() => {
      this.search = event.target.value.trim();
      this.load();
    }, 180);
  }

  async change(event) {
    if (event.target.matches("[data-files-include-runs]")) {
      this.includeRunFiles = event.target.checked;
      await this.load();
      return;
    }
    if (!event.target.matches("[data-files-upload-input]") || !event.target.files?.length) return;
    this.uploading = true;
    this.toast(`Uploading ${event.target.files.length} file${event.target.files.length === 1 ? "" : "s"}…`, "loading");
    try {
      for (const file of event.target.files) await this.api.upload(file);
      await window.taroaiChat?.loadCapabilities?.();
      await this.load();
      this.toast("Workspace files uploaded", "success");
    } catch (error) {
      this.toast(error.message, "error");
    } finally {
      this.uploading = false;
      event.target.value = "";
    }
  }

  useInChat() {
    if (!this.selected) return;
    window.taroaiChat?.addExistingAttachment?.(this.selected);
    window.location.hash = "#chat";
  }

  async download() {
    if (!this.selected) return;
    try {
      const blob = await this.api.blob(`/api/storage/objects/${encodeURIComponent(this.selected.id)}/content`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = (this.selected.filename || "workspace-file").replaceAll("/", "-");
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      this.toast(error.message, "error");
    }
  }

  async rename() {
    if (!this.selected) return;
    const next = window.prompt("Rename or move this file", this.selected.logical_path || this.selected.filename)?.trim();
    if (!next || next === (this.selected.logical_path || this.selected.filename)) return;
    try {
      await this.api.patch(`/api/storage/objects/${encodeURIComponent(this.selected.id)}`, { filename: next }, { scope: "file-move" });
      await window.taroaiChat?.loadCapabilities?.();
      await this.load();
      this.toast("File path updated", "success");
    } catch (error) {
      this.toast(error.message, "error");
    }
  }

  async remove() {
    if (!this.selected || !window.confirm(`Delete ${this.selected.logical_path || this.selected.filename}?`)) return;
    try {
      await this.api.delete(`/api/storage/objects/${encodeURIComponent(this.selected.id)}`, { scope: "file-delete" });
      this.selected = null;
      await window.taroaiChat?.loadCapabilities?.();
      await this.load();
      this.toast("File deleted", "success");
    } catch (error) {
      this.toast(error.message, "error");
    }
  }

  toast(message, tone = "idle") {
    const target = this.root.querySelector("[data-files-toast]");
    if (!target) return;
    target.hidden = false;
    target.dataset.state = tone;
    target.textContent = message;
    window.clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => { target.hidden = true; }, 3600);
  }

  releasePreview() {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
  }
}

let singleton;
export function createFilesUI() {
  if (!singleton) {
    singleton = new FilesUI();
    singleton.init();
  }
  return singleton;
}

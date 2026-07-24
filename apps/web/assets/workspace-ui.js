import { chatApi } from "./chat-api.js?v=20260722-flow115";
import { icon } from "./icons.js?v=20260724-icons2";

function values(payload, key) {
  return Array.isArray(payload?.[key]) ? payload[key] : [];
}

function roleNames(member) {
  return (member.roles || member.role_ids || []).map((role) =>
    typeof role === "string" ? role : role.name || role.id,
  );
}

export class WorkspaceUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.organization = null;
    this.workspaces = [];
    this.members = [];
    this.invitations = [];
    this.permissions = [];
    this.manage = false;
  }

  init() {
    window.addEventListener("hashchange", () => this.route());
    window.addEventListener("taroai:auth-changed", (event) => {
      if (this.root?.dataset.owner !== "workspaces") return;
      if (event.detail?.authenticated) {
        this.load();
        return;
      }
      this.organization = null;
      this.workspaces = [];
      this.members = [];
      this.invitations = [];
      this.permissions = [];
      this.manage = false;
      this.render("Sign in to manage this organization.");
    });
    this.root?.addEventListener("click", (event) => this.click(event));
    this.root?.addEventListener("submit", (event) => this.submit(event));
    this.route();
  }

  route() {
    const active = window.location.hash.replace(/^#/, "").split("/")[0] === "workspaces";
    if (!active) {
      if (this.root?.dataset.owner === "workspaces") {
        this.root.hidden = true;
        this.root.replaceChildren();
        delete this.root.dataset.owner;
        document.querySelector("[data-app='taroai-workspace']")?.removeAttribute("data-rich-route");
      }
      return;
    }
    this.root.dataset.owner = "workspaces";
    this.root.hidden = false;
    document.querySelector("[data-app='taroai-workspace']")?.setAttribute("data-rich-route", "workspaces");
    this.renderShell();
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page workspace-page">
        <header class="capability-page-header">
          <div><p>Organization</p><h1 data-organization-name>Workspace</h1><span>Manage shared workspaces and the people who can use them.</span></div>
          <div class="capability-header-actions"><button type="button" data-organization-rename>${icon("settings")}<span>Rename</span></button><button type="button" class="primary" data-invitation-open>${icon("plus")}<span>Invite member</span></button></div>
        </header>
        <div class="workspace-overview" data-workspace-overview><div class="route-loading">Loading organization…</div></div>
        <dialog class="chat-dialog workspace-dialog" data-workspace-create-dialog><form method="dialog" data-workspace-create-form><header><div><small>NEW WORKSPACE</small><h2>Create workspace</h2></div><button type="button" data-dialog-close aria-label="Close">${icon("x")}</button></header><label><span>Workspace name</span><input name="name" minlength="1" required autocomplete="off" /></label><footer><button type="button" data-dialog-close>Cancel</button><button class="primary" type="submit">Create</button></footer></form></dialog>
        <dialog class="chat-dialog workspace-dialog" data-invitation-dialog><form method="dialog" data-invitation-form><header><div><small>ORGANIZATION ACCESS</small><h2>Invite a member</h2></div><button type="button" data-dialog-close aria-label="Close">${icon("x")}</button></header><label><span>Email</span><input name="email" type="email" required autocomplete="email" /></label><p>The invitation expires after 72 hours. The link is shown once so you can send it privately.</p><div class="workspace-invitation-link" data-invitation-link hidden><label><span>Invitation link</span><input readonly data-invitation-link-value /></label><button type="button" data-invitation-copy>Copy link</button></div><footer><button type="button" data-dialog-close>Close</button><button class="primary" type="submit">Create invitation</button></footer></form></dialog>
        <div class="route-toast" data-workspace-toast hidden></div>
      </section>`;
  }

  async load() {
    try {
      const payload = await this.api.get("/api/tenants/current");
      this.organization = payload.tenant || payload.organization || {
        id: payload.tenant_id,
        name: payload.tenant_name,
      };
      this.workspaces = values(payload, "workspaces");
      this.members = values(payload, "members");
      this.invitations = values(payload, "invitations");
      this.manage = Boolean(payload.can_manage);
      this.permissions = values(payload, "permissions").map((permission) =>
        typeof permission === "string" ? permission : `${permission.action}:${permission.resource}`,
      );
      this.render();
    } catch (error) {
      this.render(error.message);
    }
  }

  canManage() {
    return Boolean(
      this.manage ||
      this.permissions.some((permission) => permission === "organization.manage" || permission.startsWith("organization.manage:")),
    );
  }

  render(error = "") {
    const target = this.root.querySelector("[data-workspace-overview]");
    const title = this.root.querySelector("[data-organization-name]");
    if (!target || !title) return;
    title.textContent = this.organization?.name || "Workspaces";
    document.querySelector("[data-account-meta]").textContent = this.organization?.name || "Workspace";
    for (const button of this.root.querySelectorAll("[data-organization-rename], [data-invitation-open]")) {
      button.hidden = !this.canManage();
    }
    target.replaceChildren();
    if (error) {
      target.innerHTML = `<div class="route-empty"><span>${icon("triangle-alert")}</span><strong>Organization unavailable</strong><p></p><button type="button" data-workspace-refresh>Retry</button></div>`;
      target.querySelector("p").textContent = error;
      return;
    }
    target.append(this.renderWorkspaces(), this.renderMembers());
  }

  renderWorkspaces() {
    const section = document.createElement("section");
    section.className = "workspace-management-section workspace-list-section";
    const header = document.createElement("header");
    header.innerHTML = `<div><small>SHARED CONTEXT</small><h2>Workspaces</h2><p>Chats, files, Agents, and Skills stay inside the selected workspace.</p></div>`;
    if (this.canManage()) {
      const create = document.createElement("button");
      create.type = "button";
      create.dataset.workspaceCreateOpen = "";
      create.innerHTML = `${icon("plus")}<span>New workspace</span>`;
      header.append(create);
    }
    const list = document.createElement("div");
    list.className = "workspace-management-list";
    const currentId = this.api.settings().workspaceId;
    for (const workspace of this.workspaces) {
      const row = document.createElement("article");
      row.className = "workspace-management-row";
      row.classList.toggle("is-current", workspace.id === currentId);
      const copy = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = workspace.name || workspace.id;
      const meta = document.createElement("small");
      meta.textContent = workspace.id === currentId ? "Current workspace" : workspace.id;
      copy.append(name, meta);
      const actions = document.createElement("div");
      if (workspace.id !== currentId) {
        const open = document.createElement("button");
        open.type = "button";
        open.dataset.workspaceSelect = workspace.id;
        open.innerHTML = `${icon("arrow-right")}<span>Open</span>`;
        actions.append(open);
      }
      if (this.canManage()) {
        const rename = document.createElement("button");
        rename.type = "button";
        rename.dataset.workspaceRename = workspace.id;
        rename.dataset.workspaceName = workspace.name || workspace.id;
        rename.innerHTML = `${icon("settings")}<span>Rename</span>`;
        actions.append(rename);
      }
      row.append(copy, actions);
      list.append(row);
    }
    if (!this.workspaces.length) list.innerHTML = `<div class="route-empty compact"><span>${icon("grid-2x2")}</span><strong>No workspaces</strong><p>Create the first shared workspace.</p></div>`;
    section.append(header, list);
    return section;
  }

  renderMembers() {
    const section = document.createElement("section");
    section.className = "workspace-management-section workspace-members-section";
    const header = document.createElement("header");
    const activeMembers = this.members.filter((member) => member.status === "active").length;
    header.innerHTML = `<div><small>ACCESS</small><h2>Members</h2><p>${activeMembers} active member${activeMembers === 1 ? "" : "s"}.</p></div>`;
    const list = document.createElement("div");
    list.className = "workspace-management-list";
    const currentUserId = this.api.settings().userId;
    for (const member of this.members) {
      const roles = roleNames(member);
      const row = document.createElement("article");
      row.className = "workspace-management-row";
      const copy = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = member.display_name || member.email || member.id;
      const meta = document.createElement("small");
      meta.textContent = `${member.email || ""}${member.email ? " · " : ""}${roles.join(", ") || member.status || "member"}`;
      copy.append(name, meta);
      row.append(copy);
      const isOwner = roles.some((role) => role === "tenant_owner" || role.toLowerCase().includes("owner"));
      if (this.canManage() && member.id !== currentUserId && !isOwner) {
        const action = document.createElement("button");
        action.type = "button";
        if (member.status === "disabled") {
          action.dataset.memberRestore = member.id;
          action.textContent = "Restore";
        } else {
          action.className = "danger";
          action.dataset.memberRemove = member.id;
          action.textContent = "Remove";
        }
        row.append(action);
      }
      list.append(row);
    }
    const pendingInvitations = this.invitations.filter((item) => item.status === "pending");
    if (this.canManage() && pendingInvitations.length) {
      const invitations = document.createElement("div");
      invitations.className = "workspace-invitations";
      const label = document.createElement("h3");
      label.textContent = "Pending invitations";
      invitations.append(label);
      for (const invitation of pendingInvitations) {
        const row = document.createElement("article");
        row.className = "workspace-management-row";
        const copy = document.createElement("div");
        const email = document.createElement("strong");
        email.textContent = invitation.email;
        const expires = document.createElement("small");
        expires.textContent = invitation.expires_at ? `Expires ${new Date(invitation.expires_at).toLocaleString()}` : "Pending";
        copy.append(email, expires);
        const revoke = document.createElement("button");
        revoke.type = "button";
        revoke.dataset.invitationRevoke = invitation.id;
        revoke.textContent = "Revoke";
        row.append(copy, revoke);
        invitations.append(row);
      }
      section.append(header, list, invitations);
      return section;
    }
    section.append(header, list);
    return section;
  }

  click(event) {
    const control = event.target?.closest?.("button");
    if (!control) return;
    if (control.matches("[data-dialog-close]")) return control.closest("dialog")?.close();
    if (control.matches("[data-workspace-refresh]")) return this.load();
    if (control.matches("[data-workspace-create-open]")) return this.root.querySelector("[data-workspace-create-dialog]")?.showModal();
    if (control.matches("[data-invitation-open]")) return this.root.querySelector("[data-invitation-dialog]")?.showModal();
    if (control.matches("[data-invitation-copy]")) return this.copyInvitationLink();
    if (control.matches("[data-workspace-select]")) return this.selectWorkspace(control.dataset.workspaceSelect);
    if (control.matches("[data-organization-rename]")) return this.renameOrganization();
    if (control.matches("[data-workspace-rename]")) return this.renameWorkspace(control.dataset.workspaceRename, control.dataset.workspaceName);
    if (control.matches("[data-invitation-revoke]")) return this.revokeInvitation(control.dataset.invitationRevoke);
    if (control.matches("[data-member-remove]")) return this.removeMember(control.dataset.memberRemove);
    if (control.matches("[data-member-restore]")) return this.restoreMember(control.dataset.memberRestore);
  }

  async submit(event) {
    if (event.target.matches("[data-workspace-create-form]")) {
      event.preventDefault();
      const name = new FormData(event.target).get("name").trim();
      await this.mutate(() => this.api.post("/api/workspaces", { name }, { scope: "workspace-create" }), "Workspace created");
      event.target.closest("dialog")?.close();
    }
    if (event.target.matches("[data-invitation-form]")) {
      event.preventDefault();
      const email = new FormData(event.target).get("email").trim();
      await this.createInvitation(email);
    }
  }

  async renameOrganization() {
    const name = window.prompt("Organization name", this.organization?.name || "");
    if (name?.trim()) await this.mutate(() => this.api.patch("/api/tenants/current", { name: name.trim() }, { scope: "organization-rename" }), "Organization renamed");
  }

  async renameWorkspace(id, currentName) {
    const name = window.prompt("Workspace name", currentName || "");
    if (name?.trim()) await this.mutate(() => this.api.patch(`/api/workspaces/${encodeURIComponent(id)}`, { name: name.trim() }, { scope: "workspace-rename" }), "Workspace renamed");
  }

  async createInvitation(email) {
    try {
      const invitation = await this.api.post("/api/tenants/current/invitations", { email }, { scope: "member-invite" });
      const token = invitation.token || invitation.invitation_token;
      const link = invitation.invite_url || invitation.invitation_url || (token
        ? `${window.location.origin}/?tenantId=${encodeURIComponent(this.organization.id)}&invite=${encodeURIComponent(token)}#workspaces`
        : "");
      const result = this.root.querySelector("[data-invitation-link]");
      const value = this.root.querySelector("[data-invitation-link-value]");
      if (result && value && link) {
        value.value = link;
        result.hidden = false;
      }
      this.toast("Invitation created", "success");
      await this.load();
    } catch (error) {
      this.toast(error.message, "error");
    }
  }

  async copyInvitationLink() {
    const value = this.root.querySelector("[data-invitation-link-value]");
    if (!value?.value) return;
    try {
      await navigator.clipboard.writeText(value.value);
      this.toast("Invitation link copied", "success");
    } catch {
      value.select();
      this.toast("Copy the selected invitation link", "error");
    }
  }

  revokeInvitation(id) {
    return this.mutate(() => this.api.delete(`/api/tenants/current/invitations/${encodeURIComponent(id)}`, { scope: "invitation-revoke" }), "Invitation revoked");
  }

  removeMember(id) {
    if (!window.confirm("Remove this member from the organization?")) return;
    return this.mutate(() => this.api.delete(`/api/tenants/current/members/${encodeURIComponent(id)}`, { scope: "member-remove" }), "Member removed");
  }

  restoreMember(id) {
    return this.mutate(
      () => this.api.post(`/api/tenants/current/members/${encodeURIComponent(id)}/restore`, {}, { scope: "member-restore" }),
      "Member restored",
    );
  }

  selectWorkspace(id) {
    const input = document.querySelector("#workspace-id");
    localStorage.setItem("taroai.workspaceId", id);
    if (input) {
      input.value = id;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    window.dispatchEvent(new CustomEvent("taroai:workspace-changed", { detail: { workspaceId: id } }));
    document.querySelector("[data-new-chat]")?.click();
  }

  async mutate(operation, message) {
    try {
      await operation();
      this.toast(message, "success");
      await this.load();
    } catch (error) {
      this.toast(error.message, "error");
    }
  }

  toast(message, tone = "success") {
    const toast = this.root.querySelector("[data-workspace-toast]");
    if (!toast) return;
    toast.textContent = message;
    toast.dataset.tone = tone;
    toast.hidden = false;
    window.setTimeout(() => { toast.hidden = true; }, 3200);
  }
}

export function createWorkspaceUI() {
  const ui = new WorkspaceUI();
  ui.init();
  return ui;
}

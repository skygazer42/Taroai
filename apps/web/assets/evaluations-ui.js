import { chatApi } from "./chat-api.js";

function list(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return Array.isArray(value?.items) ? value.items : [];
}

export class EvaluationsUI {
  constructor(api = chatApi) {
    this.api = api;
    this.root = document.querySelector("[data-product-route-experience]");
    this.suites = [];
    this.agents = [];
    this.runs = [];
    this.selectedAgentId = "";
  }

  init() {
    window.addEventListener("hashchange", () => this.route());
    this.root?.addEventListener("click", (event) => this.click(event));
    this.root?.addEventListener("submit", (event) => this.submit(event));
    this.root?.addEventListener("change", (event) => this.change(event));
    this.route();
  }

  route() {
    const active = window.location.hash.replace(/^#/, "").split("/")[0] === "evaluations";
    if (!active) {
      if (this.root?.dataset.owner === "evaluations") {
        this.root.hidden = true;
        this.root.replaceChildren();
        delete this.root.dataset.owner;
        document.querySelector("[data-app='taroai-workspace']")?.removeAttribute("data-rich-route");
      }
      return;
    }
    this.root.dataset.owner = "evaluations";
    this.root.hidden = false;
    document.querySelector("[data-app='taroai-workspace']")?.setAttribute("data-rich-route", "evaluations");
    this.renderShell();
    this.load();
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="capability-page evaluations-page">
        <header class="capability-page-header"><div><p>Quality gates</p><h1>Evaluations</h1><span>Golden cases, release thresholds, regression baselines, and evidence.</span></div><div class="capability-header-actions"><button data-evaluation-new>New suite</button><button class="primary" data-evaluations-refresh>Refresh</button></div></header>
        <div class="evaluation-target-bar"><label><span>Agent target</span><select data-evaluation-agent><option value="">Select an Agent</option></select></label><div data-evaluation-target-state>Select a versioned Agent to inspect its release evidence.</div></div>
        <div class="evaluation-product-layout"><section><div class="evaluation-section-title"><div><small>Immutable definitions</small><h2>Suites</h2></div></div><div class="evaluation-suite-grid" data-evaluation-suites><div class="route-loading">Loading suites…</div></div></section><aside><div class="evaluation-section-title"><div><small>Target history</small><h2>Runs</h2></div></div><div class="evaluation-run-stack" data-evaluation-runs><div class="route-empty"><strong>No Agent selected</strong><p>Select an Agent to view scores and release gates.</p></div></div></aside></div>
        <div class="route-toast" data-evaluation-toast hidden></div>
      </section>`;
  }

  async load() {
    try {
      const workspace = encodeURIComponent(this.api.settings().workspaceId);
      const [suites, agents] = await Promise.all([
        this.api.get("/api/evaluations/suites?target_kind=agent"),
        this.api.get(`/api/agents?workspace_id=${workspace}`),
      ]);
      this.suites = list(suites, "suites");
      this.agents = list(agents, "agents");
      this.renderSuites();
      const selector = this.root.querySelector("[data-evaluation-agent]");
      for (const agent of this.agents) {
        const option = document.createElement("option");
        option.value = agent.id || agent.agent_id;
        option.textContent = `${agent.name || "Untitled Agent"} · v${agent.latest_version || agent.version || 1}`;
        selector.append(option);
      }
      if (this.selectedAgentId && this.agents.some((agent) => (agent.id || agent.agent_id) === this.selectedAgentId)) {
        selector.value = this.selectedAgentId;
        await this.loadRuns();
      }
    } catch (error) { this.toast(error.message, "error"); }
  }

  renderSuites() {
    const root = this.root.querySelector("[data-evaluation-suites]");
    root.replaceChildren();
    if (!this.suites.length) {
      root.innerHTML = `<div class="route-empty"><strong>No evaluation suites</strong><p>Create a suite with one or more golden cases.</p></div>`;
      return;
    }
    for (const record of this.suites) {
      const suite = record.suite || record;
      const card = document.createElement("article");
      card.className = "evaluation-suite-card";
      card.innerHTML = `<header><span>v${suite.version}</span><small>${suite.target_kind}</small></header><h3></h3><p></p><div class="evaluation-thresholds"><span>Score ≥ ${Math.round((suite.gate?.minimum_score || 0) * 100)}%</span><span>Success ≥ ${Math.round((suite.gate?.minimum_success_rate || 0) * 100)}%</span><span>${suite.cases?.length || 0} cases</span></div><footer><button data-evaluation-run-suite="${suite.id}@@${suite.version}">Run on selected Agent</button></footer>`;
      card.querySelector("h3").textContent = suite.id;
      card.querySelector("p").textContent = suite.description || "Versioned release quality contract";
      root.append(card);
    }
  }

  async loadRuns() {
    if (!this.selectedAgentId) { this.runs = []; this.renderRuns(); return; }
    this.runs = list(await this.api.get(`/api/evaluations/runs?target_id=${encodeURIComponent(this.selectedAgentId)}&target_kind=agent`), "runs");
    this.renderRuns();
    const agent = this.agents.find((item) => (item.id || item.agent_id) === this.selectedAgentId);
    this.root.querySelector("[data-evaluation-target-state]").textContent = `${agent?.name || "Agent"} · ${this.runs.length} evaluation runs`;
  }

  renderRuns() {
    const root = this.root.querySelector("[data-evaluation-runs]");
    root.replaceChildren();
    if (!this.runs.length) {
      root.innerHTML = `<div class="route-empty"><strong>No evaluation runs</strong><p>Bind a suite in the Agent editor or run one from here.</p></div>`;
      return;
    }
    for (const run of this.runs) {
      const card = document.createElement("article");
      card.className = `evaluation-run-card is-${run.status}`;
      card.innerHTML = `<header><div><small>${run.suite_id} · ${run.suite_version}</small><strong>${Math.round((run.metrics?.weighted_score || 0) * 100)}%</strong></div><span>${run.promotion_gate?.allowed ? "Release ready" : "Blocked"}</span></header><div class="evaluation-metrics"><span><small>Success</small>${Math.round((run.metrics?.success_rate || 0) * 100)}%</span><span><small>Tool errors</small>${Math.round((run.metrics?.tool_error_rate || 0) * 100)}%</span><span><small>P95</small>${Number(run.metrics?.p95_latency_seconds || 0).toFixed(1)}s</span><span><small>Cost</small>$${Number(run.metrics?.total_cost || 0).toFixed(2)}</span></div><p>${run.promotion_gate?.reasons?.join(" · ") || "All configured thresholds passed."}</p><footer><button data-evaluation-evidence="${run.id}">Evidence</button>${run.promotion_gate?.allowed ? `<button data-evaluation-baseline="${run.id}">Set baseline</button>` : ""}</footer>`;
      root.append(card);
    }
  }

  change(event) {
    if (!event.target.matches("[data-evaluation-agent]")) return;
    this.selectedAgentId = event.target.value;
    this.loadRuns().catch((error) => this.toast(error.message, "error"));
  }

  click(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.matches("[data-evaluations-refresh]")) return this.load();
    if (button.matches("[data-evaluation-new]")) return this.openSuiteEditor();
    if (button.dataset.evaluationRunSuite) return this.runSuite(button.dataset.evaluationRunSuite);
    if (button.dataset.evaluationEvidence) return this.openEvidence(button.dataset.evaluationEvidence);
    if (button.dataset.evaluationBaseline) return this.promoteBaseline(button.dataset.evaluationBaseline);
  }

  submit(event) {
    if (!event.target.matches("[data-evaluation-suite-form]")) return;
    event.preventDefault();
    this.saveSuite(event.target);
  }

  openSuiteEditor() {
    const example = { id: "agent-quality", version: "1.0.0", target_kind: "agent", description: "Release checks for reusable Agents", cases: [{ id: "basic-request", version: "1", input: { request: "Produce a concise result." }, input_schema: { type: "object", properties: { request: { type: "string" } }, required: ["request"] }, expected: { scorer: "contains", contains: ["result"] }, critical: true }], gate: { minimum_score: 0.85, minimum_success_rate: 0.9, maximum_tool_error_rate: 0.05, maximum_human_intervention_rate: 0.1 } };
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-editor-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card evaluation-suite-editor" data-evaluation-suite-form><header><div><small>Immutable version</small><h2>New evaluation suite</h2></div><button type="button" data-close>×</button></header><label><span>Suite JSON</span><textarea name="suite" rows="22"></textarea></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Register suite</button></footer></form>`;
    dialog.querySelector("textarea").value = JSON.stringify(example, null, 2);
    dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog); dialog.showModal();
  }

  async saveSuite(form) {
    const submit = form.querySelector("[type='submit']"); submit.disabled = true;
    try {
      const suite = JSON.parse(new FormData(form).get("suite"));
      await this.api.post("/api/evaluations/suites", suite, { scope: "evaluation-suite" });
      form.closest("dialog")?.close(); this.toast("Evaluation suite registered", "success"); await this.load();
    } catch (error) { submit.disabled = false; this.toast(error.message, "error"); }
  }

  async promoteBaseline(runId) {
    try { await this.api.post(`/api/evaluations/runs/${encodeURIComponent(runId)}/baseline`, {}, { scope: "evaluation-baseline" }); this.toast("Baseline promoted", "success"); }
    catch (error) { this.toast(error.message, "error"); }
  }

  async runSuite(binding) {
    if (!this.selectedAgentId) return this.toast("Select an Agent target first", "error");
    const agent = this.agents.find((item) => (item.id || item.agent_id) === this.selectedAgentId);
    const version = agent?.latest_version || agent?.version || 1;
    const [suiteId, suiteVersion] = binding.split("@@");
    try {
      await this.api.post(`/api/evaluations/agents/${encodeURIComponent(this.selectedAgentId)}/versions/${encodeURIComponent(version)}/run`, { suite_id: suiteId, suite_version: suiteVersion }, { scope: "agent-evaluation" });
      this.toast("Evaluation completed", "success"); await this.loadRuns();
    } catch (error) { this.toast(error.message, "error"); }
  }

  async openEvidence(runId) {
    try {
      const evidence = await this.api.get(`/api/evaluations/runs/${encodeURIComponent(runId)}/evidence`);
      const dialog = document.createElement("dialog"); dialog.className = "chat-dialog agent-editor-dialog";
      dialog.innerHTML = `<div class="chat-dialog-card evaluation-evidence-dialog"><header><div><small>Redaction-safe record</small><h2>Evaluation evidence</h2></div><button type="button" data-close>×</button></header><pre></pre><footer><button type="button" data-close>Close</button></footer></div>`;
      dialog.querySelector("pre").textContent = JSON.stringify(evidence, null, 2);
      dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
      dialog.addEventListener("close", () => dialog.remove()); document.body.append(dialog); dialog.showModal();
    } catch (error) { this.toast(error.message, "error"); }
  }

  toast(message, state = "idle") {
    const toast = this.root.querySelector("[data-evaluation-toast]");
    if (!toast) return; toast.hidden = false; toast.dataset.state = state; toast.textContent = message;
    window.clearTimeout(this.toastTimer); this.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4000);
  }
}

export function createEvaluationsUI(api = chatApi) {
  const ui = new EvaluationsUI(api); ui.init(); return ui;
}

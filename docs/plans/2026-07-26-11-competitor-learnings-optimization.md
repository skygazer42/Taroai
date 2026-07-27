# Competitor Learnings and Runtime Optimization Plan

**Goal:** Absorb the strongest mechanisms from competing agent products (Creao, Manus, Jules, ChatGPT Agent, Flowith, Genspark, n8n, Relevance AI, Gumloop, Dust, Zapier Agents, Lindy) into Taroai, and land the agent-loop performance fixes identified in the 2026-07-26 runtime review.

**Method:** Two parallel web-research sweeps (general autonomous agents; business workflow platforms) plus a full code review of `taroai/agent/loop.py`, `taroai/agent/runtime.py`, `taroai/store.py`, `taroai/db/repository.py`, and the dispatch/SSE layer in `app.py`. Every adoption below is mapped to an existing Taroai primitive — nothing here requires a new subsystem from scratch.

---

## Part 1: Performance fixes landed on 2026-07-26

| Fix | Files | Effect |
|---|---|---|
| Run-scoped billing SUM replaces tenant-wide meter scan (was 2 full scans per iteration) | `db/repository.py`, `store.py`, `agent/loop.py`, migration `049` | Budget check drops from O(tenant history) to one indexed aggregate |
| `ModelBudgetGuard` early-returns when no limits configured (defaults are all 0) | `model_gateway/budget.py` | Removes a tenant meter scan per model call in the common case |
| `_decide` fetches agent actions once and threads the list into `_repeated_failed_action` / `_repeats_successful_action` / `_matching_action_observations` | `agent/loop.py` | Cuts up to 3 duplicate full-table reads per decide cycle |
| Second-precision timestamp moved out of the static chat system prompt into a separate minute-granularity system message; decide-JSON timestamp rounded to minutes | `agent/loop.py` | Static prompt prefix is byte-stable → provider prefix caching can hit; the 6 KB decide controller prompt was already stable |
| `_persist_checkpoint` derives the next sequence from `state.checkpoint_sequence` (query fallback only for fresh/restored state) | `agent/loop.py` | One fewer round-trip per checkpoint |
| `_ensure_coding_workspace` early-returns via cached `runtime_metadata["coding_workspace_id"]` | `agent/runtime.py` | Removes a per-step workspace list scan |
| In-memory store event listing filters before deep-copying; `update_run_status` now holds the repository lock | `store.py` | SSE polling no longer deep-copies already-delivered events inside the global lock; fixes a lost-update race |

## Part 2: Performance backlog (reviewed, not yet landed)

Ordered by impact; all verified against code. Status updated 2026-07-26 evening.

1. ~~**Queue dispatch as production default.**~~ **Resolved at the deployment layer:** `.env.example`, k8s configmap, helm values, and the release compose all set `TAROAI_RUN_EXECUTION_DISPATCH_MODE=queue`; the `inline` code default only covers dev/test.
2. ~~**Stop full-state dump per graph node.**~~ **Landed:** `_route` returns the Pydantic state instance (commit `2d05b05`). Observation compaction became recoverable paging via `observation.read` (commit `dd692ac`). Remaining: cap in-state observation growth itself.
3. ~~**Event-driven SSE.**~~ **Landed:** asyncio `ThreadEventHub` + store notifier hook + async endpoint (commit `c258a2f`). Redis pub/sub only needed for multi-replica API fan-out later.
4. **Batch bookkeeping writes — partially landed:** repository-level existence checks dropped from `append_run_event`/`save_runtime_state` (commit `7cfcd8a`). Remaining: `append_run_events(batch)` API and node-boundary save coalescing with a dirty flag.
5. **HTTP connection pooling.** In progress (httpx swap across model gateway, embeddings, web search, connectors, guardrails, observability).
6. **Serialize-once checkpoint chain.** Commit path does `model_copy(deep=True)` + `model_dump` + a second canonical `json.dumps` for the checksum, and the store serializes the dict a third time; sandbox snapshot API is called for every checkpoint even when no filesystem state changed. (`loop.py:2649`, `loop.py:3839`)
7. **Cache per-run invariants.** Connector tools, skill summaries, workflow-task lookup, and tool schemas are rebuilt every decide cycle; cache in `runtime_metadata`, invalidate on connector reconnect. (`loop.py:1213–1330`)
8. **Terminal-path scans.** Completion-key dedup and memory capture scan the full run-event stream at finalization; record flags in `runtime_metadata` instead. (`loop.py:3140`, `loop.py:3078`)
9. **Budget/timeout failures should synthesize.** A run that exhausts iterations after 11 successful tool calls surfaces nothing; add a bounded best-effort `_stream_final_response` in the fail node for budget-class failures. (`loop.py:3309`)
10. **Rolling thread summaries.** `_conversation_context` re-pays a summarization LLM call per run in long threads; persist a per-thread rolling summary keyed by last-summarized sequence. (`loop.py:797`)
11. **Cleanups:** single `AgentExecutionServices` instance (17 fresh constructions), unified guardrail ladder (3 copies), unified `_fail_for_*` shims (7 copies), remove or TTL the `pending_states` dict, wire or delete `max_step_retries` (dead static-retry machinery). (`runtime.py`)

## Part 3: Competitor mechanisms to adopt

Ranked by leverage × fit with existing primitives. Sources: see research appendix links in section notes.

### P1 — Run → Playbook: deterministic re-execution (Creao Super Agent; Manus Playbooks; Mariner Teach & Repeat)
The defining mechanism of the category leader: one click freezes a successful run into a parameterized, scheduled app whose re-executions involve **no LLM** — zero hallucination, near-zero token cost.
**Taroai mapping:** compile a completed run's `agent_actions` trace (decision + observation per step) into a static `WorkflowSpec`; execute via the existing coordinator with the (currently dead) static-plan path in `nodes.py`; bind to the existing cron `triggers` module; fall back to LLM repair on tool failure or output-schema drift — a reliability improvement over Creao's all-or-nothing determinism. This also directly eliminates the loop's entire per-iteration cost for repeat workloads.

### P2 — Editable plan approval gate (Google Jules)
Agent proposes its step plan before acting; user edits/rejects; auto-approves after a TTL. Catches wrong approaches at their cheapest point.
**Taroai mapping:** a `plan_pending` pause state emitting the plan as an editable approval item through the existing approvals system.

### P3 — Graduated per-tool autonomy + argument-level rules (Relevance AI; Gumloop)
Per-tool approval modes (auto-run / approval-required / let-agent-decide), max-auto-run counters, read/write asymmetry, and predicate rules over actual tool arguments ("approve only when emailing an external domain").
**Taroai mapping:** `approval_mode` + `max_auto_runs` fields on skills-registry entries; a JSONLogic/CEL predicate layer in the Tool Gateway evaluated against the proposed tool-call JSON before dispatch; rejected calls feed the approvals queue with a tool+intent+args card, and the rejection reason is fed back to the loop as steering.

### P4 — Shareable run replays (Manus)
A public read-only link replays the whole run step-by-step: support artifact, documentation, and a viral template gallery seed.
**Taroai mapping:** the `sharing/` module already issues thread links; extend to runs — a token-scoped read-only rendering of the run-event timeline + artifacts.

### P5 — Sandbox environment snapshots that are actually reused (Jules)
Verified setup snapshot per project/agent; every task boots warm. Manus's disposable-VM reinstall tax is its top user complaint.
**Taroai mapping:** `_capture_reusable_runtime_snapshot` already exists but runs synchronously on every successful run and is rarely consumed. Invert it: capture lazily/async (on agent publish or explicit save), store the E2B template/snapshot ref on the agent version, and boot `_ensure_sandbox_session` from it.

### P6 — Eval/regression harness over recorded runs (n8n Evaluations)
Datasets seeded from real past runs, replayed with numeric metrics (deterministic + LLM-judge), compared release-over-release. The moat feature for maintainability; no general-agent product has it.
**Taroai mapping:** `skills/evaluation.py` + the evaluations UI stub already exist; add a runner that replays recorded run inputs through the loop (sandboxed), scores via the model gateway, and stores metric series next to run history. Pin-data/resume-from-step (n8n/Gumloop) reuses the same recorded step I/O.

### P7 — Per-step cost surfacing, estimate before / actual after (Gumloop; Dust — Genspark as anti-pattern)
Genspark's 82 % one-star Trustpilot is driven by opaque credit burn; Gumloop shows per-node cost in the run log.
**Taroai mapping:** billing meters are already recorded per model/tool call with `run_id` — attribute them to steps and render inline in the operations-drawer timeline; show model-tier multiplier before run start (per-trigger default tier). Frontend hooks exist (`ops-timeline`, statusbar).

### P8 — Memory from corrections (Jules)
Mid-run steering and approval rejections are distilled into durable preferences applied to future runs.
**Taroai mapping:** on terminal state, feed `state.steering_messages` + rejection reasons through the existing `_capture_agent_session_memory` path with a "correction" type, injected into future planner prompts (user+workspace scope).

### P9 — "Needs action" queue + agent versioning (Zapier; Lindy)
A first-class inbox of runs blocked on humans; named restorable versions of agent config.
**Taroai mapping:** unify pending approvals + `request_input` waits + failed runs into one queue endpoint + UI tab; agent registry already versions (`get_version`) — add restore + named versions.

### P10 — NL→cron compiler + governed webhook sources (Dust)
"Weekdays 8am Pacific" compiled to a concrete schedule echoed back for confirmation; admin-created webhook sources with NL-generated event filters and request history.
**Taroai mapping:** thin LLM front on the existing `triggers` module; add Relevance-style anti-reentrancy (defer a cron fire while a run is active — one guard clause).

### Deliberately not adopted
- Multimodal generation (video/image/voice) — different track; speech already exists.
- Flowith-style infinite canvas — the operations drawer timeline covers run legibility at lower complexity.
- Genspark cross-model retry orchestration — the model gateway's policy/fallback already covers the useful subset.

## Part 4: Suggested sequencing

1. **Now (perf):** backlog items 1–5 (queue default, node-dump elimination, SSE push, write batching, httpx pooling).
2. **Next (product, high leverage):** P1 Run→Playbook MVP (compiler + static executor + cron binding), P2 plan gate, P7 cost surfacing (small, mostly UI).
3. **Then:** P3 autonomy dials, P4 replays, P5 warm snapshots, P8 correction memory.
4. **Later:** P6 eval harness (needs P1's replay machinery), P9, P10.

Cross-cutting rule from the research: **never let cost be a surprise** — every new mechanism surfaces estimated cost before and actual cost after execution.

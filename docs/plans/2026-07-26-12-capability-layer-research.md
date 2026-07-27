# Capability Layer Research and Adoption Plan

**Goal:** Upgrade Taroai's capability layer — execution (sandbox/browser/artifacts/terminal), cognition (context engineering, planning/verification, orchestration, routing), and knowledge (memory, skills, tools, retrieval) — based on a 2026-07 state-of-the-art sweep. Companion to `2026-07-26-11-competitor-learnings-optimization.md` (product mechanisms); this document covers what the agent *can do* and how cheaply/reliably it does it.

**Method:** Three parallel research sweeps (execution infra; cognition techniques; knowledge/memory/skills), each mapped against the corresponding Taroai modules (`sandbox/` adapters, browser controller, `agent/loop.py` LangGraph loop, `skills/`, `memory/`, `knowledge/`, `connectors/`, tool gateway). Full sources inline.

---

## Part 1: Highest-leverage adoptions (cross-cutting ranking)

Ranked across all three sweeps by (expected impact) × (fit with existing code) ÷ (risk).

### C1. KV-cache-stable, append-only context discipline — *cognition*
Manus's production metric #1: KV-cache hit rate (cached input ~10× cheaper). Rules: byte-stable prefix (no timestamps — **partially landed 2026-07-26**), append-only message layout, never swap tool definitions mid-run (mask, don't remove), deterministic serialization. Our last-8 observation *sliding window* rewrites the prefix every turn and defeats caching — replace windowing with in-place stub substitution (see C2).
**Map:** `loop.py` message assembly + `_model_observations`; gateway cache breakpoints after system prompt and tool defs.
Source: [Manus context engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)

### C2. Recoverable compression of observations — *cognition*
Our 12 KB observation cap and 24 KB conversation summarization are irreversible truncation — the exact anti-pattern Manus warns about. Change: persist every full observation (already durable in `agent_actions`); in context, truncate to the cap **plus a retrieval handle** (`full output: obs/step7.json — re-read if needed`) and give the loop a `read_observation` tool. Converts caps into just-in-time paging. Anthropic's context-editing evidence: +29–39 % on agentic evals, −84 % tokens on long-horizon tasks.
**Map:** `_model_observations` + one new tool-gateway entry; composes with checkpoint persistence for free.
Sources: [Anthropic context management](https://www.anthropic.com/news/context-management), [effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### C3. SKILL.md open standard + progressive disclosure — *knowledge*
Agent Skills became a cross-vendor open standard (32+ tools, 400 K-skill marketplaces, governance donated to Agentic AI Foundation). Three-tier loading: name+description (~100 tokens) → full body on activation (<5 K) → scripts on execution. Adopting the format gives free interop and bounds skill context cost at 100+ skills; our `skills/` registry (manifest/discovery/materializer/import) maps 1:1 onto the tiers.
**Map:** manifest emits/consumes SKILL.md; discovery injects tier-1 only; materializer loads tier-2/3 on demand; BM25 over metadata past ~50 skills.
Sources: [agentskills.io](https://agentskills.io/home), [progressive disclosure](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)

### C4. Sandbox snapshot → pause/resume + fork semantics — *execution*
The industry differentiator moved from "run code safely" to live-state snapshot/fork: E2B pause/resume preserves memory+filesystem; Morph/Mitos fork a running VM into N copies in <250 ms. Our provider contract already has `snapshot` — promote its semantics; degrade gracefully per adapter (Docker → filesystem checkpoint). Unlocks resumable long tasks, warm boots (kills the checkpoint-time snapshot tax found in the perf review), and try-N-approaches-keep-winner.
**Map:** sandbox provider contract + E2B adapter; evaluate [Mitos](https://github.com/mitos-run/mitos) for the K8s adapter.
Sources: [Morph Infinibranch](https://www.morph.so/blog/sandbox-sdk-morph-cloud), [Firecracker snapshots](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md)

### C5. A11y-tree hybrid browser perception + persistent auth contexts + takeover — *execution*
2026 consensus: accessibility-tree-first (~5–10 % of DOM nodes, <500 ms, 90 % of apps), vision fallback only for canvas/anti-bot. Computer-use models remain a fallback tier (best system: 20.6 % on OSWorld 2.0 long-horizon). Auth is the real task-success unlock: persisted encrypted contexts (login once, reuse), live-view takeover for 2FA/captcha, and network-layer credential injection so secrets never enter model context.
**Map:** add `observe/act/extract` (a11y snapshot) + `context_id` + live-view URL to the browser controller HTTP contract; Browserbase Contexts in the existing adapter; screenshot path stays as fallback.
Sources: [hybrid architecture](https://arxiv.org/html/2511.19477v1), [Browserbase Contexts](https://docs.browserbase.com/features/contexts), [OSWorld 2.0](https://arxiv.org/abs/2606.29537)

### C6. Verifier-gated repair + errors kept verbatim — *cognition*
Best-validated loop pattern: deterministic verification (tests/lint/execution) → repair prompt carries exact failure evidence → prior failures stay visible (Manus: scrubbing failures removes the evidence the model adapts from). Gate the repair budget on verdict *category*: deterministic failure → cheap-model repair; repeated identical failure → escalate to strong model or re-plan, instead of burning the remaining budget on the same approach. Zero-shot LLM-judge best-of-N is NOT worth it (+2.5 pp at 6.4× cost); calibrated verify-then-retry beats fixed-N at 1–3× fewer tokens.
**Map:** verify→repair edge in `nodes.py`; keep failure observations exempt from summarization; route repair attempts through the gateway's fast/strong hint.
Sources: [scaffold taxonomy](https://arxiv.org/pdf/2604.03515), [calibrated verifier](https://arxiv.org/pdf/2509.19681)

### C7. Memory: extraction-at-write + ADD/UPDATE/DELETE arbitration + background consolidation — *knowledge*
Production consensus (Mem0, ChatGPT, Claude): extract facts at write, LLM-arbitrate against similar existing memories (invalidate with validity windows, never hard-delete — Zep's bi-temporal trick without the graph DB), consolidate asynchronously post-run with a stronger model (Letta sleep-time pattern). Keep raw episodes *and* the distilled layer. Split semantic/episodic/procedural; procedural feeds skill self-authoring (C8).
**Map:** upgrade `_capture_agent_session_memory` → two-phase pipeline in `memory/`; async worker post-run; user-visible memory editor later.
Sources: [Mem0 paper](https://arxiv.org/pdf/2504.19413), [Letta sleep-time](https://www.letta.com/blog/sleep-time-compute/), [Zep](https://arxiv.org/abs/2501.13956)

### C8. Agent-authored skills from successful runs — *knowledge*
Manus's marquee compounding mechanism: distill a successful session into a draft SKILL.md. Our unique edge: the existing `skills/evaluation.py` module is the quality gate marketplaces lack — score drafts before registry entry. Same run-end hook as memory capture; also the capability-layer twin of the product-layer "Run → Playbook" (P1 in doc 11).
Source: [Manus skills](https://manus.im/blog/manus-skills)

### C9. Role-based model routing inside the loop — *cognition*
Move fast/strong from per-workflow-task to per-loop-stage: strong for plan/re-plan/escalated repair; fast for routine act-turns, first repair, observation distillation, compaction. Anthropic's Opus-lead/Sonnet-worker economics applied intra-loop; 40–85 % reported savings with verification backstopping cheap turns. Resist per-query ML routing classifiers — roles capture most of the win at zero latency.
**Map:** `model_hint` already exists on workflow tasks; add per-operation hints in `_model_request` (operation kind is already passed).
Source: [multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

### C10. Skill-scaffolded document generation + render-verify — *execution*
Anthropic's source-available pptx/docx/xlsx/pdf skills (python-pptx/openpyxl in a sandbox) are the quality leader for *editable* Office output; HTML-as-IR competitors (Genspark/Manus pipeline) drift on export. Ship skill packs into sandbox images; artifacts flow through the existing `/workspace/artifacts` upload+guardrail pipeline unchanged; add a render-verify step (open/screenshot the export) before upload.
Sources: [Agent Skills quickstart](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/quickstart), [anthropics/skills](https://github.com/anthropics/skills)

### C11. Tool search with defer-loading — *knowledge*
Accuracy degrades past ~30–50 upfront tools; Anthropic's tool-search: 85 % token reduction, 49 %→74 % MCP-eval accuracy, cache-safe (deferred tools excluded from prefix). We already hold per-tool schemas in the gateway — index name/description/args with BM25, expose `tool_search`, append matched schemas.
Source: [tool search docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)

### C12. Egress-substituted credential vault — *execution + knowledge*
The converged security bar (Anthropic Vaults, Arcade Token Vault, Cloudflare Outbound Workers, Anchor secret values): credentials substituted into outbound requests at the dispatch/egress layer, never entering agent context — the mitigation for the failure mode behind Composio's May 2026 breach and MCP's dismal auth stats (8.5 % OAuth, 25 % no auth). Our `connectors/` OAuth+dispatch is most of the way there — harden with per-credential `allowed_hosts`, auto-refresh, egress substitution; add an egress-proxy option to Docker/K8s sandbox adapters.
Sources: [MCP security 2026](https://nimblebrain.ai/blog/state-of-mcp-security-2026/), [Cloudflare Sandboxes GA](https://blog.cloudflare.com/sandbox-ga/)

---

## Part 2: Secondary adoptions

- **Plan artifact + recitation + triggered re-plan** — lightweight plan object in graph state, status-flag updates only (Manus found unbounded todo-maintenance ate ~1/3 of actions), re-injected near the context tail; re-plan on triggers (verify-exhausted, iteration 8/12 with <half done, steering message). Steering already exists — route it into plan revision, not just next-action.
- **Read-only subagent firewall** — isolated-context workers for big fetches/log analysis returning ≤1 KB summaries; read tasks only (Cognition/LangChain consensus: multi-agent is safe for reads, risky for writes). Default remains single-loop.
- **Code-mode batching (CodeAct)** — one sandboxed script batches N tool calls; 97–98.7 % token reduction replicated. Needs policy-layer redesign (approve a script, not a call) — sequence after the autonomy dials (doc 11 P3).
- **PTY sessions + PR-per-task** — opt-in `exec_session`/`write_stdin` alongside one-shot exec; structured "run tests → failure report" tool; coding work exits as reviewable diffs (pairs with the existing coding_workspaces module).
- **Agentic retrieval** — expose lexical/grep + semantic retrieval as tools the agent iterates with, instead of one-shot pre-retrieval; contextual retrieval (+hybrid+rerank) on whatever stays indexed; skip RAG for small bounded corpora (long context + caching).
- **Budget invisibility** — Devin's "context anxiety" finding: enforce iteration/token limits in the graph but don't surface "iteration 11/12" to the model; prevents premature wrap-up.

## Part 3: Explicitly not recommended now

- Pure-vision browser automation as primary (fallback tier only).
- Per-query ML model-routing classifiers (role routing first).
- Zero-shot LLM-judge best-of-N atop the existing verify step (weak evidence, 5–6× cost).
- Temporal knowledge-graph memory (Zep-class ops cost; revisit on real multi-hop temporal need).
- Wide-Research-scale fan-out (no published accuracy evidence).
- Building captcha solving or GPU sandboxes (buy/defer).

## Part 4: Suggested sequencing

1. **Wave 1 (loop economics, small diffs) — LANDED 2026-07-26:** C1 cache discipline (prompt-stable prefix; sliding-window stubs remain open), C2 recoverable observations (`observation.read` paging, commit `dd692ac`), C6 verifier-gated repair (identical-failure escalation + verbatim evidence, commit `31fb82a`), C9 role routing (`agent_loop_fast_model`/`agent_loop_fast_operations`, opt-in "repair" operation). Measurable via `model.operation.*` events and billing meters.
2. **Wave 2 (standards + compounding):** C3 SKILL.md, C7 memory pipeline, C8 self-authored skills (they share the run-end hook and the registry).
3. **Wave 3 (execution surface):** C5 browser a11y+auth contexts, C10 document skills, C4 sandbox fork semantics.
4. **Wave 4 (scale + security):** C11 tool search, C12 credential vault hardening, secondary items as pulled by product needs.

**Measurement rule:** every wave lands with its own billing-meter attribution (cache hit rate, tokens per run, repair success rate, task success rate on a fixed eval set) — the eval harness from doc 11 (P6) becomes the regression gate for waves 2+.

# CREAO functional parity execution map

Authoritative reference surfaces:

- `https://agent.creao.ai/chat` (authenticated product UI inspected 2026-07-11)
- `https://docs.creao.ai/features/chat`
- `https://docs.creao.ai/features/skills-and-connectors`
- `https://docs.creao.ai/features/agents`
- `https://docs.creao.ai/features/workspaces`

## Already implemented on `main`

- Durable Thread CRUD, server-side queue, steering, SSE reconnect, stop, sharing, suggestions, and message actions.
- Observe/Decide/Act/Verify Agent Loop with repair, replan, approval waits, budgets, checkpoints, and fenced Action leases.
- Progressive Skill discovery and `SKILL.md` materialization with immutable version and digest pins.
- ZIP/GitHub Skill package import, file browsing, evaluation records, publish gate, install, upgrade, and rollback APIs.
- Agent definitions and versions, extraction from successful Threads, structured input, publish/restore/run APIs.
- Dynamic Connector tools with policy, approval, billing, OAuth reconnect, and exactly-once Action recovery.
- Typed artifact preview for HTML, SVG, PDF, images, code, and dashboards.

## P0 execution order

1. **Repair Agent Brain Skills management contracts**
   - Align ZIP/GitHub payloads with the package API.
   - Load package file contents from `/packages/{version}/files`.
   - Wire enable/disable, evaluation, publish, install, upgrade, and rollback to real routes.

2. **Make Chat create real Skill packages**
   - Register `skill.package.create_draft` in API and worker Tool Gateways.
   - Generate a structurally validated `SKILL.md` package from the model action.
   - Keep enterprise evaluation and publish gates explicit.

3. **Make `@agent` a real runtime binding**
   - Resolve the published Agent Version from the workspace registry.
   - Inject its instructions, contracts, and pinned bindings into Decide.
   - Record the loaded Agent Version in Run evidence.

4. **Build Agent Brain Connectors UI**
   - Show configured and available connector definitions.
   - Connect/disconnect, OAuth status, scopes, capabilities, and reconnect state.
   - Add configured connectors immediately to `@` mentions.

5. **Complete queue semantics**
   - Preserve current automatic server-side continuation.
   - Add CREAO-style manual mode that promotes the next queued message into the composer without executing it.

## P1 gaps

- Workspace Files page backed by persistent workspace storage rather than route cards.
- Agent runtime snapshot restore and bundled reference scripts across fresh runs.
- Agent detail parity: Overview, Files, Config, Sessions, export/import, team sharing, autonomy modes.
- Configured speech provider for transcription, read-aloud, and summarization (current gateway is capability-safe but disabled by default).
- Persistent browser profiles and explicit browser-session management.
- Artifact-level share action and richer source highlighting/diff views.

## Delivery rule

UI presence does not count as parity. A feature is complete only when the visible control calls a real tenant-scoped backend path and the resulting state is consumed by Agent Runtime or can be reopened from a later session.

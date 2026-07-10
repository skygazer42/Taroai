# Client Portal Minimal Workspace Implementation Plan

**Goal:** Ship the first CREAO-consistent workspace slice that makes the backend execution loop visible: chat input, run controls, run timeline, sandbox terminal output, browser observations, artifacts, approval actions, and PoC Bearer login.

**Architecture:** Keep the first frontend deliberately small and static under `apps/web`. The frontend calls the real FastAPI routes; it does not introduce runtime fixtures, provider shortcuts, or a parallel data model. API state remains authoritative for runs, events, approvals, browser observations, and artifacts. The local cloud PoC serves the static workspace through Compose, while full frontend packaging, SSO/admin auth UX, admin console, and skill marketplace stay as later phases.

**Tech Stack:** Static HTML/CSS/JavaScript, FastAPI CORS, Docker Compose with nginx for local serving, pytest contract tests. Next.js, generated SDKs, Playwright, and component-system work remain future options after the first workspace slice proves the execution flow.

---

## Current Decision

Frontend implementation is now allowed for the narrow MVP workspace slice needed to demonstrate:

```text
create run
→ runtime creates sandbox
→ sandbox executes
→ artifact is uploaded
→ frontend displays timeline / terminal / browser / artifact / approval state
```

The earlier "defer all frontend implementation" decision is superseded for this slice only. This is not approval to build a full portal, admin console, skill marketplace, landing page, or app builder.

## Design Context

Intent:

- Human: enterprise employee using an agent during real work, plus solution engineers validating delivery.
- Job: start tasks, login with a PoC Bearer token, monitor agent execution, cancel or retry runs, approve risky actions, inspect terminal and browser output, and open artifacts.
- Feel: focused, utilitarian, calm, execution-oriented; not a marketing landing page and not a generic dashboard.

Domain concepts:

- Run timeline, approval gate, artifact handoff, cloud workspace, skill manifest, tenant boundary, audit trail, cost meter.

Interface signature:

- A chat-first execution column anchors the screen, with adjacent operational panels for run control, timeline, terminal, browser observations, artifacts, and approvals.

Defaults to avoid:

- Landing hero.
- Generic metric-card dashboard as first screen.
- Decorative card grids.
- Replacing chat flow with admin navigation.

## Task 1: Implement Static Workspace Shell

**Files:**

- Create: `apps/web/index.html`
- Create: `apps/web/assets/styles.css`
- Create: `apps/web/assets/main.js`
- Create: `apps/web/package.json`
- Test: `tests/web/test_workspace_frontend_contract.py`

**Steps:**

1. Preserve `data-testid="chat-column"`.
2. Preserve selector shape: `[data-testid="chat-column"] > div:nth-of-type(4) > div:nth-of-type(2)`.
3. Preserve composer hint: `Press Enter to send, Shift+Enter for a new line.`
4. Preserve keyboard behavior: Enter sends; Shift+Enter inserts newline.
5. Render the first screen as a working chat workspace, not a landing page.

**Current Implementation Notes:**

- `apps/web/index.html` contains the chat column, run control panel, run history panel, run trace panel, runtime state panel, timeline, sandbox terminal, browser panel, artifact list, approval panel, PoC Bearer login controls, and local PoC connection controls.
- `apps/web/assets/styles.css` uses a CREAO-adjacent light paper palette, restrained borders, 8px-or-less panel radius, and responsive chat-first layout.
- `apps/web/assets/main.js` logs in through `/api/auth/login`, stores the Bearer token in `sessionStorage`, posts to `/api/runs`, executes the run, loads recent workspace runs through `/api/runs`, can select a historical run and refresh its event/artifact state, can cancel active runs through `/api/runs/{run_id}/cancel`, can retry failed/cancelled/timed-out runs through `/api/runs/{run_id}/retry`, fetches replayable SSE events, fetches run trace evidence through `/api/runs/{run_id}/trace`, fetches runtime snapshots through `/api/runs/{run_id}/state`, renders `sandbox.command.executed` terminal summaries without raw stdout/stderr dependence, renders `browser.action.performed` observations, lists artifacts, resolves storage-backed artifacts through `/api/runs/{run_id}/storage-objects`, previews and downloads text/Markdown artifact content through `/api/storage/objects/{id}/content`, and resolves or rejects approvals through the real API.

## Task 2: Wire Browser-to-API Access

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_app.py`

**Steps:**

1. Use existing Pydantic `Settings.cors_origins`.
2. Add FastAPI CORS middleware so the local workspace at `http://localhost:3000` can call the API at `http://localhost:8000`.
3. Keep PoC dev request headers explicit: `X-Tenant-ID` and `X-User-ID`.
4. Send `Authorization: Bearer ...` when the workspace user logs in through the static UI.

**Current Implementation Notes:**

- `create_app()` now installs `CORSMiddleware` using `resolved_settings.cors_origins`.
- Contract coverage verifies preflight access for the workspace origin, dev headers, static Bearer login controls, and token-backed request headers.

## Task 3: Package with Local Cloud PoC

**Files:**

- Modify: `infra/docker-compose.yml`
- Modify: `.env.example`
- Modify: `docs/operations/mvp-local-cloud-poc.md`
- Test: `tests/web/test_workspace_frontend_contract.py`

**Steps:**

1. Add a `web` service that serves `apps/web` through nginx.
2. Expose it on `TAROAI_WEB_PORT`, default `3000`.
3. Keep API, PostgreSQL, Redis, and MinIO unchanged.
4. Document local URL and validation commands.

**Current Implementation Notes:**

- Compose now includes `web` with `../apps/web:/usr/share/nginx/html:ro` and `${TAROAI_WEB_PORT:-3000}:80`.

## Task 4: Keep Full Portal Scope Explicitly Later

**Still Planned:**

- SSO-grade auth UX beyond the PoC Bearer login strip.
- Full run-list UX beyond the current recent-run history panel.
- Live browser streaming beyond the current browser observation panel.
- Rich artifact preview and signed-URL sharing beyond the current authenticated content download.
- Admin console for users, roles, audit, billing, knowledge, SSO/SCIM, licenses, and deployment readiness.
- Skill marketplace upload/reuse flows.
- Generated SDKs and frontend route-level tests.
- Playwright screenshots and accessibility tests once a browser test stack is approved.

## Verification

Run after changes:

```bash
python -m pytest tests/web/test_workspace_frontend_contract.py -q
python -m pytest tests/api/test_app.py::test_create_app_applies_configured_cors_origins_for_workspace_frontend -q
docker compose -f infra/docker-compose.yml config
```

Expected current result: a minimal static workspace exists, calls real backend routes, is packaged into the local cloud PoC, and preserves the CREAO-compatible chat selector and composer behavior. Full client portal work remains a later phase.

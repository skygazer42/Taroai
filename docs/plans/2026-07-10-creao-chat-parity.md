# CREAO Chat Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reproduce the logged-in CREAO `/chat` shell and core interaction states in Taroai while preserving all existing governed-run functionality and honestly representing unsupported backend capabilities.

**Architecture:** Keep the dependency-free HTML/CSS/JavaScript frontend. Recompose existing DOM regions into a 256px navigation sidebar, centered chat workspace, conditional Artifact panel, Files dialog, and hidden Operations drawer. Extend the existing state and API adapters instead of introducing a second frontend runtime.

**Tech Stack:** Static HTML5, CSS custom properties, browser-native JavaScript modules, Python `html.parser` source-contract tests, Playwright browser verification through the in-app browser.

---

### Task 1: Lock the new semantic shell with failing tests

**Files:**
- Create: `tests/web/test_creao_chat_frontend_contract.py`
- Modify: `tests/web/test_workspace_frontend_contract.py:88-102`

**Step 1: Write the failing shell test**

Assert the presence and nesting of `app-sidebar`, `chat-column`, `chat-empty-state`, `chat-composer`, `artifact-panel`, and `operations-drawer`. Assert that Run history is located in the sidebar and the existing artifact list is located in the Artifact panel.

**Step 2: Write failing accessible interaction tests**

Assert model and add buttons expose `aria-haspopup="menu"`, `aria-controls`, and `aria-expanded="false"`; their menu nodes use `role="menu"` and are initially hidden. Assert `files-dialog`, its filters/list/confirm controls, and `data-attachment-chips` exist.

**Step 3: Write failing JavaScript contract tests**

Assert the source contains `selectedAttachments`, `openFilesDialog()`, `renderAttachmentChips()`, attachment IDs in `submitRun()`, `renderConversationForRun(run)`, `setArtifactPanelOpen(true)`, and accessible popover dismissal logic.

**Step 4: Run the focused tests and observe failure**

Run:

```powershell
python -X utf8 -B -m pytest -q -p no:cacheprovider tests/web/test_creao_chat_frontend_contract.py tests/web/test_workspace_frontend_contract.py
```

Expected: the new tests fail because the new shell and state do not exist; the original 44 tests remain green except for the one deliberately relaxed positional Composer assertion.

### Task 2: Recompose the HTML without deleting operational capability

**Files:**
- Modify: `apps/web/index.html`

**Step 1: Implement the sidebar and chat header**

Add the Taroai wordmark, New chat/Search actions, product navigation, Recents container, Operations entry, account row, model selector, and plan/files/share controls with semantic navigation labels.

**Step 2: Implement empty and conversation regions**

Keep `data-testid="conversation-log"`; add the serif greeting, CREAO-shaped composer, Create Agent strip, and compact popular-agent cards. Mark empty-only content so JavaScript can hide it after a Run is selected or submitted.

**Step 3: Implement menus, attachment chips, and Files dialog**

Add the model menu, add-content menu, hidden file input, attachment chip region, and labelled `<dialog>`.

**Step 4: Rehome Artifact and Operations content**

Move the existing Artifact panel markup into a conditional right panel. Wrap the remaining existing workbench and its view switcher in `operations-drawer`, preserving every legacy ID and `data-testid`.

**Step 5: Run contract tests**

Expected: HTML-structure tests pass; JavaScript behavior tests remain red.

### Task 3: Match the CREAO visual system

**Files:**
- Modify: `apps/web/assets/styles.css`

**Step 1: Replace global tokens and layout primitives**

Use the measured warm palette, 256px sidebar, serif display heading, Instrument Sans-compatible stack, border radii, and control sizes from the extraction.

**Step 2: Style empty state and Composer**

Match the `813 × 158px` desktop composer, placement, action strip, send/add buttons, placeholder treatment, and centered heading at `1129 × 856`.

**Step 3: Style sidebar, cards, popovers, dialog, and historical messages**

Prioritize geometry, typography, spacing, borders, then shadows. Do not copy CREAO logos or external art assets; use Taroai text/neutral illustrative placeholders.

**Step 4: Preserve existing run-state styles in the Operations drawer**

Scope legacy panel rules under the drawer where necessary; do not delete approval, terminal, browser, evidence, admin, or artifact states.

**Step 5: Add responsive rules**

Switch sidebar/Artifact/Operations to accessible overlay drawers below 1024px and compact the Composer below 720px.

### Task 4: Wire accessible interactions and compatibility data

**Files:**
- Modify: `apps/web/assets/main.js`

**Step 1: Extend state and element references**

Add model, attachment, popover, panel, drawer, and sidebar fields while keeping existing state keys.

**Step 2: Implement menus and dialog**

Implement one-open-popover behavior, outside click, Escape, focus return, model choice UI, add-menu commands, Files dialog open/close, local storage-object selection, and attachment-chip removal.

**Step 3: Map Recents and historical Run state**

Render Run history inside the sidebar and call `renderConversationForRun(run)` on selection. The compatibility renderer must rebuild the visible conversation instead of leaving stale DOM.

**Step 4: Include attachments in Run creation**

Map `selectedAttachments` to stable IDs in the already-supported `attachments` field and clear chips only after a Run is accepted.

**Step 5: Open Artifact panel from preview actions**

Make preview success reveal the conditional Artifact panel; implement close control and desktop/mobile behavior.

**Step 6: Run all web contract tests**

Expected: both focused files pass with no regression.

### Task 5: Add browser behavior coverage

**Files:**
- Create: `tests/web/test_creao_chat_browser.py`

**Step 1: Serve the frontend without the API**

Run `python -m http.server 3456 -d apps/web` in a hidden background process. The shell must render even when readiness calls fail.

**Step 2: Verify the core interactions**

Using Playwright, verify model menu selection, add menu Escape behavior and focus return, Files dialog open/close, attachment chip add/remove with a seeded test storage object, Operations drawer, and Artifact panel close.

**Step 3: Verify responsive states**

Check `1129 × 856` and `390 × 844` without horizontal overflow, clipped Composer controls, or inaccessible dialog content.

**Step 4: Run focused browser tests**

Expected: all interaction and viewport checks pass.

### Task 6: Visual-verdict iteration

**Files:**
- Update: `.omx/state/taroai-creao-chat/web-clone-verdicts.json`
- Update: `.omx/state/taroai-creao-chat/ralph-progress.json`

**Step 1: Capture the local empty state**

Save `clone-empty.png` at `1129 × 856` and compare against `target-full.png`.

**Step 2: Emit and persist the composite verdict**

Record visual score/differences, structure landmark match, and the tested interactions.

**Step 3: Fix only the top visual deltas**

Iterate in this order: global layout, Composer geometry, sidebar density, heading typography, popover/dialog positioning, colors/shadows.

**Step 4: Repeat until pass or five iterations**

Pass threshold: visual score at least 85, zero functional failures, all major landmarks present.

### Task 7: Prepare the backend-parity continuation

**Files:**
- Create: `docs/plans/2026-07-10-conversation-agent-schedule-vertical.md`

Document exact API and migration tasks for Conversation/Message/Attachment, true SSE deltas, thread workspace persistence, immutable AgentVersion, version-pinned Schedule, and Agent Session artifacts. Do not represent these as completed by the frontend work.

### Final verification

Run:

```powershell
python -X utf8 -B -m pytest -q -p no:cacheprovider tests/web
```

Then run the browser E2E, inspect the captured screenshots, confirm no console errors caused by missing DOM references, and record the final visual-verdict evidence before reporting completion.

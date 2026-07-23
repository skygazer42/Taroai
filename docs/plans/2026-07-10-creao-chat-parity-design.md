# CREAO Chat Parity Design

## Scope

The first implementation slice reproduces the observable CREAO `/chat` application shell and its core interaction states inside the existing static Taroai frontend. It keeps Taroai branding and existing backend contracts while matching the reference layout, density, typography, navigation hierarchy, composer, popovers, historical-run presentation, and files/artifact surfaces.

This slice does not claim backend capabilities that do not exist. In particular, model selection is a UI preference until the API exposes a model-routing field, Runs remain the compatibility data source for Recents until Conversation APIs exist, and the current event polling remains visible as run progress rather than being disguised as token streaming.

## Reference Baseline

- Reference viewport: `1129 × 856`, DPR 1.
- Page background: `#f8f6f3`; sidebar background: `#f4f1ed`; primary ink: `#16181a`.
- UI font: Instrument Sans / Inter fallback, `16px/24px` base.
- Empty-state heading: EB Garamond / Georgia fallback, `52px/78px`, weight 400.
- Sidebar: `256px` wide, full viewport height; header `52px` high with `20px` horizontal padding.
- Empty composer: approximately `813 × 158px`, `24px` radius, `#fefdfb` surface, subtle warm border.
- Model selector: `32px` high, `14px/20px`, `6px 10px` padding.
- Primary Create Agent action: dark pill, `28px` high, `12px/16px` label.

Reference screenshots and extraction metadata are external test evidence and are not required at runtime.

## Information Architecture

```text
App shell
├── Sidebar
│   ├── Taroai wordmark + collapse
│   ├── New chat + Search
│   ├── Discover / Feed / Agents / Workspaces / Files / Agent Brain
│   ├── Recents (existing Run history compatibility)
│   └── Operations + account
└── Chat workspace
    ├── Header (model selector, plan/status, Files, Share)
    ├── Empty state or conversation timeline
    ├── Popular agent cards (empty state only)
    ├── Composer
    │   ├── editable textarea
    │   ├── add-content menu
    │   ├── attachment chips
    │   ├── Create Agent affordance
    │   └── voice/send actions
    ├── Artifact panel (conditional desktop split / mobile drawer)
    ├── Chat Files dialog
    └── Operations drawer (all existing governed-run controls)
```

## State Model

The existing global state is extended, not replaced:

- `selectedModel`: visual preference only in this slice.
- `selectedAttachments`: registered storage object IDs sent through the existing `attachments` Run field.
- `activePopover`: mutually exclusive `model` or `add` menu.
- `artifactPanelOpen`: opens after an artifact preview is selected.
- `operationsOpen`: preserves all current admin/run controls without permanently occupying the page.
- `sidebarCollapsed`: desktop compact state and mobile overlay state.

Every popover uses `aria-expanded`, `aria-controls`, Escape handling, outside-click dismissal, and focus return. The Files surface is a native `<dialog>` with labelled controls.

## Compatibility Strategy

- Preserve every existing ID, API path, function name, and operational `data-testid` relied on by `test_workspace_frontend_contract.py`.
- Move existing workbench content into an Operations drawer instead of deleting it.
- Move the existing artifact list and preview into the conditional Artifact panel so download/preview logic remains intact.
- Render Run history into the sidebar Recents list and rebuild the visible chat state when a Run is selected.
- Keep the current `/api/runs` submit flow and add only the already-supported attachment ID list.

## Responsive Behavior

- `>= 1024px`: fixed 256px sidebar; chat content centered; Artifact panel appears as a right split when open.
- `720–1023px`: collapsible sidebar; Artifact and Operations use overlay drawers.
- `< 720px`: single chat canvas, top-left menu button, composer pinned to the safe bottom edge, dialogs constrained to viewport.

## Acceptance Criteria

1. The empty state, model menu, add menu, historical run state, and Files dialog are visually recognizable against the captured references.
2. All existing web contract tests remain green.
3. New source-contract tests prove semantic regions, accessible menu/dialog contracts, attachment mapping, history rendering, and conditional Artifact behavior.
4. Browser E2E verifies menu open/close, keyboard dismissal, Files dialog, attachment chips, history selection, and Artifact panel control.
5. `visual-verdict` reaches at least 85 at the captured `1129 × 856` viewport, or the iteration log records the best result and remaining deltas after five passes.

## Deferred Backend Parity

Persistent Conversation/Message records, provider-history injection, true long-lived SSE deltas, immutable Agent versions, version-pinned schedules, and Agent Sessions remain the next vertical slice. The frontend names and regions introduced here are chosen so those APIs can replace the compatibility adapters without another visual rewrite.

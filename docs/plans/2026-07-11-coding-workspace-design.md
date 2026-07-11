# Coding Workspace Design

## Goal

Give every coding Run an isolated repository worktree with inspectable changes, test evidence, checkpoints, commits, and pull-request delivery.

## Architecture

Repository bindings are workspace-scoped control-plane records. A Coding Workspace is run-scoped and points to an Engine/Sandbox session that owns the actual checkout. Taroai never executes tenant Git commands on the API host. Runners publish normalized file changes, unified diffs, test results, checkpoints, commits, and pull-request references back to the Coding Workspace API.

## Data model

- `repository_bindings`: provider, repository URL, default branch, connector reference, status.
- `coding_workspaces`: Run, repository binding, Engine session, branch, worktree path, base/head revisions, lifecycle status.
- `coding_changes`: path, status, additions, deletions, patch, binary flag.
- `coding_test_results`: command, status, duration, summary, output artifact.
- `coding_checkpoints`: revision, label, snapshot reference.
- `coding_deliveries`: commit SHA/message and pull-request URL/number/status.

## Lifecycle

1. User selects a repository binding in Chat or an Agent version.
2. Taroai creates a run-scoped Coding Workspace and delegates checkout to the selected Engine/Sandbox.
3. Runner pushes evidence after meaningful changes and tests.
4. User reviews Diff and approvals in the Chat sidecar.
5. Approved delivery produces a commit and optional pull request through the governed Connector.

## Security

Only HTTPS repository URLs are accepted. Credentials are Secret/Connector references. Taroai stores evidence and immutable identifiers, never a reusable Git token. Worktree paths must remain under `/workspace/repos/`.

## Failure behavior

Failed checkouts, tests, commits, and pull requests retain their evidence and do not erase the worktree record. Cancel closes execution but keeps checkpoints and artifacts.

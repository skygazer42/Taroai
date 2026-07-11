# Agent Engine Framework Design

## Scope

Taroai treats Native, OpenCode, Codex app-server, and Claude Agent SDK runners as first-class Agent Engines rather than model names or shell tools.

## Boundary

Taroai owns tenant/workspace authorization, Secret references, approvals, budgets, audit, billing, and normalized events. The remote runner owns its internal Agent Loop. Engine credentials are referenced by `secret_ref_id`; plaintext is never stored in an Engine connection or session.

## Data flow

1. A workspace administrator creates an Engine connection with type, endpoint, capabilities, and optional Secret reference.
2. A user creates an Engine session for a Run and sends a complete task.
3. Taroai forwards turns, steering, approvals, rejection, cancel, resume, and close operations through one adapter contract.
4. Runner events are normalized and persisted so Chat can render plans, commands, files, diffs, usage, approvals, and completion consistently.
5. Agent versions pin `engine_type` and optionally `engine_connection_id` in the runtime snapshot.

## Runner protocol

Remote runners expose a Taroai-normalized HTTP surface under `/v1`: sessions, turns, steering, events, approvals, cancel, resume, and close. Provider-specific processes remain behind the runner boundary.

## Failure behavior

Transport failures preserve the session and record an error event. A missing or disabled connection blocks new sessions. Session lifecycle operations are idempotent at the Taroai API boundary.

## Explicit exclusions

This batch does not place provider credentials in Sandbox images and does not make the outer orchestrator control each inner shell command.

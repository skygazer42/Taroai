# Upgrade Matrix

This matrix records supported package compatibility for private delivery. It must be checked before upgrade, rollback, or air-gapped package transfer.

| App Version | Chart Version | Migration Range | PostgreSQL Version | Redis Version | Object Storage | Rollback Boundary | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1.0 | 0.1.0 | 001_initial to 039_evaluation_runtime | PostgreSQL 16 | Redis 7 | S3-compatible or MinIO current local PoC profile | Restore backup for schema rollback beyond 001_initial | Private package baseline including Agent Loop V2, Skill Runtime V2, versioned Agents, browser profiles, Agent Engines, coding workspaces, thread sharing, rich artifacts, and evaluation release gates. See docs/operations/private-upgrade-rollback.md. |

## Compatibility Rules

- App Version must match the deployment package `app_version`.
- Chart Version must match the Helm chart `version`.
- Migration Range must be fully present before the migration job starts.
- PostgreSQL Version is the tested major version for database migrations and tenant isolation policy.
- Redis Version is the tested major version for short-term memory and worker queue paths.
- model policy version history must remain readable across the full supported migration range so policy rollback evidence stays available.
- browser controller compatibility must include the packaged `taroai-browser-controller` image whenever browser actions are enabled.
- sandbox controller compatibility must include the packaged `taroai-sandbox-controller` image whenever HTTP sandbox execution is enabled.
- Web Workspace compatibility must include the packaged `taroai-web` image and the install-validation web workspace contract whenever the package exposes the customer-facing workspace.
- Rollback Boundary defines whether a code-only rollback is allowed or a database restore is required.

## Release Gate

Before a private package is released:

1. update this matrix with the new app version, chart version, migration range, database version, Redis version, and rollback boundary.
2. run private install validation on the target package.
3. confirm license and entitlement compatibility.
4. confirm air-gapped package transfer contents when the target includes air-gapped delivery.

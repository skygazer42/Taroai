# Disaster Recovery

This runbook defines first-pass recovery targets and the current operational boundary.

## Recovery Targets

| Tier | RPO | RTO | Notes |
| --- | --- | --- | --- |
| PoC | 24 hours | 8 hours | Manual restore from database/object storage snapshots is acceptable. |
| Business | 4 hours | 2 hours | Scheduled database backups, object storage versioning, and Redis rebuild plan required. |
| Enterprise | 1 hour | 30 minutes | Automated backup verification, approved-region replication, and restore drills required. |

## Restore Order

1. Restore control-plane database.
2. Restore object storage bucket contents.
3. Restore or rebuild Redis-backed short-term state and queues where policy requires it.
4. Load Pydantic settings from approved environment values.
5. Start workers after stores and object storage are reachable.
6. Run lifecycle backup manifest verification checks.
7. Run data residency report and confirm checked regions are approved.

## Degraded Mode

If model gateway, sandbox provider, browser provider, or Redis queue are unavailable, the control plane should keep tenant/auth/audit reads available and disable the affected execution path through settings or provider configuration.

## Current Boundary

The repository now has backup manifests and data residency reports. Automated cloud backup jobs, cross-region replication, restore drills, and live provider failover remain implementation work.

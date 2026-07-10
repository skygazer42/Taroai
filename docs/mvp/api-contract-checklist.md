# MVP API Contract Checklist

This checklist freezes the first internal MVP route contract for the cloud PoC.
It is not a generated OpenAPI snapshot. The authoritative runtime contract remains
the FastAPI OpenAPI document, and `tests/api/test_openapi_contract.py` verifies
that these routes stay present until a versioning migration is explicitly
planned.

## Route Ownership

| Area | Owner Boundary | Route |
| --- | --- | --- |
| Auth and session | auth/session | `POST /api/auth/login` |
| Auth and session | auth/session | `POST /api/auth/logout` |
| Tenant onboarding | tenant onboarding | `POST /api/tenants/bootstrap` |
| Tenant onboarding | tenant onboarding | `GET /api/tenants/current/readiness` |
| Runs | run control plane | `GET /api/runs` |
| Runs | run control plane | `POST /api/runs` |
| Runs | run control plane | `GET /api/runs/{run_id}` |
| Runtime execution | agent runtime | `POST /api/runs/{run_id}/execute` |
| Runtime execution | run event stream | `GET /api/runs/{run_id}/events` |
| Runtime execution | agent runtime | `GET /api/runs/{run_id}/state` |
| Runs | run control plane | `POST /api/runs/{run_id}/cancel` |
| Runs | run control plane | `POST /api/runs/{run_id}/retry` |
| Approvals | approval control | `POST /api/runs/{run_id}/approvals` |
| Approvals | approval control | `POST /api/runs/{run_id}/approvals/reject` |
| Artifacts | artifact delivery | `GET /api/runs/{run_id}/artifacts` |
| Artifacts | artifact delivery | `GET /api/runs/{run_id}/storage-objects` |
| Knowledge | knowledge retrieval | `POST /api/knowledge/query` |
| Billing | billing | `GET /api/billing/meters` |
| Audit | audit | `GET /api/audit-events` |
| Skills | skill registry | `GET /api/skills` |
| Skills | skill registry | `POST /api/skills` |
| Skills | skill registry | `GET /api/skills/{skill_id}` |
| Skills | skill registry | `POST /api/skills/{skill_id}/publish` |
| Skills | skill registry | `POST /api/skills/{skill_id}/disable` |
| Skills | skill registry | `GET /api/skills/{skill_id}/versions` |
| Skills | workspace skills | `GET /api/workspaces/{workspace_id}/skills` |
| Skills | workspace skills | `POST /api/workspaces/{workspace_id}/skills/{skill_id}/install` |
| Skills | workspace skills | `POST /api/workspaces/{workspace_id}/skills/{skill_id}/invoke` |

## Compatibility Rules

- Keep the MVP public paths under `/api/*` until the API versioning decision is
  approved.
- Do not introduce `/api/v1/*` routes for these MVP paths without updating this
  checklist, plan 14, and the OpenAPI contract tests in the same change.
- Route handlers may later move out of `app.py` into route modules, but the
  public method/path pairs above must remain backward-compatible for the MVP
  workspace, verifier, and API-level acceptance tests.
- Dev-only endpoints, such as workspace skill invocation, must stay explicitly
  gated by settings and permission checks. Moving them into production flows
  requires a separate route contract update.

## /api/v1 migration

The current MVP intentionally keeps unversioned `/api/*` routes for speed and
local workspace compatibility. Before generated SDK release or external
customer integration, create a versioning plan that either migrates these
routes to `/api/v1/*` or supports both paths during a deprecation window.

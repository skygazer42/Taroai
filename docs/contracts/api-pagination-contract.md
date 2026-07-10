# API Pagination Contract

This contract defines the shared list response shape for external clients and SDKs.

## Implemented Route

- `GET /api/runs`
- `GET /api/billing/meters` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/audit-events` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/skills` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/memory` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/memory/short-term` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/runs/{run_id}/artifacts` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/runs/{run_id}/storage-objects` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/knowledge-bases` when `limit`, `cursor`, or `sort_direction` is present
- `GET /api/knowledge-documents` when `limit`, `cursor`, or `sort_direction` is present

## Query Parameters

- `limit`: page size, default `50`, minimum `1`, maximum `100`
- `cursor`: opaque cursor returned by the previous page
- `sort_direction`: `desc` by default, or `asc`
- `workspace_id`: optional run filter
- `status`: optional run status filter

`GET /api/billing/meters` keeps its existing meter filters: `run_id`, `workspace_id`, `user_id`, `agent_id`, `skill_id`, and `meter_type`.

`GET /api/audit-events` keeps its existing audit filters: `run_id`, `workspace_id`, `user_id`, `event_type`, `created_after`, and `created_before`.

`GET /api/skills` keeps its existing visibility filters: `workspace_id` and `department_id`.

`GET /api/memory` keeps its required scope filters: `scope_type` and `scope_id`.

`GET /api/memory/short-term` keeps its required `run_id` filter.

`GET /api/runs/{run_id}/artifacts` and `GET /api/runs/{run_id}/storage-objects` keep their run boundary from the path.

`GET /api/knowledge-bases` supports optional `workspace_id`.

`GET /api/knowledge-documents` supports optional `knowledge_base_id` and `workspace_id`.

## Response Shape

```json
{
  "items": [],
  "limit": 50,
  "next_cursor": null,
  "has_more": false
}
```

## Ordering

List pages are ordered by `created_at` and a stable record identifier.

The default order is newest first. The cursor encodes the last item returned by a page and is opaque to clients. Records use `id` when present, short-term memory uses `key`, and skill records use `manifest.id`.

## Tenant Boundary

Paged list routes only return records for the caller tenant.

## Migration Compatibility

These routes return an array shape when no pagination parameter is supplied, preserving existing callers where the route already existed and keeping a consistent transition mode for new list routes. New clients and SDKs should always send `limit` and consume the page shape.

## Remaining Routes

The next API contract pass should remove legacy array response assumptions from client code and SDKs after callers migrate to the page shape.

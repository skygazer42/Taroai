# Data Residency

This document records the first deployable data residency contract for the control plane.

## Configuration

Data residency is configured through Pydantic Settings with the `TAROAI_` prefix:

- `TAROAI_DATA_RESIDENCY_PRIMARY_REGION`: the tenant deployment's primary operating region.
- `TAROAI_DATA_RESIDENCY_ALLOWED_REGIONS`: JSON list of approved regions.
- `TAROAI_DATA_RESIDENCY_CROSS_REGION_REPLICATION_MODE`: `disabled`, `approved_regions`, or `any_region`.
- `TAROAI_OBJECT_STORAGE_REGION`: object storage bucket region.
- `TAROAI_VECTOR_INDEX_REGION`: vector index region for future vector backend rollout.
- `TAROAI_SANDBOX_PROVIDER_REGION`: sandbox provider region when sandbox execution is enabled.

Settings validation rejects a primary region that is not included in the allowed region list.

## Runtime Report

`POST /api/lifecycle/data-residency/reports` requires `lifecycle.read` and returns a tenant-scoped report with:

- primary region
- allowed regions
- cross-region replication mode
- object storage region check
- vector index region check
- sandbox provider region check when sandbox execution is enabled
- overall `compliant` status

The API emits `lifecycle.data_residency.report_created` with summary-only audit metadata. Audit metadata includes counts, resource types, and checked regions, but not the full check details.

## Current Enforcement Boundary

This first implementation validates configuration shape and reports mismatches. It does not provision or move infrastructure across regions. Physical enforcement still requires cloud deployment wiring for object storage, vector backend, sandbox provider, backup replication, and monitoring.

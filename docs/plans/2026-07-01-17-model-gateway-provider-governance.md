# Model Gateway and Provider Governance Implementation Plan


**Goal:** Build a governed model access layer so agents, skills, evaluations, and embeddings use approved providers through tenant-aware routing, budgets, audit, fallback, and observability.

**Architecture:** Runtime code must not call provider SDKs directly. Agent Runtime asks a Model Gateway for chat, response, embedding, rerank, or multimodal calls through an OpenAI-compatible request/response boundary; the gateway resolves tenant policy, model capability, budget, provider credentials, rate limits, and trace metadata. Local contract fixtures belong under `tests/` or contract-verification code only; they are never runtime defaults or product-flow dependencies.

**Tech Stack:** FastAPI, Pydantic, pytest, OpenAI-compatible model API contract, optional LiteLLM adapter, direct provider adapters, OTel-compatible traces, PostgreSQL, Redis-backed rate-limit samples.

---

## Summary

Enterprise customers need model control: which providers are allowed, which models can touch sensitive data, how cost is capped, and how failover behaves. This plan turns model use into a platform service instead of scattered SDK calls.

Current state has OpenAI-compatible request/response/usage models, a default gateway boundary used by Agent Runtime planning, runtime planning records for `model_call_count`, `model_tokens_input`, `model_tokens_output`, `model_tokens_cached_input`, `model_latency_ms`, and `model.plan.created` audit metadata when the gateway returns usage, plus a Pydantic budget guard for run, tenant, workspace, user, and agent model call/token limits. Pydantic Settings now support global and tenant/workspace-scoped model defaults, allowed model lists, denied model lists, model sensitivity limits, scoped model budget limits, a configurable model-budget window (`TAROAI_MODEL_GATEWAY_BUDGET_WINDOW_SECONDS`), model API-key secret references, a first-pass `TAROAI_MODEL_GATEWAY_PROVIDERS` provider registry, `TAROAI_MODEL_GATEWAY_PROVIDER_STORE_BACKEND` for tenant-scoped provider records, and `TAROAI_MODEL_GATEWAY_PROVIDER_RATE_LIMIT_BACKEND` with `memory`, `sql`, and `redis` options for provider rate-limit samples. A first-pass model policy store now supports in-memory and SQLite-compatible SQL persistence, model sensitivity limit persistence, `model_policy.read`/`model_policy.manage`/`model_policy.approve` API access, direct admin writes with safe audit metadata, staged policy change-request create/list/approve/reject APIs that keep pending policy scopes out of runtime until approval, model policy version history for direct and approved changes, runtime refresh in the API process, and worker startup loading through Settings. Denied planning attempts, including sensitive-context requests without an approved model sensitivity limit, fail before provider invocation, emit `model.policy_denied` audit metadata, and classify run traces as `policy_denied`. Budget-exhausted runs fail before provider invocation and emit `model.budget_exceeded` audit metadata with the active budget window. Runtime model-request guardrails can block or redact content before provider invocation; runtime model-response guardrails can block or redact content before planned steps execute. Runtime now writes the policy-resolved model back into the gateway request before provider invocation. Model Gateway can resolve OpenAI-compatible provider credentials through `SecretService` short-lived leases using `TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID` or provider-level secret references, while the legacy direct env API key remains a compatibility fallback and is excluded from gateway dumps/reprs. Provider HTTP error response bodies are sanitized before becoming `ModelGatewayResponseError`, so key-shaped values, bearer tokens, and sensitive fields returned by upstream providers are replaced with `[REDACTED]` before CLI/API/log surfaces can expose them. `ModelProviderRegistry` orders OpenAI-compatible providers by tenant/workspace/model specificity plus priority, `ModelGatewayRouter` is wired into API and worker startup when providers are configured, retryable provider response failures can fall back to the next eligible provider according to typed provider fallback policy, provider IDs flow into model meter records and cost-estimate matching, and a first-pass `ModelProviderRateLimiter` can skip providers that exceed request/token-per-minute limits using process-local samples, SQL-backed tenant/provider samples, or Redis-backed tenant/provider sorted-set samples shared across API and worker router instances. Redis-backed provider limits now use a pre-call Lua reservation before provider invocation, so concurrent API/worker calls cannot all pass request-per-minute or `max_output_tokens` output-token-per-minute gates before the success recorder writes; successful reserved calls append only token deltas above the reserved output tokens to avoid double-counting request count or reserved token samples while preserving token-window checks. The router now returns first-pass provider attempt summaries for successful fallback paths, including provider ID, resolved model, status, invocation state, fallback allowance, and sanitized error type; Agent Runtime includes those summaries in `model.plan.created` audit and model billing metadata without prompt content, provider response bodies, or raw error detail. OpenAI-compatible usage parsing now reads `prompt_tokens_details.cached_tokens`/`input_tokens_details.cached_tokens` into cached-token meters without double-counting token-budget enforcement. `/readyz` now exposes a Pydantic model gateway configuration check with configured state, missing items, provider counts, and source labels without exposing keys or calling providers. `taroai.model_gateway.verification` sends a live OpenAI-compatible planning request and requires the expected `planning.record` tool in the parsed plan; private install validation also rejects model-gateway evidence whose planned tool names do not include `planning.record`, so hand-written or stale evidence cannot prove the provider is ready. `GET /api/model-providers` now exposes a first-pass safe provider listing behind `model_providers.read`, while direct write/enable/disable/credential/version/rollback APIs and staged provider change-request create/list/approve/reject APIs are permission-gated; these APIs persist tenant-scoped provider records, append version history, and keep pending changes out of runtime until approval, in memory or SQL. The API/worker runtime loads active records and never accepts or returns direct API-key values. Model Gateway configuration/response failures now fail the run, emit `model.gateway_failed` audit metadata, and keep prompt/response text out of audit metadata. Broader distributed budget governance and reviewed provider adapters remain planned work.

## Task 1: Model Gateway Package and Contracts

**Files:**

- Create: `apps/api/src/taroai/model_gateway/__init__.py`
- Create: `apps/api/src/taroai/model_gateway/models.py`
- Create: `apps/api/src/taroai/model_gateway/gateway.py`
- Test: `tests/api/test_model_gateway_contract.py`

**Steps:**

1. Keep `ModelGatewayRequest`, `ModelGatewayResponse`, `ModelUsage`, `ModelMessage`, and `PlannedToolCall` as the product boundary models.
2. Add `ModelCallType`, `ModelProviderRef`, and `ModelCapability` when provider policy work starts.
3. Preserve OpenAI-compatible fields where practical: `model`, `messages` or `input`, `tools`, `tool_choice`, `temperature`, `max_output_tokens`, `stream`, and response `usage`.
4. Require tenant ID, workspace ID, user ID, and run ID on every request; add purpose, sensitivity level, and requested capability when policy is introduced.
5. Keep gateway logic in `gateway.py` around the OpenAI-compatible boundary.
6. Add tests that requests without tenant/workspace/user/run context are rejected.

**Acceptance Criteria:**

- Model calls have one typed service boundary.
- Runtime uses the Model Gateway boundary; product flow never references tests-only adapters.

## Task 2: Tenant Model Policy

**Files:**

- Create: `apps/api/src/taroai/model_gateway/policy.py`
- Modify: `apps/api/src/taroai/config.py`
- Test: `tests/api/test_model_policy.py`

**Steps:**

1. Define `ModelPolicy` with allowed providers, denied providers, allowed models, sensitivity restrictions, default model, fallback models, and max context tokens.
2. Support tenant defaults and workspace overrides. Current implementation covers Settings-backed and API/SQL-managed scoped defaults plus allowed/denied lists and model sensitivity limits; provider references, first-pass provider change approvals, first-pass policy change approvals, and model policy version history are started.
3. Deny sensitive requests when the selected model is not approved for that sensitivity. Current implementation blocks runtime requests before provider invocation when the request sensitivity exceeds the selected model limit or the limit is missing.
4. Require explicit policy for external providers in enterprise tenants.
5. Add tests for allowed model, denied model, missing policy, and sensitivity mismatch.

**Acceptance Criteria:**

- Model choice is policy-driven.
- Sensitive enterprise data cannot silently route to models without explicit sensitivity approval.

## Task 3: Provider Adapters and Credential Boundary

**Files:**

- Create: `apps/api/src/taroai/model_gateway/providers.py`
- Future: `apps/api/src/taroai/secrets/service.py`
- Test: `tests/api/test_model_gateway_credentials.py`

**Steps:**

1. Define `ModelProviderAdapter` protocol with `invoke`.
2. Add an OpenAI-compatible adapter interface for providers that expose `/chat/completions`-style or Responses-style semantics. Current implementation keeps the runtime-facing adapter as `OpenAICompatibleModelGateway`.
3. Add placeholder adapter config for LiteLLM-compatible gateway or direct OpenAI-compatible provider endpoints. Current implementation starts with `ModelProviderConfig` records for OpenAI-compatible endpoints and provider-scoped model IDs.
4. Store provider credentials as secret references, never raw keys. Current implementation lets the OpenAI-compatible gateway resolve `api_key_secret_ref_id` through `SecretService` leases scoped to tenant/workspace/run and `model_gateway:invoke`, and provider registry entries can carry their own secret reference plus lease TTL.
5. Add tests that provider configs and responses do not expose secrets. Current tests cover secret-ref invocation, missing secret-service configuration, legacy raw key redaction from gateway dump/repr output, provider HTTP error body redaction, provider registry routing, API/worker router wiring, typed provider fallback policy, and SQL/Redis-backed provider rate-limit sharing.

**Acceptance Criteria:**

- Provider replacement does not affect Agent Runtime.
- Raw model API keys never enter run state, memory, artifacts, audit metadata, gateway dump/repr output, or provider-error messages exposed through verifier/API/log surfaces.

## Task 4: Budget, Rate Limit, and Metering

**Files:**

- Create: `apps/api/src/taroai/model_gateway/budget.py`
- Modify: `apps/api/src/taroai/store.py`
- Future: `apps/api/src/taroai/billing/service.py`
- Test: `tests/api/test_model_budget_metering.py`

**Steps:**

1. Define `ModelBudgetDecision` with allowed, reason, remaining budget, and retry time.
2. Check tenant, workspace, user, agent, and run budget before model call.
3. Record model usage meters: input tokens, output tokens, cached input tokens, call count, provider, model, and latency. Current implementation records call/input/output/cached-input/latency meters with model and provider when the router supplies a provider ID.
4. Return quota exceeded as a unified API error through `ApiExceptionManager`.
5. Add tests for allowed call, quota denied call, and meter creation.

**Acceptance Criteria:**

- Model spend can be capped before calls happen.
- Billing sees every model call through one meter path.

## Task 5: Fallback, Retry, and Failure Taxonomy

**Files:**

- Modify: `apps/api/src/taroai/model_gateway/gateway.py`
- Future: `apps/api/src/taroai/observability/models.py`
- Test: `tests/api/test_model_gateway_fallback.py`

**Steps:**

1. Define retryable provider failures: timeout, rate limited, transient unavailable, and network error. Current implementation treats provider response errors and provider rate-limit skips as fallback-eligible inside `ModelGatewayRouter`.
2. Define non-retryable failures: policy denied, quota exceeded, invalid prompt, and unsupported capability.
3. Try fallback models only when policy allows fallback and data sensitivity is compatible. Current implementation preserves model policy before router invocation, then routes the policy-resolved model across matching providers.
4. Record trace metadata for primary/fallback attempts. Current implementation returns first-pass `provider_attempts` on successful fallback paths and forwards them to runtime audit/billing metadata as safe summaries.
5. Add tests for retryable fallback and no fallback on policy denial. Current tests cover fallback on provider response failure, skipping a rate-limited provider before invocation, typed fallback controls, and runtime audit metadata for provider attempt summaries.

**Acceptance Criteria:**

- Fallback improves reliability without bypassing policy.
- Failures are classified consistently for evaluations and operations.

## Task 6: Runtime and Evaluation Integration

**Files:**

- Modify: `apps/api/src/taroai/agent/planning.py`
- Modify: `apps/api/src/taroai/agent/runtime.py`
- Future: `apps/api/src/taroai/evaluations/service.py`
- Test: `tests/api/test_runtime_uses_model_gateway.py`

**Steps:**

1. Keep Agent Runtime dependent on the gateway-facing interface only.
2. Runtime planning calls the OpenAI-compatible Model Gateway boundary.
3. Tests-only fixture adapters live under tests or fixtures and are not named as flow components.
4. Evaluation service uses the same gateway but marks purpose as `evaluation`.
5. Add tests that runtime model usage produces billing and audit records.
6. Ensure model prompts and responses are not written to audit unless redacted.

**Acceptance Criteria:**

- Runtime, evaluations, and future skills use one model access layer.
- Model governance is enforced uniformly.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_model_gateway_contract.py -q
python -m pytest tests/api/test_model_policy.py -q
python -m pytest tests/api/test_model_gateway_credentials.py -q
python -m pytest tests/api/test_model_budget_metering.py -q
python -m pytest tests/api/test_model_gateway_fallback.py -q
python -m pytest tests/api/test_runtime_uses_model_gateway.py -q
python -m pytest -q
```

Expected final result: all model calls flow through a tenant-aware gateway with policy, credentials, budget, metering, fallback, and trace controls.

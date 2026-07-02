# Model Gateway and Provider Governance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a governed model access layer so agents, skills, evaluations, and embeddings use approved providers through tenant-aware routing, budgets, audit, fallback, and observability.

**Architecture:** Runtime code must not call provider SDKs directly. Agent Runtime asks a Model Gateway for chat, response, embedding, rerank, or multimodal calls through an OpenAI-compatible request/response boundary; the gateway resolves tenant policy, model capability, budget, provider credentials, rate limits, and trace metadata. Local contract fixtures belong under `tests/` or contract-verification code only; they are never runtime defaults or product-flow dependencies.

**Tech Stack:** FastAPI, Pydantic, pytest, OpenAI-compatible model API contract, optional LiteLLM adapter, direct provider adapters, OTel-compatible traces, PostgreSQL, Redis rate limit counters later.

---

## Summary

Enterprise customers need model control: which providers are allowed, which models can touch sensitive data, how cost is capped, and how failover behaves. This plan turns model use into a platform service instead of scattered SDK calls.

Current state has OpenAI-compatible request/response/usage models, a default gateway boundary used by Agent Runtime planning, runtime planning records for `model_call_count`, `model_tokens_input`, `model_tokens_output`, and `model.plan.created` audit metadata when the gateway returns usage, plus a Pydantic budget guard for run, tenant, workspace, user, and agent model call/token limits. Pydantic Settings now support global and tenant/workspace-scoped model defaults, allowed model lists, denied model lists, and scoped model budget limits. A first-pass model policy store now supports in-memory and SQLite-compatible SQL persistence, `model_policy.read`/`model_policy.manage` API access, admin writes with safe audit metadata, runtime refresh in the API process, and worker startup loading through Settings. Denied planning attempts fail before provider invocation, emit `model.policy_denied` audit metadata, and classify run traces as `policy_denied`. Budget-exhausted runs fail before provider invocation and emit `model.budget_exceeded` audit metadata. Runtime model-request guardrails can block or redact content before provider invocation; runtime model-response guardrails can block or redact content before planned steps execute. Model Gateway configuration/response failures now fail the run, emit `model.gateway_failed` audit metadata, and keep prompt/response text out of audit metadata. Provider references, sensitivity constraints, windowed budget periods, rate limits, fallback routing, policy versioning/approval workflow, and live collector deployment verification remain planned work.

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
2. Support tenant defaults and workspace overrides. Current implementation covers Settings-backed and API/SQL-managed scoped defaults plus allowed/denied lists; provider references, policy versioning, and approval workflow remain planned.
3. Deny sensitive requests when the selected model is not approved for that sensitivity.
4. Require explicit policy for external providers in enterprise tenants.
5. Add tests for allowed model, denied model, missing policy, and sensitivity mismatch.

**Acceptance Criteria:**

- Model choice is policy-driven.
- Sensitive enterprise data cannot silently route to unapproved providers.

## Task 3: Provider Adapters and Credential Boundary

**Files:**

- Create: `apps/api/src/taroai/model_gateway/providers.py`
- Future: `apps/api/src/taroai/secrets/service.py`
- Test: `tests/api/test_model_gateway_credentials.py`

**Steps:**

1. Define `ModelProviderAdapter` protocol with `invoke`.
2. Add an OpenAI-compatible adapter interface for providers that expose `/chat/completions`-style or Responses-style semantics.
3. Add placeholder adapter config for LiteLLM-compatible gateway or direct OpenAI-compatible provider endpoints.
4. Store provider credentials as secret references, never raw keys.
5. Add tests that provider configs and responses do not expose secrets.

**Acceptance Criteria:**

- Provider replacement does not affect Agent Runtime.
- Raw model API keys never enter run state, memory, artifacts, or logs.

## Task 4: Budget, Rate Limit, and Metering

**Files:**

- Create: `apps/api/src/taroai/model_gateway/budget.py`
- Modify: `apps/api/src/taroai/store.py`
- Future: `apps/api/src/taroai/billing/service.py`
- Test: `tests/api/test_model_budget_metering.py`

**Steps:**

1. Define `ModelBudgetDecision` with allowed, reason, remaining budget, and retry time.
2. Check tenant, workspace, user, agent, and run budget before model call.
3. Record model usage meters: input tokens, output tokens, call count, cached tokens, provider, model, and latency.
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

1. Define retryable provider failures: timeout, rate limited, transient unavailable, and network error.
2. Define non-retryable failures: policy denied, quota exceeded, invalid prompt, and unsupported capability.
3. Try fallback models only when policy allows fallback and data sensitivity is compatible.
4. Record trace metadata for primary/fallback attempts.
5. Add tests for retryable fallback and no fallback on policy denial.

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

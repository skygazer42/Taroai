# Prompt and Guardrail Governance Implementation Plan


**Goal:** Build a governed prompt and guardrail layer so system prompts, agent prompts, workflow prompt nodes, safety rules, redaction rules, and prompt-injection defenses are versioned, testable, auditable, and tenant-aware.

**Architecture:** Prompts are platform configuration, not strings scattered through runtime code. A Prompt Registry stores prompt templates, variables, ownership, tenant/workspace scope, status, versions, and test cases. Guardrails run before retrieval, before model calls, after model calls, and before tool execution; policy decides whether to block, redact, request approval, or continue with warnings.

**Tech Stack:** FastAPI, Pydantic, pytest, PostgreSQL later, optional Promptfoo/Langfuse adapters, Model Gateway integration.

---

## Summary

Enterprise agents need repeatable prompts and explicit guardrails. This plan separates prompt governance from Agent Builder and self-evolving so prompts can be managed, tested, published, rolled back, and inspected independently.

Current state has a first-pass `taroai/guardrails` package with Pydantic stage/action/severity/rule/condition/evaluation/decision/finding models, tenant/workspace-scoped in-memory evaluation for allow, warn, redact, approval-required, block, and quarantine-run decisions, Settings-backed built-in secret-pattern and prompt-threat detectors, and a Settings-backed HTTP detector boundary for dedicated safety services. Tool Gateway calls guardrails before handler execution for block, approval-required, and redaction actions. Agent Runtime evaluates retrieval-stage rules before adding knowledge excerpts to model-planning context, and records summary-only guardrail audit metadata when excerpts are blocked, held for approval, or redacted. Agent Runtime also evaluates model-request rules before calling the OpenAI-compatible Model Gateway, model-response rules before executing planned steps, and artifact rules before publishing generated run artifacts. Long-term and short-term memory write services evaluate memory-write rules before persistence; long-term memory candidate writes and short-term run-memory writes with approval-required guardrail decisions stay in review until approved. Prompt registry, prompt rendering, provider-specific detector policy, and publication gates are still open.

## Task 1: Prompt Registry Package

**Files:**

- Create: `apps/api/src/taroai/prompts/__init__.py`
- Create: `apps/api/src/taroai/prompts/models.py`
- Create: `apps/api/src/taroai/prompts/registry.py`
- Test: `tests/api/test_prompt_registry_contract.py`

**Steps:**

1. Define `PromptTemplate` with tenant ID, workspace ID, name, owner, description, variables, sensitivity, status, and current version.
2. Define `PromptVersion` with version ID, template ID, content, changelog, created by, created at, and checksum.
3. Define statuses: draft, review, published, archived, rejected.
4. Implement in-memory registry for tests.
5. Add tests for create draft, publish version, rollback, tenant isolation, and immutable published content.

**Acceptance Criteria:**

- Prompts are typed and versioned.
- Published prompt versions cannot be mutated in place.

## Task 2: Prompt Variable and Secret Handling

**Files:**

- Modify: `apps/api/src/taroai/prompts/models.py`
- Create: `apps/api/src/taroai/prompts/rendering.py`
- Test: `tests/api/test_prompt_rendering_security.py`

**Steps:**

1. Define `PromptVariable` with name, type, required flag, default value, sensitivity, and source.
2. Reject raw secret values in prompt variables.
3. Render prompts with explicit variable maps only.
4. Redact sensitive variables in logs and audit payloads.
5. Add tests for missing variable, unexpected variable, secret rejection, and redacted render metadata.

**Acceptance Criteria:**

- Prompt rendering is repeatable.
- Secrets do not enter prompt content, logs, run events, or artifacts.

## Task 3: Guardrail Policy Models

**Files:**

- Create: `apps/api/src/taroai/guardrails/__init__.py`
- Create: `apps/api/src/taroai/guardrails/models.py`
- Create: `apps/api/src/taroai/guardrails/service.py`
- Test: `tests/api/test_guardrail_policy_models.py`

**Steps:**

1. Define guardrail stages: input, retrieval, model request, model response, tool request, tool response, artifact, and memory write.
2. Define guardrail actions: allow, warn, redact, require approval, block, and quarantine run.
3. Define `GuardrailRule` with tenant/workspace scope, stage, condition, action, severity, message, and audit requirement.
4. Implement in-memory guardrail evaluation.
5. Add tests for allow, block, redact, and approval-required outcomes.

**Acceptance Criteria:**

- Guardrail decisions are explicit Pydantic objects.
- Rules can differ by tenant and workspace.

**Current Implementation Notes:**

- `apps/api/src/taroai/guardrails/` defines Pydantic guardrail stages, actions, severities, rules, conditions, evaluation requests, and decisions.
- `InMemoryGuardrailService` stores tenant/workspace-scoped rules and evaluates stage, text, and attribute conditions.
- Decisions currently cover allow, warn, redact, approval-required, block, and quarantine-run actions.
- Tool Gateway request enforcement is started: guardrail block stops handlers, approval-required pauses execution, and redaction rewrites string inputs before handler execution.
- Agent Runtime retrieval-context enforcement is started: guarded knowledge excerpts are removed or redacted before model planning, and audit metadata records rule/action/resource IDs without raw excerpt content.
- Agent Runtime model request/response enforcement is started: guarded request content can be blocked or redacted before provider invocation, guarded response content can be blocked or redacted before planned steps execute, and audit metadata records summary fields without prompt or response text.
- Agent Runtime artifact publication enforcement is started: guarded artifact metadata can be blocked, redacted, or paused for persisted approval before artifact creation, sandbox artifact file content is evaluated for block/approval decisions before upload, and audit metadata records summary fields without raw artifact names, URIs, or artifact content.
- Long-term and short-term memory-write enforcement is started: guarded memory content can be blocked or redacted before persistence, approval-required long-term candidate writes and short-term run-memory writes stay in review until approved, and audit metadata records scope/key summaries without raw memory content.
- `create_app` and the agent worker runner inject a guardrail service into their default Tool Gateway and runtime paths.
- Built-in secret-pattern, prompt-threat, and HTTP detector boundaries are started through Settings-backed guardrail wiring, model-request/model-response guardrail approval can persist and resume through runtime state snapshots, artifact guardrail approval can persist and resume before publication, approval-required long-term memory candidates stay in review until approved, and approval-required short-term memory writes stay out of active run memory until approved with SQL-backed review storage when the SQL control-plane backend is selected. Provider-specific detector policy remains implementation work.

## Task 4: Prompt Injection and Data Exfiltration Checks

**Files:**

- Create: `apps/api/src/taroai/guardrails/detectors.py`
- Modify: `apps/api/src/taroai/knowledge/models.py`
- Test: `tests/api/test_prompt_injection_guardrails.py`

**Steps:**

1. Add detector interfaces for prompt injection, instruction override, credential request, and data exfiltration.
2. Start with rule-based detector cases for tests.
3. Check retrieved knowledge chunks before inserting them into model context.
4. Require approval or block when retrieved text asks the model to ignore system instructions or reveal secrets.
5. Add tests for benign document, injection-like document, credential request, and blocked exfiltration.

**Acceptance Criteria:**

- Retrieved documents cannot silently override platform instructions.
- Injection findings are auditable and visible in run traces.

**Current Implementation Notes:**

- Agent Runtime evaluates retrieved knowledge excerpts with retrieval-stage guardrail rules before model planning.
- Blocked or approval-required retrieval results are excluded from loaded context, and redacted results are rewritten before the model sees them.
- Retrieval guardrail audit records include rule/action/resource IDs and severity, not raw retrieved excerpt text.
- Built-in prompt-threat detection is started for high-confidence instruction override and data exfiltration patterns without serializing matched text in findings. Run traces now expose sanitized guardrail finding summaries for review without raw prompt/content fields. Provider-specific detector policy and broader semantic exfiltration checks remain implementation work.

## Task 5: Runtime and Model Gateway Integration

**Files:**

- Modify: `apps/api/src/taroai/agent/runtime.py`
- Future: `apps/api/src/taroai/model_gateway/service.py`
- Test: `tests/api/test_guardrails_runtime_integration.py`

**Steps:**

1. Runtime calls guardrails before planning, before tool invocation, before memory write, and before artifact publication.
2. Model Gateway calls guardrails before and after provider invocation.
3. Guardrail block returns a unified API error through `ApiExceptionManager`.
4. Approval-required guardrail creates approval request and pauses the run.
5. Add tests for blocked prompt, redacted model response, and approval-required tool request.

**Acceptance Criteria:**

- Guardrails are not optional route-level checks.
- Runtime behavior is bounded by policy at every risky boundary.

**Current Implementation Notes:**

- Runtime model-request guardrails run before model policy, budget, and provider invocation.
- Runtime model-response guardrails run before model plan billing/audit and before tool execution.
- Runtime artifact guardrails run before generated run artifacts are created.
- Memory-write guardrails run before long-term candidate persistence and short-term run-memory persistence.
- Blocked model guardrails fail the run with summary-only audit and run-failure metadata; redacted requests/responses continue with redacted content.
- Dedicated non-step approval resume is started for model-planning and artifact-publication guardrail decisions; long-term memory approval-required decisions route through candidate review, and short-term memory approval-required decisions route through a review queue before activation.

## Task 6: Evaluation and Publication Gates

**Files:**

- Modify: `apps/api/src/taroai/prompts/registry.py`
- Future: `apps/api/src/taroai/evaluations/service.py`
- Create: `docs/security/prompt-governance.md`
- Test: `tests/api/test_prompt_publication_gates.py`

**Steps:**

1. Attach test cases to prompt templates.
2. Require passing evals before publishing prompts used by production agents.
3. Record audit events for prompt draft, review, publish, rollback, and archive.
4. Document prompt review rules for solution engineers and tenant admins.
5. Add tests that failed eval blocks publication unless admin override is recorded.

**Acceptance Criteria:**

- Production prompts have review and test history.
- Prompt changes can be rolled back and investigated.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_prompt_registry_contract.py -q
python -m pytest tests/api/test_prompt_rendering_security.py -q
python -m pytest tests/api/test_guardrail_policy_models.py -q
python -m pytest tests/api/test_prompt_injection_guardrails.py -q
python -m pytest tests/api/test_guardrails_runtime_integration.py -q
python -m pytest tests/api/test_prompt_publication_gates.py -q
python -m pytest -q
```

Expected final result: prompts and guardrails are versioned, tenant-aware, test-gated, auditable, and integrated into runtime/model/tool boundaries.

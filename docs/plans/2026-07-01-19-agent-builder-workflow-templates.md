# Agent Builder and Workflow Templates Implementation Plan


**Goal:** Build the reusable agent/workflow creation layer that lets solution engineers and tenant admins turn successful work patterns into governed templates, workflows, forms, and published agent apps.

**Architecture:** Agent Builder is a versioned configuration system, not hard-coded prompts. Agent templates, workflow graphs, input forms, prompt blocks, skill bindings, connector bindings, knowledge scopes, memory policies, model policies, and approval policies are stored as Pydantic models and published through the same review/versioning path as skills. Runtime executes a published version, while drafts can be tested in preview mode.

**Tech Stack:** FastAPI, Pydantic, pytest, LangGraph-compatible workflow representation, future Next.js builder UI, PostgreSQL.

---

## Summary

The enterprise difference is low cold start through custom skills and workflows. This plan defines how teams build, test, publish, version, and reuse those agent apps without editing Python code for every customer.

## Task 1: Agent Template Package and Models

**Files:**

- Create: `apps/api/src/taroai/builder/__init__.py`
- Create: `apps/api/src/taroai/builder/models.py`
- Create: `apps/api/src/taroai/builder/service.py`
- Test: `tests/api/test_agent_template_models.py`

**Steps:**

1. Define `AgentTemplate` with tenant, workspace scope, name, description, owner, status, mode, prompt blocks, model policy, knowledge scopes, memory policy, skill bindings, connector bindings, and approval policy.
2. Define `AgentTemplateDraft` and `AgentTemplateVersion`.
3. Define allowed statuses: draft, in review, published, archived, rejected.
4. Require all backend management models to use Pydantic.
5. Add tests for draft creation, required policy fields, and tenant isolation.

**Acceptance Criteria:**

- Agent definitions are stored as typed config.
- Published agent versions are immutable.

## Task 2: Workflow Graph Model

**Files:**

- Create: `apps/api/src/taroai/builder/workflow.py`
- Test: `tests/api/test_workflow_graph_model.py`

**Steps:**

1. Define `WorkflowGraph` with nodes, edges, start node, end nodes, and variables.
2. Define node types: prompt, tool, connector, approval, condition, loop, agent handoff, transform, and artifact.
3. Validate graph has one start node and no unreachable required nodes.
4. Limit loop nodes with max iterations.
5. Add tests for valid graph, missing start, unreachable node, and unbounded loop.

**Acceptance Criteria:**

- Workflow templates can be validated before runtime.
- Builder cannot publish structurally invalid graphs.

## Task 3: Input Forms and Structured App Contract

**Files:**

- Create: `apps/api/src/taroai/builder/forms.py`
- Test: `tests/api/test_agent_input_forms.py`

**Steps:**

1. Define `AgentInputForm` with fields, validation schema, default values, examples, and secret-field marker.
2. Map form submission to `RunCreate` payload plus workflow variables.
3. Support reusable templates for common fields such as customer, product, region, dataset, due date, and output format.
4. Ensure secret fields are stored as secret refs, not run messages.
5. Add tests for form validation and secret-field redaction.

**Acceptance Criteria:**

- Agent apps can be launched from structured forms, not only blank chat.
- Secrets do not enter prompts or run logs.

## Task 4: Preview, Test Cases, and Publication

**Files:**

- Modify: `apps/api/src/taroai/builder/service.py`
- Future: `apps/api/src/taroai/evaluations/service.py`
- Test: `tests/api/test_agent_template_publication.py`

**Steps:**

1. Add preview run mode that uses draft templates but marks all outputs as non-production.
2. Attach test cases to agent templates.
3. Require passing tests or explicit admin override before publication.
4. Publication creates a version and never mutates previous versions.
5. Add tests for preview, publish, failed tests blocking publish, and rollback.

**Acceptance Criteria:**

- Drafts can be tested before employees use them.
- Publishing and rollback are auditable and versioned.

## Task 5: Convert Run to Template

**Files:**

- Modify: `apps/api/src/taroai/promotion/service.py`
- Modify: `apps/api/src/taroai/builder/service.py`
- Test: `tests/api/test_run_to_template_conversion.py`

**Steps:**

1. Take a completed run and propose a template draft using the run plan, tool sequence, artifacts, prompts, and approvals.
2. Redact user data and sensitive values from the draft.
3. Convert repeated tool calls into workflow graph nodes.
4. Require human review before saving as template.
5. Add tests for redaction, graph generation, and review-required status.

**Acceptance Criteria:**

- Successful work can become reusable workflow safely.
- Run-to-template does not leak customer data.

## Task 6: Builder API and Future UI Contract

**Files:**

- Modify: `apps/api/src/taroai/app.py`
- Create: `docs/builder/ui-contract.md`
- Future: `apps/web/app/(workspace)/builder`
- Test: `tests/api/test_builder_api_contract.py`

**Steps:**

1. Add APIs for drafts, versions, preview, test cases, publish, archive, rollback, and run-to-template candidate.
2. Document future UI surfaces: template list, editor, workflow graph, form builder, policy panel, test panel, publish review.
3. Keep UI consistent with the CREAO-compatible workspace style from `2026-07-01-08-client-portal-creao-ui.md`.
4. Enforce builder permissions for solution engineer, tenant owner, and skill publisher roles.
5. Add tests for permission checks and route shape.

**Acceptance Criteria:**

- Builder has a backend contract before frontend implementation.
- Tenant admins and solution engineers can manage reusable agents safely.

## Verification

Run after implementation:

```bash
python -m pytest tests/api/test_agent_template_models.py -q
python -m pytest tests/api/test_workflow_graph_model.py -q
python -m pytest tests/api/test_agent_input_forms.py -q
python -m pytest tests/api/test_agent_template_publication.py -q
python -m pytest tests/api/test_run_to_template_conversion.py -q
python -m pytest tests/api/test_builder_api_contract.py -q
python -m pytest -q
```

Expected final result: agent templates and workflow apps can be drafted, validated, previewed, tested, published, rolled back, and generated from successful runs.

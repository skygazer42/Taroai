# Knowledge, RAG, and Long-Term Memory Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the enterprise knowledge and long-term memory layer with ACL-aware document ingestion, retrieval, source citation, and reviewed memory writes.

**Architecture:** Keep knowledge separate from memory. Knowledge is enterprise content from documents/connectors; memory is learned operational context. Start with an internal retrieval contract and no-network in-memory retrieval fixture for tests; add pgvector or another vector backend only after Q-003 is answered. LlamaIndex is a candidate adapter for ingestion/retrieval orchestration, not a required dependency for the first unit-testable service.

**Tech Stack:** FastAPI, Pydantic, pytest, PostgreSQL metadata, object storage, tests-only retrieval fixtures, candidate LlamaIndex adapter, candidate pgvector/vector backend.

---

## Summary

This plan covers the layer that feeds Agent Runtime with enterprise context:

- Knowledge bases and documents.
- Document chunks and embeddings.
- ACL-aware retrieval.
- Source citations.
- Long-term memory read/write flow.
- Memory write review before activation.

Current state has an initial `taroai/knowledge` package with Pydantic knowledge base, document, chunk, retrieval request, and retrieval result models; in-memory and SQLite-compatible SQL metadata/chunk persistence; knowledge document source-content upload into `knowledge-documents` object storage with `storage_object_id` metadata; ACL-aware query-time retrieval; citations; API endpoints for base creation, document registration, and query behind `knowledge.write`/`knowledge.read`; safe audit events for base/document/query operations; and Agent Runtime context loading.

## Task 1: Knowledge Package Structure

**Files:**

- Modify: `apps/api/src/taroai/knowledge/__init__.py`
- Modify: `apps/api/src/taroai/knowledge/models.py`
- Modify: `apps/api/src/taroai/knowledge/service.py`
- Modify: `apps/api/src/taroai/knowledge/retrieval.py`
- Test: `tests/api/test_knowledge.py`

**Steps:**

1. Keep architecture tests requiring `knowledge/` package files.
2. Extend Pydantic models: `KnowledgeBase`, `KnowledgeDocument`, `DocumentChunk`, `RetrievalRequest`, `RetrievalResult`, and API request models.
3. Keep required metadata: tenant, workspace, source URI, ACL subjects, sensitivity level, document version, content hash.
4. Extend the in-memory knowledge service until durable repositories are introduced.

**Acceptance Criteria:**

- Knowledge code does not live in `app.py`, `domain.py`, or `memory/`.
- Knowledge and memory models are separate.

## Task 2: Document Ingestion Contract

**Files:**

- Modify: `apps/api/src/taroai/knowledge/models.py`
- Modify: `apps/api/src/taroai/knowledge/service.py`
- Modify: `apps/api/migrations/001_initial.sql`
- Test: `tests/api/test_knowledge.py`

**Steps:**

1. Add tests for creating a knowledge base.
2. Add tests for document registration with source metadata and ACL subjects.
3. Add tests for chunk creation and content hash deduplication.
4. Store large source files in object storage; store metadata and chunks in PostgreSQL.
5. Keep embedding generation behind a provider interface so unit tests can use local fixture embeddings.

**Acceptance Criteria:**

- Ingestion records document source and ACL.
- Duplicate content hash can be detected.
- Chunks are tenant/workspace scoped.

**Current Implementation Notes:**

- `SqlKnowledgeService` persists knowledge bases, documents, and chunks through `TAROAI_KNOWLEDGE_SERVICE_BACKEND=sql`.
- SQLite-compatible migration tables exist for `knowledge_bases`, `knowledge_documents`, and `knowledge_chunks`; `knowledge_documents.storage_object_id` links document metadata to the managed source object.
- SQL retrieval reuses the internal ACL/sensitivity-aware retrieval contract.
- Document registration writes source content through the object storage adapter under `knowledge-documents` and returns the document `storage_object_id`; embeddings, vector backend, and connector ingestion remain implementation work.

## Task 3: ACL-Aware Retrieval

**Files:**

- Modify: `apps/api/src/taroai/knowledge/retrieval.py`
- Test: `tests/api/test_knowledge.py`

**Steps:**

1. Add tests where an allowed user retrieves a chunk.
2. Add tests where a user without ACL cannot retrieve the same chunk.
3. Add sensitivity-level filtering.
4. Retrieval request must include tenant, allowed workspaces, ACL subjects, and clearance level.
5. Result must include document ID, chunk ID, source URI, excerpt, score, and citation metadata.

**Acceptance Criteria:**

- Query-time ACL filtering is mandatory.
- Ingestion-time filtering alone is not accepted.
- Retrieval results are citeable.

## Task 4: Candidate LlamaIndex Adapter

**Files:**

- Create: `apps/api/src/taroai/knowledge/llama_index_adapter.py`
- Test: `tests/api/test_knowledge_llama_index_adapter.py`

**Steps:**

1. Add adapter contract tests with sample documents and local fixture embeddings.
2. Keep LlamaIndex behind an adapter so core service tests do not require external API keys or a live index.
3. Adapter should accept Pydantic `RetrievalRequest` and return Pydantic `RetrievalResult`.
4. Do not let LlamaIndex bypass ACL filtering.

**Acceptance Criteria:**

- Adapter can be replaced without changing Agent Runtime.
- No external network calls in unit tests.

## Task 5: Memory Candidate Review

**Files:**

- Modify: `apps/api/src/taroai/memory/models.py`
- Modify: `apps/api/src/taroai/memory/service.py`
- Test: `tests/api/test_memory_review.py`

**Steps:**

1. Add memory statuses: `candidate`, `active`, `rejected`, `expired`.
2. Runtime proposes memory as `candidate`.
3. User/admin approves candidate into `active`.
4. Rejected memory is retained for audit but not returned in active reads.
5. Emit audit event for candidate creation and approval/rejection.

**Acceptance Criteria:**

- Agent Runtime cannot silently write active long-term memory.
- Active memory reads exclude rejected/expired records.

**Current Implementation Notes:**

- `MemoryStatus` supports `candidate`, `active`, `rejected`, and `expired`.
- In-memory and SQLite-compatible SQL long-term memory services support candidate creation plus approve/reject review.
- `/api/memory/candidates`, `/api/memory`, `/api/memory/{memory_id}/approve`, and `/api/memory/{memory_id}/reject` are started behind identity permissions.
- Candidate creation, approval, and rejection emit audit metadata without storing memory content in audit metadata.
- Agent Runtime loads approved memory records before planning. Runtime proposal wiring, richer review policy, and advanced context policy remain implementation work.

## Task 6: Agent Runtime Context Loading

**Files:**

- Modify: `apps/api/src/taroai/agent/runtime.py`
- Modify: `apps/api/src/taroai/agent/state.py`
- Test: `tests/api/test_agent_runtime_context.py`

**Steps:**

1. Add `retrieved_context` to `AgentRuntimeState`.
2. Inject knowledge and memory services into `AgentRuntime`.
3. Before planning, load allowed knowledge and memory context.
4. Emit `context.loaded` with counts and source IDs, not full sensitive content.
5. Keep context loading failures recoverable unless policy marks retrieval as required.

**Acceptance Criteria:**

- Runtime plan generation sees allowed context.
- Run events do not leak full sensitive document content.

**Current Implementation Notes:**

- `AgentRuntimeState` includes `retrieved_context`.
- `AgentRuntime` can load ACL/sensitivity-filtered knowledge retrieval results and approved long-term memory before planning.
- Model Gateway planning requests receive allowed context in a system message.
- `context.loaded` run events include only counts and source IDs, not knowledge excerpts or memory content.
- SQL runtime state persistence includes `retrieved_context`.
- Durable knowledge ingestion, embeddings/vector backend, connector sync, advanced context policy, and context quality evaluation remain implementation work.

## Verification

Run after each task:

```bash
python -m pytest tests/api/test_knowledge_ingestion.py -q
python -m pytest tests/api/test_knowledge_retrieval_acl.py -q
python -m pytest tests/api/test_memory_review.py -q
python -m pytest tests/api/test_agent_runtime_context.py -q
python -m pytest -q
```

Expected final result: knowledge retrieval is tenant/workspace/ACL aware, memory writes are reviewed, and runtime context loading is tested end to end.

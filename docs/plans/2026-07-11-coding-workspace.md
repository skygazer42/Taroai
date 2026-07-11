# Coding Workspace Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add isolated repository workspaces and a complete code-change delivery surface.

**Architecture:** Workspace repository bindings create run-scoped Coding Workspaces owned by Engine/Sandbox sessions. Runners submit normalized evidence; Taroai governs review, checkpoints, commit, and pull-request delivery.

**Tech Stack:** FastAPI, Pydantic, memory/SQL registries, existing Connector/Secret boundaries, vanilla JavaScript Chat UI.

---

### Task 1: Domain and persistence

Create repository binding, Coding Workspace, change, test, checkpoint, and delivery models. Add memory/SQL registry and migration.

### Task 2: API and runtime integration

Add CRUD/lifecycle/evidence APIs. Add repository mentions and Agent runtime snapshot bindings. Create a Coding Workspace when an eligible Run starts.

### Task 3: Coding Workspace UI

Add repository management and a Chat sidecar for files, Diff, tests, checkpoints, commit, and pull-request state.

### Task 4: Commit

Commit to `main`. Per user instruction, do not run tests, database validation, lint, typecheck, Docker, or browser QA.

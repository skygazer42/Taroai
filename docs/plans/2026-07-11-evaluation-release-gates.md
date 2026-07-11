# Evaluation and Release Gates Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the existing unified Evaluation engine to real Agent Runs, publication gates, and a workspace Evaluation UI.

**Architecture:** Persist immutable Suites, Runs, and baselines through the existing repository. A real Agent executor creates pinned Runs and returns normalized observations. Publication verifies the latest passing digest-matched evaluation.

**Tech Stack:** FastAPI, existing EvaluationService/scorers/repository, AgentRuntime, SQL migrations, vanilla JavaScript.

---

### Task 1: Wire repository and Agent executor

Build memory/SQL repository selection and a real Agent Evaluation executor.

### Task 2: Evaluation APIs and publication gates

Add Suite, Run, evidence, baseline, and target-history APIs. Require a matching passing Run when an Agent version binds an Evaluation Suite.

### Task 3: Evaluation product page

Add navigation and UI for Suites, Runs, metrics, regressions, cases, and baseline promotion.

### Task 4: Commit

Commit to `main` without tests, database validation, lint, typecheck, Docker, or browser QA.

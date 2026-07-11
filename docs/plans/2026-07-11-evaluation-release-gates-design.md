# Evaluation and Release Gates Design

## Goal

Make Agent and Skill quality measurable, reproducible, regression-aware, and mandatory before publication.

## Architecture

The existing unified Evaluation models and scorers remain canonical. A real Agent executor creates a version-pinned Agent Run for each Golden Case and converts its output, usage, latency, tool errors, interventions, and side effects into an `EvaluationObservation`. Skill package evaluations remain package-native but are aggregated in the same product surface.

## Lifecycle

1. Register an immutable Evaluation Suite with Golden Cases and Gate policy.
2. Run the Suite against a pinned Agent or Skill version and digest.
3. Persist case results, metrics, evidence digest, and baseline comparison.
4. Promote a passing Run to baseline.
5. Agent/Skill publication requires a passing, digest-matching Evaluation Run when a Suite is bound.

## Product surface

The Evaluation page lists Suites, Runs, baselines, weighted score, success rate, tool errors, intervention rate, latency, token/cost totals, regressions, and case evidence. Users can register Suites, run them, inspect failures, and promote baselines.

## Failure behavior

Individual case errors remain evidence and do not erase the Evaluation Run. Budget, side-effect, and regression violations block promotion. Evaluation execution never silently changes the target version.

## User constraint

Implementation proceeds without tests or runtime/database/browser validation.

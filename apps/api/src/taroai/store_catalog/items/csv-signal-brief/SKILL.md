---
name: CSV Signal Brief
description: Turn an attached CSV into a concise, evidence-backed quality, trend, and anomaly brief.
license: Apache-2.0
---

# CSV Signal Brief

Analyze CSV data supplied by the user and produce a compact decision brief.

## Procedure

1. If no CSV or tabular content is available, ask the user to attach or identify it. Do not invent a dataset.
2. Inspect headers, row count, missing values, duplicate rows, obvious type inconsistencies, and numeric ranges.
3. Use `sandbox.command` only when calculation is needed. Prefer Python's standard `csv` and `statistics` modules; do not install packages or use the network.
4. Identify useful distributions, comparisons, trends, or outliers. Name the method used and distinguish observed facts from interpretations.
5. Avoid reproducing sensitive row-level values. Quote column names and aggregate values that support each finding.

## Response format

- Overview
- Data quality
- Key signals
- Caveats
- Suggested next steps

For small or incomplete datasets, state the limitation prominently. Never imply causation from correlation alone.

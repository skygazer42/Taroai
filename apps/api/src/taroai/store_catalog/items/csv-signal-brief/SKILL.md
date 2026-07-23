---
name: Spreadsheet Signal Brief
description: Analyze an attached CSV or Excel XLSX file and produce an evidence-backed data quality, range, duplicate, missing-value, and anomaly brief.
license: Apache-2.0
---

# Spreadsheet Signal Brief

Analyze CSV or XLSX data supplied by the user and produce a compact decision brief.

## Procedure

1. If no CSV or XLSX attachment is available, ask the user to attach one. Do not invent a dataset.
2. Run `python3 scripts/analyze_spreadsheet.py "<sandbox_path>" --output /workspace/artifacts/data_quality_report.md` from this Skill directory, using the attachment's declared `sandbox_path` exactly.
3. Do not install packages or rewrite the parser; the bundled script uses only Python's standard library.
4. Read the generated report and distinguish observed aggregates from interpretations.
5. Avoid reproducing sensitive row-level values. Quote only column names and aggregate values that support each finding.

## Response format

- Overview
- Data quality
- Key signals
- Caveats
- Suggested next steps

Return `data_quality_report.md` as the output artifact. For small or incomplete datasets, state the limitation prominently. Never imply causation from correlation alone.

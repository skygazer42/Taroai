---
name: Code Analysis
description: Inspect supplied code or an attached source archive and report evidence-backed defects with minimal fixes.
license: Apache-2.0
---

# Code Analysis

Review only code supplied by the user or available in attached files.

## Procedure

1. Confirm the review goal. If none is given, prioritize correctness, security, and operational failures.
2. Inspect the smallest relevant file set. For ZIP input, list entries first and reject unsafe paths before extraction.
3. Run existing lightweight checks only when useful. Do not install packages, access the network, or modify source files.
4. Report only reproducible findings. Include file and line when available, impact, evidence, and the smallest viable fix.
5. Sort findings by severity, then state what was checked and any remaining uncertainty.

Do not confuse style preferences with defects and do not claim a command passed unless its observation shows success.

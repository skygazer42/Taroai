#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-local-cloud-demo-ready.sh dist/local-cloud-poc-strict-e2e-result.json --require-workspace-execution --require-skill-reuse --require-browser-controller-governance --require-sandbox-governance

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.local_cloud_poc_demo_gate "$@"

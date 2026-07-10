#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-local-cloud-poc.sh --api-base-url http://localhost:8000 --browser-base-url http://localhost:8001 --web-base-url http://localhost:3000 --bootstrap-token "$TAROAI_TENANT_BOOTSTRAP_TOKEN"
# Strict demo: scripts/verify-local-cloud-poc.sh --require-model-execution --browser-workspace-url http://web --browser-workspace-api-base-url http://api:8000 --browser-workspace-submit-message "Generate a hello report in the sandbox."

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.local_cloud_poc_verification "$@"

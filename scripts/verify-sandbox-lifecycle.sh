#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-sandbox-lifecycle.sh --base-url http://localhost:8002 --api-key "$TAROAI_SANDBOX_CONTROLLER_API_KEY" > sandbox-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.sandbox.lifecycle_verification "$@"

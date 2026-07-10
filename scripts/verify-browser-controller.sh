#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-browser-controller.sh --base-url http://localhost:8001 --api-key "$TAROAI_BROWSER_CONTROLLER_API_KEY" > browser-controller-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.sandbox.browser_verification "$@"

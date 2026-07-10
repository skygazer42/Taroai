#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-model-gateway.sh --profile deepseek --api-key-env-var TAROAI_MODEL_GATEWAY_API_KEY > model-gateway-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.model_gateway.verification "$@"

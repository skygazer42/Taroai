#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-kubernetes-sandbox.sh --namespace taroai --service-account-name taroai-sandbox-runner --verify-runtime-policy > kubernetes-sandbox-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.sandbox.kubernetes_verification "$@"

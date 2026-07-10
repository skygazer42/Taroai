#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-secret-manager.sh --backend "$TAROAI_SECRET_SERVICE_BACKEND" --secret-value-env-var TAROAI_SECRET_MANAGER_VERIFICATION_VALUE > secret-manager-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.secrets.verification "$@"

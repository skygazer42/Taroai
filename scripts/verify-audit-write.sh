#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-audit-write.sh --api-base-url http://localhost:8000 --tenant-id tenant_verify --user-id user_verify --denied-tenant-id tenant_denied --denied-user-id user_denied [--run-id run_existing] > audit-write-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.api_verification --check audit_write "$@"

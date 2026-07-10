#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-restore-drill.sh --drill-id restore_drill_2026_07 --backup-manifest backup-manifest.json --executed-restore-order database,object_storage,redis,config,workers --migration-plan migration-plan.json --object-storage-verification object-storage-verification.json --redis-queue-verification redis-queue-verification.json --config-restored --post-restore-checks-passed --rpo-minutes 45 --rto-minutes 25 > restore-drill-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.restore_drill_verification "$@"

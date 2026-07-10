#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-redis-queue.sh --redis-url "$TAROAI_REDIS_URL" > redis-queue-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

if [ "$#" -eq 0 ]; then
  exec python -m taroai.workers.redis_verification \
    --redis-url "${TAROAI_REDIS_URL:?TAROAI_REDIS_URL is required}"
fi

exec python -m taroai.workers.redis_verification "$@"

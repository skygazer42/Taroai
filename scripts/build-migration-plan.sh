#!/usr/bin/env sh
set -eu

# Usage: scripts/build-migration-plan.sh --database-url "$TAROAI_DATABASE_URL" --migrations-path apps/api/migrations > migration-plan.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

if [ "$#" -eq 0 ]; then
  exec python -m taroai.db.migration_cli \
    --database-url "${TAROAI_DATABASE_URL:?TAROAI_DATABASE_URL is required}" \
    --migrations-path "$REPO_ROOT/apps/api/migrations"
fi

exec python -m taroai.db.migration_cli "$@"

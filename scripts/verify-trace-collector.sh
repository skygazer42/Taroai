#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-trace-collector.sh --endpoint-url "$TAROAI_TRACE_EXPORTER_ENDPOINT_URL" --api-key "$TAROAI_TRACE_EXPORTER_API_KEY" > trace-collector-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.observability.verification "$@"

#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-object-storage.sh --endpoint-url "$TAROAI_OBJECT_STORAGE_ENDPOINT" --bucket "$TAROAI_OBJECT_STORAGE_BUCKET" --access-key-id "$TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID" --secret-access-key "$TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY" > object-storage-verification.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

if [ "$#" -eq 0 ]; then
  exec python -m taroai.storage.object_storage_verification \
    --endpoint-url "${TAROAI_OBJECT_STORAGE_ENDPOINT:?TAROAI_OBJECT_STORAGE_ENDPOINT is required}" \
    --bucket "${TAROAI_OBJECT_STORAGE_BUCKET:?TAROAI_OBJECT_STORAGE_BUCKET is required}" \
    --region "${TAROAI_OBJECT_STORAGE_REGION:-us-east-1}" \
    --access-key-id "${TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID:?TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID is required}" \
    --secret-access-key "${TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY:?TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY is required}"
fi

exec python -m taroai.storage.object_storage_verification "$@"

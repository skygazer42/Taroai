#!/usr/bin/env sh
set -eu

# Usage: scripts/build-release-transfer-evidence.sh --package dist/taroai-release.zip --signature dist/taroai-release.zip.sig.json --trusted-public-key creao-release-2026-01=<base64-public-key> --output dist/release-transfer-evidence.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.transfer_evidence "$@"

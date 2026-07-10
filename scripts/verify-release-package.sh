#!/usr/bin/env sh
set -eu

# Usage: scripts/verify-release-package.sh --output dist/taroai-release.zip --expected-sha256 <sha256> --signature dist/taroai-release.zip.sig.json --trusted-public-key creao-release-2026-01=<base64-public-key>

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.release_package --verify "$@"

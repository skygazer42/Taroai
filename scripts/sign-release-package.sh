#!/usr/bin/env sh
set -eu

# Usage: TAROAI_RELEASE_SIGNING_PRIVATE_KEY=<base64-raw-ed25519-private-key> scripts/sign-release-package.sh --output dist/taroai-release.zip --signature-output dist/taroai-release.zip.sig.json --key-id creao-release-2026-01 --private-key-env TAROAI_RELEASE_SIGNING_PRIVATE_KEY

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.release_package --sign "$@"

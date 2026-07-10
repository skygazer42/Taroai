#!/usr/bin/env sh
set -eu

# Usage: TAROAI_INSTALL_VALIDATION_OUTPUT=install-validation.json scripts/validate-install.sh --mode private --release-package dist/taroai-release.zip --release-transfer-evidence dist/release-transfer-evidence.json --expected-release-package-sha256 <sha256> --release-package-signature dist/taroai-release.zip.sig.json --release-package-trusted-public-key creao-release-2026-01=<base64-public-key> --migration-plan migration-plan.json --object-storage-verification object-storage-verification.json --redis-queue-verification redis-queue-verification.json --secret-manager-verification secret-manager-verification.json --model-gateway-verification model-gateway-verification.json --sandbox-controller-api-key "$TAROAI_SANDBOX_CONTROLLER_API_KEY" --sandbox-verification sandbox-verification.json --kubernetes-sandbox-verification kubernetes-sandbox-verification.json --browser-controller-api-key "$TAROAI_BROWSER_CONTROLLER_API_KEY" --browser-controller-verification browser-controller-verification.json --web-base-url https://workspace.customer.example --event-stream-verification event-stream-verification.json --audit-write-verification audit-write-verification.json --trace-collector-verification trace-collector-verification.json --support-bundle-redaction-evidence support-bundle-redaction.json --restore-drill-verification restore-drill-verification.json --runtime-closed-loop-evidence local-cloud-poc-demo-gate-result.json --output install-validation.json

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/apps/api/src"
else
  export PYTHONPATH="$REPO_ROOT/apps/api/src:$PYTHONPATH"
fi

exec python -m taroai.deployment.install_validation "$@"

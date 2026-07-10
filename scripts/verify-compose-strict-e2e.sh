#!/usr/bin/env sh
set -eu

# Usage:
#   TAROAI_MODEL_GATEWAY_API_KEY=... \
#   TAROAI_MODEL_GATEWAY_MODEL=... \
#   scripts/verify-compose-strict-e2e.sh
#
# Optional:
#   TAROAI_COMPOSE_ENV_FILE=infra/config/deepseek.env.example \
#   TAROAI_COMPOSE_STRICT_E2E_OUTPUT=dist/local-cloud-poc-strict-e2e-result.json \
#   TAROAI_COMPOSE_STRICT_E2E_DEMO_GATE_OUTPUT=dist/local-cloud-poc-demo-gate-result.json \
#   TAROAI_COMPOSE_STRICT_E2E_EVENT_STREAM_OUTPUT=dist/event-stream-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_AUDIT_WRITE_OUTPUT=dist/audit-write-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_SANDBOX_OUTPUT=dist/sandbox-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_BROWSER_CONTROLLER_OUTPUT=dist/browser-controller-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_MODEL_GATEWAY_OUTPUT=dist/model-gateway-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_OBJECT_STORAGE_OUTPUT=dist/object-storage-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_REDIS_QUEUE_OUTPUT=dist/redis-queue-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_MIGRATION_PLAN_OUTPUT=dist/migration-plan.json \
#   TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_OUTPUT=dist/support-bundle.zip \
#   TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_REDACTED_OUTPUT=dist/support-bundle-redacted.zip \
#   TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT=dist/support-bundle-redaction.json \
#   TAROAI_COMPOSE_STRICT_E2E_RELEASE_PACKAGE_OUTPUT=dist/taroai-release.zip \
#   TAROAI_COMPOSE_STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT=dist/taroai-release.zip.sig.json \
#   TAROAI_COMPOSE_STRICT_E2E_RELEASE_SIGNING_OUTPUT=dist/release-signing-result.json \
#   TAROAI_COMPOSE_STRICT_E2E_RELEASE_TRANSFER_OUTPUT=dist/release-transfer-evidence.json \
#   TAROAI_COMPOSE_STRICT_E2E_SECRET_MANAGER_VERIFICATION=dist/secret-manager-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_TRACE_COLLECTOR_VERIFICATION=dist/trace-collector-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_RESTORE_DRILL_VERIFICATION=dist/restore-drill-verification.json \
#   TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_OUTPUT=dist/install-validation.json \
#   TAROAI_COMPOSE_STRICT_E2E_REQUIRE_SANDBOX_GOVERNANCE=1 \
#   TAROAI_COMPOSE_STRICT_E2E_KEEP_STACK=1 \
#   scripts/verify-compose-strict-e2e.sh
#
# This gate delegates strict API/UI/artifact assertions to
# scripts/verify-local-cloud-poc.sh after Compose services become healthy, then
# optionally uses scripts/verify-event-stream.sh, scripts/verify-audit-write.sh,
# scripts/verify-sandbox-lifecycle.sh, and scripts/verify-browser-controller.sh
# plus scripts/build-migration-plan.sh, scripts/verify-model-gateway.sh,
# scripts/verify-object-storage.sh, scripts/verify-redis-queue.sh, and
# scripts/redact-support-bundle.sh. It also builds, signs, and verifies a
# local release package with scripts/build-release-package.sh,
# scripts/sign-release-package.sh, and scripts/build-release-transfer-evidence.sh
# before passing same-run demo evidence into scripts/validate-install.sh.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
COMPOSE_FILE="${TAROAI_COMPOSE_FILE:-$REPO_ROOT/infra/docker-compose.yml}"
COMPOSE_PROJECT_NAME="${TAROAI_COMPOSE_PROJECT_NAME:-taroai-strict-e2e-$$}"

export TAROAI_WEB_PORT="${TAROAI_WEB_PORT:-3300}"
export TAROAI_API_PORT="${TAROAI_API_PORT:-8800}"
export TAROAI_BROWSER_CONTROLLER_PORT="${TAROAI_BROWSER_CONTROLLER_PORT:-8801}"
export TAROAI_SANDBOX_CONTROLLER_PORT="${TAROAI_SANDBOX_CONTROLLER_PORT:-8802}"
export POSTGRES_PORT="${POSTGRES_PORT:-55432}"
export REDIS_PORT="${REDIS_PORT:-56379}"
export MINIO_API_PORT="${MINIO_API_PORT:-59000}"
export MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-59001}"
export TAROAI_TENANT_BOOTSTRAP_TOKEN="${TAROAI_TENANT_BOOTSTRAP_TOKEN:-local_bootstrap_token}"
export TAROAI_BROWSER_CONTROLLER_API_KEY="${TAROAI_BROWSER_CONTROLLER_API_KEY:-local_browser_controller_key_2026_dev_only}"
export TAROAI_SANDBOX_CONTROLLER_API_KEY="${TAROAI_SANDBOX_CONTROLLER_API_KEY:-local_sandbox_controller_key_2026_dev_only}"

API_BASE_URL="${TAROAI_COMPOSE_STRICT_E2E_API_BASE_URL:-http://localhost:$TAROAI_API_PORT}"
BROWSER_BASE_URL="${TAROAI_COMPOSE_STRICT_E2E_BROWSER_BASE_URL:-http://localhost:$TAROAI_BROWSER_CONTROLLER_PORT}"
SANDBOX_BASE_URL="${TAROAI_COMPOSE_STRICT_E2E_SANDBOX_BASE_URL:-http://localhost:$TAROAI_SANDBOX_CONTROLLER_PORT}"
WEB_BASE_URL="${TAROAI_COMPOSE_STRICT_E2E_WEB_BASE_URL:-http://localhost:$TAROAI_WEB_PORT}"
BROWSER_WORKSPACE_URL="${TAROAI_COMPOSE_STRICT_E2E_BROWSER_WORKSPACE_URL:-http://web}"
BROWSER_WORKSPACE_API_BASE_URL="${TAROAI_COMPOSE_STRICT_E2E_BROWSER_WORKSPACE_API_BASE_URL:-http://api:8000}"
SUBMIT_MESSAGE="${TAROAI_COMPOSE_STRICT_E2E_SUBMIT_MESSAGE:-Generate a hello report in the sandbox.}"
SUBMIT_EXPECTED_TEXT="${TAROAI_COMPOSE_STRICT_E2E_SUBMIT_EXPECTED_TEXT:-succeeded}"
WAIT_TIMEOUT_SECONDS="${TAROAI_COMPOSE_STRICT_E2E_WAIT_TIMEOUT_SECONDS:-240}"
RUN_STATUS_POLL_ATTEMPTS="${TAROAI_COMPOSE_STRICT_E2E_RUN_STATUS_POLL_ATTEMPTS:-90}"
RUN_STATUS_POLL_INTERVAL_SECONDS="${TAROAI_COMPOSE_STRICT_E2E_RUN_STATUS_POLL_INTERVAL_SECONDS:-1}"
BROWSER_WORKSPACE_SUBMIT_POLL_ATTEMPTS="${TAROAI_COMPOSE_STRICT_E2E_BROWSER_SUBMIT_POLL_ATTEMPTS:-120}"
HTTP_TIMEOUT_SECONDS="${TAROAI_COMPOSE_STRICT_E2E_HTTP_TIMEOUT_SECONDS:-45}"
STRICT_E2E_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_OUTPUT:-$REPO_ROOT/dist/local-cloud-poc-strict-e2e-result.json}"
STRICT_E2E_DEMO_GATE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_DEMO_GATE_OUTPUT:-$REPO_ROOT/dist/local-cloud-poc-demo-gate-result.json}"
STRICT_E2E_EVENT_STREAM_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_EVENT_STREAM_OUTPUT:-$REPO_ROOT/dist/event-stream-verification.json}"
STRICT_E2E_AUDIT_WRITE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_AUDIT_WRITE_OUTPUT:-$REPO_ROOT/dist/audit-write-verification.json}"
STRICT_E2E_SANDBOX_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_SANDBOX_OUTPUT:-$REPO_ROOT/dist/sandbox-verification.json}"
STRICT_E2E_BROWSER_CONTROLLER_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_BROWSER_CONTROLLER_OUTPUT:-$REPO_ROOT/dist/browser-controller-verification.json}"
STRICT_E2E_MODEL_GATEWAY_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_MODEL_GATEWAY_OUTPUT:-$REPO_ROOT/dist/model-gateway-verification.json}"
STRICT_E2E_OBJECT_STORAGE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_OBJECT_STORAGE_OUTPUT:-$REPO_ROOT/dist/object-storage-verification.json}"
STRICT_E2E_REDIS_QUEUE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_REDIS_QUEUE_OUTPUT:-$REPO_ROOT/dist/redis-queue-verification.json}"
STRICT_E2E_MIGRATION_PLAN_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_MIGRATION_PLAN_OUTPUT:-$REPO_ROOT/dist/migration-plan.json}"
STRICT_E2E_SUPPORT_BUNDLE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_OUTPUT:-$REPO_ROOT/dist/support-bundle.zip}"
STRICT_E2E_SUPPORT_BUNDLE_REDACTED_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_REDACTED_OUTPUT:-$REPO_ROOT/dist/support-bundle-redacted.zip}"
STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT:-$REPO_ROOT/dist/support-bundle-redaction.json}"
STRICT_E2E_RELEASE_PACKAGE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_RELEASE_PACKAGE_OUTPUT:-$REPO_ROOT/dist/taroai-release.zip}"
STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT:-$REPO_ROOT/dist/taroai-release.zip.sig.json}"
STRICT_E2E_RELEASE_SIGNING_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_RELEASE_SIGNING_OUTPUT:-$REPO_ROOT/dist/release-signing-result.json}"
STRICT_E2E_RELEASE_TRANSFER_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_RELEASE_TRANSFER_OUTPUT:-$REPO_ROOT/dist/release-transfer-evidence.json}"
STRICT_E2E_RELEASE_SIGNING_KEY_ID="${TAROAI_COMPOSE_STRICT_E2E_RELEASE_SIGNING_KEY_ID:-taroai-local-strict-e2e}"
STRICT_E2E_SECRET_MANAGER_VERIFICATION="${TAROAI_COMPOSE_STRICT_E2E_SECRET_MANAGER_VERIFICATION:-}"
STRICT_E2E_TRACE_COLLECTOR_VERIFICATION="${TAROAI_COMPOSE_STRICT_E2E_TRACE_COLLECTOR_VERIFICATION:-}"
STRICT_E2E_RESTORE_DRILL_VERIFICATION="${TAROAI_COMPOSE_STRICT_E2E_RESTORE_DRILL_VERIFICATION:-}"
STRICT_E2E_INSTALL_VALIDATION_OUTPUT="${TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_OUTPUT:-}"
STRICT_E2E_INSTALL_VALIDATION_MODE="${TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_MODE:-cloud}"
STRICT_E2E_OWNER_EMAIL="${TAROAI_COMPOSE_STRICT_E2E_OWNER_EMAIL:-owner@example.com}"
STRICT_E2E_OWNER_DISPLAY_NAME="${TAROAI_COMPOSE_STRICT_E2E_OWNER_DISPLAY_NAME:-Owner}"
STRICT_E2E_OWNER_PASSWORD="${TAROAI_COMPOSE_STRICT_E2E_OWNER_PASSWORD:-correct horse battery staple}"
STRICT_E2E_DENIED_TENANT_ID="${TAROAI_COMPOSE_STRICT_E2E_DENIED_TENANT_ID:-tenant_strict_e2e_denied}"
STRICT_E2E_DENIED_USER_ID="${TAROAI_COMPOSE_STRICT_E2E_DENIED_USER_ID:-user_strict_e2e_denied}"
SANDBOX_GOVERNANCE_ARGS=""
if [ "${TAROAI_COMPOSE_STRICT_E2E_REQUIRE_SANDBOX_GOVERNANCE:-0}" = "1" ]; then
  SANDBOX_GOVERNANCE_ARGS="--require-sandbox-governance"
fi

env_file_value() {
  key="$1"
  if [ -z "${TAROAI_COMPOSE_ENV_FILE:-}" ] || [ ! -f "$TAROAI_COMPOSE_ENV_FILE" ]; then
    return 0
  fi
  awk -v expected_key="$key" '
    /^[[:space:]]*($|#)/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
      sub(/^export[[:space:]]+/, "", line)
      separator = index(line, "=")
      if (separator == 0) {
        next
      }
      name = substr(line, 1, separator - 1)
      value = substr(line, separator + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (name == expected_key) {
        if ((value ~ /^".*"$/) || (value ~ /^'\''.*'\''$/)) {
          value = substr(value, 2, length(value) - 2)
        }
        print value
        exit
      }
    }
  ' "$TAROAI_COMPOSE_ENV_FILE"
}

effective_env_value() {
  key="$1"
  eval "shell_value=\${$key:-}"
  if [ -n "$shell_value" ]; then
    printf '%s' "$shell_value"
    return 0
  fi
  env_file_value "$key"
}

effective_env_value_or_default() {
  key="$1"
  default_value="$2"
  value=$(effective_env_value "$key")
  if [ -n "$value" ]; then
    printf '%s' "$value"
    return 0
  fi
  printf '%s' "$default_value"
}

json_field() {
  path="$1"
  field="$2"
  python - "$path" "$field" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
field = sys.argv[2]
try:
    parsed = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
value = parsed.get(field) if isinstance(parsed, dict) else ""
print(value if isinstance(value, str) else "")
PY
}

owner_access_token() {
  tenant_id="$1"
  python - "$API_BASE_URL" "$tenant_id" "$STRICT_E2E_OWNER_EMAIL" "$STRICT_E2E_OWNER_PASSWORD" "$HTTP_TIMEOUT_SECONDS" <<'PY'
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

api_base_url = sys.argv[1].rstrip("/")
tenant_id = sys.argv[2]
email = sys.argv[3]
password = sys.argv[4]
timeout_seconds = int(float(sys.argv[5]))
payload = json.dumps(
    {"tenant_id": tenant_id, "email": email, "password": password},
    separators=(",", ":"),
).encode("utf-8")
request = Request(
    f"{api_base_url}/api/auth/login",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with build_opener(ProxyHandler({})).open(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
except HTTPError as error:
    print(f"strict Compose E2E owner login failed with HTTP {error.code}", file=sys.stderr)
    raise SystemExit(2)
except URLError as error:
    print(f"strict Compose E2E owner login failed: {error}", file=sys.stderr)
    raise SystemExit(2)
try:
    token = json.loads(body).get("access_token") or ""
except Exception:
    token = ""
if not isinstance(token, str) or not token:
    print("strict Compose E2E owner login did not return an access token", file=sys.stderr)
    raise SystemExit(2)
print(token)
PY
}

ensure_parent_dir() {
  path="$1"
  parent=$(dirname -- "$path")
  if [ -n "$parent" ] && [ "$parent" != "." ]; then
    mkdir -p "$parent"
  fi
}

optional_evidence_arg() {
  flag="$1"
  path="$2"
  if [ -z "$path" ]; then
    return 0
  fi
  if [ ! -f "$path" ]; then
    printf '%s\n' "strict Compose E2E optional evidence $flag does not exist: $path" >&2
    exit 2
  fi
}

create_support_bundle() {
  path="$1"
  run_id="$2"
  tenant_id="$3"
  python - "$path" "$run_id" "$tenant_id" <<'PY'
import json
import sys
import zipfile
from pathlib import Path

path = Path(sys.argv[1])
run_id = sys.argv[2]
tenant_id = sys.argv[3]
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "tenant_id": tenant_id,
    "run_id": run_id,
    "password": "support-bundle-redaction-value",
    "access_token": "support-bundle-redaction-token",
}
with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
    bundle.writestr("run/context.json", json.dumps(payload, sort_keys=True))
    bundle.writestr(
        "logs/api.log",
        "\n".join(
            [
                f"run_id={run_id}",
                "authorization: Bearer support-bundle-redaction-token",
                "database_url=postgresql://user:pass@db.internal/taroai",
            ]
        ),
    )
PY
}

generate_release_signing_private_key() {
  python - <<'PY'
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_key = Ed25519PrivateKey.generate()
private_bytes = private_key.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption(),
)
print(base64.b64encode(private_bytes).decode("ascii"))
PY
}

MODEL_GATEWAY_API_KEY_EFFECTIVE=$(effective_env_value TAROAI_MODEL_GATEWAY_API_KEY)
MODEL_GATEWAY_MODEL_EFFECTIVE=$(effective_env_value TAROAI_MODEL_GATEWAY_MODEL)
MODEL_GATEWAY_PROVIDERS_EFFECTIVE=$(effective_env_value TAROAI_MODEL_GATEWAY_PROVIDERS)
MODEL_GATEWAY_BASE_URL_EFFECTIVE=$(effective_env_value_or_default TAROAI_MODEL_GATEWAY_BASE_URL "https://api.openai.com/v1")
MODEL_GATEWAY_CHAT_REQUEST_OPTIONS_EFFECTIVE=$(effective_env_value_or_default TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS "{}")
MODEL_GATEWAY_VERIFICATION_PROFILE_EFFECTIVE=$(effective_env_value TAROAI_MODEL_GATEWAY_VERIFICATION_PROFILE)
MODEL_GATEWAY_VERIFICATION_SECRET_VALUES_EFFECTIVE=$(effective_env_value TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUES)
MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON_EFFECTIVE=$(effective_env_value TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON)
OBJECT_STORAGE_ENDPOINT_EFFECTIVE="${TAROAI_COMPOSE_STRICT_E2E_OBJECT_STORAGE_ENDPOINT:-http://localhost:$MINIO_API_PORT}"
OBJECT_STORAGE_BUCKET_EFFECTIVE=$(effective_env_value_or_default TAROAI_OBJECT_STORAGE_BUCKET "taroai-artifacts")
OBJECT_STORAGE_REGION_EFFECTIVE=$(effective_env_value_or_default TAROAI_OBJECT_STORAGE_REGION "us-east-1")
OBJECT_STORAGE_ACCESS_KEY_ID_EFFECTIVE=$(effective_env_value_or_default TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID "taroai_minio")
OBJECT_STORAGE_SECRET_ACCESS_KEY_EFFECTIVE=$(effective_env_value_or_default TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY "taroai_minio_password")
REDIS_URL_EFFECTIVE="${TAROAI_COMPOSE_STRICT_E2E_REDIS_URL:-redis://localhost:$REDIS_PORT/0}"
DATABASE_URL_EFFECTIVE="${TAROAI_COMPOSE_STRICT_E2E_DATABASE_URL:-postgresql://taroai_app:taroai_app@localhost:$POSTGRES_PORT/taroai}"
MODEL_GATEWAY_PROVIDERS_NORMALIZED=$(printf '%s' "$MODEL_GATEWAY_PROVIDERS_EFFECTIVE" | tr -d '[:space:]')
if [ -z "$MODEL_GATEWAY_PROVIDERS_NORMALIZED" ]; then
  MODEL_GATEWAY_PROVIDERS_NORMALIZED="[]"
fi

if [ -z "$MODEL_GATEWAY_API_KEY_EFFECTIVE" ] \
  && [ "$MODEL_GATEWAY_PROVIDERS_NORMALIZED" = "[]" ]; then
  printf '%s\n' "strict Compose E2E host model verification requires TAROAI_MODEL_GATEWAY_API_KEY or TAROAI_MODEL_GATEWAY_PROVIDERS; TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID alone cannot provide host verifier credentials" >&2
  exit 2
fi
if [ -z "$MODEL_GATEWAY_MODEL_EFFECTIVE" ] \
  && [ "$MODEL_GATEWAY_PROVIDERS_NORMALIZED" = "[]" ]; then
  printf '%s\n' "strict Compose E2E requires TAROAI_MODEL_GATEWAY_MODEL unless TAROAI_MODEL_GATEWAY_PROVIDERS is configured" >&2
  exit 2
fi

TAROAI_MODEL_GATEWAY_BASE_URL="$MODEL_GATEWAY_BASE_URL_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_API_KEY="$MODEL_GATEWAY_API_KEY_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_MODEL="$MODEL_GATEWAY_MODEL_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_PROVIDERS="$MODEL_GATEWAY_PROVIDERS_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS="$MODEL_GATEWAY_CHAT_REQUEST_OPTIONS_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_VERIFICATION_PROFILE="$MODEL_GATEWAY_VERIFICATION_PROFILE_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUES="$MODEL_GATEWAY_VERIFICATION_SECRET_VALUES_EFFECTIVE" \
TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON="$MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON_EFFECTIVE" \
PYTHONPATH="$REPO_ROOT/apps/api/src${PYTHONPATH:+:$PYTHONPATH}" \
python - <<'PY'
import sys

from taroai.model_gateway.verification import parse_args

try:
    parse_args([])
except Exception as error:
    print(
        f"strict Compose E2E host model verification config is invalid: {error}",
        file=sys.stderr,
    )
    raise SystemExit(2)
PY

compose() {
  if [ -n "${TAROAI_COMPOSE_ENV_FILE:-}" ]; then
    docker compose --env-file "$TAROAI_COMPOSE_ENV_FILE" -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
  else
    docker compose -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
  fi
}

cleanup() {
  if [ "${TAROAI_COMPOSE_STRICT_E2E_KEEP_STACK:-0}" = "1" ]; then
    return
  fi
  compose down --remove-orphans --volumes >/dev/null 2>&1 || true
}

trap cleanup EXIT

compose up -d --build --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS"

"$SCRIPT_DIR/verify-local-cloud-poc.sh" \
  --api-base-url "$API_BASE_URL" \
  --browser-base-url "$BROWSER_BASE_URL" \
  --web-base-url "$WEB_BASE_URL" \
  --bootstrap-token "$TAROAI_TENANT_BOOTSTRAP_TOKEN" \
  --browser-controller-api-key "$TAROAI_BROWSER_CONTROLLER_API_KEY" \
  --owner-email "$STRICT_E2E_OWNER_EMAIL" \
  --owner-display-name "$STRICT_E2E_OWNER_DISPLAY_NAME" \
  --owner-password "$STRICT_E2E_OWNER_PASSWORD" \
  --browser-workspace-url "$BROWSER_WORKSPACE_URL" \
  --browser-workspace-api-base-url "$BROWSER_WORKSPACE_API_BASE_URL" \
  --browser-workspace-submit-message "$SUBMIT_MESSAGE" \
  --browser-workspace-submit-expected-text "$SUBMIT_EXPECTED_TEXT" \
  --browser-workspace-submit-poll-attempts "$BROWSER_WORKSPACE_SUBMIT_POLL_ATTEMPTS" \
  --run-status-poll-attempts "$RUN_STATUS_POLL_ATTEMPTS" \
  --run-status-poll-interval-seconds "$RUN_STATUS_POLL_INTERVAL_SECONDS" \
  --timeout-seconds "$HTTP_TIMEOUT_SECONDS" \
  --output "$STRICT_E2E_OUTPUT" \
  --require-model-execution

"$SCRIPT_DIR/verify-local-cloud-demo-ready.sh" \
  "$STRICT_E2E_OUTPUT" \
  --require-workspace-execution \
  --require-skill-reuse \
  --require-browser-controller-governance \
  $SANDBOX_GOVERNANCE_ARGS \
  --output "$STRICT_E2E_DEMO_GATE_OUTPUT"

if [ -n "$STRICT_E2E_INSTALL_VALIDATION_OUTPUT" ]; then
  STRICT_E2E_RUN_ID=$(json_field "$STRICT_E2E_OUTPUT" run_id)
  STRICT_E2E_TENANT_ID=$(json_field "$STRICT_E2E_OUTPUT" tenant_id)
  STRICT_E2E_USER_ID=$(json_field "$STRICT_E2E_OUTPUT" owner_user_id)
  if [ -z "$STRICT_E2E_RUN_ID" ] || [ -z "$STRICT_E2E_TENANT_ID" ] || [ -z "$STRICT_E2E_USER_ID" ]; then
    printf '%s\n' "strict Compose E2E install validation requires run_id, tenant_id, and owner_user_id in $STRICT_E2E_OUTPUT" >&2
    exit 2
  fi
  STRICT_E2E_ACCESS_TOKEN=$(owner_access_token "$STRICT_E2E_TENANT_ID")
  export STRICT_E2E_ACCESS_TOKEN
  ensure_parent_dir "$STRICT_E2E_EVENT_STREAM_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_AUDIT_WRITE_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_SANDBOX_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_BROWSER_CONTROLLER_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_MODEL_GATEWAY_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_OBJECT_STORAGE_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_REDIS_QUEUE_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_MIGRATION_PLAN_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_SUPPORT_BUNDLE_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_SUPPORT_BUNDLE_REDACTED_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_RELEASE_PACKAGE_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_RELEASE_SIGNING_OUTPUT"
  ensure_parent_dir "$STRICT_E2E_RELEASE_TRANSFER_OUTPUT"
  "$SCRIPT_DIR/build-release-package.sh" \
    --output "$STRICT_E2E_RELEASE_PACKAGE_OUTPUT"
  STRICT_E2E_RELEASE_SIGNING_PRIVATE_KEY=$(generate_release_signing_private_key)
  export STRICT_E2E_RELEASE_SIGNING_PRIVATE_KEY
  "$SCRIPT_DIR/sign-release-package.sh" \
    --output "$STRICT_E2E_RELEASE_PACKAGE_OUTPUT" \
    --signature-output "$STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT" \
    --key-id "$STRICT_E2E_RELEASE_SIGNING_KEY_ID" \
    --private-key-env STRICT_E2E_RELEASE_SIGNING_PRIVATE_KEY \
    > "$STRICT_E2E_RELEASE_SIGNING_OUTPUT"
  STRICT_E2E_RELEASE_PUBLIC_KEY=$(json_field "$STRICT_E2E_RELEASE_SIGNING_OUTPUT" public_key_base64)
  if [ -z "$STRICT_E2E_RELEASE_PUBLIC_KEY" ]; then
    printf '%s\n' "strict Compose E2E release signing did not return a public key" >&2
    exit 2
  fi
  "$SCRIPT_DIR/build-release-transfer-evidence.sh" \
    --package "$STRICT_E2E_RELEASE_PACKAGE_OUTPUT" \
    --signature "$STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT" \
    --trusted-public-key "$STRICT_E2E_RELEASE_SIGNING_KEY_ID=$STRICT_E2E_RELEASE_PUBLIC_KEY" \
    --output "$STRICT_E2E_RELEASE_TRANSFER_OUTPUT"
  "$SCRIPT_DIR/build-migration-plan.sh" \
    --database-url "$DATABASE_URL_EFFECTIVE" \
    --migrations-path "$REPO_ROOT/apps/api/migrations" \
    > "$STRICT_E2E_MIGRATION_PLAN_OUTPUT"
  export TAROAI_MODEL_GATEWAY_BASE_URL="$MODEL_GATEWAY_BASE_URL_EFFECTIVE"
  export TAROAI_MODEL_GATEWAY_API_KEY="$MODEL_GATEWAY_API_KEY_EFFECTIVE"
  export TAROAI_MODEL_GATEWAY_MODEL="$MODEL_GATEWAY_MODEL_EFFECTIVE"
  export TAROAI_MODEL_GATEWAY_PROVIDERS="$MODEL_GATEWAY_PROVIDERS_EFFECTIVE"
  export TAROAI_MODEL_GATEWAY_CHAT_REQUEST_OPTIONS="$MODEL_GATEWAY_CHAT_REQUEST_OPTIONS_EFFECTIVE"
  "$SCRIPT_DIR/verify-model-gateway.sh" \
    --profile "$MODEL_GATEWAY_VERIFICATION_PROFILE_EFFECTIVE" \
    --tenant-id "$STRICT_E2E_TENANT_ID" \
    --user-id "$STRICT_E2E_USER_ID" \
    --run-id "$STRICT_E2E_RUN_ID" \
    --timeout-seconds "$HTTP_TIMEOUT_SECONDS" \
    > "$STRICT_E2E_MODEL_GATEWAY_OUTPUT"
  export TAROAI_OBJECT_STORAGE_ENDPOINT="$OBJECT_STORAGE_ENDPOINT_EFFECTIVE"
  export TAROAI_OBJECT_STORAGE_BUCKET="$OBJECT_STORAGE_BUCKET_EFFECTIVE"
  export TAROAI_OBJECT_STORAGE_REGION="$OBJECT_STORAGE_REGION_EFFECTIVE"
  export TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID="$OBJECT_STORAGE_ACCESS_KEY_ID_EFFECTIVE"
  export TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY="$OBJECT_STORAGE_SECRET_ACCESS_KEY_EFFECTIVE"
  "$SCRIPT_DIR/verify-object-storage.sh" > "$STRICT_E2E_OBJECT_STORAGE_OUTPUT"
  export TAROAI_REDIS_URL="$REDIS_URL_EFFECTIVE"
  "$SCRIPT_DIR/verify-redis-queue.sh" > "$STRICT_E2E_REDIS_QUEUE_OUTPUT"
  create_support_bundle \
    "$STRICT_E2E_SUPPORT_BUNDLE_OUTPUT" \
    "$STRICT_E2E_RUN_ID" \
    "$STRICT_E2E_TENANT_ID"
  "$SCRIPT_DIR/redact-support-bundle.sh" \
    --input "$STRICT_E2E_SUPPORT_BUNDLE_OUTPUT" \
    --output "$STRICT_E2E_SUPPORT_BUNDLE_REDACTED_OUTPUT" \
    --evidence-output "$STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT" \
    >/dev/null
  "$SCRIPT_DIR/verify-event-stream.sh" \
    --api-base-url "$API_BASE_URL" \
    --tenant-id "$STRICT_E2E_TENANT_ID" \
    --user-id "$STRICT_E2E_USER_ID" \
    --access-token-env-var STRICT_E2E_ACCESS_TOKEN \
    --denied-tenant-id "$STRICT_E2E_DENIED_TENANT_ID" \
    --denied-user-id "$STRICT_E2E_DENIED_USER_ID" \
    --run-id "$STRICT_E2E_RUN_ID" \
    --timeout-seconds "$HTTP_TIMEOUT_SECONDS" \
    > "$STRICT_E2E_EVENT_STREAM_OUTPUT"
  "$SCRIPT_DIR/verify-audit-write.sh" \
    --api-base-url "$API_BASE_URL" \
    --tenant-id "$STRICT_E2E_TENANT_ID" \
    --user-id "$STRICT_E2E_USER_ID" \
    --access-token-env-var STRICT_E2E_ACCESS_TOKEN \
    --denied-tenant-id "$STRICT_E2E_DENIED_TENANT_ID" \
    --denied-user-id "$STRICT_E2E_DENIED_USER_ID" \
    --run-id "$STRICT_E2E_RUN_ID" \
    --timeout-seconds "$HTTP_TIMEOUT_SECONDS" \
    > "$STRICT_E2E_AUDIT_WRITE_OUTPUT"
  "$SCRIPT_DIR/verify-sandbox-lifecycle.sh" \
    --base-url "$SANDBOX_BASE_URL" \
    --api-key "$TAROAI_SANDBOX_CONTROLLER_API_KEY" \
    --timeout-seconds "$HTTP_TIMEOUT_SECONDS" \
    > "$STRICT_E2E_SANDBOX_OUTPUT"
  "$SCRIPT_DIR/verify-browser-controller.sh" \
    --base-url "$BROWSER_BASE_URL" \
    --api-key "$TAROAI_BROWSER_CONTROLLER_API_KEY" \
    --timeout-seconds "$HTTP_TIMEOUT_SECONDS" \
    > "$STRICT_E2E_BROWSER_CONTROLLER_OUTPUT"
  optional_evidence_arg \
    --secret-manager-verification \
    "$STRICT_E2E_SECRET_MANAGER_VERIFICATION"
  optional_evidence_arg \
    --trace-collector-verification \
    "$STRICT_E2E_TRACE_COLLECTOR_VERIFICATION"
  optional_evidence_arg \
    --restore-drill-verification \
    "$STRICT_E2E_RESTORE_DRILL_VERIFICATION"
  export TAROAI_RUNTIME_CLOSED_LOOP_EVIDENCE_PATH="$STRICT_E2E_DEMO_GATE_OUTPUT"
  set -- \
    --mode "$STRICT_E2E_INSTALL_VALIDATION_MODE" \
    --api-base-url "$API_BASE_URL" \
    --sandbox-controller-api-key "$TAROAI_SANDBOX_CONTROLLER_API_KEY" \
    --browser-base-url "$BROWSER_BASE_URL" \
    --browser-controller-api-key "$TAROAI_BROWSER_CONTROLLER_API_KEY" \
    --web-base-url "$WEB_BASE_URL" \
    --release-transfer-evidence "$STRICT_E2E_RELEASE_TRANSFER_OUTPUT" \
    --migration-plan "$STRICT_E2E_MIGRATION_PLAN_OUTPUT" \
    --model-gateway-verification "$STRICT_E2E_MODEL_GATEWAY_OUTPUT" \
    --object-storage-verification "$STRICT_E2E_OBJECT_STORAGE_OUTPUT" \
    --redis-queue-verification "$STRICT_E2E_REDIS_QUEUE_OUTPUT" \
    --sandbox-verification "$STRICT_E2E_SANDBOX_OUTPUT" \
    --browser-controller-verification "$STRICT_E2E_BROWSER_CONTROLLER_OUTPUT" \
    --support-bundle-redaction-evidence "$STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT" \
    --runtime-closed-loop-evidence "$STRICT_E2E_DEMO_GATE_OUTPUT" \
    --event-stream-verification "$STRICT_E2E_EVENT_STREAM_OUTPUT" \
    --audit-write-verification "$STRICT_E2E_AUDIT_WRITE_OUTPUT" \
    --output "$STRICT_E2E_INSTALL_VALIDATION_OUTPUT"
  if [ -n "$STRICT_E2E_SECRET_MANAGER_VERIFICATION" ]; then
    set -- "$@" --secret-manager-verification "$STRICT_E2E_SECRET_MANAGER_VERIFICATION"
  fi
  if [ -n "$STRICT_E2E_TRACE_COLLECTOR_VERIFICATION" ]; then
    set -- "$@" --trace-collector-verification "$STRICT_E2E_TRACE_COLLECTOR_VERIFICATION"
  fi
  if [ -n "$STRICT_E2E_RESTORE_DRILL_VERIFICATION" ]; then
    set -- "$@" --restore-drill-verification "$STRICT_E2E_RESTORE_DRILL_VERIFICATION"
  fi
  "$SCRIPT_DIR/validate-install.sh" "$@"
fi

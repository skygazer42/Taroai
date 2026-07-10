# Deployment Package Manifest

The deployment package manifest is the operator-facing contract for cloud, BYOC, VPC, private, and air-gapped installs. It describes what will be installed before Kubernetes or customer-operated environments receive images, migrations, and configuration.

## Required Services

Every package must declare these services:

- api
- worker
- database
- redis
- object storage
- sandbox provider
- browser controller
- web workspace
- model gateway
- secrets manager

The manifest is intentionally separate from runtime flow. It does not provide model responses, agent state, or local development shortcuts. It only describes package contents and install-time requirements.

## Contents

- `package_version`: version of the package contract.
- `app_version`: application version delivered by the package.
- `targets`: supported deployment targets.
- `images`: deployable container images. API, worker, sandbox controller, browser controller, and Web Workspace images are required. The default Web Workspace image repository is `ghcr.io/creao-ai/taroai-web`.
- `migrations`: database migrations with checksums and app version ranges.
- `config_keys`: environment keys and whether each key comes from config or secret storage.
- `dependency_versions`: external runtime dependency versions.
- `required_services`: required platform dependencies.
- `compatibility_matrix`: supported dependency version ranges.

Secrets are referenced by key name only. The package must not contain literal secret values.

## Generate Manifest

Build the release manifest from the current repository state:

```bash
scripts/build-package-manifest.sh --output infra/package/manifest.generated.json
```

The generator reads `apps/api/migrations/*.sql`, computes SHA256 checksums,
adds API, worker, sandbox-controller, browser-controller, and Web Workspace images, classifies
config keys from `.env.example` and secret keys from
`infra/k8s/secrets.example.yaml`, and validates the result with the Pydantic
`DeploymentPackageManifest` model before writing JSON. Manifest and schema
outputs are written through temporary files and atomically replace existing
files only after a successful write.
The migration directory, migration SQL files, `.env.example`, and
`infra/k8s/secrets.example.yaml` must be real paths; the generator rejects
symlink sources, including broken symlinks, instead of following or silently
skipping them.

Regenerate the JSON schema from the same Pydantic model:

```bash
scripts/build-package-schema.sh --output infra/package/manifest.schema.json
```

The committed schema is checked against `DeploymentPackageManifest` in tests so
offline package validators do not drift from the backend package contract.

## Build Release Zip

Build a clean release archive from the current repository state:

```bash
scripts/build-release-package.sh --output dist/taroai-release.zip
```

The release builder includes `apps`, `docs`, `infra`, `scripts`, `.env.example`,
`README.md`, and `pyproject.toml`, then writes the generated package manifest to
`infra/package/manifest.json` and the generated Pydantic schema to
`infra/package/manifest.schema.json` inside the archive. It excludes local
development state such as `.git`, `.direnv`, `.idea`, `.pytest_cache`, `.tox`,
`.venv`, `venv`, `__pycache__`, `.env`, `.envrc`, `.env.local`, non-example `*.env` files, `a.md`, `a.out`, `dist`,
compiled Python bytecode, symlink paths, and test sources. The builder does not
follow symlinks or package symlink targets from outside the repository. If local
`infra/package/manifest.json` or `infra/package/manifest.schema.json` files
exist from a previous run, the builder skips those files and keeps only freshly
generated entries in the release archive. The build result includes the release
zip SHA256; record it in the transfer evidence packet for private and
air-gapped installs.
Release package build, signing, verification, and report payloads use strict
Pydantic models; unknown option or evidence fields are rejected instead of being
silently ignored.
Explicit release include roots must be real paths under the repository root; the
builder rejects absolute or parent-relative include roots that escape the
repository, and rejects symlink include roots, including broken symlinks,
instead of silently omitting them.
Generated manifest and schema archive paths must also be relative, safe entries;
absolute paths, parent traversal, and forbidden local-development names are
rejected before the zip is written.
If the output archive path exists inside an included directory, the builder
excludes that current output path so old release zips are not nested into the
new package.
The archive is written to a temporary file in the output directory and then
renamed into place only after the zip is complete, so a failed build preserves
any existing release package instead of leaving a partial archive.
Before writing the archive, the builder scans included source files for
secret-shaped values such as provider API keys, private key blocks, and
credentialed URLs, then fails the build with only affected path names if any are
found.
The output archive path itself must not be a symlink, including a broken
symlink, so package creation does not follow links to external files.

Sign the archive before transfer with an Ed25519 release key. The private key
is read from an environment variable and must not be written into logs,
support bundles, or transfer evidence:

```bash
TAROAI_RELEASE_SIGNING_PRIVATE_KEY=<base64-raw-ed25519-private-key> \
scripts/sign-release-package.sh \
  --output dist/taroai-release.zip \
  --signature-output dist/taroai-release.zip.sig.json \
  --key-id creao-release-2026-01 \
  --private-key-env TAROAI_RELEASE_SIGNING_PRIVATE_KEY
```

The signing result prints the archive SHA256 and base64 public key. Record the
SHA256, `key_id`, public key, and detached signature path in the transfer
evidence packet. Do not record the private key.
The detached signature envelope is also written through a temporary file and
renamed into place after the write succeeds, preserving any existing signature
file if signing output fails.

Build the transfer evidence packet after signing. The builder verifies the
archive checksum and detached signature before writing the evidence JSON:

```bash
scripts/build-release-transfer-evidence.sh \
  --package dist/taroai-release.zip \
  --signature dist/taroai-release.zip.sig.json \
  --trusted-public-key creao-release-2026-01=<base64-public-key> \
  --output dist/release-transfer-evidence.json
```

The evidence packet records the archive SHA256, package/app versions, signature
key id, public key, image count, migration count, and required service count. It
does not include the detached signature value or any private key material.
The transfer evidence JSON is written through the same temporary-file replace
path, preserving any existing evidence packet if output fails.
When the package, signature, and evidence output are in the same directory, the
builder records portable file names instead of build-machine absolute paths.
Private install validation can consume this packet through
`--release-transfer-evidence` and will use its checksum, signature path, key id,
and public key before calling the release verifier.
When `--release-package` is omitted and the validator derives the package path
from transfer evidence, that package path must resolve beside the evidence JSON.
Pass an explicit trusted `--release-package` if the package was moved after the
evidence packet was generated.
Relative package and signature paths in the transfer evidence are resolved from
the evidence JSON directory first, so a transferred `dist/` bundle can be
validated from any shell working directory.
When the transfer evidence supplies the signature path, that signature file must
live beside the release package. The install validator rejects evidence whose
signature path points outside the package directory unless the operator provides
an explicit `--release-package-signature` path.

Verify the archive before transfer:

```bash
scripts/verify-release-package.sh --output dist/taroai-release.zip
```

For customer-operated installs such as BYOC, VPC, private, production, and
air-gapped deployments, checksum-only verification is not enough for install
acceptance. `scripts/validate-install.sh` requires either
`--release-package-signature` plus `--release-package-trusted-public-key`, or a
valid `--release-transfer-evidence` packet that supplies the package checksum,
signature path, key id, and public key before `release_package_integrity` can
pass.

When validating a received package against transfer evidence, pass the expected
package checksum:

```bash
scripts/verify-release-package.sh \
  --output dist/taroai-release.zip \
  --expected-sha256 <release-zip-sha256>
```

When transfer evidence includes a detached release signature, pass the signature
envelope and trusted release package public key:

```bash
scripts/verify-release-package.sh \
  --output dist/taroai-release.zip \
  --expected-sha256 <release-zip-sha256> \
  --signature dist/taroai-release.zip.sig.json \
  --trusted-public-key creao-release-2026-01=<base64-public-key>
```

The verifier rejects forbidden local files, validates the embedded manifest with
the Pydantic package model, checks required package entries, and compares every
manifest migration checksum against the SQL files inside the zip. It also
requires the packaged upgrade matrix to mention the manifest's current migration
range, so stale private upgrade guidance cannot pass package verification. It
also scans archive content for secret-shaped tokens such as model provider API
keys, private key blocks, and credentialed HTTP URLs, then reports only the
affected archive entry names, never the secret values. The
verifier also rejects duplicate archive entries and unsafe absolute or parent
directory paths plus symlink entries before package transfer, confirms packaged `scripts/*.sh`
entries retain executable permissions, requires the verifier script itself to be
present, requires the local cloud PoC verifier script to be present, requires
the schema generator script to be present, and checks that the packaged manifest
schema still matches the Pydantic package model. It also
checks that API, worker, sandbox-controller, browser-controller, and Web Workspace image
repositories match the official release baseline. It also requires the core
API, sandbox-controller, and browser-controller image build inputs, including requirements files,
entrypoint, baseline migrations, script-backed verifier modules, the HTTP/Docker/Kubernetes
sandbox provider modules, and the browser-controller and sandbox-controller
service modules. It also requires release and install validation dependency
modules such as configuration, deployment manifest model, database model,
evidence model, validation result, sandbox image policy, and worker model
modules, so customer-side package, transfer, and install validation scripts
remain self-contained inside the package. Runtime verifier dependencies for
model gateway, observability, sandbox, storage, secrets, restore drills, and
worker queue checks are required for the same reason. The verifier also scans
packaged Python sources for first-party `taroai.*` imports and fails the package
when an imported module or package entry is missing, including
`from taroai import module` submodule imports. It also scans packaged
`scripts/*.sh` wrappers for `python -m taroai...` targets and fails when the
target module is absent, so customer-side verifier scripts cannot ship with
dead CLI entry points. Packaged Python sources must also parse successfully, so
truncated or corrupted modules fail before customer transfer. It also requires the core
Kubernetes manifests and Helm chart files for API, worker, sandbox-controller, browser-controller,
Web Workspace, backing services, config, network policy, migration, ingress,
autoscaling, service accounts, and sandbox runtime policy. The sandbox runtime
policy entries must include the raw Kubernetes namespace guard manifest and the
Helm template for `sandboxRuntimePolicy.enabled`, so private installs preserve
Pod Security Admission labels, sandbox `ResourceQuota`, and container
`LimitRange` defaults, plus the default-deny sandbox runtime `NetworkPolicy`.
Web Workspace package entries must include the Dockerfile plus `index.html`,
`assets/main.js`, and `assets/styles.css`.
The required verifier scripts include the Kubernetes sandbox provider verifier
so customer-operated clusters can capture real Pod, NetworkPolicy, command,
artifact, snapshot, and destroy evidence before enabling
`TAROAI_SANDBOX_CONTROLLER_PROVIDER=kubernetes`.
The package metadata, upgrade matrix, cloud/BYOC/private/provider env profiles,
and local/private/air-gapped/DR/offboarding/trigger runbooks are also required
entries so customer-operated installs retain the operator evidence needed for
validation and rollback. Top-level `README.md`, `pyproject.toml`, and
`.env.example` are required so source-level package identity, test/build
configuration, and local configuration templates survive archive transfer.
The verification report
includes the package SHA256 and any expected SHA256 supplied by the operator so
the receiving operator can compare it with the transfer evidence, and
`--expected-sha256` makes a mismatch fail the verification gate. When
`--signature` is supplied, the verifier checks the detached Ed25519 envelope
against the archive SHA256 and the configured trusted release package public
key; an untrusted key id, mismatched archive checksum, malformed key, or invalid
signature fails the verification gate.

## Support Bundle Redaction

Support bundles must be redacted inside the customer boundary before they are
shared for review. Generate a sanitized archive and evidence report with:

```bash
scripts/redact-support-bundle.sh \
  --input support-bundle.zip \
  --output support-bundle-redacted.zip \
  --evidence-output support-bundle-redaction.json
```

The redactor handles structured JSON log lines and plain-text assignments such
as `prompt=...`, `connector_payload=...`, and `access_token=...`. It also
redacts sensitive header-style fields such as `Authorization`, `X-API-Key`,
`Cookie`, and `Set-Cookie`. The redaction report records archive entry names,
finding categories, and counts only. It does not include original API keys,
bearer tokens, signed URLs, connection strings, connector payloads, cookies,
model prompts, or other redacted values. The sanitized archive and evidence
report are written through temporary files and atomically replace existing
outputs only after a successful write, so interrupted redaction does not corrupt
previously approved handoff files.
Private install validation can consume the report with
`--support-bundle-redaction-evidence` so support handoff redaction becomes part
of the operator evidence gate.

## Signed License Files

Offline and air-gapped installs use signed license envelopes. The package should declare or deliver the trusted license public key through customer-approved secret/config channels, and operators validate the envelope before entitlement checks. Logs and support bundles must not include signatures, public keys, or license private material.

Private and BYOC profiles should import the signed license envelope through `POST /api/licenses/import` using an operator account with `licenses.manage`, then enable runtime license checks with `TAROAI_LICENSE_RUNTIME_ENFORCEMENT_ENABLED=true` after the response is `active` and `activated=true`. The import endpoint persists the active validation in the control-plane store and emits sanitized license audit events without signature material. Runtime integrations now block connector creation when the active tenant license exceeds `private_connector_count`, block sandbox session creation when active sessions exceed `sandbox_concurrency`, reject API/worker audit writes when configured retention exceeds `audit_retention_days`, require `solution_packs` before solution pack installation, require `sso` before SSO provider configure/enable, and require `scim` before SCIM provider configure/enable/import.

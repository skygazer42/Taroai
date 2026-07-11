# Taroai Release Bundle

This directory is a production delivery bundle, not a repository snapshot. It contains only immutable image references or air-gap image archives, deployable Compose/Helm assets, configuration templates, migrations, license notices, integrity metadata, and operator scripts.

## Mandatory acceptance flow

1. Run `scripts/verify-release.ps1` against the received directory before using any image or migration.
2. Verify the detached signature for `SHA256SUMS` with the customer-approved public key when a signing hook was used.
3. Copy `config/runtime.env.template` outside the bundle and populate it through the target secret-management process. Never edit or add secrets inside the bundle.
4. Record the database and object-storage backup identifiers required by `metadata/rollback.json`.
5. Use `scripts/install-release.ps1` in `Validate`, `Compose`, or `Helm` mode.

## Bundle layout

| Path | Purpose |
| --- | --- |
| `release-manifest.json` | Top-level version, commit, images, migrations, integrity, and rollback contract |
| `SHA256SUMS` | SHA256 inventory for all release payload files |
| `compose/` | Production Compose topology and Web/API reverse proxy |
| `helm/` | Packaged Helm chart and immutable generated image values |
| `config/` | Non-secret image references and operator configuration templates |
| `migrations/` | Ordered SQL migrations with checksums in the manifest |
| `metadata/` | Build plan, provenance, SBOM state, build facts, and rollback boundary |
| `images/` | Docker image archives, present only for air-gap builds |
| `licenses/` | Product and third-party notice handoff location |
| `scripts/` | Offline verification and installation entrypoints |

## Compose install

```powershell
Copy-Item .\config\runtime.env.template C:\secure\taroai\runtime.env
# Populate C:\secure\taroai\runtime.env using the approved secret workflow.
.\scripts\install-release.ps1 `
  -BundlePath $PWD `
  -Mode Compose `
  -EnvironmentFile C:\secure\taroai\runtime.env
```

The installer loads `config/images.env` before the operator environment file, so a controlled override remains possible without editing the signed bundle.

## Helm install

Create an external Kubernetes Secret named by the chart values, prepare customer-specific values outside the bundle, and run:

```powershell
.\scripts\install-release.ps1 `
  -BundlePath $PWD `
  -Mode Helm `
  -Namespace taroai `
  -AdditionalHelmValues C:\secure\taroai\customer-values.yaml
```

## Air-gap install

An air-gap build contains `images/*.tar`. Load them before Compose or before mirroring them into the private registry:

```powershell
.\scripts\install-release.ps1 -BundlePath $PWD -Mode Validate -LoadAirGapImages
```

## Rollback

`metadata/rollback.json` records the previous release identity when the builder receives `-PreviousReleaseManifest`. A code-only rollback redeploys those previous immutable image references. If a migration has been applied and is not backward compatible, restore the pre-release database backup; the bundle never assumes destructive SQL down-migrations are safe.

## Security boundaries

- Populated `.env` files, private keys, customer credentials, development secrets, source tests, caches, virtual environments, and Git metadata are forbidden.
- The release builder fails when it detects forbidden paths or secret-shaped content.
- The sandbox controller's Docker socket grants host-level power. Use the Kubernetes provider with the packaged namespace/runtime policy for production isolation.
- Registry push is never implicit. It must have been explicitly selected during the build.

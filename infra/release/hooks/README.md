# Supply-chain hooks

The release builder supports optional external signing without owning or storing a private key.

Pass a trusted PowerShell hook with `-SigningHook`. The builder invokes it as:

```powershell
& <hook> -BundlePath <directory> -ManifestPath <release-manifest.json> -ChecksumPath <SHA256SUMS>
```

The hook should sign `SHA256SUMS` through the organization's KMS, HSM, cosign, or approved signing service and write either `SHA256SUMS.sig` or `SHA256SUMS.sig.json` at the bundle root. Those detached signature filenames are intentionally outside the checksum set so the signature never signs itself.

The builder never accepts a private key argument and never puts signing credentials in the release bundle.

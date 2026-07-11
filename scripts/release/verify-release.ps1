[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [switch]$RequireImageArchives,
    [switch]$PassThru
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$bundleRoot = [System.IO.Path]::GetFullPath($BundlePath)
if (-not (Test-Path -LiteralPath $bundleRoot -PathType Container)) {
    throw "Release bundle does not exist: $bundleRoot"
}

$requiredPaths = @(
    "release-manifest.json",
    "SHA256SUMS",
    "compose/docker-compose.release.yml",
    "config/images.env",
    "config/runtime.env.template",
    "config/credential-refs.env.template",
    "helm/taroai/Chart.yaml",
    "helm/values.release.yaml",
    "metadata/build.json",
    "metadata/image-build-plan.json",
    "metadata/provenance.json",
    "metadata/rollback.json",
    "metadata/sbom.json",
    "scripts/install-release.ps1",
    "scripts/verify-release.ps1"
)
foreach ($requiredPath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot $requiredPath))) {
        throw "Release bundle is missing required entry: $requiredPath"
    }
}

$manifest = Get-Content -LiteralPath (Join-Path $bundleRoot "release-manifest.json") -Raw | ConvertFrom-Json
if ($manifest.kind -ne "TaroaiReleaseBundle" -or $manifest.schemaVersion -ne "1.0") {
    throw "Unsupported release manifest kind or schema version."
}
if ($manifest.delivery.repositoryArchive -ne $false) {
    throw "Repository archives are not accepted as formal release bundles."
}
if ([string]::IsNullOrWhiteSpace($manifest.version) -or $manifest.commit -notmatch '^[0-9a-f]{40}$') {
    throw "Release manifest has an invalid version or commit."
}

$forbiddenSegments = @(
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", "cache", "node_modules", "tests", "test", "dev", "development"
)
$forbiddenNames = @(
    ".env", ".env.local", ".env.production", ".env.development", "id_rsa",
    "id_ed25519", "credentials.json", "service-account.json"
)
$forbiddenExtensions = @(".pyc", ".pyo", ".log", ".key", ".pem", ".p12", ".pfx")
$rootPrefix = $bundleRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar

foreach ($item in Get-ChildItem -LiteralPath $bundleRoot -Recurse -Force) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Release bundle contains a symlink or reparse point: $($item.FullName)"
    }
    $relative = $item.FullName.Substring($rootPrefix.Length).Replace('\', '/')
    $segments = $relative.Split('/', [System.StringSplitOptions]::RemoveEmptyEntries)
    foreach ($segment in $segments) {
        if ($forbiddenSegments -contains $segment.ToLowerInvariant()) {
            throw "Release bundle contains forbidden path: $relative"
        }
    }
    $leaf = $item.Name.ToLowerInvariant()
    if ($forbiddenNames -contains $leaf) {
        throw "Release bundle contains forbidden file: $relative"
    }
    if ($leaf.StartsWith(".env.") -and -not $leaf.EndsWith(".example")) {
        throw "Release bundle contains a forbidden environment file: $relative"
    }
    if (-not $item.PSIsContainer -and $forbiddenExtensions -contains $item.Extension.ToLowerInvariant()) {
        throw "Release bundle contains forbidden key, cache, or build output: $relative"
    }
}

$checksums = @{}
$checksumPath = Join-Path $bundleRoot "SHA256SUMS"
foreach ($line in Get-Content -LiteralPath $checksumPath) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }
    $match = [regex]::Match($line, '^([a-f0-9]{64})  (.+)$')
    if (-not $match.Success) {
        throw "Malformed SHA256SUMS line."
    }
    $expected = $match.Groups[1].Value
    $relative = $match.Groups[2].Value.Replace('\', '/')
    if ([System.IO.Path]::IsPathRooted($relative) -or $relative.Split('/') -contains "..") {
        throw "Unsafe path in SHA256SUMS: $relative"
    }
    if ($checksums.ContainsKey($relative)) {
        throw "Duplicate checksum entry: $relative"
    }
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $bundleRoot $relative))
    if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Checksum entry escapes the release bundle: $relative"
    }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Checksum entry is missing from the release bundle: $relative"
    }
    $actual = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "Checksum mismatch: $relative"
    }
    $checksums[$relative] = $expected
}

$allowedUnsigned = @("SHA256SUMS", "SHA256SUMS.sig", "SHA256SUMS.sig.json")
foreach ($file in Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Force) {
    $relative = $file.FullName.Substring($rootPrefix.Length).Replace('\', '/')
    if ($allowedUnsigned -contains $relative) {
        continue
    }
    if (-not $checksums.ContainsKey($relative)) {
        throw "Release file is not covered by SHA256SUMS: $relative"
    }
}

$textExtensions = @(
    ".conf", ".css", ".csv", ".env", ".example", ".html", ".ini", ".js", ".json",
    ".md", ".ps1", ".py", ".sh", ".sql", ".template", ".toml", ".txt", ".xml",
    ".yaml", ".yml"
)
$secretPatterns = @(
    "-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "\b(?:sk|gh[pousr])[-_][A-Za-z0-9_-]{20,}\b",
    "\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b",
    "https?://[^\s/:@]+:[^\s/@]+@",
    '(?im)(?:api[_-]?key|client[_-]?secret|password|access[_-]?token|auth[_-]?token)\s*[:=]\s*["'']?(?!\$\{|<|REPLACE|CHANGE|VAULT|SECRET_REF|required)[A-Za-z0-9+/=_-]{16,}'
)
foreach ($file in Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Force) {
    if (($textExtensions -notcontains $file.Extension.ToLowerInvariant()) -and ($file.Name -notmatch "Dockerfile|Chart.yaml")) {
        continue
    }
    if ($file.Length -gt 10MB) {
        continue
    }
    $content = [System.IO.File]::ReadAllText($file.FullName)
    foreach ($pattern in $secretPatterns) {
        if ([regex]::IsMatch($content, $pattern)) {
            $relative = $file.FullName.Substring($rootPrefix.Length).Replace('\', '/')
            throw "Sensitive content scan failed for: $relative"
        }
    }
}

$migrationByPath = @{}
foreach ($migration in $manifest.migrations) {
    $migrationByPath[$migration.path] = $migration.sha256
}
foreach ($migrationFile in Get-ChildItem -LiteralPath (Join-Path $bundleRoot "migrations") -Filter "*.sql" -File) {
    $relative = "migrations/$($migrationFile.Name)"
    if (-not $migrationByPath.ContainsKey($relative)) {
        throw "Migration is absent from release-manifest.json: $relative"
    }
    $actual = (Get-FileHash -LiteralPath $migrationFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $migrationByPath[$relative]) {
        throw "Migration checksum does not match release-manifest.json: $relative"
    }
}

foreach ($image in $manifest.images) {
    if ([string]::IsNullOrWhiteSpace($image.component) -or [string]::IsNullOrWhiteSpace($image.reference)) {
        throw "Release manifest contains an incomplete image record."
    }
    if (($RequireImageArchives -or $manifest.delivery.airGap) -and [string]::IsNullOrWhiteSpace($image.archive)) {
        throw "Air-gap release is missing an image archive for $($image.component)."
    }
    if (-not [string]::IsNullOrWhiteSpace($image.archive)) {
        if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot $image.archive) -PathType Leaf)) {
            throw "Image archive is missing for $($image.component): $($image.archive)"
        }
    }
}

$result = [ordered]@{
    status = "valid"
    bundle = $bundleRoot
    version = $manifest.version
    commit = $manifest.commit
    filesVerified = $checksums.Count
    images = @($manifest.images).Count
    migrations = @($manifest.migrations).Count
    airGap = [bool]$manifest.delivery.airGap
}
if ($PassThru) {
    return [pscustomobject]$result
}
$result | ConvertTo-Json -Depth 8

[CmdletBinding()]
param(
    [string]$Version,
    [string]$Commit,
    [string]$OutputRoot = "dist/releases",
    [string]$Registry = "ghcr.io/creao-ai",
    [ValidateSet("Plan", "Build")]
    [string]$ImageMode = "Plan",
    [switch]$Push,
    [switch]$AirGap,
    [switch]$AllowDirty,
    [switch]$RequireSbom,
    [string]$SigningHook,
    [string]$PreviousReleaseManifest,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$script:ReleaseConfigPath = Join-Path $script:RepoRoot "infra/release/release.config.json"
$script:ForbiddenSegments = @(
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", "cache", "node_modules", "tests", "test", "dev",
    "development"
)
$script:ForbiddenFileNames = @(
    ".env", ".env.local", ".env.production", ".env.development", "id_rsa",
    "id_ed25519", "credentials.json", "service-account.json"
)
$script:ForbiddenExtensions = @(".pyc", ".pyo", ".log", ".key", ".pem", ".p12", ".pfx")
$script:TextExtensions = @(
    ".conf", ".css", ".csv", ".env", ".example", ".html", ".ini", ".js", ".json",
    ".md", ".ps1", ".py", ".sh", ".sql", ".template", ".toml", ".txt", ".xml",
    ".yaml", ".yml"
)

function Invoke-CapturedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = & $Executable @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE."
    }
    return (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
}

function Invoke-StreamingCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE."
    }
}

function Resolve-RepositoryPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if ([System.IO.Path]::IsPathRooted($RelativePath)) {
        throw "Release input must be repository-relative: $RelativePath"
    }
    $resolved = [System.IO.Path]::GetFullPath((Join-Path $script:RepoRoot $RelativePath))
    $rootPrefix = $script:RepoRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release input escapes the repository root: $RelativePath"
    }
    if (-not (Test-Path -LiteralPath $resolved)) {
        throw "Required release input is missing: $RelativePath"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Release input cannot be a symlink or reparse point: $RelativePath"
    }
    return $resolved
}

function Test-ForbiddenRelativePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace('\', '/')
    $segments = $normalized.Split('/', [System.StringSplitOptions]::RemoveEmptyEntries)
    foreach ($segment in $segments) {
        if ($script:ForbiddenSegments -contains $segment.ToLowerInvariant()) {
            return $true
        }
    }

    $leaf = [System.IO.Path]::GetFileName($normalized).ToLowerInvariant()
    if ($script:ForbiddenFileNames -contains $leaf) {
        return $true
    }
    if ($leaf.StartsWith(".env.") -and -not $leaf.EndsWith(".example")) {
        return $true
    }
    if ($script:ForbiddenExtensions -contains [System.IO.Path]::GetExtension($leaf)) {
        return $true
    }
    return $false
}

function Copy-SafeTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $sourceItem = Get-Item -LiteralPath $Source -Force
    if (-not $sourceItem.PSIsContainer) {
        $relativeName = $sourceItem.Name
        if (Test-ForbiddenRelativePath -RelativePath $relativeName) {
            throw "Forbidden release file: $relativeName"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
        return
    }

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $sourcePrefixLength = $sourceItem.FullName.TrimEnd('\', '/').Length + 1
    foreach ($item in Get-ChildItem -LiteralPath $Source -Recurse -Force) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release inputs cannot contain symlinks or reparse points: $($item.FullName)"
        }
        $relative = $item.FullName.Substring($sourcePrefixLength).Replace('\', '/')
        if (Test-ForbiddenRelativePath -RelativePath $relative) {
            continue
        }
        $target = Join-Path $Destination $relative
        if ($item.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
        }
        else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
}

function Assert-ReleaseTreeIsClean {
    param([Parameter(Mandatory = $true)][string]$BundleRoot)

    $rootLength = $BundleRoot.TrimEnd('\', '/').Length + 1
    $violations = New-Object System.Collections.Generic.List[string]
    foreach ($item in Get-ChildItem -LiteralPath $BundleRoot -Recurse -Force) {
        $relative = $item.FullName.Substring($rootLength).Replace('\', '/')
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            $violations.Add("$relative (reparse point)")
        }
        elseif (Test-ForbiddenRelativePath -RelativePath $relative) {
            $violations.Add($relative)
        }
    }
    if ($violations.Count -gt 0) {
        throw "Forbidden files were found in the release bundle:`n$($violations -join [Environment]::NewLine)"
    }
}

function Assert-NoSensitiveContent {
    param([Parameter(Mandatory = $true)][string]$BundleRoot)

    $patterns = @(
        @{ Name = "private-key"; Regex = "-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----" },
        @{ Name = "aws-access-key"; Regex = "\b(?:AKIA|ASIA)[A-Z0-9]{16}\b" },
        @{ Name = "provider-token"; Regex = "\b(?:sk|gh[pousr])[-_][A-Za-z0-9_-]{20,}\b" },
        @{ Name = "jwt"; Regex = "\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b" },
        @{ Name = "credentialed-url"; Regex = "https?://[^\s/:@]+:[^\s/@]+@" },
        @{ Name = "literal-secret"; Regex = '(?im)(?:api[_-]?key|client[_-]?secret|password|access[_-]?token|auth[_-]?token)\s*[:=]\s*["'']?(?!\$\{|<|REPLACE|CHANGE|VAULT|SECRET_REF|required)[A-Za-z0-9+/=_-]{16,}' }
    )
    $rootLength = $BundleRoot.TrimEnd('\', '/').Length + 1
    $findings = New-Object System.Collections.Generic.List[string]

    foreach ($file in Get-ChildItem -LiteralPath $BundleRoot -Recurse -File -Force) {
        $extension = $file.Extension.ToLowerInvariant()
        if (($script:TextExtensions -notcontains $extension) -and ($file.Name -notmatch "Dockerfile|Chart.yaml")) {
            continue
        }
        if ($file.Length -gt 10MB) {
            continue
        }
        $content = [System.IO.File]::ReadAllText($file.FullName)
        foreach ($pattern in $patterns) {
            if ([regex]::IsMatch($content, $pattern.Regex)) {
                $relative = $file.FullName.Substring($rootLength).Replace('\', '/')
                $findings.Add("$relative [$($pattern.Name)]")
            }
        }
    }
    if ($findings.Count -gt 0) {
        throw "Sensitive content scan failed. Only affected paths are shown:`n$($findings -join [Environment]::NewLine)"
    }
}

function Get-ChartVersion {
    $chartPath = Resolve-RepositoryPath -RelativePath "infra/helm/taroai/Chart.yaml"
    $chartText = [System.IO.File]::ReadAllText($chartPath)
    $match = [regex]::Match($chartText, '(?m)^appVersion:\s*["'']?([^"''\r\n]+)')
    if (-not $match.Success) {
        throw "Cannot determine appVersion from infra/helm/taroai/Chart.yaml."
    }
    return $match.Groups[1].Value.Trim()
}

function Get-ImageDigest {
    param([Parameter(Mandatory = $true)][string]$ImageReference)

    return Invoke-CapturedCommand -Executable "docker" -Arguments @(
        "image", "inspect", "--format", "{{.Id}}", $ImageReference
    )
}

function Get-PushedDigest {
    param([Parameter(Mandatory = $true)][string]$ImageReference)

    $json = Invoke-CapturedCommand -Executable "docker" -Arguments @(
        "image", "inspect", "--format", "{{json .RepoDigests}}", $ImageReference
    )
    $repoDigests = $json | ConvertFrom-Json
    if ($null -eq $repoDigests -or @($repoDigests).Count -eq 0) {
        return $null
    }
    return @($repoDigests)[0]
}

if (-not (Test-Path -LiteralPath $script:ReleaseConfigPath)) {
    throw "Missing release configuration: $script:ReleaseConfigPath"
}
$releaseConfig = Get-Content -LiteralPath $script:ReleaseConfigPath -Raw | ConvertFrom-Json

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-ChartVersion
}
if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]*$') {
    throw "Version contains unsafe characters: $Version"
}

if ([string]::IsNullOrWhiteSpace($Commit)) {
    $Commit = Invoke-CapturedCommand -Executable "git" -Arguments @("-C", $script:RepoRoot, "rev-parse", "HEAD")
}
else {
    $Commit = Invoke-CapturedCommand -Executable "git" -Arguments @("-C", $script:RepoRoot, "rev-parse", "$Commit^{commit}")
}
if ($Commit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Resolved commit is not a full Git commit SHA: $Commit"
}
$Commit = $Commit.ToLowerInvariant()
$shortCommit = $Commit.Substring(0, 12)

$dirtyOutput = Invoke-CapturedCommand -Executable "git" -Arguments @(
    "-C", $script:RepoRoot, "status", "--porcelain", "--untracked-files=all"
)
$isDirty = -not [string]::IsNullOrWhiteSpace($dirtyOutput)
if ($isDirty -and -not $AllowDirty) {
    throw "The repository is dirty. Commit/stash changes or pass -AllowDirty for an explicitly non-reproducible build."
}
if ($Push -and $ImageMode -ne "Build") {
    throw "-Push requires -ImageMode Build. Registry push is disabled by default."
}

$normalizedRegistry = $Registry.Trim().TrimEnd('/')
if ([string]::IsNullOrWhiteSpace($normalizedRegistry) -or $normalizedRegistry -match '\s') {
    throw "Registry must be a non-empty image repository prefix."
}

if ([System.IO.Path]::IsPathRooted($OutputRoot)) {
    $resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
}
else {
    $resolvedOutputRoot = [System.IO.Path]::GetFullPath((Join-Path $script:RepoRoot $OutputRoot))
}
New-Item -ItemType Directory -Path $resolvedOutputRoot -Force | Out-Null

$bundleName = "taroai-$Version-$shortCommit"
$finalBundle = Join-Path $resolvedOutputRoot $bundleName
if (Test-Path -LiteralPath $finalBundle) {
    if (-not $Force) {
        throw "Release output already exists: $finalBundle. Pass -Force to replace this exact bundle."
    }
    Remove-Item -LiteralPath $finalBundle -Recurse -Force
}
$stagingRoot = Join-Path $resolvedOutputRoot (".{0}.staging-{1}" -f $bundleName, [guid]::NewGuid().ToString("N"))

try {
    $bundleDirectories = @(
        "compose", "config", "helm", "hooks", "images", "licenses", "metadata",
        "migrations", "scripts", "sbom"
    )
    foreach ($directory in $bundleDirectories) {
        New-Item -ItemType Directory -Path (Join-Path $stagingRoot $directory) -Force | Out-Null
    }

    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/release/compose") -Destination (Join-Path $stagingRoot "compose")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/release/config") -Destination (Join-Path $stagingRoot "config")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/release/licenses") -Destination (Join-Path $stagingRoot "licenses")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/release/hooks") -Destination (Join-Path $stagingRoot "hooks")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/helm/taroai") -Destination (Join-Path $stagingRoot "helm/taroai")
    Copy-SafeTree -Source (Resolve-RepositoryPath "apps/api/migrations") -Destination (Join-Path $stagingRoot "migrations")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/package/upgrade-matrix.md") -Destination (Join-Path $stagingRoot "metadata/upgrade-matrix.md")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/package/manifest.schema.json") -Destination (Join-Path $stagingRoot "metadata/deployment-manifest.schema.json")
    Copy-SafeTree -Source (Resolve-RepositoryPath "scripts/release/verify-release.ps1") -Destination (Join-Path $stagingRoot "scripts/verify-release.ps1")
    Copy-SafeTree -Source (Resolve-RepositoryPath "scripts/release/install-release.ps1") -Destination (Join-Path $stagingRoot "scripts/install-release.ps1")
    Copy-SafeTree -Source (Resolve-RepositoryPath "infra/release/README.md") -Destination (Join-Path $stagingRoot "README.md")

    $imageRecords = New-Object System.Collections.Generic.List[object]
    $buildPlan = New-Object System.Collections.Generic.List[object]
    $dockerAvailable = $null -ne (Get-Command docker -ErrorAction SilentlyContinue)
    if (($ImageMode -eq "Build" -or $AirGap) -and -not $dockerAvailable) {
        throw "Docker is required for image build or air-gap export. Use -ImageMode Plan without -AirGap to generate a build plan only."
    }

    foreach ($component in $releaseConfig.images) {
        $contextPath = Resolve-RepositoryPath -RelativePath $component.context
        $dockerfilePath = Resolve-RepositoryPath -RelativePath $component.dockerfile
        $imageRepository = "$normalizedRegistry/$($component.repository)"
        $imageReference = "${imageRepository}:$Version"
        $buildArguments = @(
            "build",
            "--label", "org.opencontainers.image.version=$Version",
            "--label", "org.opencontainers.image.revision=$Commit",
            "--label", "org.opencontainers.image.source=taroai",
            "--tag", $imageReference,
            "--file", $dockerfilePath,
            $contextPath
        )
        $buildPlan.Add([ordered]@{
            component = $component.name
            image = $imageReference
            context = $component.context
            dockerfile = $component.dockerfile
            command = "docker " + (($buildArguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join " ")
        })

        $localDigest = $null
        $registryDigest = $null
        if ($ImageMode -eq "Build") {
            Write-Host "Building $imageReference"
            Invoke-StreamingCommand -Executable "docker" -Arguments $buildArguments
            $localDigest = Get-ImageDigest -ImageReference $imageReference
            if ($Push) {
                Write-Host "Pushing $imageReference"
                Invoke-StreamingCommand -Executable "docker" -Arguments @("push", $imageReference)
                $registryDigest = Get-PushedDigest -ImageReference $imageReference
            }
        }
        elseif ($AirGap) {
            $localDigest = Get-ImageDigest -ImageReference $imageReference
        }

        $archivePath = $null
        if ($AirGap) {
            $archiveName = "$($component.name)-$Version.tar"
            $archiveFullPath = Join-Path $stagingRoot "images/$archiveName"
            Write-Host "Exporting $imageReference for air-gap delivery"
            Invoke-StreamingCommand -Executable "docker" -Arguments @("save", "--output", $archiveFullPath, $imageReference)
            $archivePath = "images/$archiveName"
        }

        $imageRecords.Add([ordered]@{
            component = $component.name
            repository = $imageRepository
            tag = $Version
            reference = $imageReference
            localDigest = $localDigest
            registryDigest = $registryDigest
            archive = $archivePath
            built = ($ImageMode -eq "Build")
            pushed = [bool]$Push
            external = $false
        })
    }

    foreach ($dependency in $releaseConfig.externalImages) {
        $dependencyReference = [string]$dependency.reference
        $dependencyDigest = $null
        $dependencyArchive = $null
        $buildPlan.Add([ordered]@{
            component = $dependency.name
            image = $dependencyReference
            context = $null
            dockerfile = $null
            command = "docker pull $dependencyReference"
        })
        if ($ImageMode -eq "Build" -or $AirGap) {
            Write-Host "Resolving external runtime image $dependencyReference"
            Invoke-StreamingCommand -Executable "docker" -Arguments @("pull", $dependencyReference)
            $dependencyDigest = Get-ImageDigest -ImageReference $dependencyReference
        }
        if ($AirGap) {
            $dependencyArchiveName = "$($dependency.name)-$Version.tar"
            $dependencyArchivePath = Join-Path $stagingRoot "images/$dependencyArchiveName"
            Invoke-StreamingCommand -Executable "docker" -Arguments @(
                "save", "--output", $dependencyArchivePath, $dependencyReference
            )
            $dependencyArchive = "images/$dependencyArchiveName"
        }
        $imageRecords.Add([ordered]@{
            component = $dependency.name
            repository = ($dependencyReference -replace ':[^/:]+$', '')
            tag = if ($dependencyReference -match ':([^/:]+)$') { $Matches[1] } else { $null }
            reference = $dependencyReference
            localDigest = $dependencyDigest
            registryDigest = $null
            archive = $dependencyArchive
            built = $false
            pushed = $false
            external = $true
        })
    }

    $buildPlanPayload = [ordered]@{
        schemaVersion = "1.0"
        version = $Version
        commit = $Commit
        mode = $ImageMode.ToLowerInvariant()
        registryPushEnabled = [bool]$Push
        images = $buildPlan
    }
    $buildPlanPayload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $stagingRoot "metadata/image-build-plan.json") -Encoding UTF8

    $apiImage = $imageRecords | Where-Object { $_.component -eq "api" } | Select-Object -First 1
    $webImage = $imageRecords | Where-Object { $_.component -eq "web" } | Select-Object -First 1
    $sandboxImage = $imageRecords | Where-Object { $_.component -eq "sandbox-controller" } | Select-Object -First 1
    $browserImage = $imageRecords | Where-Object { $_.component -eq "browser-controller" } | Select-Object -First 1
    $imageEnvironmentLines = @(
        "TAROAI_API_IMAGE=$($apiImage.reference)",
        "TAROAI_WEB_IMAGE=$($webImage.reference)",
        "TAROAI_SANDBOX_IMAGE=$($sandboxImage.reference)",
        "TAROAI_BROWSER_IMAGE=$($browserImage.reference)"
    )
    [System.IO.File]::WriteAllLines(
        (Join-Path $stagingRoot "config/images.env"),
        $imageEnvironmentLines,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $helmValues = @"
# Generated by scripts/release/build-release.ps1. Do not add credentials here.
image:
  repository: $($apiImage.repository)
  tag: "$Version"
web:
  image:
    repository: $($webImage.repository)
    tag: "$Version"
sandboxController:
  image:
    repository: $($sandboxImage.repository)
    tag: "$Version"
browserController:
  image:
    repository: $($browserImage.repository)
    tag: "$Version"
"@
    $helmValues | Set-Content -LiteralPath (Join-Path $stagingRoot "helm/values.release.yaml") -Encoding UTF8

    $sbomTool = Get-Command syft -ErrorAction SilentlyContinue
    $sbomRecords = New-Object System.Collections.Generic.List[object]
    if ($null -ne $sbomTool -and ($ImageMode -eq "Build" -or $AirGap)) {
        foreach ($image in $imageRecords) {
            $sbomPath = Join-Path $stagingRoot "sbom/$($image.component).spdx.json"
            Invoke-StreamingCommand -Executable $sbomTool.Source -Arguments @(
                $image.reference, "-o", "spdx-json=$sbomPath"
            )
            $sbomRecords.Add([ordered]@{
                component = $image.component
                status = "generated"
                path = "sbom/$($image.component).spdx.json"
                generator = "syft"
            })
        }
    }
    else {
        if ($RequireSbom) {
            throw "SBOM generation was required, but syft is unavailable or no local images were selected."
        }
        foreach ($image in $imageRecords) {
            $sbomRecords.Add([ordered]@{
                component = $image.component
                status = "pending"
                path = $null
                generator = "syft"
                command = "syft $($image.reference) -o spdx-json=sbom/$($image.component).spdx.json"
            })
        }
    }
    ([ordered]@{
        schemaVersion = "1.0"
        required = [bool]$RequireSbom
        records = $sbomRecords
    } | ConvertTo-Json -Depth 12) | Set-Content -LiteralPath (Join-Path $stagingRoot "metadata/sbom.json") -Encoding UTF8

    $migrationFiles = Get-ChildItem -LiteralPath (Join-Path $stagingRoot "migrations") -Filter "*.sql" -File | Sort-Object Name
    $migrationRecords = @($migrationFiles | ForEach-Object {
        [ordered]@{
            id = $_.BaseName
            path = "migrations/$($_.Name)"
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $latestMigration = if ($migrationFiles.Count -gt 0) { $migrationFiles[-1].BaseName } else { $null }

    $rollbackFromManifest = $null
    if (-not [string]::IsNullOrWhiteSpace($PreviousReleaseManifest)) {
        $resolvedPrevious = [System.IO.Path]::GetFullPath($PreviousReleaseManifest)
        if (-not (Test-Path -LiteralPath $resolvedPrevious -PathType Leaf)) {
            throw "Previous release manifest does not exist: $resolvedPrevious"
        }
        $rollbackFromManifest = Get-Content -LiteralPath $resolvedPrevious -Raw | ConvertFrom-Json
    }
    $rollbackMetadata = [ordered]@{
        schemaVersion = "1.0"
        releaseVersion = $Version
        releaseCommit = $Commit
        previousVersion = if ($null -ne $rollbackFromManifest) { $rollbackFromManifest.version } else { $null }
        previousCommit = if ($null -ne $rollbackFromManifest) { $rollbackFromManifest.commit } else { $null }
        previousImages = if ($null -ne $rollbackFromManifest) { $rollbackFromManifest.images } else { @() }
        migrationBoundary = $latestMigration
        codeRollback = "Redeploy the previous immutable image references from this metadata."
        databaseRollback = "Restore the pre-release database backup when an applied migration is not backward compatible. SQL down-migrations are not assumed."
        requiredEvidence = @("database-backup-id", "object-storage-backup-id", "previous-release-manifest", "rollback-owner")
    }
    $rollbackMetadata | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $stagingRoot "metadata/rollback.json") -Encoding UTF8

    $createdAt = [DateTime]::UtcNow.ToString("o")
    $buildMetadata = [ordered]@{
        schemaVersion = "1.0"
        product = $releaseConfig.product
        version = $Version
        commit = $Commit
        shortCommit = $shortCommit
        createdAt = $createdAt
        builder = "scripts/release/build-release.ps1"
        host = [ordered]@{
            operatingSystem = [System.Environment]::OSVersion.VersionString
            powershell = $PSVersionTable.PSVersion.ToString()
        }
        source = [ordered]@{
            repositoryDirty = $isDirty
            dirtyBuildExplicitlyAllowed = [bool]$AllowDirty
        }
        imageMode = $ImageMode.ToLowerInvariant()
        registryPushEnabled = [bool]$Push
        airGap = [bool]$AirGap
    }
    $buildMetadata | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $stagingRoot "metadata/build.json") -Encoding UTF8

    $provenance = [ordered]@{
        schemaVersion = "https://slsa.dev/provenance/v1"
        buildType = "https://taroai.ai/buildtypes/release-bundle/v1"
        subject = @($imageRecords | ForEach-Object {
            [ordered]@{
                name = $_.reference
                digest = [ordered]@{
                    local = $_.localDigest
                    registry = $_.registryDigest
                }
            }
        })
        invocation = [ordered]@{
            configSource = [ordered]@{ uri = "git:taroai"; digest = [ordered]@{ sha1 = $Commit } }
            parameters = [ordered]@{
                version = $Version
                imageMode = $ImageMode.ToLowerInvariant()
                registry = $normalizedRegistry
                push = [bool]$Push
                airGap = [bool]$AirGap
            }
        }
        metadata = [ordered]@{
            buildStartedOn = $createdAt
            reproducible = (-not $isDirty)
        }
    }
    $provenance | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $stagingRoot "metadata/provenance.json") -Encoding UTF8

    $releaseManifest = [ordered]@{
        schemaVersion = "1.0"
        kind = "TaroaiReleaseBundle"
        product = $releaseConfig.product
        version = $Version
        commit = $Commit
        createdAt = $createdAt
        delivery = [ordered]@{
            format = "release-bundle"
            repositoryArchive = $false
            airGap = [bool]$AirGap
            registryPushEnabled = [bool]$Push
        }
        images = $imageRecords
        migrations = $migrationRecords
        deployment = [ordered]@{
            compose = "compose/docker-compose.release.yml"
            helmChart = "helm/taroai"
            helmValues = "helm/values.release.yaml"
            imageReferences = "config/images.env"
            configurationTemplate = "config/runtime.env.template"
            credentialReferenceTemplate = "config/credential-refs.env.template"
        }
        integrity = [ordered]@{
            checksumAlgorithm = "SHA256"
            checksumManifest = "SHA256SUMS"
            verificationScript = "scripts/verify-release.ps1"
            sensitiveContentScan = "required"
        }
        supplyChain = [ordered]@{
            buildMetadata = "metadata/build.json"
            provenance = "metadata/provenance.json"
            sbom = "metadata/sbom.json"
            signing = if ([string]::IsNullOrWhiteSpace($SigningHook)) { "not-requested" } else { "external-hook-requested" }
        }
        rollback = "metadata/rollback.json"
        licenses = "licenses"
    }
    $manifestPath = Join-Path $stagingRoot "release-manifest.json"
    $releaseManifest | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

    Assert-ReleaseTreeIsClean -BundleRoot $stagingRoot
    Assert-NoSensitiveContent -BundleRoot $stagingRoot

    $checksumPath = Join-Path $stagingRoot "SHA256SUMS"
    $checksumLines = Get-ChildItem -LiteralPath $stagingRoot -Recurse -File -Force |
        Where-Object { $_.FullName -ne $checksumPath } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($stagingRoot.TrimEnd('\', '/').Length + 1).Replace('\', '/')
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relative"
        }
    [System.IO.File]::WriteAllLines($checksumPath, $checksumLines, (New-Object System.Text.UTF8Encoding($false)))

    if (-not [string]::IsNullOrWhiteSpace($SigningHook)) {
        $resolvedHook = [System.IO.Path]::GetFullPath($SigningHook)
        if (-not (Test-Path -LiteralPath $resolvedHook -PathType Leaf)) {
            throw "Signing hook does not exist: $resolvedHook"
        }
        & $resolvedHook -BundlePath $stagingRoot -ManifestPath $manifestPath -ChecksumPath $checksumPath
        if ($LASTEXITCODE -ne 0) {
            throw "Signing hook failed with exit code $LASTEXITCODE."
        }
    }

    Move-Item -LiteralPath $stagingRoot -Destination $finalBundle

    $airGapArchive = $null
    if ($AirGap) {
        $tarCommand = Get-Command tar -ErrorAction SilentlyContinue
        if ($null -eq $tarCommand) {
            throw "tar is required to create the air-gap .tar.gz archive. The release directory remains at $finalBundle."
        }
        $airGapArchive = Join-Path $resolvedOutputRoot "$bundleName-airgap.tar.gz"
        if (Test-Path -LiteralPath $airGapArchive) {
            if (-not $Force) {
                throw "Air-gap archive already exists: $airGapArchive"
            }
            Remove-Item -LiteralPath $airGapArchive -Force
        }
        Invoke-StreamingCommand -Executable $tarCommand.Source -Arguments @(
            "-czf", $airGapArchive, "-C", $resolvedOutputRoot, $bundleName
        )
    }

    [ordered]@{
        status = "complete"
        bundle = $finalBundle
        version = $Version
        commit = $Commit
        imageMode = $ImageMode.ToLowerInvariant()
        pushed = [bool]$Push
        airGapArchive = $airGapArchive
        verification = Join-Path $finalBundle "scripts/verify-release.ps1"
        manifest = Join-Path $finalBundle "release-manifest.json"
    } | ConvertTo-Json -Depth 8
}
catch {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
    throw
}

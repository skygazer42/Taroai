[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [ValidateSet("Validate", "Compose", "Helm")]
    [string]$Mode = "Validate",
    [string]$EnvironmentFile,
    [string]$Namespace = "taroai",
    [string]$ReleaseName = "taroai",
    [string]$AdditionalHelmValues,
    [switch]$LoadAirGapImages,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-ReleaseCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE."
    }
}

$bundleRoot = [System.IO.Path]::GetFullPath($BundlePath)
$verifyScript = Join-Path $bundleRoot "scripts/verify-release.ps1"
if (-not (Test-Path -LiteralPath $verifyScript -PathType Leaf)) {
    throw "Bundle verification script is missing: $verifyScript"
}
$verification = & $verifyScript -BundlePath $bundleRoot -PassThru
$manifest = Get-Content -LiteralPath (Join-Path $bundleRoot "release-manifest.json") -Raw | ConvertFrom-Json

if ($LoadAirGapImages) {
    if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is required to load air-gap image archives."
    }
    foreach ($image in $manifest.images) {
        if ([string]::IsNullOrWhiteSpace($image.archive)) {
            throw "No air-gap image archive is declared for $($image.component)."
        }
        $archive = Join-Path $bundleRoot $image.archive
        Invoke-ReleaseCommand -Executable "docker" -Arguments @("load", "--input", $archive)
    }
}

switch ($Mode) {
    "Validate" {
        [ordered]@{
            status = "validated"
            version = $verification.version
            commit = $verification.commit
            bundle = $bundleRoot
        } | ConvertTo-Json -Depth 6
    }
    "Compose" {
        if ([string]::IsNullOrWhiteSpace($EnvironmentFile)) {
            throw "Compose installation requires -EnvironmentFile. Copy config/runtime.env.template outside the bundle and supply real secret references."
        }
        $resolvedEnvironment = [System.IO.Path]::GetFullPath($EnvironmentFile)
        if (-not (Test-Path -LiteralPath $resolvedEnvironment -PathType Leaf)) {
            throw "Compose environment file does not exist: $resolvedEnvironment"
        }
        if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker is required for Compose installation."
        }
        $arguments = @(
            "compose",
            "--env-file", (Join-Path $bundleRoot "config/images.env"),
            "--env-file", $resolvedEnvironment,
            "--file", (Join-Path $bundleRoot "compose/docker-compose.release.yml"),
            "--project-name", "taroai"
        )
        if ($DryRun) {
            $arguments += @("config")
        }
        else {
            $arguments += @("up", "--detach", "--remove-orphans")
        }
        Invoke-ReleaseCommand -Executable "docker" -Arguments $arguments
    }
    "Helm" {
        if ($null -eq (Get-Command helm -ErrorAction SilentlyContinue)) {
            throw "Helm is required for Kubernetes installation."
        }
        $arguments = @(
            "upgrade", "--install", $ReleaseName,
            (Join-Path $bundleRoot "helm/taroai"),
            "--namespace", $Namespace,
            "--create-namespace",
            "--values", (Join-Path $bundleRoot "helm/values.release.yaml"),
            "--atomic",
            "--wait"
        )
        if (-not [string]::IsNullOrWhiteSpace($AdditionalHelmValues)) {
            $resolvedValues = [System.IO.Path]::GetFullPath($AdditionalHelmValues)
            if (-not (Test-Path -LiteralPath $resolvedValues -PathType Leaf)) {
                throw "Additional Helm values file does not exist: $resolvedValues"
            }
            $arguments += @("--values", $resolvedValues)
        }
        if ($DryRun) {
            $arguments += @("--dry-run")
        }
        Invoke-ReleaseCommand -Executable "helm" -Arguments $arguments
    }
}

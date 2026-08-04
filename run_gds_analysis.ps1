[CmdletBinding()]
param(
    [string]$ConfigPath = "config\config.yaml",
    [string]$OutputDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = if ([System.IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath
} else {
    Join-Path $ProjectRoot $ConfigPath
}
$OutputOverride = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $null
} elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $ProjectRoot $OutputDirectory
}
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Configuration file was not found: $Config"
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw ".env was not found. Add NEO4J_PASSWORD before running GDS analysis."
}

Write-Host ""
Write-Host "========== Read-only Neo4j GDS structural analysis ==========" -ForegroundColor Cyan
$AnalysisArguments = @(
    "src\analyze_gds.py",
    "--config", $Config
)
if ($null -ne $OutputOverride) {
    $AnalysisArguments += @("--output-directory", $OutputOverride)
}
& $Python @AnalysisArguments
if ($LASTEXITCODE -ne 0) {
    throw "GDS analysis failed with exit code $LASTEXITCODE."
}

$ResolveArguments = @(
    "src\analyze_gds.py",
    "--config", $Config,
    "--print-output-directory"
)
if ($null -ne $OutputOverride) {
    $ResolveArguments += @("--output-directory", $OutputOverride)
}
$ResolvedOutputLines = & $Python @ResolveArguments
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve the GDS output directory."
}
$Output = [string]($ResolvedOutputLines | Select-Object -Last 1)
if ([string]::IsNullOrWhiteSpace($Output)) {
    throw "The resolved GDS output directory was empty."
}

$FinalizeArguments = @(
    "src\analyze_gds.py",
    "--config", $Config,
    "--output-directory", $Output,
    "--finalize-manifest"
)
& $Python @FinalizeArguments
if ($LASTEXITCODE -ne 0) {
    throw "GDS manifest finalization failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "GDS analysis completed." -ForegroundColor Green
Write-Host "Chinese report: $(Join-Path $Output 'gds_results_cn.md')"
Write-Host "English report: $(Join-Path $Output 'gds_results_en.md')"
Write-Host "Tables: $(Join-Path $Output 'tables')"
Write-Host "Figures: $(Join-Path $Output 'figures')"
Write-Host "Manifest: $(Join-Path $Output 'gds_manifest.json')"

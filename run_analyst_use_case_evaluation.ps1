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
    throw ".env was not found. Add NEO4J_PASSWORD before running the analyst use-case evaluation."
}

Write-Host ""
Write-Host "========== Read-only analyst use-case evaluation ==========" -ForegroundColor Cyan
$EvaluationArguments = @(
    "src\evaluate_analyst_use_cases.py",
    "--config", $Config
)
if ($null -ne $OutputOverride) {
    $EvaluationArguments += @("--output-directory", $OutputOverride)
}
& $Python @EvaluationArguments
if ($LASTEXITCODE -ne 0) {
    throw "Analyst use-case evaluation failed with exit code $LASTEXITCODE."
}

$ResolveArguments = @(
    "src\evaluate_analyst_use_cases.py",
    "--config", $Config,
    "--print-output-directory"
)
if ($null -ne $OutputOverride) {
    $ResolveArguments += @("--output-directory", $OutputOverride)
}
$ResolvedOutputLines = & $Python @ResolveArguments
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve the analyst use-case output directory."
}
$Output = [string]($ResolvedOutputLines | Select-Object -Last 1)
if ([string]::IsNullOrWhiteSpace($Output)) {
    throw "The resolved analyst use-case output directory was empty."
}

& $Python "src\evaluate_analyst_use_cases.py" `
    "--config" $Config `
    "--output-directory" $Output `
    "--finalize-manifest"
if ($LASTEXITCODE -ne 0) {
    throw "Analyst use-case manifest finalization failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Analyst use-case evaluation completed." -ForegroundColor Green
Write-Host "Chinese report: $(Join-Path $Output 'analyst_use_case_evaluation_cn.md')"
Write-Host "English report: $(Join-Path $Output 'analyst_use_case_evaluation_en.md')"
Write-Host "Tables: $(Join-Path $Output 'tables')"
Write-Host "Figures: $(Join-Path $Output 'figures')"
Write-Host "Manifest: $(Join-Path $Output 'analyst_use_case_manifest.json')"

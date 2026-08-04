[CmdletBinding()]
param(
    [string]$ConfigPath = "config\config.yaml",
    [string]$OutputDirectory = "outputs\chapter4_results"
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
$Output = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $ProjectRoot $OutputDirectory
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Configuration file was not found: $Config"
}

Write-Host ""
Write-Host "========== Reproducible Chapter 4 results ==========" -ForegroundColor Cyan
& $Python "src\build_chapter4_results.py" `
    "--config" $Config `
    "--output-directory" $Output
if ($LASTEXITCODE -ne 0) {
    throw "Chapter 4 result generation failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Chapter 4 results completed." -ForegroundColor Green
Write-Host "Chinese report: $(Join-Path $Output 'chapter4_results_cn.md')"
Write-Host "English report: $(Join-Path $Output 'chapter4_results_en.md')"
Write-Host "Tables: $(Join-Path $Output 'tables')"
Write-Host "Figures: $(Join-Path $Output 'figures')"
Write-Host "Manifest: $(Join-Path $Output 'results_manifest.json')"

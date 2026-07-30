[CmdletBinding()]
param(
    [string]$ConfigPath = "config\config.yaml",
    [string]$OutputDirectory = "outputs\analyst_report",
    [string]$CompanyId = "",
    [string]$EventType = "",
    [string]$StartDate = "",
    [string]$EndDate = "",
    [Nullable[double]]$MinimumNlpProbability = $null
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

New-Item -ItemType Directory -Path $Output -Force | Out-Null

$ExportArguments = @(
    "src\export_analyst_report.py",
    "--config", $Config,
    "--output-directory", $Output
)
if (-not [string]::IsNullOrWhiteSpace($CompanyId)) {
    $ExportArguments += @("--company-id", $CompanyId.Trim())
}
if (-not [string]::IsNullOrWhiteSpace($EventType)) {
    $ExportArguments += @("--event-type", $EventType.Trim())
}
if (-not [string]::IsNullOrWhiteSpace($StartDate)) {
    $ExportArguments += @("--start-date", $StartDate.Trim())
}
if (-not [string]::IsNullOrWhiteSpace($EndDate)) {
    $ExportArguments += @("--end-date", $EndDate.Trim())
}
if ($null -ne $MinimumNlpProbability) {
    $ExportArguments += @(
        "--minimum-nlp-probability",
        $MinimumNlpProbability.Value.ToString(
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    )
}

Write-Host ""
Write-Host "========== Read-only Neo4j analyst export ==========" -ForegroundColor Cyan
& $Python @ExportArguments
if ($LASTEXITCODE -ne 0) {
    throw "Neo4j analyst export failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Analyst report completed." -ForegroundColor Green
Write-Host "JSON data: $(Join-Path $Output 'analyst_report_data.json')"
Write-Host "Chinese briefing: $(Join-Path $Output 'analyst_briefing_cn.md')"
Write-Host "English briefing: $(Join-Path $Output 'analyst_briefing_en.md')"

[CmdletBinding()]
param(
    [ValidateSet("all", "market", "news", "downstream", "coverage", "event", "nlp", "dedup", "kg", "analysis", "evaluation", "report")]
    [string]$Stage = "downstream",

    [string]$ConfigPath = "config\config.yaml",

    [switch]$RefreshNews,

    [string[]]$RefreshCompany = @(),

    [switch]$IncludeNotRecommended
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = $ScriptRoot
$NestedRoot = Join-Path $ScriptRoot "financial_kg_setup"

# Support both layouts:
#   <root>\run_full_pipeline.ps1
#   <root>\financial_kg_setup\run_full_pipeline.ps1
if (
    -not (Test-Path -LiteralPath (Join-Path $ProjectRoot "config\config.yaml")) -and
    (Test-Path -LiteralPath (Join-Path $NestedRoot "config\config.yaml"))
) {
    $ProjectRoot = $NestedRoot
}

Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = if ([System.IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath
} else {
    Join-Path $ProjectRoot $ConfigPath
}
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python was not found: $Python. Follow the Environment section in README.md to create .venv and install the requirements."
}

if (-not (Test-Path -LiteralPath $Config)) {
    throw "Configuration file was not found: $Config"
}
$Config = (Resolve-Path -LiteralPath $Config).Path

Write-Host "Project root: $ProjectRoot" -ForegroundColor DarkGray
Write-Host "Configuration: $Config" -ForegroundColor DarkGray

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host ""
    Write-Host "========== $Label ==========" -ForegroundColor Cyan
    & $Python @Arguments
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "$Label failed with exit code $ExitCode. Resolve the error shown above and run the command again."
    }
}

function Assert-EnvFile {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw ".env was not found. Copy .env.example to .env and add the API keys."
    }
}

function Invoke-MarketStage {
    Assert-EnvFile
    Invoke-PythonStep -Label "1/9 Market-data validation and candidate-pool selection" -Arguments @(
        "src\validate_market_data.py",
        "--config", $Config
    )
}

function Invoke-NewsStage {
    Assert-EnvFile

    if ($RefreshNews -and $RefreshCompany.Count -gt 0) {
        throw "Do not use -RefreshNews and -RefreshCompany together. Choose either a full refresh or a company-specific refresh."
    }

    $Arguments = @(
        "src\collect_guardian_news.py",
        "--config", $Config,
        "--mode", "full"
    )

    if ($RefreshNews) {
        $Arguments += "--refresh"
    }

    foreach ($CompanyId in $RefreshCompany) {
        if ([string]::IsNullOrWhiteSpace($CompanyId)) {
            continue
        }
        $Arguments += @("--refresh-company", $CompanyId.Trim())
    }

    Invoke-PythonStep -Label "2/9 Guardian candidate-pool news collection" -Arguments $Arguments
}

function Invoke-PrepareStage {
    Invoke-PythonStep -Label "3/9 Candidate-pool news evidence cleaning" -Arguments @(
        "src\prepare_guardian_news.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-CoverageStage {
    Invoke-PythonStep -Label "4/9 News-coverage exclusion and ranked backfill" -Arguments @(
        "src\select_news_coverage.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-ExtractStage {
    Invoke-PythonStep -Label "5/9 Rule-based event-candidate and company-link extraction" -Arguments @(
        "src\extract_event_candidates.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-NlpStage {
    Invoke-PythonStep -Label "6/9 NLP semantic event and relationship validation" -Arguments @(
        "src\enrich_events_nlp.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-DedupStage {
    Invoke-PythonStep -Label "7/9 Cross-article canonical Event deduplication" -Arguments @(
        "src\deduplicate_events.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-AlignStage {
    $Arguments = @(
        "src\align_event_market_data.py",
        "--config", $Config,
        "--mode", "full"
    )
    if ($IncludeNotRecommended) {
        $Arguments += "--include-not-recommended"
    }
    Invoke-PythonStep -Label "8/9 Event and market-window alignment" -Arguments $Arguments
}

function Invoke-KgStage {
    Invoke-PythonStep -Label "9/9 Automatic Neo4j import package" -Arguments @(
        "src\build_kg_import.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-AnalysisStage {
    Assert-EnvFile
    Invoke-PythonStep -Label "Neo4j graph validation and analysis export" -Arguments @(
        "src\query_kg.py",
        "--config", $Config
    )
}

function Invoke-EvaluationStage {
    Invoke-PythonStep -Label "Automatic ablation and threshold-sensitivity evaluation" -Arguments @(
        "src\evaluate_pipeline.py",
        "--config", $Config,
        "--mode", "full"
    )
}

function Invoke-ReportStage {
    $ReportScript = Join-Path $ProjectRoot "run_analyst_report.ps1"
    if (-not (Test-Path -LiteralPath $ReportScript)) {
        throw "Analyst-report entry point was not found: $ReportScript"
    }
    Write-Host ""
    Write-Host "========== Formula-driven analyst report ==========" -ForegroundColor Cyan
    & $ReportScript -ConfigPath $Config
    $ExitCode = $LASTEXITCODE
    if ($null -ne $ExitCode -and $ExitCode -ne 0) {
        throw "Analyst report failed with exit code $ExitCode."
    }
}

switch ($Stage) {
    "all" {
        Invoke-MarketStage
        Invoke-NewsStage
        Invoke-PrepareStage
        Invoke-CoverageStage
        Invoke-ExtractStage
        Invoke-NlpStage
        Invoke-DedupStage
        Invoke-AlignStage
        Invoke-KgStage
    }
    "market" {
        Invoke-MarketStage
    }
    "news" {
        Invoke-NewsStage
    }
    "downstream" {
        Invoke-PrepareStage
        Invoke-CoverageStage
        Invoke-ExtractStage
        Invoke-NlpStage
        Invoke-DedupStage
        Invoke-AlignStage
        Invoke-KgStage
    }
    "coverage" {
        Invoke-CoverageStage
        Invoke-ExtractStage
        Invoke-NlpStage
        Invoke-DedupStage
        Invoke-AlignStage
        Invoke-KgStage
    }
    "event" {
        Invoke-ExtractStage
        Invoke-NlpStage
        Invoke-DedupStage
        Invoke-AlignStage
        Invoke-KgStage
    }
    "nlp" {
        Invoke-NlpStage
        Invoke-DedupStage
        Invoke-AlignStage
        Invoke-KgStage
    }
    "dedup" {
        Invoke-DedupStage
        Invoke-AlignStage
        Invoke-KgStage
    }
    "kg" {
        Invoke-KgStage
    }
    "analysis" {
        Invoke-AnalysisStage
        Invoke-EvaluationStage
    }
    "evaluation" {
        Invoke-EvaluationStage
    }
    "report" {
        Invoke-ReportStage
    }
}

Write-Host ""
Write-Host "Pipeline completed. Inspect the output paths declared in $Config." -ForegroundColor Green

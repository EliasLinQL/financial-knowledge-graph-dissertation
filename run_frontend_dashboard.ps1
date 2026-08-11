param(
    [ValidateSet("dev", "build", "snapshot")]
    [string]$Stage = "dev",
    [switch]$SkipSnapshot,
    [string]$ProxyUrl
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendRoot = Join-Path $projectRoot "frontend"
$snapshotBuilder = Join-Path $projectRoot "src\build_frontend_snapshot.py"
$snapshotPath = Join-Path $frontendRoot "public\data\dashboard.json"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

if (-not $SkipSnapshot) {
    Write-Host "[1/2] Building validated, credential-free dashboard snapshot..." -ForegroundColor Cyan
    Push-Location $projectRoot
    try {
        & $python $snapshotBuilder --output $snapshotPath
        if ($LASTEXITCODE -ne 0) {
            throw "Dashboard snapshot build failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

if ($Stage -eq "snapshot") {
    Write-Host "Snapshot ready: $snapshotPath" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path (Join-Path $frontendRoot "node_modules"))) {
    Write-Host "Frontend dependencies are missing. Run 'npm install' in $frontendRoot first." -ForegroundColor Yellow
    exit 1
}

Push-Location $frontendRoot
try {
    if ($ProxyUrl) {
        $parsedProxy = $null
        if (-not [Uri]::TryCreate($ProxyUrl, [UriKind]::Absolute, [ref]$parsedProxy) -or
            $parsedProxy.Scheme -notin @("http", "https")) {
            throw "ProxyUrl must be an absolute http:// or https:// URL."
        }
        $env:HTTP_PROXY = $parsedProxy.AbsoluteUri
        $env:HTTPS_PROXY = $parsedProxy.AbsoluteUri
        $env:ALL_PROXY = $parsedProxy.AbsoluteUri
        Write-Host "Using the configured HTTP(S) proxy for frontend server requests." -ForegroundColor Cyan
    }

    if ($Stage -eq "build") {
        Write-Host "[2/2] Building production frontend..." -ForegroundColor Cyan
        & npm run build
    }
    else {
        Write-Host "[2/2] Starting dashboard at http://localhost:3000 ..." -ForegroundColor Cyan
        & npm run dev
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Frontend $Stage failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

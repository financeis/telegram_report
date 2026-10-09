param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the project .venv and install requirements-workspace.txt first.'
}
Push-Location $projectRoot
try {
    if (-not $SkipBuild) {
        if (-not (Test-Path -LiteralPath 'frontend\node_modules')) {
            npm --prefix frontend ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        }
        npm --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    Write-Host 'Research Desk: http://127.0.0.1:8520/'
    & $pythonPath -m research_desk web
} finally {
    Pop-Location
}

[CmdletBinding()]
param(
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$appRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $appRoot
try {
    if (-not (Test-Path .\output\last_cosmos_insert.json)) {
        throw "No insertion receipt found. Run .\scripts\insert-cosmos-demo.ps1 first."
    }

    Write-Host "Checking live-flow dependencies..."
    python -m live_intent_app.check_demo_readiness --mode live
    if ($LASTEXITCODE -ne 0) {
        throw "Live-flow readiness checks failed."
    }

    Write-Host "Waiting $WaitSeconds seconds for the change feed and Azure Function..."
    Start-Sleep -Seconds $WaitSeconds

    python -m live_intent_app.verify_demo --mode live
    if ($LASTEXITCODE -ne 0) {
        throw "Live-flow verification failed."
    }
}
finally {
    Pop-Location
}

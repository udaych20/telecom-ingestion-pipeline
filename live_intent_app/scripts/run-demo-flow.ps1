[CmdletBinding()]
param(
    [int]$WaitSeconds = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$appRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $appRoot
try {
    if (-not (Test-Path .\output\last_cosmos_insert.json)) {
        throw "No insertion receipt found. Run .\scripts\insert-cosmos-demo.ps1 first."
    }

    Write-Host "Checking demo-flow dependencies..."
    python -m live_intent_app.check_demo_readiness --mode demo
    if ($LASTEXITCODE -ne 0) {
        throw "Demo-flow readiness checks failed."
    }

    Write-Host "Reading the inserted Cosmos record and bypassing the Function..."
    python -m live_intent_app.replay_cosmos_to_kafka
    if ($LASTEXITCODE -ne 0) {
        throw "Demo bypass processing failed."
    }

    Write-Host "Waiting $WaitSeconds seconds before verification..."
    Start-Sleep -Seconds $WaitSeconds

    python -m live_intent_app.verify_demo --mode demo
    if ($LASTEXITCODE -ne 0) {
        throw "Demo-flow verification failed."
    }
}
finally {
    Pop-Location
}

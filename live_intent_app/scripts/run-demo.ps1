[CmdletBinding()]
param(
    [ValidateSet("Live", "Demo")]
    [string]$Mode = "Live",
    [string]$Container = "chat-history-uat",
    [string]$Message = "Please check the status of my mobile service",
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$appRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $appRoot
try {
    $modeName = $Mode.ToLowerInvariant()
    Write-Host "Checking $Mode mode dependencies..."
    python -m live_intent_app.check_demo_readiness --mode $modeName
    if ($LASTEXITCODE -ne 0) {
        throw "Demo readiness checks failed."
    }

    if ($Mode -eq "Live") {
        Write-Host "Live mode requires func start in a separate terminal."
    }

    Write-Host "Inserting a unique test document into Cosmos DB..."
    python -m live_intent_app.insert_cosmos_test_document `
        --container $Container `
        --message $Message
    if ($LASTEXITCODE -ne 0) {
        throw "Cosmos test insertion failed."
    }

    if ($Mode -eq "Demo") {
        Write-Host "Running SLM and CSV directly, then publishing the result to Kafka..."
        python -m live_intent_app.replay_cosmos_to_kafka
        if ($LASTEXITCODE -ne 0) {
            throw "Cosmos-to-Kafka replay failed."
        }
    }

    Write-Host "Waiting $WaitSeconds seconds for asynchronous processing..."
    Start-Sleep -Seconds $WaitSeconds

    python -m live_intent_app.verify_demo --mode $modeName
    if ($LASTEXITCODE -ne 0) {
        throw "Demo verification command failed."
    }
}
finally {
    Pop-Location
}

[CmdletBinding()]
param(
    [int]$WaitSeconds = 5,
    [string]$DocumentFile = "",
    [string]$Container = "chat-history-uat"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$appRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $appRoot
try {
    $backupEnabled = python -c "from live_intent_app.settings import load_local_environment; import os; load_local_environment(); print(os.getenv('DEMO_SLM_BACKUP_ENABLED','false').lower())"
    $isBackup = $backupEnabled.Trim() -in @("1", "true", "yes", "on")
    if ($isBackup) {
        $readinessMode = "demo-file"
        $verifyMode = "backup"
    }
    elseif ($DocumentFile) {
        $resolvedDocument = Resolve-Path $DocumentFile -ErrorAction Stop
        $readinessMode = "demo-file"
        $verifyMode = "demo"
    }
    elseif (-not (Test-Path .\output\last_cosmos_insert_batch.json)) {
        throw "No Cosmos batch receipt found. Run insert-cosmos-demo.ps1 first."
    }
    else {
        $readinessMode = "demo"
        $verifyMode = "demo"
    }

    Write-Host "Checking demo-flow dependencies..."
    python -m live_intent_app.check_demo_readiness --mode $readinessMode
    if ($LASTEXITCODE -ne 0) {
        throw "Demo-flow readiness checks failed."
    }

    Write-Host "Processing the inserted Cosmos record and bypassing the Function..."
    if ($DocumentFile -and -not $isBackup) {
        python -m live_intent_app.replay_cosmos_to_kafka `
            --document-file $resolvedDocument `
            --container $Container
    }
    else {
        python -m live_intent_app.replay_cosmos_to_kafka
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Demo bypass processing failed."
    }

    Write-Host "Waiting $WaitSeconds seconds before verification..."
    Start-Sleep -Seconds $WaitSeconds

    python -m live_intent_app.verify_demo --mode $verifyMode
    if ($LASTEXITCODE -ne 0) {
        throw "Demo-flow verification failed."
    }
}
finally {
    Pop-Location
}

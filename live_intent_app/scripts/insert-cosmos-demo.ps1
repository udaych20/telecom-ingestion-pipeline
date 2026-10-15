[CmdletBinding()]
param(
    [string]$Container = "chat-history-uat",
    [string]$Message = "Please check the status of my mobile service"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$appRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $appRoot
try {
    Write-Host "Inserting a new demo record into Cosmos DB..."
    python -m live_intent_app.insert_cosmos_test_document `
        --container $Container `
        --message $Message
    if ($LASTEXITCODE -ne 0) {
        throw "Cosmos demo insertion failed."
    }

    Write-Host ""
    Write-Host "Inserted record receipt:"
    Get-Content .\output\last_cosmos_insert.json

}
finally {
    Pop-Location
}

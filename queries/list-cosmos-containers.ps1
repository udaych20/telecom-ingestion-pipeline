<#
.SYNOPSIS
Lists Cosmos DB SQL containers and their partition-key paths.

.EXAMPLE
.\queries\list-cosmos-containers.ps1

.EXAMPLE
.\queries\list-cosmos-containers.ps1 -DatabaseName "another-database"
#>

[CmdletBinding()]
param(
    [string]$AccountName = "nora-copy-db-stream-test",
    [string]$ResourceGroup = "qtmNpeInfraQtmAiPocWu2Rg",
    [string]$DatabaseName = "nora-copy-db-stream-test",
    [string]$SubscriptionId = "3ce9fffc-e404-4b89-a569-33333f13d01a"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI was not found. Install it and run 'az login' first."
}

az cosmosdb sql container list `
    --account-name $AccountName `
    --resource-group $ResourceGroup `
    --database-name $DatabaseName `
    --subscription $SubscriptionId `
    --query "[].{Container:resource.id,PartitionKey:resource.partitionKey.paths[0]}" `
    --output table

if ($LASTEXITCODE -ne 0) {
    throw "Azure CLI could not list the Cosmos DB containers."
}

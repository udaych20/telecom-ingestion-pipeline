[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ResourceGroup,

    [Parameter(Mandatory)]
    [string]$AccountName,

    [Parameter(Mandatory)]
    [string]$Database,

    [Parameter(Mandatory)]
    [string]$Container
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$dataContributorRoleId = "00000000-0000-0000-0000-000000000002"
$principalId = az ad signed-in-user show --query id -o tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($principalId)) {
    throw "Unable to resolve the signed-in Azure user. Run 'az login' with your user account and try again."
}

$accountId = az cosmosdb show `
    --resource-group $ResourceGroup `
    --name $AccountName `
    --query id `
    -o tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($accountId)) {
    throw "Unable to resolve Cosmos DB account '$AccountName' in resource group '$ResourceGroup'."
}

$roleDefinitionId = "$accountId/sqlRoleDefinitions/$dataContributorRoleId"
$scope = "/dbs/$Database/colls/$Container"

az cosmosdb sql role assignment create `
    --resource-group $ResourceGroup `
    --account-name $AccountName `
    --role-definition-id $roleDefinitionId `
    --principal-id $principalId `
    --scope $scope
if ($LASTEXITCODE -ne 0) {
    throw "Failed to grant Cosmos DB Built-in Data Contributor access."
}

Write-Host "Granted Cosmos DB insert access to user '$principalId' at scope '$scope'."

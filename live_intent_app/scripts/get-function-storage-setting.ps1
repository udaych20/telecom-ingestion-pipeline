param(
    [Parameter(Mandatory)]
    [string]$ResourceGroup,

    [Parameter(Mandatory)]
    [string]$StorageAccount
)

$connectionString = az storage account show-connection-string `
    --resource-group $ResourceGroup `
    --name $StorageAccount `
    --query connectionString `
    --output tsv

Write-Host 'Copy this entry into the "Values" section of local.settings.json:'
Write-Host "`"AzureWebJobsStorage`": `"$connectionString`""

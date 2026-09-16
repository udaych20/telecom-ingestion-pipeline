# Test the NORA event app from an Azure Windows VM

Run the sections below in PowerShell, one at a time. Replace every value that
starts with `YOUR-` before running the Azure setup commands.

## 1. Open the project

```powershell
Set-Location D:\git\telecom-ingestion-pipeline\event_app
```

## 2. Verify the required tools

Python, Azure CLI, and Azure Functions Core Tools v4 must already be installed.
Core Tools can be installed with the official Windows MSI; npm is not required.

```powershell
python --version
az version
func --version
```

## 3. Create the Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m py_compile function_app.py
```

## 4. Sign in to Azure

```powershell
az login
az account show --output table
```

If necessary, select the correct subscription:

```powershell
az account set --subscription "YOUR-SUBSCRIPTION-ID-OR-NAME"
```

## 5. Set the resource names

Only change the values on the right side.

```powershell
$ResourceGroup = "YOUR-RESOURCE-GROUP"
$FunctionApp = "YOUR-FUNCTION-APP"
$CosmosAccount = "YOUR-COSMOS-ACCOUNT"
$CosmosDatabase = "NORA"
$CosmosEndpoint = "https://YOUR-COSMOS-ACCOUNT.documents.azure.com:443/"
$EventHubNamespaceName = "YOUR-EVENT-HUB-NAMESPACE"
$EventHubNamespace = "YOUR-EVENT-HUB-NAMESPACE.servicebus.windows.net"
$EventHubName = "nora-updates"
$ConsumerGroup = "nora-update-subscriber"
```

Confirm that none of the placeholders remain:

```powershell
Get-Variable ResourceGroup,FunctionApp,CosmosAccount,CosmosDatabase,CosmosEndpoint,EventHubNamespaceName,EventHubNamespace,EventHubName,ConsumerGroup |
  Format-Table Name,Value
```

## 6. Create the Event Hub consumer group

The Event Hub and namespace must already exist.

```powershell
az eventhubs eventhub consumer-group create `
  --resource-group $ResourceGroup `
  --namespace-name $EventHubNamespaceName `
  --eventhub-name $EventHubName `
  --name $ConsumerGroup
```

## 7. Create the Cosmos DB lease containers

These commands are safe to run when the containers already exist only if their
partition key is already `/id`. Check existing containers before changing them.

```powershell
az cosmosdb sql container create --resource-group $ResourceGroup --account-name $CosmosAccount --database-name $CosmosDatabase --name "leases-chat" --partition-key-path "/id"
az cosmosdb sql container create --resource-group $ResourceGroup --account-name $CosmosAccount --database-name $CosmosDatabase --name "leases-tools" --partition-key-path "/id"
az cosmosdb sql container create --resource-group $ResourceGroup --account-name $CosmosAccount --database-name $CosmosDatabase --name "leases-context" --partition-key-path "/id"
az cosmosdb sql container create --resource-group $ResourceGroup --account-name $CosmosAccount --database-name $CosmosDatabase --name "leases-feedback" --partition-key-path "/id"
```

## 8. Enable the Function App identity and grant test permissions

```powershell
$PrincipalId = az functionapp identity assign `
  --resource-group $ResourceGroup `
  --name $FunctionApp `
  --query principalId `
  --output tsv

$EventHubScope = az eventhubs namespace show `
  --resource-group $ResourceGroup `
  --name $EventHubNamespaceName `
  --query id `
  --output tsv

az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal --role "Azure Event Hubs Data Sender" --scope $EventHubScope
az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal --role "Azure Event Hubs Data Receiver" --scope $EventHubScope
```

For a test environment, grant the Function identity the Cosmos DB built-in data
contributor role. Use narrower container-level permissions in production.

```powershell
$CosmosRoleId = az cosmosdb sql role definition list `
  --resource-group $ResourceGroup `
  --account-name $CosmosAccount `
  --query "[?roleName=='Cosmos DB Built-in Data Contributor'].id | [0]" `
  --output tsv

az cosmosdb sql role assignment create `
  --resource-group $ResourceGroup `
  --account-name $CosmosAccount `
  --role-definition-id $CosmosRoleId `
  --principal-id $PrincipalId `
  --scope "/"
```

Role assignments can take several minutes to become effective.

## 9. Configure the Function App

`local.settings.json` is not uploaded during deployment. Configure Azure settings
explicitly with this command:

```powershell
az functionapp config appsettings set `
  --resource-group $ResourceGroup `
  --name $FunctionApp `
  --settings `
    "COSMOS_CONNECTION__accountEndpoint=$CosmosEndpoint" `
    "COSMOS_DATABASE=$CosmosDatabase" `
    "COSMOS_CHAT_CONTAINER=chat-history-uat" `
    "COSMOS_TOOLS_CONTAINER=context-history-all-tools" `
    "COSMOS_CONTEXT_CONTAINER=context-history-uat" `
    "COSMOS_FEEDBACK_CONTAINER=chat-feedback" `
    "EVENT_HUB_NAMESPACE=$EventHubNamespace" `
    "EVENT_HUB_CONNECTION__fullyQualifiedNamespace=$EventHubNamespace" `
    "EVENT_HUB_NAME=$EventHubName" `
    "EVENT_HUB_CONSUMER_GROUP=$ConsumerGroup"
```

The Function App must also have a valid `AzureWebJobsStorage` configuration. An
Azure Function App created normally already has this setting.

```powershell
az functionapp config appsettings list `
  --resource-group $ResourceGroup `
  --name $FunctionApp `
  --query "[?name=='AzureWebJobsStorage'].name" `
  --output tsv
```

The command must print `AzureWebJobsStorage`.

## 10. Publish the code

```powershell
func azure functionapp publish $FunctionApp
```

## 11. Confirm that Azure discovered the functions

```powershell
func azure functionapp list-functions $FunctionApp
```

Expected functions:

```text
chat_history_changes
tool_history_changes
context_history_changes
feedback_changes
nora_update_subscriber
```

## 12. Stream logs and test an update

```powershell
func azure functionapp logstream $FunctionApp
```

Leave that command running. In Azure Portal, open Cosmos DB Data Explorer and
insert or update one document in a configured NORA source container.

Successful logs contain both messages:

```text
Published NORA change event ...
Received NORA update ...
```

If neither appears, check the Cosmos endpoint, source-container names, leases,
managed-identity permissions, Function App logs, and network/firewall rules. If
only the publish message appears, check the Event Hub name, consumer group, and
Data Receiver role.

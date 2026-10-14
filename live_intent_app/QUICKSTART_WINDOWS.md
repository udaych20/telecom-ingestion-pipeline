# End-to-end Windows test

This guide tests exactly this path:

```text
Cosmos test insert -> change feed -> local Azure Function -> local Kafka
  -> Cloud Foundry model -> output/live_intent.csv
```

Run application commands from:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
```

## 1. Prerequisites

Install Python 3.11, Azure CLI, and Azure Functions Core Tools 4. The supplied
PowerShell setup installs Eclipse Temurin OpenJDK 21 and Apache Kafka. Node.js,
npm, Docker, and Azurite are not required.

```powershell
python -m pip install -r .\requirements.txt
az login
```

Install Azure Functions Core Tools 4 using Microsoft's
[official 64-bit Windows MSI](https://learn.microsoft.com/azure/azure-functions/functions-run-local#install-the-azure-functions-core-tools).
Then verify the installation:

```powershell
func --version
```

## 2. Configuration

```powershell
Copy-Item .\.env.example .\.env
Copy-Item .\local.settings.example.json .\local.settings.json
```

Update `.env` for the insert script, Kafka worker, model, and CSV:

```dotenv
COSMOS_CONNECTION__accountEndpoint=https://YOUR-ACCOUNT.documents.azure.com:443/
COSMOS_DATABASE=NORA
COSMOS_CHAT_CONTAINER=chat-history-uat

STREAM_TRANSPORT=kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=live-intent
KAFKA_CONSUMER_GROUP=live-intent-inference
KAFKA_SECURITY_PROTOCOL=PLAINTEXT

MODEL_ENDPOINT=https://YOUR-CLOUD-FOUNDRY-ROUTE.example.com/v1
MODEL_DEPLOYMENT=YOUR-SLM
MODEL_API_KEY=YOUR-MODEL-KEY
MODEL_API_KEY_HEADER=Authorization

CSV_SINK=local
CSV_OUTPUT=output/live_intent.csv
```

Update `local.settings.json` for the local Functions host:

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "PASTE-AZURE-STORAGE-CONNECTION-STRING-HERE",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "COSMOS_CONNECTION__accountEndpoint": "https://YOUR-ACCOUNT.documents.azure.com:443/",
    "COSMOS_DATABASE": "NORA",
    "COSMOS_CHAT_CONTAINER": "chat-history-uat",
    "COSMOS_CHAT_LEASE": "leases-live-chat",
    "COSMOS_CONTEXT_CONTAINER": "context-history-uat",
    "COSMOS_CONTEXT_LEASE": "leases-live-context",
    "COSMOS_TOOLS_CONTAINER": "context-history-all-tools",
    "COSMOS_TOOLS_LEASE": "leases-live-tools",
    "COSMOS_FEEDBACK_CONTAINER": "chat-feedback",
    "COSMOS_FEEDBACK_LEASE": "leases-live-feedback",
    "STREAM_TRANSPORT": "kafka",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "KAFKA_TOPIC": "live-intent",
    "KAFKA_SECURITY_PROTOCOL": "PLAINTEXT"
  }
}
```

The source and lease containers must exist. To create missing resources:

```powershell
python -m live_intent_app.provision --dry-run
python -m live_intent_app.provision
```

The provisioner also creates the real Azure Storage account required by the
local Functions host. Put its connection string in the ignored
`local.settings.json` file:

```powershell
$storageConnection = az storage account show-connection-string `
    --subscription YOUR-SUBSCRIPTION-ID `
    --resource-group rg-live-intent `
    --name YOUR-STORAGE-ACCOUNT `
    --query connectionString `
    --output tsv

$settings = Get-Content .\local.settings.json -Raw | ConvertFrom-Json
$settings.Values.AzureWebJobsStorage = $storageConnection
$settings | ConvertTo-Json -Depth 10 | Set-Content .\local.settings.json
```

## 3. Install OpenJDK and Kafka once

The setup downloads OpenJDK and Kafka into `live_intent_app\tools`, verifies both
download checksums, and initializes a single-node KRaft data directory:

```powershell
.\scripts\setup-openjdk-kafka.ps1
```

Start Kafka:

```powershell
.\scripts\start-kafka.ps1
```

Keep Kafka running. In another terminal, create the topic:

```powershell
.\scripts\create-kafka-topic.ps1
```

## 4. Run the end-to-end test

Keep each process running in a separate terminal.

Terminal 1 — Kafka, if it is not already running:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
.\scripts\start-kafka.ps1
```

Terminal 2 — inference worker:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
python -m live_intent_app.kafka_worker
```

Terminal 3 — Cosmos change-feed listener:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
func start
```

Wait until all Cosmos triggers are listed without errors. Then use Terminal 4 to
insert a new Cosmos document:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
python -m live_intent_app.insert_cosmos_test_document `
    --container chat-history-uat `
    --message "Please check the status of my mobile service"
```

The script creates a unique document ID on every run.

## 5. Verify

The Function terminal should report a change-feed invocation. The Kafka worker
should report the inferred event. Check the CSV:

```powershell
Get-Content .\output\live_intent.csv
```

## 6. Access requirements

- The `az login` identity needs Cosmos DB Built-in Data Contributor access so
  the script can insert and the local Function can read changes and write leases.
- The Cloud Foundry endpoint requires a valid key. This real model call cannot
  run without the model's required authorization.
- `AzureWebJobsStorage` must contain the real Azure Storage connection string,
  and the Windows server must be able to reach Azure Storage.

## 7. Tests

```powershell
python -m unittest discover -s .\tests -v
```

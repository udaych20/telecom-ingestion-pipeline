# Actual/live flow quick start for Windows

This guide runs the complete integration without bypasses:

```text
Cosmos insert
  -> Cosmos change feed
  -> local Azure Function
  -> Cloud Foundry SLM
  -> output/live_intent.csv
  -> Kafka topic: cosmos-changes
```

Run every command from:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
```

## 1. One-time setup

Install Python 3.11, Azure CLI, and Azure Functions Core Tools 4 using the
official 64-bit Windows MSI. Node.js, npm, Docker, and Azurite are not required.

```powershell
python -m pip install -r .\requirements.txt
az login
func --version

Copy-Item .\.env.example .\.env
Copy-Item .\local.settings.example.json .\local.settings.json

.\scripts\setup-openjdk-kafka.ps1
```

Configure `.env` with the real Cosmos, Cloud Foundry, CSV, and Kafka values.
Configure `local.settings.json` with the same application values and a real
Azure Storage connection string in `AzureWebJobsStorage`.

If the Cosmos source/lease containers or Function storage account are missing:

```powershell
python -m live_intent_app.provision --dry-run
python -m live_intent_app.provision
```

## 2. Start Kafka

Terminal 1:

```powershell
.\scripts\start-kafka.ps1
```

Keep it running. Create the topic once in another terminal:

```powershell
.\scripts\create-kafka-topic.ps1
```

## 3. Start the actual Function flow

Terminal 2:

```powershell
.\scripts\verify-and-start-function.ps1
```

The script creates and activates `.venv`, installs `requirements.txt`, verifies
the required Python imports, displays the Core Tools version, and starts the
Function host from the directory containing `host.json`.

Wait until these triggers are listed without errors:

```text
chat_changes
context_changes
tool_changes
feedback_changes
```

## 4. Insert the Cosmos record separately

Terminal 3:

```powershell
.\scripts\insert-cosmos-demo.ps1 `
    -Container chat-history-uat `
    -Message "Please check the status of my mobile service"
```

The script displays the inserted document and saves its receipt to
`output/last_cosmos_insert.json`.

## 5. Verify the live flow

```powershell
.\scripts\run-live-flow.ps1
```

Expected result:

```text
PASS         Cosmos insert
PASS         Cosmos change feed
PASS         Local Azure Function
PASS         Cloud Foundry SLM
PASS         Intent CSV
PASS         Kafka result topic
```

Evidence files:

```powershell
Get-Content .\output\last_cosmos_insert.json
Get-Content .\output\change_feed_audit.jsonl
Get-Content .\output\live_intent.csv
```

If `func start` reports `WorkerConfig for runtime: python not found`, repair the
64-bit Azure Functions Core Tools installation or use the separate demo guide.

If `func start` reports that `COSMOS_CONNECTION` does not exist, confirm that
`local.settings.json` exists and contains these Function host settings:

```json
{
  "IsEncrypted": false,
  "Values": {
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AzureWebJobsStorage": "PASTE-AZURE-STORAGE-CONNECTION-STRING-HERE",
    "COSMOS_CONNECTION__accountEndpoint": "https://YOUR-ACCOUNT.documents.azure.com:443/",
    "COSMOS_DATABASE": "YOUR-DATABASE"
  }
}
```

The Function host does not read binding connection settings from `.env`.
`AzureWebJobsStorage` must be a valid, reachable Storage connection string.
Keep `local.settings.json` local because it can contain secrets.

The identity running the Function requires Cosmos data access to every source
and lease container. For this four-trigger flow, grant the Cosmos DB Built-in
Data Contributor role at database scope (`/dbs/YOUR-DATABASE`) and ensure the
configured lease containers already exist.

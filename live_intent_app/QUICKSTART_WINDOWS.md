# Windows quick start: live and demo modes

This project has two test modes. Complete the common setup once, then choose
Mode 1 or Mode 2.

## Modes

### Mode 1 — live end-to-end flow

Use this mode first. It verifies the actual integration path:

```text
Cosmos test insert -> change feed -> local Azure Function
  -> Cloud Foundry model -> output/live_intent.csv -> local Kafka
```

Nothing is bypassed. This mode requires the Cosmos change feed, Azure Functions
Core Tools, Azure Storage, Kafka, and the Cloud Foundry model to be reachable.

### Mode 2 — demo bypass flow

Use this only when the Cosmos insert works but the change feed or local Azure
Function cannot run because of environment or access limitations:

```text
Cosmos test insert -> read the same Cosmos record
  -> Cloud Foundry model -> output/live_intent.csv -> local Kafka
```

Mode 2 bypasses only `cosmos_change_feed` and `local_azure_function`. It calls
the real Cloud Foundry model, writes the real CSV, and then publishes the
enriched result to Kafka.

Run application commands from:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
```

After the common setup, the automated runners are:

```powershell
# Full flow; start Kafka and func first.
.\scripts\run-demo.ps1 -Mode Live

# Targeted bypass; start Kafka first.
.\scripts\run-demo.ps1 -Mode Demo
```

Each runner checks its dependencies before inserting data. Use the standalone
check when troubleshooting:

```powershell
python -m live_intent_app.check_demo_readiness --mode live
python -m live_intent_app.check_demo_readiness --mode demo
```

## Common setup

### 1. Prerequisites

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

### 2. Configuration

```powershell
Copy-Item .\.env.example .\.env
Copy-Item .\local.settings.example.json .\local.settings.json
```

Update `.env` for the insert script, model, CSV, and Kafka publisher:

```dotenv
COSMOS_CONNECTION__accountEndpoint=https://YOUR-ACCOUNT.documents.azure.com:443/
COSMOS_DATABASE=NORA
COSMOS_CHAT_CONTAINER=chat-history-uat

KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=cosmos-changes
KAFKA_SECURITY_PROTOCOL=PLAINTEXT

MODEL_ENDPOINT=https://YOUR-CLOUD-FOUNDRY-ROUTE.example.com/v1
MODEL_DEPLOYMENT=YOUR-SLM
MODEL_API_KEY=YOUR-MODEL-KEY
MODEL_API_KEY_HEADER=Authorization

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
    "MODEL_ENDPOINT": "https://YOUR-CLOUD-FOUNDRY-ROUTE.example.com/v1",
    "MODEL_DEPLOYMENT": "YOUR-SLM",
    "MODEL_API_KEY": "YOUR-MODEL-KEY",
    "MODEL_API_KEY_HEADER": "Authorization",
    "MODEL_TIMEOUT_SECONDS": "30",
    "CSV_OUTPUT": "output/live_intent.csv",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "KAFKA_TOPIC": "cosmos-changes",
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

### 3. Install OpenJDK and Kafka once

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

## Mode 1 — run the live end-to-end test

Keep each process running in a separate terminal.

Terminal 1 — Kafka, if it is not already running:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
.\scripts\start-kafka.ps1
```

Terminal 2 — Cosmos change-feed listener, inference, CSV, and Kafka publisher:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
func start
```

Wait until all Cosmos triggers are listed without errors. Then use Terminal 3 to
insert a new Cosmos document:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
python -m live_intent_app.insert_cosmos_test_document `
    --container chat-history-uat `
    --message "Please check the status of my mobile service"
```

The script creates a unique document ID on every run.

### Verify Mode 1

The Function terminal should report the change-feed invocation, SLM result,
CSV write, and Kafka publication.

Run the verifier after allowing the Function and worker to process the event:

```powershell
python -m live_intent_app.verify_demo --mode live
```

It reports separate results for the Cosmos insert, change feed, local Function,
SLM inference, CSV, and final Kafka publication. You can also inspect:

```powershell
Get-Content .\output\change_feed_audit.jsonl
Get-Content .\output\live_intent.csv
```

Expected Mode 1 result:

```text
PASS         Cosmos insert
PASS         Cosmos change feed
PASS         Local Azure Function
PASS         Cloud Foundry SLM
PASS         Intent CSV
PASS         Kafka result topic
```

## Mode 2 — run the demo bypass

Use the bypass only when the Cosmos insert succeeded but the verifier cannot
confirm the change feed or local Azure Function. It reads the same inserted
document back from Cosmos, calls the SLM, writes CSV, and publishes the enriched
result to Kafka:

Kafka must be running. The local Functions host is not required for this mode.

If you are starting directly with Mode 2, start Kafka as shown in Mode 1. Then
insert a new Cosmos record:

```powershell
python -m live_intent_app.insert_cosmos_test_document `
    --container chat-history-uat `
    --message "Please check the status of my mobile service"
```

After the insert succeeds, process that exact record and publish its result:

```powershell
python -m live_intent_app.replay_cosmos_to_kafka
```

The event is marked with `demo_bypass=true` and lists exactly these skipped
stages:

```text
cosmos_change_feed
local_azure_function
```

Run the verifier:

```powershell
python -m live_intent_app.verify_demo --mode demo
```

The Cosmos change-feed and Function stages remain `NOT VERIFIED`; the SLM,
intent CSV, and final Kafka publication can pass. This is intentional.

Expected Mode 2 result:

```text
PASS         Cosmos insert
NOT VERIFIED Cosmos change feed
NOT VERIFIED Local Azure Function
PASS         Cloud Foundry SLM
PASS         Intent CSV
PASS         Kafka result topic
```

## Access requirements

- The `az login` identity needs Cosmos DB Built-in Data Contributor access so
  the script can insert and the local Function can read changes and write leases.
- The Cloud Foundry endpoint requires a valid key. This real model call cannot
  run without the model's required authorization.
- `AzureWebJobsStorage` must contain the real Azure Storage connection string,
  and the Windows server must be able to reach Azure Storage.

## Unit tests

```powershell
python -m unittest discover -s .\tests -v
```

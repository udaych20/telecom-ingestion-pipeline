# Demo bypass flow quick start for Windows

Use this guide when Cosmos access works but the change feed or local Azure
Function cannot run:

```text
Cosmos insert
  -> read the exact inserted Cosmos record
  -> bypass change feed and local Azure Function
  -> Cloud Foundry SLM
  -> output/live_intent.csv
  -> Kafka topic: cosmos-changes
```

Cloud Foundry inference, CSV output, and Kafka are real. Only the change feed
and local Function are bypassed.

Run every command from:

```powershell
cd D:\git\telecom-ingestion-pipeline\live_intent_app
```

## 1. One-time setup

Azure Functions Core Tools and Azure Storage are not required for this flow.
Node.js, npm, Docker, and Azurite are also not required.

```powershell
python -m pip install -r .\requirements.txt
az login

Copy-Item .\.env.example .\.env
.\scripts\setup-openjdk-kafka.ps1
```

Configure `.env` with real values for:

```dotenv
COSMOS_CONNECTION__accountEndpoint=https://YOUR-ACCOUNT.documents.azure.com:443/
COSMOS_DATABASE=NORA
COSMOS_CHAT_CONTAINER=chat-history-uat

MODEL_ENDPOINT=https://YOUR-CLOUD-FOUNDRY-ROUTE.example.com/v1
MODEL_DEPLOYMENT=YOUR-SLM
MODEL_API_KEY=YOUR-MODEL-KEY
MODEL_API_KEY_HEADER=Authorization

# Or use Azure OpenAI for the SLM (do not configure both blocks):
# AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
# AZURE_OPENAI_DEPLOYMENT=YOUR-SLM-DEPLOYMENT
# AZURE_OPENAI_API_KEY=YOUR-KEY
# AZURE_OPENAI_API_VERSION=2024-10-21

CSV_OUTPUT=output/live_intent.csv

KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=cosmos-changes
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
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

## 3. Check demo dependencies

```powershell
# When Python can read Cosmos:
python -m live_intent_app.check_demo_readiness --mode demo

# When using JSON copied from Azure Data Explorer:
python -m live_intent_app.check_demo_readiness --mode demo-file
```

Fix every `FAIL` before continuing.

## 4. Provide the Cosmos record

### Option A — Python identity has Cosmos access

```powershell
.\scripts\insert-cosmos-demo.ps1 `
    -Container chat-history-uat `
    -Message "Please check the status of my mobile service"
```

This saves the exact inserted record receipt to
`output/last_cosmos_insert.json`.

Configure the common 10-event JSON batch in `.env`. The flag selects its source
path:

```dotenv
DEMO_SLM_BACKUP_ENABLED=true
DEMO_BATCH_SIZE=10
DEMO_DOCUMENT_DIR=input/demo-events
```

With `DEMO_SLM_BACKUP_ENABLED=false`, run both commands:

```powershell
.\scripts\insert-cosmos-demo.ps1 -Container chat-history-uat
.\scripts\run-demo-flow.ps1
```

The first command inserts all 10 JSON events into Cosmos. The second reads those
inserted records and runs SLM inference, CSV persistence, and Kafka publishing.

With `DEMO_SLM_BACKUP_ENABLED=true`, Cosmos insertion is skipped. Run only:

```powershell
.\scripts\run-demo-flow.ps1
```

The runner reads the same 10 JSON events directly and performs SLM inference,
CSV persistence, and Kafka publishing without writing to Cosmos.

### Option B — record was inserted in Azure Data Explorer

The demo cannot automatically discover a browser-inserted record without
Cosmos read permission. In Azure Data Explorer, open the inserted item and copy
the complete JSON, including `id`, `_etag`, and `_ts`, into a local file such as:

```text
output/browser_cosmos_document.json
```

Use [demo_document.example.json](demo_document.example.json) as the shape
reference. Do not wrap the document in another JSON object.

## 5. Run and verify the demo flow

For Option A:

```powershell
.\scripts\run-demo-flow.ps1
```

For Option B, which requires no Python Cosmos permission:

```powershell
.\scripts\run-demo-flow.ps1 `
    -DocumentFile .\output\browser_cosmos_document.json `
    -Container chat-history-uat
```

The script registers the exact exported document, invokes the SLM, writes CSV,
publishes the enriched user-intent result to Kafka for ontology consumers, and
verifies it. Kafka publish is skipped unless a new CSV row is saved. With Option A, it reads
the receipt and retrieves the exact record from Cosmos first.

Expected result:

```text
PASS         Cosmos insert
NOT VERIFIED Cosmos change feed
NOT VERIFIED Local Azure Function
PASS         Cloud Foundry SLM
PASS         Intent CSV
PASS         Kafka result topic
```

The two `NOT VERIFIED` entries are expected because those are the only bypassed
stages.

Evidence files:

```powershell
Get-Content .\output\last_cosmos_insert.json
Get-Content .\output\last_bypass_event.json
Get-Content .\output\live_intent.csv
```

The Kafka event contains:

```json
{
  "demo_bypass": true,
  "bypassed_stages": [
    "cosmos_change_feed",
    "local_azure_function"
  ]
}
```

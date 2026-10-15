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
python -m live_intent_app.check_demo_readiness --mode demo
```

Fix every `FAIL` before continuing.

## 4. Insert the Cosmos record separately

```powershell
.\scripts\insert-cosmos-demo.ps1 `
    -Container chat-history-uat `
    -Message "Please check the status of my mobile service"
```

This saves the exact inserted record receipt to
`output/last_cosmos_insert.json`.

## 5. Run and verify the demo flow

```powershell
.\scripts\run-demo-flow.ps1
```

The script reads the receipt, retrieves that exact record from Cosmos, invokes
the SLM, writes CSV, publishes the enriched result to Kafka, and verifies it.

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

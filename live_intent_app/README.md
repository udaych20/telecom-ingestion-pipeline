# Live intent inference

For copy-paste Windows setup and test commands, see
[`QUICKSTART_WINDOWS.md`](QUICKSTART_WINDOWS.md).

This is a separate, near-real-time project based on the extraction behavior of
`../intent_app.py`. The reference application is not imported at runtime and was
not changed.

## Flow

```text
Cosmos DB inserts/updates
  -> Cosmos change feed trigger (Azure Function)
  -> Event Hubs OR on-prem Kafka
  -> Cloud Foundry-hosted SLM inference
  -> CSV output (immutable Blob CSVs in production, one local CSV in demo)
```

The same `LiveIntentPipeline.process` method is used by Event Hubs, Kafka, the
Azure Function HTTP endpoint, and the local REST demo. Records are masked before
model inference. Source and conversation IDs are SHA-256 hashed in output.

## Run without external access

From the repository root in PowerShell:

```powershell
$env:MODEL_PROVIDER = "demo"
$env:CSV_SINK = "local"
$env:CSV_OUTPUT = "live_intent_app/output/live_intent.csv"
python -m live_intent_app.demo_service
```

Then submit the same document shape that Cosmos will provide:

```powershell
$body = @{ id = "demo-1"; _etag = "v1"; messages = @(
  @{ type = "user"; data = @{ content = "Please check my service status"; cid = "cid-1" } }
) } | ConvertTo-Json -Depth 6
Invoke-RestMethod http://127.0.0.1:8080/demo/infer -Method Post -ContentType application/json -Body $body
```

Health check: `GET /health`. Reposting the same `id` + `_etag` is safely
deduplicated. Change `_etag` to simulate a Cosmos update.

## Azure Functions deployment

1. Copy `local.settings.example.json` to `local.settings.json` for local use and
   fill only non-secret settings. Put secrets in Key Vault/app settings.
2. Create the four lease containers named in `function_app.py`; partition them by
   `/id`. Grant the Function managed identity Cosmos data access, Event Hubs
   sender/receiver access, and Blob Data Contributor access.
3. Create an Event Hub and the `live-intent-inference` consumer group.
4. From this directory run `pip install -r requirements.txt`, `func start` for a
   local check, then `func azure functionapp publish <app-name>`.

The Cosmos trigger publishes inserts and updates. Event Hubs invokes the
subscriber in the same Function App. Use `CSV_SINK=blob` for scale-out; each event
becomes `<prefix>/<event-id>.csv`, so retries do not duplicate results.

The HTTP fallback is `POST /api/demo/infer` and uses Function-key authorization.
It accepts either a raw Cosmos-style document or the complete event envelope.

## Automatic Azure resource creation

Install the Azure CLI, sign in with `az login`, copy `.env.example` to `.env`,
and fill the `AZURE_*` and resource-name settings. Preview the operations first:

```powershell
python -m live_intent_app.provision --dry-run
```

Create any missing resource group, serverless Cosmos account, database, eight
source/lease containers, Event Hubs namespace, hub, and consumer group:

```powershell
python -m live_intent_app.provision
```

The command is idempotent: existing resources are retained. If
`FUNCTION_PRINCIPAL_ID` is supplied, it also assigns the Cosmos DB Built-in Data
Contributor and Event Hubs sender/receiver roles. Resource creation and role
assignment require sufficient Azure control-plane permissions.

If those permissions are unavailable, the provisioner prints the permission-free
demo instructions. Set `STREAM_TRANSPORT=rest` to send every change through an
HTTP endpoint, or set `REST_FALLBACK_ENDPOINT` while using Event Hubs/Kafka to
fall back only for authentication or authorization failures.

## On-prem Kafka mode

Set `STREAM_TRANSPORT=kafka` on the Azure Function publisher and configure the
`KAFKA_*` variables. Run inference on a host that can reach the brokers:

```powershell
python -m live_intent_app.kafka_worker
```

Kafka offsets are committed only after inference and CSV persistence succeed.
Use a stable consumer group and multiple workers for partition-based scaling.

## Model contract

`MODEL_ENDPOINT` must expose an OpenAI-compatible `/chat/completions` endpoint.
The model response must be JSON:

```json
{"intent":"query","confidence":0.94,"reason":"Customer asks for status"}
```

Allowed intents match the batch reference: `ticket`, `modify`, `rca`, `query`,
`general`, and `clarification_needed`. If the Cloud Foundry service uses a custom
key header, set `MODEL_API_KEY_HEADER`; its default is `Authorization: Bearer`.

## Verification

```powershell
python -m unittest discover -s live_intent_app/tests -v
```

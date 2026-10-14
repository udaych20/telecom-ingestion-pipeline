# Windows quick start

This guide provides two tests:

1. A permission-free REST demo with no Azure, Cosmos DB, Kafka, or model access.
2. A local native-Windows Kafka test with no Docker.

Run all commands from the repository root:

```powershell
cd D:\git\telecom-ingestion-pipeline
```

## 1. Python setup

The repository already contains a virtual environment. Install the new project's
dependencies into it:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\live_intent_app\requirements.txt
```

If PowerShell prevents activation, you can use the virtual-environment Python
directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\live_intent_app\requirements.txt
```

Create the local configuration file:

```powershell
Copy-Item .\live_intent_app\.env.example .\live_intent_app\.env
```

Do not commit `.env`; it may contain credentials.

## 2. Permission-free REST demo

For this test, change these values in `live_intent_app\.env`:

```dotenv
MODEL_PROVIDER=demo
CSV_SINK=local
CSV_OUTPUT=live_intent_app/output/live_intent.csv
DEMO_HOST=127.0.0.1
DEMO_PORT=8080
```

Start the API:

```powershell
python -m live_intent_app.demo_service
```

Keep that terminal open. In a second PowerShell window:

```powershell
cd D:\git\telecom-ingestion-pipeline

$body = @{
    id = "demo-1"
    _etag = "version-1"
    messages = @(
        @{
            type = "user"
            data = @{
                cid = "cid-1"
                content = "Please check the status of my mobile service"
            }
        }
    )
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
    -Uri http://127.0.0.1:8080/demo/infer `
    -Method Post `
    -ContentType application/json `
    -Body $body
```

Expected intent: `query`. Check the generated CSV:

```powershell
Get-Content .\live_intent_app\output\live_intent.csv
```

Posting the same `id` and `_etag` again is deduplicated. Change `_etag` to
`version-2` to simulate a Cosmos DB update.

## 3. Install Kafka directly on Windows

Docker is not required. Install a Java 17 or newer JDK and confirm it:

```powershell
java -version
$env:JAVA_HOME
```

Download the current binary Kafka release from:

<https://kafka.apache.org/community/downloads/>

Extract it to a short path such as `C:\kafka`. The commands below assume that
location.

Format a new single-node KRaft data directory once:

```powershell
cd C:\kafka
$clusterId = & .\bin\windows\kafka-storage.bat random-uuid
.\bin\windows\kafka-storage.bat format `
    --standalone `
    -t $clusterId `
    -c .\config\server.properties
```

Start Kafka:

```powershell
cd C:\kafka
.\bin\windows\kafka-server-start.bat .\config\server.properties
```

Keep Kafka running. In another terminal, create the topic:

```powershell
cd C:\kafka
.\bin\windows\kafka-topics.bat `
    --create `
    --if-not-exists `
    --topic live-intent `
    --bootstrap-server localhost:9092 `
    --partitions 1 `
    --replication-factor 1
```

Verify it:

```powershell
.\bin\windows\kafka-topics.bat `
    --list `
    --bootstrap-server localhost:9092
```

## 4. Configure the application for local Kafka

Change these values in `live_intent_app\.env`:

```dotenv
STREAM_TRANSPORT=kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=live-intent
KAFKA_CONSUMER_GROUP=live-intent-inference
KAFKA_SECURITY_PROTOCOL=PLAINTEXT

MODEL_PROVIDER=demo
CSV_SINK=local
CSV_OUTPUT=live_intent_app/output/live_intent.csv
```

Leave the SASL username and password empty for this local-only test.

Start the inference consumer from the repository root:

```powershell
python -m live_intent_app.kafka_worker
```

Keep the worker running. Open one more terminal and publish a test change event:

```powershell
cd D:\git\telecom-ingestion-pipeline
.\.venv\Scripts\Activate.ps1
python -m live_intent_app.send_test_event
```

The worker should log the event and intent. Inspect the result:

```powershell
Get-Content .\live_intent_app\output\live_intent.csv
```

To send your own document:

```powershell
python -m live_intent_app.send_test_event `
    --document C:\path\to\cosmos-document.json `
    --container chat-history-uat
```

## 5. Test the automated Azure provisioning

Before running this section, update these values in `.env`:

```dotenv
AZURE_SUBSCRIPTION_ID=<subscription-id>
AZURE_RESOURCE_GROUP=rg-live-intent-demo
AZURE_LOCATION=eastus
AZURE_COSMOS_ACCOUNT=<globally-unique-lowercase-name>
EVENT_HUB_NAMESPACE_NAME=<globally-unique-name>
```

Sign in and preview the commands:

```powershell
az login
python -m live_intent_app.provision --dry-run
```

Create the missing resources:

```powershell
python -m live_intent_app.provision
```

This creates the Cosmos account, database, four source containers, four lease
containers, Event Hubs namespace, Event Hub, and consumer group. Existing
resources are retained.

If you already have a Function App managed identity, also set:

```dotenv
FUNCTION_PRINCIPAL_ID=<managed-identity-object-id>
```

The provisioner will assign the required Cosmos data and Event Hubs roles if the
signed-in Azure account is permitted to create role assignments.

## 6. Use the Cloud Foundry model

Once the demo pipeline works, replace only the model settings:

```dotenv
MODEL_PROVIDER=foundry
MODEL_ENDPOINT=https://YOUR-CF-ROUTE.example.com/v1
MODEL_DEPLOYMENT=YOUR-SLM
MODEL_API_KEY=<model-api-key>
MODEL_API_KEY_HEADER=Authorization
```

The endpoint must provide an OpenAI-compatible `/chat/completions` route and
return JSON containing `intent`, `confidence`, and `reason`.

If the actual model endpoint requires credentials, those credentials cannot be
bypassed. `MODEL_PROVIDER=demo` is the permission-free test implementation.

## 7. REST fallback when Kafka or Event Hubs access is denied

Start the REST service on a machine reachable by the producer, then configure:

```dotenv
REST_FALLBACK_ENDPOINT=http://HOST:8080/demo/infer
```

Keep `STREAM_TRANSPORT=kafka` or `eventhub`. Authentication and authorization
failures will be sent to REST. To skip the broker entirely:

```dotenv
STREAM_TRANSPORT=rest
REST_FALLBACK_ENDPOINT=http://HOST:8080/demo/infer
```

An Azure Function cannot reach `localhost` on your Windows computer. Azure-to-
on-premises Kafka or REST requires VPN, ExpressRoute, or another secured and
routable connection.

## 8. Run automated tests

```powershell
python -m unittest discover -s .\live_intent_app\tests -v
```

Expected result: all tests pass.

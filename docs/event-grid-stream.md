# Live mode with an Event Grid namespace

This supports the Event Grid **namespace** shown in your screenshot,
`nora-copy-db-stream-test-topic`. It is not an Event Hub, a Basic Event Grid
topic, or an MQTT topic space. No Event Hubs resources are used in this mode.

The flow is: Cosmos change feed → Function publisher → Event Grid HTTP namespace
topic → pull subscription → timer Function subscriber. The subscriber currently
validates and logs notifications, just like the original Event Hubs subscriber.
It does not yet classify live records, join interactions, write a classification
CSV, train a model or call NORA APIs. Initial-load classification is unchanged.

## Automatic setup and publishing

Update `intent_app_config.env`:

```dotenv
INITIAL_LOAD_ENABLED=false
STREAM_LOAD_ENABLED=true
STREAM_TRANSPORT=eventgrid
STREAM_AUTO_PROVISION=true
STREAM_DEPLOY_FUNCTION=true

EVENT_GRID_NAMESPACE=nora-copy-db-stream-test-topic
EVENT_GRID_RESOURCE_GROUP=YOUR-EXISTING-NAMESPACE-RESOURCE-GROUP
EVENT_GRID_ENDPOINT=
EVENT_GRID_TOPIC=nora-updates
EVENT_GRID_SUBSCRIPTION=nora-subscriber
EVENT_GRID_RECEIVE_BATCH_SIZE=10
```

Fill the existing automatic-setup settings too: `AZURE_SUBSCRIPTION_ID`,
`AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_COSMOS_RESOURCE_GROUP`,
`AZURE_COSMOS_ACCOUNT`, `STREAM_STORAGE_ACCOUNT`, and `STREAM_FUNCTION_APP`.
Keep your Cosmos endpoint, database, source containers and lease-container names.
The namespace resource group may differ from the Function resource group, but
all resources must be in the configured subscription.

Install dependencies in your Python environment and sign in:

```powershell
python -m pip install -r requirements.txt
az login
az extension add --name eventgrid
python intent_app.py
```

The Azure CLI namespace commands require the `eventgrid` extension and are
currently documented as preview. Use your organization's approved CLI/extension
versions. If the extension is already installed, it need not be added again.

Setup reads the existing namespace and discovers its **HTTP topics endpoint**;
leave `EVENT_GRID_ENDPOINT` blank for this. It never guesses an endpoint from the
namespace name or creates a replacement namespace. If you specify an endpoint,
it must match the namespace's endpoint. Setup then:

- Creates or reuses the HTTP topic (`CloudEventSchemaV1_0`).
- Creates or reuses the pull event subscription (`deliveryMode=Queue`). An
  existing push subscription is rejected, not silently changed.
- Creates/reuses the Function storage, Cosmos lease containers and dedicated
  Function App using the existing automatic-setup workflow.
- Grants the Function identity EventGrid Data Sender and EventGrid Data Receiver
  roles at the topic scope, plus the existing Cosmos permissions.
- Sets the Event Grid Function configuration and publishes the code. It does
  not register an Event Hubs subscriber or require any `EVENT_HUB_*` settings.

Existing resources are not resized or deleted. The new subscription defaults to
a 300-second receive lock, ten delivery attempts and a one-day event TTL; the
new topic has one-day retention. Existing subscription settings are preserved.
The namespace itself must already exist. Missing namespaces, denied access and
unreachable services cause errors, not fallback to Event Hubs.

Creating the topic/subscription and hosting still requires Azure permissions and
can incur charges. The CLI identity needs management permissions and permission
to grant roles; the Function uses managed identity for data access. The prior
restrictions on private-network Cosmos, storage keys and unrelated Function App
ownership still apply. Network-restricted Event Grid namespaces also require an
administrator-approved network-aware deployment. Nothing bypasses Azure Policy.

## Local or manually deployed Functions

Set `STREAM_AUTO_PROVISION=false` to use the existing local Functions host.
Configure `STREAM_TRANSPORT=eventgrid`, the actual `EVENT_GRID_ENDPOINT`, topic,
subscription, Cosmos trigger settings and host storage. The resources and data
permissions must already exist. Local execution still requires Functions Core
Tools (`func`) and all `event_app/requirements.txt` dependencies. Set the same
settings on the Function App if publishing manually.

In local mode, the endpoint must be copied from the namespace's HTTP endpoint
in Azure (not its MQTT hostname or a Basic topic `/api/events` URL).
`event_app/local.settings.example.json` includes both transports' settings;
select `eventgrid` there if running `func start` directly.

## Delivery and operational behavior

The timer polls every 15 seconds, receiving up to `EVENT_GRID_RECEIVE_BATCH_SIZE`
messages (1–100) per invocation with a ten-second receive wait. The Functions
timer is a single scheduled consumer, not an Event Hubs-style partition-scaled
consumer; tune the batch size and monitor backlog for higher event rates.

Each message is acknowledged only after `process_event` succeeds. Failure to
process or acknowledge releases it with a ten-second delay; if release fails,
its lock can expire for redelivery. Other messages in the received batch are
still attempted. The invocation reports failure if any message fails.

Duplicates and out-of-order delivery are possible. The publisher preserves a
stable ID for each Cosmos document version, but the subscriber does not yet
persist a deduplication ledger. Future handlers that write data or call APIs
must be idempotent. The current logging handler is safe to repeat. If extending
processing beyond the receive-lock duration, implement lock renewal or reduce
the batch size before using a slow model/API call.

There is no automatic dead-letter destination in this implementation. Configure
an approved dead-letter destination and monitor delivery failures before using
this for business-critical processing: repeated failures or TTL expiry can
result in dropped messages. A successful deployment or acknowledgement of the
logging handler is not proof that downstream model processing has happened.

The checked-in `intent_app_config.env` selects `eventgrid` for your namespace;
live execution and automatic provisioning remain disabled until you enable
them. `.env.example` retains `eventhub` as the backward-compatible example.

References: [Python Event Grid clients](https://learn.microsoft.com/en-us/python/api/overview/azure/eventgrid-readme?view=azure-python),
[namespace topic CLI](https://learn.microsoft.com/en-us/cli/azure/eventgrid/namespace/topic),
[pull subscription CLI](https://learn.microsoft.com/en-us/cli/azure/eventgrid/namespace/topic/event-subscription).

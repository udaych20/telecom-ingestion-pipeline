# Automatic live-mode setup

Run the same `python intent_app.py` command. Automatic setup creates missing
supporting Azure resources and publishes `event_app` to Azure. It does not require
Azure Functions Core Tools, and it does not leave a local Function host running.
This creates billable resources. It is disabled by default.

## Configuration

In `intent_app_config.env`:

```dotenv
INITIAL_LOAD_ENABLED=false
STREAM_LOAD_ENABLED=true
STREAM_AUTO_PROVISION=true
STREAM_DEPLOY_FUNCTION=true

AZURE_SUBSCRIPTION_ID=YOUR-SUBSCRIPTION-UUID
AZURE_RESOURCE_GROUP=YOUR-STREAM-RESOURCE-GROUP
AZURE_LOCATION=eastus
AZURE_COSMOS_RESOURCE_GROUP=YOUR-EXISTING-COSMOS-RESOURCE-GROUP
AZURE_COSMOS_ACCOUNT=YOUR-EXISTING-COSMOS-ACCOUNT
STREAM_STORAGE_ACCOUNT=youruniquestorageaccount
STREAM_FUNCTION_APP=your-unique-nora-functions

EVENT_HUB_NAMESPACE=YOUR-NAMESPACE.servicebus.windows.net
EVENT_HUB_NAME=nora-updates
EVENT_HUB_CONSUMER_GROUP=nora-update-subscriber
```

Replace the example values. Keep your existing `COSMOS_ENDPOINT`,
`COSMOS_DATABASE`, four source-container names and four lease-container names.
The Function's Cosmos endpoint comes from `COSMOS_ENDPOINT` in this mode.
Everything must be in the specified subscription (resource groups may differ).
Use an Azure region supporting Python 3.11 Flex Consumption.
If initial loading is also enabled, it completes before this setup begins.

## What is created

- The configured supporting resource group, if absent.
- Event Hubs namespace (Standard, one throughput unit), event hub (two partitions)
  and consumer group, if absent. Existing resources are reused, not resized.
- Function storage account (StorageV2, Standard_LRS, TLS 1.2, anonymous blob
  access disabled), if absent.
- Four Cosmos lease containers with partition key `/id`, if absent. Existing
  lease containers must have compatible partitioning and no default expiry.
- Dedicated Python 3.11 Flex Consumption Function App and its hosting resources,
  if absent. Azure CLI configures host/deployment storage during creation.
- System-assigned Function identity, Event Hubs sender/receiver permissions scoped
  to the hub, Cosmos data-reader permissions on the configured source containers,
  and data-contributor permissions on the lease containers.
- Allowlisted Function settings and the deployed Function code. Local `.env`,
  CSVs, checkpoints, credentials and `local.settings.json` are never packaged.

The Cosmos account, database and source containers must already exist. Setup
verifies these before creating supporting resources. It never recreates them,
changes their throughput or writes source documents. Adding lease containers
and data-role assignments is the only Cosmos provisioning it performs.
Optional training-artifact Blob/ADLS destinations are outside this live setup.

The existing live subscriber validates and logs change notifications. It does
not yet perform model inference, intent decomposition or NORA API execution.

## Prerequisites and limits

Install a current Azure CLI with Flex Consumption support and run `az login`.
The signed-in identity must be allowed to create resources, configure Functions,
create Azure role assignments, and manage Cosmos SQL containers/data-role
assignments. Reading Cosmos data alone is not enough. The app uses managed
identity for Cosmos and Event Hubs; the default CLI-created Function host and
deployment storage use storage-account keys. Your organization must allow this.

This setup targets public Azure endpoints. It does **not** configure private
endpoints, VNet integration, DNS, firewall exceptions or policy exemptions.
If the Cosmos account reports IP/VNet restrictions or disabled public access,
setup stops before creating resources and requests a network-aware deployment
from your Azure administrator. Network-restricted host storage also causes an
error. This automation does not bypass either firewall. Regional quotas, naming
collisions, provider registration and organizational policy may block creation.
It stops on errors, preserving resources already created so setup can be rerun.
It does not treat authorization or network failures as missing resources.

Existing Function Apps are reused only if tagged `managed-by=nora-intent-stream`
by this setup. An unrelated Function App causes an error before provisioning;
choose a fresh dedicated name. Do not add that tag to adopt an unrelated app.
Do not run two setup processes concurrently against the same resources.

Publishing uses Azure CLI remote build. Identical code is not republished on
later runs; changed code is deployed to the managed app. App settings are updated
only when their values change. Role propagation may take several minutes.
Deployment success is not proof of healthy change-feed processing: validate
trigger startup and send a test change before relying on the pipeline.
Application Insights is not automatically enabled; your administrator can attach
your approved monitoring resource for persistent Function logs and alerts.
Local setup progress goes to the normal console and pipeline log, without CLI
response bodies or secrets. Individual Azure commands have a 30-minute timeout
and a 15-second progress heartbeat.

## Manual deployment option

Set `STREAM_DEPLOY_FUNCTION=false` to create/configure resources without uploading
code. The program prints that manual publishing is required, then exits without
starting the local host. For a new Function App, no stream code runs until
deployment. For an existing app, its previously deployed code stays in place.
Set it back to `true` and rerun to publish automatically.

With `STREAM_AUTO_PROVISION=false`, the previous behavior remains: live mode
starts the local Functions host and expects resources/configuration to exist.

References: [Azure CLI Function App creation](https://learn.microsoft.com/en-us/cli/azure/functionapp#az-functionapp-create),
[Flex Consumption deployment](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to),
[Cosmos trigger connections](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-cosmosdb-v2-trigger).

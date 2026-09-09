# Live streaming

## Purpose

Live streaming complements the existing single-CID and batch commands. The Azure
Function observes creates and updates in the four Cosmos DB source containers and
publishes a small notification. The subscriber receives that notification,
resolves the affected CID, rebuilds the complete interaction from Cosmos DB, and
writes the normal outputs.

`LIVE_STREAM_DATA_ENABLED` controls only the subscriber. Keeping the publisher
running while the subscriber is disabled allows Event Hubs or Kafka retention to
hold changes until consumption resumes. Do not use this flag to make the Cosmos
trigger return successfully without publishing; doing that would advance the
change-feed lease and discard notifications.

The Python Cosmos DB trigger uses latest-version change feed mode. It observes
creates and the latest version of updates. It does not report deletes or every
intermediate update, and it cannot reliably distinguish a create from an update.

## Flow A: Azure Event Hubs

```text
Cosmos DB containers
        |
        | create or update (change feed)
        v
Azure Function + Cosmos leases
        |
        | CloudEvents-style notification
        v
Azure Event Hubs
        |
        | consumer group + Blob checkpoints
        v
app.py --live
        |
        `--> correlate from Cosmos --> existing CSV/JSONL outputs
```

Use a dedicated Event Hubs consumer group and a dedicated Blob container for this
application. The subscriber checkpoints only after processing succeeds. If a
record repeatedly fails, its Event Hubs partition is left uncheckpointed and
blocked until the service is restarted after the problem is corrected. Other
partitions can continue.

Production deployments should use managed identity. Grant the Function identity
Azure Event Hubs Data Sender and the subscriber identity Azure Event Hubs Data
Receiver plus the minimum Blob data role needed for checkpoint storage.

## Flow B: Apache Kafka

```text
Cosmos DB containers
        |
        | create or update (change feed)
        v
Azure Function + Cosmos leases
        |
        | keyed CloudEvents-style notification
        v
Kafka topic
        |
        | consumer group + committed offsets
        v
app.py --live
        |
        `--> correlate from Cosmos --> existing CSV/JSONL outputs
```

The producer enables idempotence and requires acknowledgements from all in-sync
replicas. The consumer disables automatic commits and commits an offset only after
processing and local event-ledger persistence succeed. A terminal processing
failure stops the Kafka subscriber so an orchestrator can restart it from the last
committed offset.

The Kafka flow works with Apache Kafka, a managed Kafka service, or the Kafka
endpoint exposed by Azure Event Hubs. TLS should remain enabled. Store SASL
credentials in Key Vault or the deployment platform's secret store, not in source
control.

## Change notification contract

The event intentionally excludes the full Cosmos document so customer content is
not copied into the broker. It carries only correlation and source metadata:

```json
{
  "specversion": "1.0",
  "id": "stable SHA-256 event identifier",
  "source": "/cosmos/NORA/chat-history-uat",
  "type": "com.nora.cosmos.document.created-or-updated",
  "subject": "cosmos-document-id",
  "time": "2026-09-09T10:15:30+00:00",
  "datacontenttype": "application/json",
  "data": {
    "database": "NORA",
    "container": "chat-history-uat",
    "document_id": "cosmos-document-id",
    "etag": "document-etag",
    "cids": ["conversation-id"],
    "cid_lists": [],
    "run_ids": ["run-id"]
  }
}
```

The event ID is derived from database, container, document ID, and ETag. Replayed
notifications therefore have the same ID. If an event contains only a run ID, the
subscriber queries the all-tools container to recover its CID. Events that cannot
be correlated are recorded in `unmatched_live_events.csv` and acknowledged.

## Delivery and idempotency

Both paths provide at-least-once delivery. Broker checkpoints prevent normal
re-reading, while `LIVE_STREAM_STATE_DATABASE` records successfully processed
event IDs and suppresses successful replays. The SQLite ledger is appropriate for
one subscriber deployment writing local files. If several replicas write to a
shared sink, replace it with a shared idempotency store and make the sink upsert by
event ID or interaction version.

There is a small failure window between appending output files and recording the
event in the ledger. A process failure in that window can create duplicate output.
Downstream consumers should retain `live_event.id` from `interactions.jsonl` as a
deduplication key.

## Provisioning requirements

Create these resources before enabling live processing:

1. Four Cosmos lease containers, one for each monitored source container, each
   partitioned by `/id`. Pre-provisioning is intentional because managed identity
   data-plane access cannot create containers.
2. An Azure Function App running Python 3.11 or later with the files from
   `azure_function/`.
3. Either an Event Hubs namespace and hub or a Kafka topic.
4. For Event Hubs consumption, a Storage account and dedicated Blob container for
   checkpoints.
5. Managed identities, private endpoints, firewall rules, and least-privilege RBAC
   appropriate to the environment.

The Function App setting `LIVE_STREAM_PROVIDER` accepts `event_hubs`, `kafka`, or
`both`. The subscriber accepts one provider per process. Run two subscriber
processes with separate state databases when consuming both paths for comparison.

## Start the subscriber

Set the provider-specific values in `.env`, then enable live processing:

```env
LIVE_STREAM_DATA_ENABLED=true
LIVE_STREAM_PROVIDER=event_hubs
```

Start the long-running process:

```powershell
python app.py --live
```

For Kafka, change `LIVE_STREAM_PROVIDER=kafka` and configure the Kafka variables.
The process is intended to run under Azure Container Apps, Kubernetes, systemd,
or another supervisor that restarts it after a terminal failure.

## Operations

- Alert on Function failures, blocked Event Hubs partitions, Kafka consumer exits,
  growing consumer lag, throttling, and unmatched change notifications.
- Keep broker retention longer than the longest expected subscriber outage.
- Treat lease containers, Blob checkpoints, Kafka offsets, and the processed-event
  ledger as operational state. Do not delete them during a normal deployment.
- Use a new consumer group and state database for intentional reprocessing.
- Rotate secrets through the platform secret store and prefer managed identity for
  Azure services.
- Load-test with realistic partition counts and Cosmos request units before enabling
  production traffic.

## Source guidance

- [Azure Cosmos DB change feed with Azure Functions](https://learn.microsoft.com/en-us/azure/cosmos-db/change-feed-functions)
- [Azure Event Hubs Python consumer and Blob checkpoints](https://learn.microsoft.com/en-us/azure/event-hubs/event-hubs-python-get-started-send)
- [Azure Event Hubs Kafka compatibility](https://learn.microsoft.com/en-us/azure/event-hubs/azure-event-hubs-apache-kafka-overview)
- [Confluent Python client delivery guarantees](https://docs.confluent.io/kafka-clients/python/current/overview.html)

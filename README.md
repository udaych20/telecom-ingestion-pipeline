# NORA interaction ingestion pipeline

A small Python pipeline that reconstructs a complete NORA user interaction from Azure Cosmos DB and prepares it for review, LLM training, or knowledge-graph loading.

## What it does

1. Reads CIDs from `chat_history.messages[].data.cid`.
2. Uses each message CID as the interaction correlation key.
3. Reads `context-history-all-tools` where `cid` matches the message CID.
4. Extracts its `run_id` values and reads `context-history-uat` using those run IDs.
5. Reads feedback where the chat CID appears in `feedbacks[].cid_list`.
6. Writes a readable interaction CSV and a complete JSONL audit log.
7. Optionally writes either LLM-training JSONL or graph node/edge CSV files.
8. Optionally consumes live Cosmos change notifications from Azure Event Hubs or Kafka.

Authentication uses `DefaultAzureCredential`; Cosmos account keys are not stored.

## Start here

- [Quickstart](docs/QUICKSTART.md)
- [Setup instructions](docs/SETUP.md)
- [System overview](docs/OVERVIEW.md)
- [Product specification](docs/SPECIFICATION.md)
- [Technical design](docs/DESIGN.md)
- [Workflow](docs/WORKFLOW.md)
- [Data contracts](docs/DATA_CONTRACTS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Testing](docs/TESTING.md)
- [Operations and troubleshooting](docs/OPERATIONS.md)
- [Security and privacy](docs/SECURITY.md)
- [Development process](docs/DEVELOPMENT.md)
- [Live streaming design and operations](docs/LIVE_STREAMING.md)

## Minimal run

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
az login
python app.py "CHAT-CID"
```

For a trial dataset run, set `BATCH_LIMIT=10` in `.env` and run `python app.py --all`. Set `BATCH_LIMIT=0` only when ready to process every chat.

To export only chat documents inserted or last updated during a time range, filter
their Cosmos `_ts` value. Timezone-aware values are converted to UTC, and an
omitted end time means the time when the process starts:

```powershell
python app.py --all --start-time "2026-09-04T18:00:00+05:30" --output-format csv
```

To export only fully joined interactions, run `python app.py --all-complete`. It writes an interaction only when chat, all-tools context, UAT context, and feedback are all present. Incomplete interactions are skipped.

The Azure identity needs the **Cosmos DB Built-in Data Reader** data-plane role.

## Live data

Historical single-ID and batch runs remain the default. To start a configured
live subscriber, set `LIVE_STREAM_DATA_ENABLED=true`, choose
`LIVE_STREAM_PROVIDER=event_hubs` or `kafka`, and run:

```powershell
python app.py --live
```

Live mode expects Cosmos change notifications produced by the Azure Function in
`azure_function/`. See [Live streaming](docs/LIVE_STREAMING.md) for the two
architectures, provisioning, delivery guarantees, and security requirements.

## Current limitation

The supplied feedback example does not show a `cid`. The current join assumes feedback documents contain `cid`; confirm the real feedback correlation field before production use.

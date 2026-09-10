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

## Minimal run

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
az login
python app.py "CHAT-CID"
```

For a trial dataset run, set `BATCH_LIMIT=10` in `.env` and run `python app.py --all`. Set `BATCH_LIMIT=0` only when ready to process every chat.

To export only fully joined interactions, run `python app.py --all-complete`. It writes an interaction only when chat, all-tools context, UAT context, and feedback are all present. Incomplete interactions are skipped.

The Azure identity needs the **Cosmos DB Built-in Data Reader** data-plane role.

## Intent classification timeframe export

Configure the Cosmos source and output in `intent_app_config.env`, then classify
only documents inserted or last updated from Friday evening through the time the
command starts:

```powershell
az login
python intent_app.py `
  --start-time "2026-09-04T18:00:00+05:30"
```

For a fixed inclusive end time:

```powershell
python intent_app.py `
  --start-time "2026-09-04T18:00:00+05:30" `
  --end-time "2026-09-09T18:00:00+05:30"
```

Both values must include a timezone. They are converted to UTC before the
parameterized Cosmos `_ts` query runs. When `--end-time` is omitted, the process
start time is used. The output path and CSV/JSONL format continue to come from
`INTENT_OUTPUT` in `intent_app_config.env`. Every run also creates the CSV named
by `INTENT_COUNT_OUTPUT`, containing classified-record and distinct-CID counts for
each intent plus an `ALL_INTENTS` total row.

## Compare old and recent intent CSVs

`intent_count_report.py` compares any number of classification CSV exports. It
accepts both the original `intent_app.py` columns and `test.py`'s flattened
`message.data.cid` column, so `test.py` does not need to change.

```powershell
python intent_count_report.py `
  --dataset "old_data=C:\path\old_intent_labels.csv" `
  --dataset "friday_to_yesterday=C:\path\friday_to_yesterday_labels.csv" `
  --output "intent_cid_count_report.csv"
```

The report contains input-row count, distinct CID count, and rows without a CID
for every intent and an `ALL_INTENTS` row for each named dataset.

## Current limitation

The supplied feedback example does not show a `cid`. The current join assumes feedback documents contain `cid`; confirm the real feedback correlation field before production use.

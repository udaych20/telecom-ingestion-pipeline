# NORA interaction ingestion pipeline

A small Python pipeline that reconstructs a complete NORA user interaction from Azure Cosmos DB and prepares it for review, LLM training, or knowledge-graph loading.

## What it does

1. Reads CIDs from `chat_history.messages[].cid`.
2. Uses each message CID as the interaction correlation key.
3. Reads both context containers where `run_id` references the chat `cid`.
4. Preserves the matching run IDs with the interaction.
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

## Current limitation

The supplied feedback example does not show a `cid`. The current join assumes feedback documents contain `cid`; confirm the real feedback correlation field before production use.

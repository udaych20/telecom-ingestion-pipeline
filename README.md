# NORA interaction ingestion pipeline

A small Python pipeline that reconstructs a complete NORA user interaction from Azure Cosmos DB and prepares it for review, LLM training, or knowledge-graph loading.

## What it does

1. Reads a chat document by `id`.
2. Extracts its `cid` values.
3. Reads tool history for each `cid` and extracts `run_id` values.
4. Reads related context and feedback.
5. Writes a readable interaction CSV and a complete JSONL audit log.
6. Optionally writes either LLM-training JSONL or graph node/edge CSV files.

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
python app.py "5638529c-2db7-45fe-8c2f-5dcfd46778c8"
```

The Azure identity needs the **Cosmos DB Built-in Data Reader** data-plane role.

## Current limitation

The supplied feedback example does not show a `cid`. The current join assumes feedback documents contain `cid`; confirm the real feedback correlation field before production use.

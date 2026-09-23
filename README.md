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

## Live NORA update events

For a command-by-command Azure Windows VM test, use
[`event_app/AZURE_WINDOWS_TEST.md`](event_app/AZURE_WINDOWS_TEST.md).

`event_app/` is an Azure Functions Python app that watches the NORA Cosmos DB
change feed. Each insert or update in the configured chat, tool, context, or
feedback container creates a compact CloudEvents-style notification in Azure
Event Hubs. The notification includes document, CID, and run identifiers, but not
the source document or message content.

The same source file contains an Event Hubs-triggered subscriber. Azure Functions
manages its consumer checkpoint and retries failed invocations.

### Azure prerequisites

Before testing, create one Cosmos lease container per source container,
partitioned by `/id`: `leases-chat`, `leases-tools`, `leases-context`, and
`leases-feedback`. Grant the Function identity Cosmos read/change-feed access,
lease-container write access, and **Azure Event Hubs Data Sender**. Grant the
subscriber **Azure Event Hubs Data Receiver**. Configure the values in
`event_app/local.settings.example.json`; use managed identity in Azure.

### Deploy

The Windows VM does not need Node.js, npm, Azurite, or Azure Functions Core
Tools. The runbook packages the three Function project files with PowerShell and
uses Azure CLI zip deployment with a remote Python build.

```powershell
Set-Location D:\git\telecom-ingestion-pipeline\event_app
az login
Compress-Archive -Path function_app.py,host.json,requirements.txt -DestinationPath function-app.zip -Force
az functionapp deployment source config-zip --resource-group YOUR-RESOURCE-GROUP --name YOUR-FUNCTION-APP --src function-app.zip --build-remote true
```

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

To select CIDs from a file, set `INTENT_CIDS_FROM_CSV=true`,
`INTENT_CID_CSV=input/cids.csv`, and `INTENT_CID_COLUMN=cid`. The CSV must have a
header, for example `cid`, followed by one CID per row. Blank CIDs are skipped,
duplicates are removed, and leading zeros are preserved. Missing files, missing
columns, and empty CID lists fail before connecting to Cosmos.

Cosmos still supplies the actual records. Timeframe and record-limit settings
still apply; the missing-record audit uses the same CID selection. If interaction
export is enabled, histories are joined for the selected classified CIDs. With
the flag false, the existing Cosmos discovery flow is unchanged. A document with
multiple CIDs follows the classifier's existing first-CID precedence, and a CID
without a matching source record produces no classified row. The flag affects
initial loads only, not the continuous change-feed triggers.

Set `INTENT_INCLUDE_INTERACTIONS=true` in `intent_app_config.env` to include
agent/context and tool-call source records in each classified row. The shared
`interaction_reader.py` uses the same joins as `app.py`: chat by message CID,
context by `run_id = CID`, tools by context run IDs, and feedback by CID list.
Configure the four `COSMOS_*_CONTAINER` settings for your source containers.

The new columns are `interaction.run_ids`, `interaction.chat_history`,
`interaction.context_history`, `interaction.tool_history`, and
`interaction.feedback`. Each contains JSON text preserving the original records,
including agent/tool payloads when present. No agent or tool schema is inferred.
Repeated CIDs share a lookup within each export pass. Missing chat or query
errors stop the export; absent context/tools/feedback are represented by empty
arrays. Linked histories are fetched in full, even when the initial classification
read uses a timeframe. This flag applies to both the normal and clarification CSVs
and to missing-record exports. Feature Build still trains on user text and intent
only, without these additional source payloads.

To separate clarification records, set `INTENT_SEPARATE_CLARIFICATION=true`.
The main classification CSV then contains all other intents, and
`INTENT_CLARIFICATION_OUTPUT` (default `intent_clarification_needed.csv`) contains
only `clarification_needed`, in `CSV_OUTPUT_DIR`. CSV has no worksheet support,
so these are two files with identical columns, including headers for empty groups.
Both files are included in optional Blob uploads. Counts, Cosmos write-back, and
Feature Build still receive all labels and retain their existing filtering rules.
The flag defaults to false and requires CSV output when enabled.

`intent_app.py` is the configuration-driven entry point for the initial and
streaming phases. Set `INITIAL_LOAD_ENABLED` and `STREAM_LOAD_ENABLED` in
`intent_app_config.env`. When both are true, the historical load completes first
and the command then starts the blocking Azure Functions host in `event_app/`.
Running the streaming phase locally requires Azure Functions Core Tools (`func`);
in Azure, deploy `event_app/` and let the Function App host own the triggers.

The initial phase writes the classified export and intent counts. It also writes
a dummy training manifest and log when `TRAINING_PLACEHOLDER_ENABLED=true`; this
records what would be trained but deliberately does not train a model. Set
`ADLS_UPLOAD_ENABLED=true` to upload these artifacts to the configured Blob/ADLS
container using `DefaultAzureCredential`. `INITIAL_BATCH_SIZE` controls the
Cosmos SDK page size.

With the supplied configuration, initial classification artifacts are written
before Feature Build under `output/csv/`, Foundry datasets under
`output/features/`, and the placeholder training log under `output/logs/`.

Set `FEATURE_BUILD_ENABLED=true` to create Microsoft Foundry chat-format
`foundry_train.jsonl`, `foundry_validation.jsonl`, and `foundry_test.jsonl`
files plus a rejection/count report. The deterministic split is performed by
CID to prevent conversation leakage. Feature build can mask common PII, remove
duplicates, exclude rows requiring review, and optionally require explicit
human approval. It prepares files only; it does not upload them to Foundry or
start a fine-tuning job. When Blob/ADLS upload is enabled, these files are
included with the other initial-load artifacts.

Set `FOUNDRY_TRAINING_ENABLED=true` to upload the generated train and validation
files and submit a supervised fine-tuning job through the configured Foundry
project. Submission is disabled by default because it creates a paid Azure job.
The job and file IDs are saved under `output/logs/foundry_training_job.json`.
The application does not deploy the resulting model; deployment remains a
separate approval-controlled operation.

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

Place these two input files in the repository root:

```text
intent_labels_old.csv
intent_labels_friday_to_yesterday.csv
```

Then copy and run this command from the repository root in PowerShell:

```powershell
python intent_count_report.py `
  --dataset "old_data=intent_labels_old.csv" `
  --dataset "friday_to_yesterday=intent_labels_friday_to_yesterday.csv" `
  --output "intent_cid_count_report.csv"
```

The command creates `intent_cid_count_report.csv` in the repository root. The
report contains input-row count, distinct CID count, and rows without a CID for
every intent and an `ALL_INTENTS` row for each named dataset.

## Current limitation

The supplied feedback example does not show a `cid`. The current join assumes feedback documents contain `cid`; confirm the real feedback correlation field before production use.

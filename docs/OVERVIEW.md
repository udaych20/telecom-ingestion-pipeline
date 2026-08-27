# System overview

## Purpose

The pipeline gathers records belonging to one customer interaction from multiple Cosmos DB containers. It keeps the original records for traceability and creates downstream-friendly files.

## Inputs and outputs

Input: one message CID, `--all` for every distinct `chat_history.messages[].data.cid`, `--all-complete` for only four-source interactions, or `--all-report` for every interaction plus relationship coverage metrics. Dataset CIDs are streamed in configured batches, and `BATCH_LIMIT` can restrict a trial.

Outputs:

| File | Created when | Purpose |
|---|---|---|
| `interactions.csv` | Always | Human-readable record inventory |
| `interactions.jsonl` | Always | Complete lossless interaction log |
| `llm_training.jsonl` | `INGESTION_MODE=llm` | User/assistant training examples |
| `graph_nodes.csv` | `INGESTION_MODE=knowledge_graph` | Graph nodes |
| `graph_edges.csv` | `INGESTION_MODE=knowledge_graph` | Graph relationships |
| `failed_interactions.csv` | When an ID fails | Interaction ID and error message |
| `interaction_coverage.csv` | `--all-report` | Per-CID source record counts and relationship flags |
| `coverage_summary.csv` | `--all-report` | Spreadsheet-friendly aggregate coverage metrics |
| `coverage_summary.json` | `--all-report` | Aggregate cross-container coverage metrics |

## Container relationship

```text
chat-history-uat.messages[].data.cid
        |
        | same value as run_id
        v
context-history-uat.run_id
        |
        | provides run_id values
        v
context-history-all-tools.run_id

chat-history-uat.messages[].data.cid
        |
        | contained in feedbacks[].cid_list
        v
chat-feedback
```

The implementation uses the message CID to query `context-history-uat.run_id`. Run IDs recursively found in those context records query `context-history-all-tools.run_id`. Feedback documents match through nested `feedbacks[].cid_list`. Duplicate documents are removed by document `id`.

## Scope

The current version is a command-line reader supporting single-interaction, complete-dataset, and complete-join-only runs. Dataset mode still performs correlation queries per interaction. Scheduling, change-feed processing, direct model fine-tuning, and direct graph-database writes are outside the current scope.

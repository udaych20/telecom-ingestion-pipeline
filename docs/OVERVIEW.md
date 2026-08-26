# System overview

## Purpose

The pipeline gathers records belonging to one customer interaction from multiple Cosmos DB containers. It keeps the original records for traceability and creates downstream-friendly files.

## Inputs and outputs

Input: one message CID, `--all` for every distinct `chat_history.messages[].data.cid`, or `--all-complete` for only four-source interactions. Dataset CIDs are streamed in configured batches, and `BATCH_LIMIT` can restrict a trial.

Outputs:

| File | Created when | Purpose |
|---|---|---|
| `interactions.csv` | Always | Human-readable record inventory |
| `interactions.jsonl` | Always | Complete lossless interaction log |
| `llm_training.jsonl` | `INGESTION_MODE=llm` | User/assistant training examples |
| `graph_nodes.csv` | `INGESTION_MODE=knowledge_graph` | Graph nodes |
| `graph_edges.csv` | `INGESTION_MODE=knowledge_graph` | Graph relationships |
| `failed_interactions.csv` | When an ID fails | Interaction ID and error message |

## Container relationship

```text
chat-history-uat.messages[].data.cid --> chat-feedback.feedbacks[].cid_list
        |
        | matched by cid
        +----------------> context-history-all-tools.cid
        |
        | yields run_id
        `----------------> context-history-uat.run_id
```

The message CID is matched to `context-history-all-tools.cid` and nested feedback CID lists. Run IDs are extracted from matching all-tools records and used to query `context-history-uat.run_id`. Duplicate documents are removed by document `id`.

## Scope

The current version is a command-line reader supporting single-interaction, complete-dataset, and complete-join-only runs. Dataset mode still performs correlation queries per interaction. Scheduling, change-feed processing, direct model fine-tuning, and direct graph-database writes are outside the current scope.

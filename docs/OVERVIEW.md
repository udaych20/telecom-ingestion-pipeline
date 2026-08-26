# System overview

## Purpose

The pipeline gathers records belonging to one customer interaction from multiple Cosmos DB containers. It keeps the original records for traceability and creates downstream-friendly files.

## Inputs and outputs

Input: one chat-history document `id` supplied on the command line.

Outputs:

| File | Created when | Purpose |
|---|---|---|
| `interactions.csv` | Always | Human-readable record inventory |
| `interactions.jsonl` | Always | Complete lossless interaction log |
| `llm_training.jsonl` | `INGESTION_MODE=llm` | User/assistant training examples |
| `graph_nodes.csv` | `INGESTION_MODE=knowledge_graph` | Graph nodes |
| `graph_edges.csv` | `INGESTION_MODE=knowledge_graph` | Graph relationships |

## Container relationship

```text
chat-history-uat.id
        |
        v
       cid ----------------------> chat-feedback.cid
        |
        v
context-history-all-tools
        |
        v
      run_id
        |
        v
context-history-uat
```

The context container is queried by both `cid` and `run_id`, then duplicate documents are removed by document `id`.

## Scope

The current version is a command-line, single-interaction batch reader. Scheduling, change-feed processing, direct model fine-tuning, and direct graph-database writes are outside the current scope.

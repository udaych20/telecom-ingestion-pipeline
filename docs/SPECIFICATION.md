# Product specification

Status: implemented baseline  
Owner: project team  
Last updated: 2026-08-26

## Problem

Interaction data is split across chat, tool-context, detailed-context, and feedback containers. A reviewer or downstream ingestion job needs one correlated representation without manually searching each container.

## Goals

- Retrieve one interaction from a chat `id`, or process every chat ID.
- Authenticate through Microsoft Entra ID using `DefaultAzureCredential`.
- Preserve original JSON documents.
- Produce a CSV that is easy to inspect.
- Produce either an LLM-training dataset or a knowledge-graph import dataset based on configuration.
- Avoid joining unrelated customer records.

## Non-goals

- Training or deploying a model.
- Selecting an LLM provider.
- Writing directly to Neo4j, Cosmos DB Gremlin, or another graph database.
- Guessing feedback relationships from email, device, or timestamp.
- Removing or masking personal data automatically in the baseline.

## Functional requirements

| ID | Requirement | Acceptance criterion |
|---|---|---|
| FR-01 | Accept a chat interaction ID | `python app.py <id>` starts the lookup |
| FR-02 | Read chat records | Matching chat documents are returned or a clear error is raised |
| FR-03 | Correlate by `cid` | All CID values are extracted from the chat record |
| FR-04 | Correlate contexts | Both context containers are queried where `run_id` references a chat CID |
| FR-05 | Read feedback | Feedback is queried by confirmed `cid` |
| FR-06 | Log complete data | Original records are retained in JSONL and in the CSV `data` column |
| FR-07 | Support LLM export | `INGESTION_MODE=llm` creates message-pair JSONL |
| FR-08 | Support graph export | `INGESTION_MODE=knowledge_graph` creates nodes and edges CSVs |
| FR-09 | Use keyless auth | Authentication is performed by `DefaultAzureCredential` |
| FR-10 | Process a complete dataset | `python app.py --all` processes all chat IDs and records per-ID failures |
| FR-11 | Limit a trial batch | A positive `BATCH_LIMIT` processes at most that many enumerated chat IDs |
| FR-12 | Export complete joins only | `--all-complete` exports only interactions with records from all four sources |
| FR-13 | Process in batches | Dataset IDs are handled in groups of `BATCH_SIZE` rather than loaded together |

## Non-functional requirements

- Queries must use SQL parameters for values.
- Cosmos account keys and access tokens must not be logged.
- Output must be UTF-8.
- Repeated context results must be de-duplicated.
- Container names and output mode must be configurable through environment variables.
- A failed interaction in dataset mode must not stop later interactions.
- An incomplete interaction in complete-only mode must be skipped without being logged as a failure.

## Acceptance scenario

Given an authorized Azure identity and a valid chat ID, running the command must create `interactions.csv` and `interactions.jsonl`. Given `--all`, it must attempt every ID permitted by `BATCH_LIMIT` and record failures without stopping the batch. If LLM mode is selected, valid user/assistant pairs must also appear in `llm_training.jsonl`. If graph mode is selected, conversation and run relationships must appear in the two graph CSVs.

## Open decisions

1. Confirm the actual feedback correlation field.
2. Confirm whether every production chat record contains `cid`; missing CID currently fails the interaction.
3. Define personal-data redaction requirements before training use.
4. Select the final LLM platform or graph database if direct ingestion is required.

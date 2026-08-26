# Product specification

Status: implemented baseline  
Owner: project team  
Last updated: 2026-08-26

## Problem

Interaction data is split across chat, tool-context, detailed-context, and feedback containers. A reviewer or downstream ingestion job needs one correlated representation without manually searching each container.

## Goals

- Retrieve one interaction from `chat_history.messages[].data.cid`, or process every distinct message CID.
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
| FR-01 | Accept a message CID | `python app.py <cid>` starts the lookup |
| FR-02 | Read chat records | Chat documents containing `messages[].data.cid` are returned or a clear error is raised |
| FR-03 | Correlate by `cid` | Distinct CIDs are enumerated from the nested messages array |
| FR-04 | Correlate contexts | Both context containers are queried where `run_id` references a chat CID |
| FR-05 | Read feedback | Feedback is queried where `feedbacks[].cid_list` contains the chat CID |
| FR-06 | Log complete data | Original records are retained in JSONL and in the CSV `data` column |
| FR-07 | Support LLM export | `INGESTION_MODE=llm` creates message-pair JSONL |
| FR-08 | Support graph export | `INGESTION_MODE=knowledge_graph` creates nodes and edges CSVs |
| FR-09 | Use keyless auth | Authentication is performed by `DefaultAzureCredential` |
| FR-10 | Process a complete dataset | `python app.py --all` processes all distinct message CIDs and records failures |
| FR-11 | Limit a trial batch | A positive `BATCH_LIMIT` processes at most that many enumerated chat CIDs |
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

Given an authorized Azure identity and a valid chat CID, running the command must create `interactions.csv` and `interactions.jsonl`. Given `--all`, it must attempt every CID permitted by `BATCH_LIMIT` and record failures without stopping the batch. If LLM mode is selected, valid user/assistant pairs must also appear in `llm_training.jsonl`. If graph mode is selected, conversation and run relationships must appear in the two graph CSVs.

## Open decisions

1. Confirm whether every production chat record contains `cid`; missing CID currently fails the interaction.
2. Define personal-data redaction requirements before training use.
3. Select the final LLM platform or graph database if direct ingestion is required.

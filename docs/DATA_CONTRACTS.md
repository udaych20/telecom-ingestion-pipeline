# Data contracts

## Source assumptions

| Container | Required link | Observed content |
|---|---|---|
| Chat history | `messages[].data.cid` | User content, assistant response, conversation details |
| Context history — UAT | `run_id` matches the message CID; provides run IDs | Agent/tool execution context |
| Context history — all tools | `run_id` matches a run ID found in UAT context | Function name, arguments, result, error |
| Chat feedback | `feedbacks[].cid_list` contains chat `cid` | User feedback and review details |

Fields may be nested. The script recursively searches for `cid`, `run_id`, and supported text fields.

The message CID queries `context-history-uat.run_id`. Run IDs recursively found in those matches query `context-history-all-tools.run_id`. Feedback matches when a nested `feedbacks[].cid_list` contains the message CID. Results are de-duplicated by document `id`.

## Coverage reports

`interaction_coverage.csv` has one row per successfully analyzed chat CID. It records source-document counts, Boolean match flags, the number of referenced containers matched (zero through three), and whether all four containers are present.

`coverage_summary.csv` and `coverage_summary.json` aggregate those rows. The CSV is intended for direct review in a spreadsheet; the JSON also records the exact join path. Failed CIDs are counted separately and are excluded from coverage percentages because their relationships could not be fully evaluated. All coverage files are overwritten by each `--all-report` run.

## Complete interaction object

```json
{
  "interaction_id": "chat-cid",
  "retrieved_at": "UTC timestamp",
  "cids": ["conversation-id"],
  "run_ids": ["run-id"],
  "chat_history": [],
  "tool_history": [],
  "context_history": [],
  "feedback": []
}
```

## Interaction CSV

One row represents one source document.

| Column | Meaning |
|---|---|
| `interaction_id` | Starting chat CID |
| `cid` | Record CID, or discovered interaction CIDs |
| `run_id` | Record run ID when present |
| `source` | Source collection in the assembled interaction |
| `record_id` | Cosmos document ID |
| `data` | Complete original document as JSON |

## LLM JSONL

Each line contains `messages` with one user message and one assistant message, plus source metadata. User text is selected from `user_content`, `user_message`, `query`, or `issue`. Assistant text is selected from `assistant_content`, `assistant_response`, or `response`. Records without both sides are skipped.

This is a preparation format, not approval for training. Personal-data review and target-model validation are required first.

## Knowledge graph CSV

Node labels are `Interaction`, `Conversation`, and `Run`. Relationships are:

```text
(Interaction)-[:HAS_CONVERSATION]->(Conversation)
(Conversation)-[:HAS_RUN]->(Run)
```

The CSVs are neutral interchange files; target-specific import headers or commands may still be needed.

## Failure CSV

`failed_interactions.csv` contains `interaction_id` and `error`. It is append-only and may contain repeated failures across reruns.

## Complete-interaction rule

For `--all-complete`, `chat_history`, `tool_history`, `context_history`, and `feedback` must each contain at least one record. The rule checks presence, not semantic quality or record counts.

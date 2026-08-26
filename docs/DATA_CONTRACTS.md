# Data contracts

## Source assumptions

| Container | Required link | Observed content |
|---|---|---|
| Chat history | `id`, preferably `cid` | User content, assistant response, conversation details |
| Tool history | `cid`, `run_id` | Function name, arguments, result, error |
| Context history | `cid` and/or `run_id` | Agent/tool execution context |
| Chat feedback | Assumed `cid` | User feedback and review details |

Fields may be nested. The script recursively searches for `cid`, `run_id`, and supported text fields.

## Complete interaction object

```json
{
  "interaction_id": "chat-id",
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
| `interaction_id` | Starting chat ID |
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

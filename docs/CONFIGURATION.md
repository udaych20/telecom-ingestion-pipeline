# Configuration reference

Configuration is loaded from process environment variables and an optional `.env` file. Process environment values take precedence.

| Variable | Required | Default | Description |
|---|---:|---|---|
| `COSMOS_ENDPOINT` | Yes | None | Cosmos account endpoint |
| `COSMOS_DATABASE` | No | `NORA` | Database name |
| `COSMOS_CHAT_CONTAINER` | No | `chat-history-uat` | Chat container |
| `COSMOS_TOOLS_CONTAINER` | No | `context-history-all-tools` | Tool-history container |
| `COSMOS_CONTEXT_CONTAINER` | No | `context-history-uat` | Detailed-context container |
| `COSMOS_FEEDBACK_CONTAINER` | No | `chat-feedback` | Feedback container |
| `OUTPUT_DIR` | No | `output` | Output directory |
| `INGESTION_MODE` | No | `none` | `none`, `llm`, or `knowledge_graph` |
| `BATCH_LIMIT` | No | `0` | Maximum chats for `--all`; `0` means every chat |
| `BATCH_SIZE` | No | `100` | Chat IDs processed in each in-memory batch |

Do not place client secrets, access tokens, or Cosmos account keys in `.env`. `DefaultAzureCredential` provides authentication.

`BATCH_LIMIT` and `BATCH_SIZE` affect `--all` and `--all-complete`, not a single chat CID. `BATCH_LIMIT` must be non-negative and `BATCH_SIZE` must be greater than zero. Environment settings are loaded once when the process starts.

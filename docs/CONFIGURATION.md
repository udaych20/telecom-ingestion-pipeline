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

Do not place client secrets, access tokens, or Cosmos account keys in `.env`. `DefaultAzureCredential` provides authentication.

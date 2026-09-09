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
| `MAX_WORKERS` | No | `10` | Parallel Cosmos lookups in historical batch mode |
| `LIVE_STREAM_DATA_ENABLED` | No | `false` | Enables `python app.py --live` when `true` |
| `LIVE_STREAM_PROVIDER` | No | `event_hubs` | Subscriber transport: `event_hubs` or `kafka` |
| `LIVE_STREAM_STATE_DATABASE` | No | `OUTPUT_DIR/live_stream_state.db` | Successful event-ID ledger |
| `LIVE_STREAM_MAX_PROCESSING_ATTEMPTS` | No | `5` | Processing attempts before the subscriber blocks or exits |
| `LIVE_STREAM_RETRY_INITIAL_SECONDS` | No | `1` | Initial exponential retry delay |
| `LIVE_STREAM_EVENT_HUB_NAMESPACE` | Event Hubs | None | Fully qualified Event Hubs namespace |
| `LIVE_STREAM_EVENT_HUB_NAME` | Event Hubs | None | Event hub name |
| `LIVE_STREAM_EVENT_HUB_CONSUMER_GROUP` | No | `$Default` | Dedicated consumer group |
| `LIVE_STREAM_EVENT_HUB_STARTING_POSITION` | No | `@latest` | Initial position when no checkpoint exists |
| `LIVE_STREAM_CHECKPOINT_STORAGE_ACCOUNT_URL` | Event Hubs | None | Blob account URL used for checkpoints |
| `LIVE_STREAM_CHECKPOINT_BLOB_CONTAINER` | Event Hubs | None | Dedicated checkpoint Blob container |
| `LIVE_STREAM_KAFKA_BOOTSTRAP_SERVERS` | Kafka | None | Comma-separated Kafka brokers |
| `LIVE_STREAM_KAFKA_TOPIC` | Kafka | None | Change notification topic |
| `LIVE_STREAM_KAFKA_CONSUMER_GROUP` | No | `telecom-ingestion-live` | Kafka consumer group |
| `LIVE_STREAM_KAFKA_CLIENT_ID` | No | `telecom-ingestion-pipeline` | Kafka client identifier |
| `LIVE_STREAM_KAFKA_AUTO_OFFSET_RESET` | No | `latest` | Initial offset when no committed offset exists |
| `LIVE_STREAM_KAFKA_SECURITY_PROTOCOL` | No | `SASL_SSL` | Kafka transport security |
| `LIVE_STREAM_KAFKA_SASL_MECHANISM` | Kafka/SASL | None | SASL mechanism, such as `PLAIN` |
| `LIVE_STREAM_KAFKA_USERNAME` | Kafka/SASL | None | Injected Kafka username |
| `LIVE_STREAM_KAFKA_PASSWORD` | Kafka/SASL | None | Injected Kafka password |
| `LIVE_STREAM_KAFKA_CA_LOCATION` | No | None | Custom CA bundle path |

Do not place client secrets, access tokens, or Cosmos account keys in `.env`. `DefaultAzureCredential` provides authentication.

`BATCH_LIMIT` and `BATCH_SIZE` affect `--all` and `--all-complete`, not a single chat CID. `BATCH_LIMIT` must be non-negative and `BATCH_SIZE` must be greater than zero. Environment settings are loaded once when the process starts.

Historical commands accept `--start-time` and `--end-time` as ISO 8601 values
with an explicit timezone. The bounds are inclusive and filter the chat document's
Cosmos-managed `_ts` value. If `--end-time` is omitted, the process start time is
used. Use `--output-format csv`, `jsonl`, or `both`; the default is `both`.

`LIVE_STREAM_DATA_ENABLED` does not change historical runs. It gates only
`--live`. Event Hubs uses `DefaultAzureCredential`; Kafka secrets should be
injected by the host or a secret store instead of being committed to `.env`.

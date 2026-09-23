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

## Intent Finder initial-load and feature outputs

`intent_app.py` uses the following settings from `intent_app_config.env`:

| Variable | Default | Description |
|---|---|---|
| `CSV_OUTPUT_DIR` | `output/csv` | Classified, count, missing-record, and manifest CSV directory |
| `INTENT_CIDS_FROM_CSV` | `false` | Restrict initial Cosmos reads and audits to CIDs from a CSV |
| `INTENT_CID_CSV` | `input/cids.csv` | Input CSV containing selected CIDs |
| `INTENT_CID_COLUMN` | `cid` | CID column header in the input file |
| `INTENT_INCLUDE_INTERACTIONS` | `false` | Attach CID-linked chat, agent/context, tool history, and feedback using the shared app.py joins |
| `INTERACTION_BATCH_SIZE` | `100` | Unique CIDs submitted in each interaction batch; must be positive |
| `INTERACTION_MAX_WORKERS` | `10` | Concurrent interaction fetch workers; writes use a single thread |
| `INTENT_SEPARATE_CLARIFICATION` | `false` | Write clarification_needed to a separate CSV; other intents stay in the main CSV |
| `INTENT_CLARIFICATION_OUTPUT` | `intent_clarification_needed.csv` | Separate clarification filename in CSV_OUTPUT_DIR |
| `FEATURE_OUTPUT_DIR` | `output/features` | Foundry train, validation, test, and feature-report directory |
| `LOG_OUTPUT_DIR` | `output/logs` | Training-placeholder log directory |
| `FEATURE_BUILD_ENABLED` | `false` | Generate Foundry-compatible feature datasets after the classified CSV is written |
| `TRAIN_PERCENT` | `80` | CID-level training split percentage |
| `VALIDATION_PERCENT` | `10` | CID-level validation split percentage |
| `TEST_PERCENT` | `10` | CID-level test split percentage |
| `FEATURE_REDACT_PII` | `true` | Mask common email, phone/account, and device identifiers |
| `FEATURE_EXCLUDE_NEEDS_REVIEW` | `true` | Exclude classifications marked for human review |
| `FEATURE_REQUIRE_REVIEWED_LABELS` | `false` | Require an explicit approved/accepted review marker |
| `FOUNDRY_TRAINING_ENABLED` | `false` | Upload datasets and submit a paid Foundry fine-tuning job |
| `FOUNDRY_PROJECT_ENDPOINT` | None | Foundry project endpoint used by `DefaultAzureCredential` |
| `FOUNDRY_FINE_TUNE_MODEL` | None | Supported base model and version to fine-tune |
| `FOUNDRY_FINE_TUNE_SUFFIX` | `nora-intent` | Name suffix for the customized model |
| `FOUNDRY_FINE_TUNE_TRAINING_TYPE` | `GlobalStandard` | Foundry training type |
| `FOUNDRY_TRAINING_RECEIPT` | `foundry_training_job.json` | Local job/file ID receipt |

Bare output filenames are placed in the corresponding directory. Absolute paths
and configured paths that already contain a directory are preserved. The three
split percentages must total 100.

Automated training requires `FEATURE_BUILD_ENABLED=true`, at least ten accepted
training examples, and at least one validation example. It submits training but
does not deploy the completed model.

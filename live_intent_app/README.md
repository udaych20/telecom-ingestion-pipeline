# Live intent inference

This project implements one live processing flow:

```text
Azure Cosmos DB insert/update
  -> Cosmos change feed
  -> Azure Function running locally
  -> Kafka running on Windows
  -> Cloud Foundry SLM
  -> CSV
```

The existing `../intent_app.py` is unchanged. Run all commands from this folder.

Follow [QUICKSTART_WINDOWS.md](QUICKSTART_WINDOWS.md) for the end-to-end test,
verification, and targeted change-feed bypass.

## Main files

- `insert_cosmos_test_document.py` inserts a unique Cosmos test conversation.
- `function_app.py` handles the change feed and publishes to Kafka.
- `kafka_worker.py` consumes Kafka events and invokes inference.
- `inference.py` calls the Cloud Foundry OpenAI-compatible endpoint.
- `pipeline.py` masks PII and saves inference results to CSV.
- `replay_cosmos_to_kafka.py` bypasses only a failed change feed/local Function.
- `check_demo_readiness.py` checks external dependencies before a demonstration.
- `verify_demo.py` reports evidence for each owned stage.
- `provision.py` creates missing Cosmos and Function-storage resources.
- `scripts/setup-openjdk-kafka.ps1` installs verified OpenJDK and Kafka archives.
- `scripts/start-kafka.ps1` starts the local Kafka broker.
- `scripts/create-kafka-topic.ps1` creates the application topic.
- `scripts/run-demo.ps1` runs either the live or targeted-bypass demonstration.

The model must return JSON containing `intent`, `confidence`, and `reason`.
Allowed intents are `ticket`, `modify`, `rca`, `query`, `general`, and
`clarification_needed`.

# Live intent inference

This project implements one live processing flow:

```text
Azure Cosmos DB insert/update
  -> Cosmos change feed
  -> Azure Function running locally
  -> Cloud Foundry SLM
  -> CSV
  -> Kafka running on Windows
```

The existing `../intent_app.py` is unchanged. Run all commands from this folder.

Choose the appropriate standalone guide:

- [QUICKSTART_LIVE_WINDOWS.md](QUICKSTART_LIVE_WINDOWS.md) for the actual flow.
- [QUICKSTART_DEMO_WINDOWS.md](QUICKSTART_DEMO_WINDOWS.md) for the bypass flow.

## Main files

- `insert_cosmos_test_document.py` inserts a unique Cosmos test conversation.
- `function_app.py` handles the change feed, inference, CSV, and Kafka publish.
- `inference.py` calls the Cloud Foundry OpenAI-compatible endpoint.
- `pipeline.py` masks PII and saves inference results to CSV.
- `replay_cosmos_to_kafka.py` runs inference and CSV before the demo Kafka publish.
- `check_demo_readiness.py` checks external dependencies before a demonstration.
- `verify_demo.py` reports evidence for each owned stage.
- `provision.py` creates missing Cosmos and Function-storage resources.
- `scripts/setup-openjdk-kafka.ps1` installs verified OpenJDK and Kafka archives.
- `scripts/start-kafka.ps1` starts the local Kafka broker.
- `scripts/create-kafka-topic.ps1` creates the application topic.
- `scripts/insert-cosmos-demo.ps1` inserts and displays one Cosmos demo record.
- `scripts/run-live-flow.ps1` waits for and verifies the live Function flow.
- `scripts/run-demo-flow.ps1` runs and verifies the targeted bypass.

The model must return JSON containing `intent`, `confidence`, and `reason`.
Allowed intents are `ticket`, `modify`, `rca`, `query`, `general`, and
`clarification_needed`.

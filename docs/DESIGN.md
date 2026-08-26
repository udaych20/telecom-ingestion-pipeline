# Technical design

## Design principles

- Keep the implementation small and readable.
- Preserve source records before transforming them.
- Prefer explicit identifiers over probabilistic joins.
- Separate extraction mode from downstream platform choice.

## Components

`app.py` contains four small responsibilities:

- Cosmos querying and identifier discovery.
- Interaction assembly.
- Complete CSV/JSONL logging.
- Mode-specific LLM or graph export.
- Single-ID or dataset orchestration with per-ID failure logging.
- Complete-only filtering after all four source queries finish.

`DefaultAzureCredential` chooses the available identity source. Locally this is normally Azure CLI; in Azure it should be managed identity or workload identity.

## Query design

The pipeline performs cross-partition parameterized queries because partition keys were not provided. Chat CIDs are enumerated and matched through `messages[].cid`. Each message CID is queried as `run_id` in both context containers. Feedback uses an `EXISTS` subquery with `ARRAY_CONTAINS` against `feedbacks[].cid_list`. Dataset mode reuses one Cosmos client and streams IDs in bounded in-memory batches, but still performs queries per interaction. This is suitable for validation; high-volume production ingestion should use known partition keys, bulk concurrency, or the Cosmos change feed.

Complete-only filtering happens after correlation. An interaction is saved only when all four assembled lists are non-empty. A missing source is a normal skip; query or export exceptions remain failures.

## Failure behavior

- Missing endpoint or argument: show usage and exit before connecting.
- Chat not found: record a clear failure for that interaction.
- Authentication, authorization, throttling, network, and export errors inside an interaction: record the error in `failed_interactions.csv` and continue.
- Failure to connect or enumerate chat CIDs: stop the run because there is no correlation key to process.
- Unknown ingestion mode: write the base logs, record each attempted interaction as failed, and continue.

## Idempotency

Files are append-only, so rerunning an ID or dataset creates duplicate rows. This is useful for audit history but not idempotent dataset creation. Production ingestion should add a run ID, deduplication step, or overwrite policy.

## Future design options

- Use the Cosmos change feed for continuous ingestion.
- Add checkpointing and retry policy for bulk workloads.
- Add schema validation before export.
- Add a redaction layer before LLM output.
- Add direct adapters only after choosing the target platform.

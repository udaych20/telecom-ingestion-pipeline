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

`DefaultAzureCredential` chooses the available identity source. Locally this is normally Azure CLI; in Azure it should be managed identity or workload identity.

## Query design

The pipeline performs cross-partition parameterized queries because partition keys were not provided. Each correlation value is queried separately. This is simple and correct for a single-interaction CLI, but higher-volume ingestion should batch differently or use known partition keys.

## Failure behavior

- Missing endpoint or argument: show usage and exit.
- Chat not found: raise a clear `ValueError`.
- Authentication, authorization, throttling, or network errors: allow the Azure SDK error to surface.
- Unknown ingestion mode: write the base logs, then raise a configuration error.

## Idempotency

Files are append-only, so rerunning the same ID creates duplicate rows. This is useful for audit history but not idempotent dataset creation. Production batch ingestion should add a run ID, deduplication step, or overwrite policy.

## Future design options

- Use the Cosmos change feed for continuous ingestion.
- Add checkpointing and retry policy for bulk workloads.
- Add schema validation before export.
- Add a redaction layer before LLM output.
- Add direct adapters only after choosing the target platform.

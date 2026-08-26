# Operations and troubleshooting

## Normal operation

Run one chat ID at a time. The script prints the assembled interaction and appends output files under `OUTPUT_DIR`.

For dataset validation, run `python app.py --all`. Use a small `BATCH_LIMIT` first because cross-partition queries consume Cosmos request units. Set it to `0` only when ready for the complete dataset.

## Common failures

| Symptom | Likely cause | Action |
|---|---|---|
| Credential unavailable | No Azure CLI login or managed identity | Run `az login` locally or configure managed identity |
| HTTP 403 | Missing Cosmos data-plane role | Assign Cosmos DB Built-in Data Reader |
| Chat not found | Wrong ID, database, container, or environment | Verify `.env` and query the source container |
| Empty tool/context output | Missing or differently named correlation field | Inspect representative source JSON |
| Empty feedback | Feedback does not use `cid` | Confirm and implement its actual link field |
| Duplicate output | Append-only rerun | Remove/archive output or add downstream deduplication |
| HTTP 429 | Cosmos throttling | Retry later; add SDK retry/batch controls for scale |

## Output retention

Outputs can contain customer information. Store them only in an approved location, restrict access, define retention, and securely remove expired files according to organizational policy.

## Production readiness checklist

- Correlation fields confirmed against real schemas.
- Least-privilege identity assigned.
- Private networking/firewall access verified.
- Personal-data policy approved.
- Output storage encrypted and access controlled.
- Monitoring and run-level audit identifiers added.
- Retry, checkpointing, and idempotency designed for batch volume.

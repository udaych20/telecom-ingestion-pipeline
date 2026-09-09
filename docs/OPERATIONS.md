# Operations and troubleshooting

## Normal operation

For targeted validation, run one chat CID at a time. The script prints progress and appends output files under `OUTPUT_DIR`.

For dataset validation, run `python app.py --all`. Use a small `BATCH_LIMIT` first because cross-partition queries consume Cosmos request units. Set it to `0` only when ready for the complete dataset.

Use `python app.py --all-complete` when downstream data must contain all four sources. Skipped records mean at least one source had no matching record; they are counted but intentionally not written.

## Common failures

| Symptom | Likely cause | Action |
|---|---|---|
| Credential unavailable | No Azure CLI login or managed identity | Run `az login` locally or configure managed identity |
| HTTP 403 | Missing Cosmos data-plane role | Assign Cosmos DB Built-in Data Reader |
| Chat not found | Wrong ID, database, container, or environment | Verify `.env` and query the source container |
| Empty tool history | No all-tools record has the message CID in `cid` | Confirm the all-tools CID field |
| Empty context history | All-tools has no run ID, or UAT has no matching `run_id` | Inspect the run-ID bridge |
| Empty feedback | CID is absent from `feedbacks[].cid_list` or schema differs | Inspect the nested feedback array |
| Duplicate output | Append-only rerun | Remove/archive output or add downstream deduplication |
| Entries in failure CSV | Per-interaction query or export error | Review the error, correct the cause, and rerun the affected ID |
| Batch stops before processing IDs | Connection or chat-ID enumeration failed | Check credentials, endpoint, role, and network access |
| HTTP 429 | Cosmos throttling | Retry later; add SDK retry/batch controls for scale |
| `--live` refuses to start | `LIVE_STREAM_DATA_ENABLED` is false | Set it to `true` after configuring a provider |
| Event Hubs partition blocked | One event exhausted processing retries | Correct the failure and restart from the Blob checkpoint |
| Kafka subscriber exits | Broker or processing failure before offset commit | Correct the failure and let the supervisor restart it |
| Live event is unmatched | Notification had no resolvable CID | Review `unmatched_live_events.csv` and correlation fields |
| Duplicate live output | Process stopped between output append and ledger update | Deduplicate by `live_event.id`; use an idempotent shared sink for multiple replicas |

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
- Trial batch completed before an unrestricted `BATCH_LIMIT=0` run.
- Dedicated change-feed leases, consumer group, and checkpoint storage provisioned.
- Broker retention and lag alerts configured for the maximum expected outage.
- Live subscriber runs under a supervisor with restart and health monitoring.

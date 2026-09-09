# Testing strategy

## Required checks

### Static check

```powershell
python -m py_compile app.py
```

### Unit cases

- Recursive `cid` extraction from dictionaries and lists.
- Multiple and duplicate `run_id` values.
- All-tools context queried with `cid = message cid`.
- UAT context queried using every run ID returned by all-tools context.
- Feedback queried through nested `feedbacks[].cid_list`.
- Multiple feedback items and multiple CIDs in one `cid_list`.
- Missing chat CID produces a recorded interaction failure.
- Context de-duplication by document ID.
- CSV header creation and row appending.
- LLM export skips incomplete message pairs.
- Graph export creates the expected nodes and edges.
- Invalid ingestion mode fails clearly.
- `BATCH_LIMIT=0`, a positive limit, and single-ID behavior.
- Batch boundaries for fewer than, exactly, and more than `BATCH_SIZE` IDs.
- Time-range parsing requires a timezone and normalizes values to UTC.
- Historical time ranges apply inclusive `_ts` parameters to the chat query.
- CSV-only export does not create the assembled interaction JSONL file.
- Complete-only mode exports all four sources and skips every incomplete combination.
- Batch processing continues after one interaction fails.
- Live mode is rejected while `LIVE_STREAM_DATA_ENABLED=false`.
- Live events with direct CIDs and run-ID-only correlation are resolved.
- Invalid event envelopes are rejected before a checkpoint or offset commit.
- Event Hubs checkpoints occur only after processing succeeds.
- Kafka auto-commit is disabled and offsets commit only after processing succeeds.
- Replayed stable event IDs do not append outputs again.

### Integration cases

Use a non-production Cosmos database containing known linked fixtures:

- Valid interaction with one CID and one run.
- Interaction with multiple CIDs and runs.
- Missing `messages[].data.cid`.
- Multiple messages sharing a CID and multiple CIDs in one chat document.
- Chat without CID.
- Context returned by both CID and run ID.
- Feedback with and without the confirmed correlation field.
- Complete-dataset enumeration with a small positive `BATCH_LIMIT`.

## Output validation

- Open `interactions.csv` in a spreadsheet and confirm UTF-8 content is readable.
- Parse every JSONL line as JSON.
- Confirm source document counts against Cosmos queries.
- Confirm no access tokens or credentials appear in outputs.
- Before training, review samples for personal or sensitive data.
- Compare the final succeeded/failed counts with output and failure records.

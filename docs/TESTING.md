# Testing strategy

## Required checks

### Static check

```powershell
python -m py_compile app.py
```

### Unit cases

- Recursive `cid` extraction from dictionaries and lists.
- Multiple and duplicate `run_id` values.
- Run IDs discovered from either context container.
- Both context containers queried by CID and discovered run IDs.
- Context de-duplication by document ID.
- CSV header creation and row appending.
- LLM export skips incomplete message pairs.
- Graph export creates the expected nodes and edges.
- Invalid ingestion mode fails clearly.
- `BATCH_LIMIT=0`, a positive limit, and single-ID behavior.
- Batch processing continues after one interaction fails.

### Integration cases

Use a non-production Cosmos database containing known linked fixtures:

- Valid interaction with one CID and one run.
- Interaction with multiple CIDs and runs.
- Missing chat ID.
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

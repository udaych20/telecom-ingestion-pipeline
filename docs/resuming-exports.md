# Resuming a stopped CID export

Back up the existing classification CSVs first. In `intent_app_config.env`, set:

```dotenv
INTENT_CIDS_FROM_CSV=true
INTENT_RESUME=true
INTENT_MAX_RECORDS=
```

Run `python intent_app.py` with the same CID input, source settings and output
paths. Keep `INTENT_INCLUDE_INTERACTIONS=true` if the original run included
interactions. Keep the clarification CSV as well when separate output is enabled.
For time-filtered runs use the original explicit start and end times.

The log reports completed CIDs skipped and CIDs remaining. Workers finish out of
order, so resumption uses CIDs rather than a batch number. Count and feature
outputs are rebuilt from saved and newly fetched labels. The optional missing
record audit covers only the remaining CIDs in the resumed run.

New unlimited CID-to-CSV runs create an adjacent `.checkpoint.sqlite3` file.
Keep it with the CSVs. Completion is recorded after output is flushed to disk
and optional write-back succeeds, including CIDs that return zero records.
Uncommitted rows are removed on resume so those CIDs can be retried.
Do not run two processes against the same output files.

For an older run without a checkpoint, existing CSV CIDs are imported as complete.
This is suitable for the reported batch-failure shutdown, which leaves saved
rows intact. It cannot identify zero-result CIDs, so those are queried again.
Do not use legacy import after a forced shutdown during a CSV write: a partial
CID may appear complete. Restore a known complete backup first.

Resume currently requires CSV output, CSV-sourced CIDs and no record limit.
Leaving `INTENT_RESUME=false` starts a fresh export and replaces the outputs.
Cosmos connections in the selected-CID worker pool are now worker-local to avoid
sharing mutable response headers between concurrent queries.

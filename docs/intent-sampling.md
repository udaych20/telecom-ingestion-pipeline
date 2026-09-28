# Samples per intent

Set these values in `intent_app_config.env`, then run `python intent_app.py`:

```dotenv
INITIAL_LOAD_ENABLED=true
STREAM_LOAD_ENABLED=false
INTENT_SAMPLING_ENABLED=true
INTENT_SAMPLES_PER_INTENT=1000
INTENT_INCLUDE_INTERACTIONS=true
INTENT_MAX_RECORDS=
INTENT_RESUME=false
```

Choose fresh classification/count-output filenames to preserve earlier exports.
Fresh runs replace their configured output files, including the clarification
CSV when separate output is enabled.

The quota applies independently to ticket, modify, rca, query, general, and
clarification_needed: at most 6,000 rows total. A sample is a classified source
row, not a distinct CID. Multiple rows may belong to one conversation. Existing
clarification splitting still applies. Interactions are attached to selected
rows in the same classification CSV. Missing interaction records remain empty;
sampling does not guarantee every CID has chat, context, tools and feedback.

The initial start/end times still filter source records by Cosmos `_ts`, and
CSV CID selection still applies when enabled. Joined interaction histories use
the existing CID/run-ID joins; they are not independently time-filtered.

Selection takes the first available rows after conversation-aware classification.
It is not random. Concurrent CID workers may select different rows between runs;
a shared lock prevents exceeding quotas. With CSV-selected CIDs, output remains
incremental and new batches stop once all six quotas are full. Without CSV CID
selection, the existing path reads/classifies the full matching dataset before
sampling; this change does not make that path memory-bounded.

Only selected labels are enriched, written back (if enabled), and passed to
feature/training generation. Feature Build may reject samples under its existing
quality rules. Fewer than 1,000 available rows means fewer exported samples;
records are never duplicated to fill quotas. The count CSV includes zero-result
intents, requested_samples and sample_shortfall; counts are also logged.

Sampling requires an empty INTENT_MAX_RECORDS and does not currently support
resume. The missing-record audit is skipped with a log message because excluded
rows are intentional. Saved rows survive a failure, but start a fresh sampling
run for a complete export. Sampling defaults to false and does not affect live
streaming.

# Agent and tool CSV report

## Sample unique interactions per intent in app.py

To restrict sampling to your supplied CIDs, also set these in `.env`:

```dotenv
INTERACTION_CIDS_FROM_CSV=true
INTERACTION_CIDS_CSV=input/cids.csv
INTERACTION_CID_COLUMN=cid
```

Use a CSV with a `cid` header and one ID per row. Duplicates and blanks are
removed; order and leading zeros are preserved. Relative paths resolve beside
`app.py`. With `--all` or `--all-complete`, only these CIDs are candidates;
Cosmos still supplies their full histories. Missing/invalid input fails instead
of falling back to Cosmos discovery. If the list cannot fill an intent quota,
the counts report shows its shortfall. An explicit single CID on the command
line still selects that CID, independently of the CSV flag.

Set these values in `.env` (not `intent_app_config.env`):

```dotenv
INTERACTION_SAMPLING_ENABLED=true
INTERACTION_SAMPLES_PER_INTENT=100
BATCH_LIMIT=0
BATCH_SIZE=50
MAX_WORKERS=10
OUTPUT_DIR=output/samples_100
```

Run `python app.py --all`. Change 100 to 500 or 1000 as needed, using a fresh
output directory. Sampling does not resume or append to previous exports.
Each CID counts once per eligible intent and is written once with all available
source histories. `sample_intents` identifies the quota memberships: count
distinct CIDs in that column, not rows. Full histories may contain additional
intents whose quotas were already full; those labels are preserved, not counted
again. A multi-intent CID can fill several quotas, so 100 per intent can require
fewer than 600 distinct CIDs. This is first-available sampling, not random sampling.

`interaction_sample_counts.csv` and progress logs show counts and shortfalls.
Failed fetches and (with `--all-complete`) incomplete conversations do not count.
The current fetch batch finishes before stopping once all quotas are full. If an
intent is scarce, all available CIDs may be scanned and the shortfall is reported.
This limit counts conversations, not tool calls or CSV rows, and no histories
are truncated to satisfy it. Sampling is disabled by default.

`app.py` now writes readable columns directly into `OUTPUT_DIR/interactions.csv`,
including `intent`, `agent`, `function_name`, `input`, `output`, and `error`.
All six intents are retained using the same rule-based classifier as `intent_app.py`.
Agent context records receive record-level labels; if no context exists, chat
records are classified instead. Other source rows show the conversation's distinct
intents separated by ` | `, with `intent_scope=conversation`; this is not a verified
tool-call-to-request mapping. Source rows are not multiplied for multiple intents.
Missing agent names remain blank. Original JSON and viewer columns are preserved.

Before running with an old-format `interactions.csv`, choose a new `OUTPUT_DIR`
in `.env` or rename the existing CSV. The exporter rejects mismatched headers
rather than appending incompatible rows or rewriting earlier exports.

Expand the same source records shown in the HTML viewer into separate agent,
function name, input, output and error columns. No additional Cosmos reads are
needed. Run against a completed export (not a CSV still being written):

```powershell
python interaction_report.py output/interactions.csv --output output/interaction_report.csv
```

The input can also be an intent classification CSV produced with
`INTENT_INCLUDE_INTERACTIONS=true`. In that case the report keeps the intent and
source ID alongside the CID, run ID and source record ID. Supply its actual path
instead of `output/interactions.csv`. Convert a separate clarification CSV
separately if clarification splitting is enabled.

Each source record gets its own row. Agent context and tool records are not
paired by guessed function names: an absent agent remains blank. Chat messages
and feedback have their own columns; arrays and objects remain valid JSON cells.
The original complete record is preserved in `data`, so the report also retains
the columns needed by the HTML viewer.

Histories repeated across multiple classified samples remain repeated, with
their sample source ID and intent. These are conversation-level histories, not
proof that each tool call belongs specifically to that intent. No sampling or
truncation is performed by this converter. It reads one input row at a time,
flushes after each input row, and logs progress every 100 input rows.

An existing output is never overwritten. Choose a new filename for another run.
Malformed input stops conversion with the input record number; already written
rows remain as a partial report. Very large cells may exceed Excel's display
limits even though their complete values are preserved in the CSV.

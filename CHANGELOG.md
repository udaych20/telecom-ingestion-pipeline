# Changelog

## Unreleased

- Added Cosmos interaction reconstruction using `DefaultAzureCredential`.
- Added complete CSV and JSONL interaction logs.
- Added configurable LLM and knowledge-graph preparation outputs.
- Added `--all` complete-dataset processing with an optional batch limit.
- Added per-interaction failure logging so a batch can continue.
- Added configurable streaming batches and `--all-complete` filtering.
- Changed dataset correlation to enumerate and query nested `messages[].data.cid` values.
- Added per-batch complete and missing-container counts.
- Corrected feedback correlation to search nested `feedbacks[].cid_list` values.
- Corrected context correlation to use all-tools `cid` as the bridge to UAT `run_id`.
- Corrected correlation so context `run_id` references chat `cid` and feedback matches chat `cid`.
- Added the spec-driven documentation set.

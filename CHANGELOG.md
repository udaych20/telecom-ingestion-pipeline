# Changelog

## Unreleased

- Added Cosmos interaction reconstruction using `DefaultAzureCredential`.
- Added complete CSV and JSONL interaction logs.
- Added configurable LLM and knowledge-graph preparation outputs.
- Added `--all` complete-dataset processing with an optional batch limit.
- Added per-interaction failure logging so a batch can continue.
- Added configurable streaming batches and `--all-complete` filtering.
- Corrected correlation so context `run_id` references chat `cid` and feedback matches chat `cid`.
- Added the spec-driven documentation set.

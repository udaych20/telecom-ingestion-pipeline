# Workflow

## Runtime workflow

```text
Load .env
   |
Authenticate with DefaultAzureCredential
   |
Read chat by id
   |
Discover cid values
   |
Read chat cid
   |
Read both context containers where run_id = cid
   |
Read feedback where cid = cid
   |
Assemble complete interaction
   |
Write interactions.csv and interactions.jsonl
   |
INGESTION_MODE?
   |-- none ------------> finish
   |-- llm -------------> llm_training.jsonl
   `-- knowledge_graph -> graph_nodes.csv + graph_edges.csv
```

## Spec-driven development workflow

1. Record the requested behavior in `SPECIFICATION.md` with an acceptance criterion.
2. Update `DATA_CONTRACTS.md` for source or output schema changes.
3. Update `DESIGN.md` when component boundaries or technical decisions change.
4. Implement the smallest code change satisfying the specification.
5. Add or update tests described in `TESTING.md`.
6. Run syntax, unit, integration, and output validation checks as applicable.
7. Update setup, configuration, and operations documentation before release.

No schema assumption should move into production code until it is documented and verified against representative Cosmos records.

## Complete-dataset workflow

`python app.py --all` enumerates chat IDs and applies the normal workflow using one Cosmos connection. IDs are streamed in groups of `BATCH_SIZE`. `BATCH_LIMIT=0` means every enumerated ID; a positive value restricts a trial run. Failed interactions are appended to `failed_interactions.csv`, and processing continues.

`python app.py --all-complete` uses the same batches but exports only interactions containing chat history, all-tools context, UAT context, and feedback. Missing any source causes a skip, not a failure. The final console line reports succeeded, skipped, and failed counts.

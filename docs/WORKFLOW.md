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
Read tool history -> discover run_id values
   |
Read context by cid and run_id + read feedback by cid
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

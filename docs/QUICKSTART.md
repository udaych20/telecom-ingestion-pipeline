# Quickstart

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
az login
```

Set at least these values in `.env`:

```env
COSMOS_ENDPOINT=https://YOUR-ACCOUNT.documents.azure.com:443/
COSMOS_DATABASE=NORA
INGESTION_MODE=none
```

Run:

```powershell
python app.py "CHAT-CID"
```

Check `output/interactions.csv` for readable records and `output/interactions.jsonl` for the complete assembled interaction.

The command prints one progress line per ID and a final succeeded/failed summary.

To create LLM preparation data, set `INGESTION_MODE=llm`. To create graph import files, set `INGESTION_MODE=knowledge_graph`.

## Test the full dataset

Start with `BATCH_LIMIT=10` in `.env`, then run:

```powershell
python app.py --all
```

After checking the output, set `BATCH_LIMIT=0` to process every chat. Failures are recorded in `output/failed_interactions.csv` without stopping the batch.

To keep only records with matches in all four containers:

```powershell
python app.py --all-complete
```

Incomplete interactions are skipped and are not written to output or the failure CSV.

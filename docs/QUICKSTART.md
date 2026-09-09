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

For a CSV-only time-range export, include a timezone in the start and end values:

```powershell
python app.py --all --start-time "2026-09-04T18:00:00+05:30" --end-time "2026-09-09T18:00:00+05:30" --output-format csv
```

Omit `--end-time` to use the time when the command starts. This range is based on
Cosmos `_ts`, which represents the chat document's latest insert or update time.

To keep only records with matches in all four containers:

```powershell
python app.py --all-complete
```

Incomplete interactions are skipped and are not written to output or the failure CSV.

## Test live mode

Provision the publisher and broker resources described in
[Live streaming](LIVE_STREAMING.md). Set `LIVE_STREAM_DATA_ENABLED=true` and the
provider-specific variables in `.env`, then run:

```powershell
python app.py --live
```

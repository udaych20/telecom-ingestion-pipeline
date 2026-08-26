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
python app.py "5638529c-2db7-45fe-8c2f-5dcfd46778c8"
```

Check `output/interactions.csv` for readable records and `output/interactions.jsonl` for the complete assembled interaction.

To create LLM preparation data, set `INGESTION_MODE=llm`. To create graph import files, set `INGESTION_MODE=knowledge_graph`.

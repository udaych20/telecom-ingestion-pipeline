# Setup instructions

## Prerequisites

- Python 3.10 or newer
- Azure CLI for local authentication
- Network access to the Cosmos DB account
- An Azure identity with **Cosmos DB Built-in Data Reader** at the required scope

Azure subscription Reader is a control-plane role and is not sufficient for reading container data.

## Windows PowerShell setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
az login
```

Edit `.env` and replace the endpoint, database, and container values.

## Azure-hosted setup

1. Enable a system-assigned or user-assigned managed identity on the host.
2. Assign the Cosmos DB Built-in Data Reader role at the narrowest suitable scope.
3. Configure the environment variables from `CONFIGURATION.md`.
4. Do not run `az login` on the hosted workload; `DefaultAzureCredential` will use its managed identity.

## Verification

```powershell
python -m py_compile app.py
python app.py "KNOWN-CHAT-CID"
```

Verify that output files are created and that their correlation IDs match the source records.

For dataset validation, set `BATCH_LIMIT` to a small positive value and run `python app.py --all`. Review `failed_interactions.csv`, then use `BATCH_LIMIT=0` for the complete dataset.

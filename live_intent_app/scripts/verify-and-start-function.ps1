Set-Location (Join-Path $PSScriptRoot "..")

python -m venv .venv
. .\.venv\Scripts\Activate.ps1

python -m pip install -r .\requirements.txt

python -c "import live_intent_app; import azure.functions; print('Imports OK')"

func --version
func start

"""Load local configuration without changing Azure app-setting behavior."""

from __future__ import annotations

from pathlib import Path


def load_local_environment() -> None:
    """Load live_intent_app/.env when python-dotenv is installed.

    Existing process variables always win, which keeps Azure Function app
    settings authoritative in deployed environments.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    env_file = Path(__file__).with_name(".env")
    load_dotenv(env_file, override=False)

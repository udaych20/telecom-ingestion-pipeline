"""Share cached Azure tokens across synchronous pipeline workers."""

import json
import os
import threading
import time

from azure.identity import AzureCliCredential, DefaultAzureCredential


class CachedCredential:
    def __init__(self, credential):
        self._credential = credential
        self._tokens = {}
        self._lock = threading.Lock()

    def get_token(self, *scopes, **kwargs):
        # Tenant/claims challenges must never reuse an ordinary cached token.
        key = (scopes, json.dumps(kwargs, sort_keys=True))
        with self._lock:
            token = self._tokens.get(key)
            if token is None or token.expires_on <= time.time() + 300:
                token = self._credential.get_token(*scopes, **kwargs)
                self._tokens[key] = token
            return token

    def close(self):
        with self._lock:
            self._tokens.clear()
            self._credential.close()


def create_pipeline_credential():
    mode = os.getenv("INTENT_AUTH_MODE", "default").strip().lower()
    timeout = int(os.getenv("INTENT_AUTH_TIMEOUT_SECONDS", "60"))
    if timeout <= 0:
        raise ValueError("INTENT_AUTH_TIMEOUT_SECONDS must be positive")
    if mode == "azure_cli":
        credential = AzureCliCredential(process_timeout=timeout)
    elif mode == "default":
        credential = DefaultAzureCredential(process_timeout=timeout)
    else:
        raise ValueError("INTENT_AUTH_MODE must be default or azure_cli")
    return CachedCredential(credential)


def check_cosmos_authentication(credential):
    try:
        credential.get_token("https://cosmos.azure.com/.default")
    except Exception as error:
        raise RuntimeError(
            "Cosmos authentication failed before starting workers. For Azure CLI "
            "authentication, verify 'az --version' and 'az login' in this terminal. "
            "INTENT_AUTH_TIMEOUT_SECONDS controls the CLI process timeout. "
            "No source queries have started and existing CSV files have not been replaced."
        ) from error

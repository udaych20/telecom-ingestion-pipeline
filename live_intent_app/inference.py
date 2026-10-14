"""Cloud model and deterministic demo inference adapters."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

INTENTS = {
    "ticket",
    "modify",
    "rca",
    "query",
    "general",
    "clarification_needed",
}
SYSTEM_PROMPT = (
    "Classify the telecom customer request as ticket, modify, rca, query, "
    "general, or clarification_needed. Return JSON with intent, confidence, "
    "and reason."
)


@dataclass(frozen=True)
class Inference:
    intent: str
    confidence: float
    reason: str
    model: str


class IntentModel(Protocol):
    def infer(self, text: str) -> Inference: ...


class FoundryModel:
    """Calls an OpenAI-compatible model endpoint hosted through Cloud Foundry."""

    def __init__(self) -> None:
        self.endpoint = required("MODEL_ENDPOINT").rstrip("/")
        self.deployment = required("MODEL_DEPLOYMENT")
        self.api_key = os.getenv("MODEL_API_KEY", "").strip()
        self.timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "30"))

    def infer(self, text: str) -> Inference:
        url = f"{self.endpoint}/chat/completions"
        request_body = {
            "model": self.deployment,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }
        body = json.dumps(request_body).encode("utf-8")
        headers = self._request_headers()
        request = urllib.request.Request(url, body, headers)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            message = f"Model request failed ({error.code}): {detail}"
            raise RuntimeError(message) from error

        return self._parse_response(payload)

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            header_name = os.getenv("MODEL_API_KEY_HEADER", "Authorization")
            header_value = self.api_key
            if header_name.lower() == "authorization":
                header_value = f"Bearer {self.api_key}"
            headers[header_name] = header_value
        return headers

    def _parse_response(self, payload: dict[str, Any]) -> Inference:
        content = payload["choices"][0]["message"]["content"]
        result = json.loads(content)
        intent = str(result.get("intent", "")).strip().lower()
        if intent not in INTENTS:
            raise ValueError(f"Model returned unsupported intent: {intent}")
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0))))
        return Inference(
            intent,
            confidence,
            str(result.get("reason", "")),
            self.deployment,
        )


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def configured_model() -> IntentModel:
    return FoundryModel()

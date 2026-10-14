"""Shared event-to-inference pipeline and CSV persistence."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Protocol

from .contracts import utc_now, validate_event
from .extraction import conversation_id, extract_user_text
from .inference import IntentModel, configured_model

CSV_FIELDS = (
    "event_id",
    "event_time",
    "processed_at",
    "container",
    "source_id",
    "conversation_id",
    "user_text",
    "intent",
    "confidence",
    "reason",
    "model",
)
_PII = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"(?<!\d)\+?\d[\d ()-]{6,}\d(?!\d)"), "[PHONE_OR_ACCOUNT]"),
    (re.compile(r"\b\d{14,16}\b"), "[DEVICE_ID]"),
)


class ResultSink(Protocol):
    def save(self, row: dict[str, Any]) -> bool: ...


def mask_text(text: str) -> str:
    for pattern, replacement in _PII:
        text = pattern.sub(replacement, text)
    return text


class LocalCsvSink:
    """Thread-safe local sink with an event-id ledger for demo/replay idempotency."""
    _lock = threading.Lock()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.ledger = self.path.with_suffix(self.path.suffix + ".events")

    def save(self, row: dict[str, Any]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            seen = self._saved_event_ids()
            if row["event_id"] in seen:
                return False

            write_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=CSV_FIELDS,
                    extrasaction="ignore",
                )
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

            with self.ledger.open("a", encoding="utf-8") as handle:
                handle.write(str(row["event_id"]) + "\n")

            return True

    def _saved_event_ids(self) -> set[str]:
        if not self.ledger.exists():
            return set()
        return set(self.ledger.read_text(encoding="utf-8").splitlines())


def configured_sink() -> ResultSink:
    return LocalCsvSink(os.getenv("CSV_OUTPUT", "output/live_intent.csv"))


class LiveIntentPipeline:
    def __init__(
        self,
        model: IntentModel | None = None,
        sink: ResultSink | None = None,
    ) -> None:
        self.model = model or configured_model()
        self.sink = sink or configured_sink()

    def process(self, event: dict[str, Any]) -> dict[str, Any]:
        event = validate_event(event)
        data = event["data"]
        document = data["document"]
        text = mask_text(extract_user_text(document))
        if not text:
            raise ValueError("No user text was found in data.document")
        prediction = self.model.infer(text)
        source_id = self._hash(document.get("id"))
        conversation = self._hash(conversation_id(document))
        row = {
            "event_id": str(event["id"]),
            "event_time": str(event.get("time", "")),
            "processed_at": utc_now(),
            "container": str(data.get("container", "")),
            "source_id": source_id,
            "conversation_id": conversation,
            "user_text": text,
            "intent": prediction.intent,
            "confidence": f"{prediction.confidence:.4f}",
            "reason": prediction.reason,
            "model": prediction.model,
        }
        row["saved"] = self.sink.save(row)
        return row

    @staticmethod
    def _hash(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

"""Wire contracts shared by the Azure Function, stream worker, and demo API."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_change_event(container: str, document: dict[str, Any]) -> dict[str, Any]:
    """Wrap a Cosmos document version in a stable CloudEvent-like envelope."""
    document_id = str(document.get("id", ""))
    etag = str(document.get("_etag", ""))
    identity = f"{container}:{document_id}:{etag}"
    if not document_id or not etag:
        identity = json.dumps(document, sort_keys=True, default=str)
    timestamp = document.get("_ts")
    event_time = (
        datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        if isinstance(timestamp, (int, float))
        else utc_now()
    )
    return {
        "specversion": "1.0",
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "source": f"/cosmos/{container}",
        "type": "com.telecom.cosmos.document.upserted",
        "subject": document_id,
        "time": event_time,
        "datacontenttype": "application/json",
        "data": {"container": container, "document": document},
    }


def validate_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Event must be a JSON object")
    for name in ("id", "source", "type", "data"):
        if name not in event:
            raise ValueError(f"Event is missing {name}")
    data = event["data"]
    if not isinstance(data, dict) or not isinstance(data.get("document"), dict):
        raise ValueError("Event data.document must be a JSON object")
    return event

"""Wire contracts shared by the Azure Function and stream worker."""

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


def mark_demo_bypass(event: dict[str, Any]) -> dict[str, Any]:
    """Mark an event that bypassed only the change feed and Azure Function."""
    event["demo_bypass"] = True
    event["bypassed_stages"] = [
        "cosmos_change_feed",
        "local_azure_function",
    ]
    return event


def attach_intent_result(
    event: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build the ontology-enriched Kafka event."""
    output = {
        key: event[key]
        for key in ("specversion", "id", "source", "time", "datacontenttype")
        if key in event
    }
    output["type"] = "com.telecom.user.intent.inferred"
    output["data"] = {
        "message": {
            "type": "user",
            "data": {"content": str(result.get("user_text", ""))},
        },
        "intent_result": {
            "ontology": {
                "Source_Intent-L1": str(result.get("source_intent_l1", "")),
                "Intent_L2": str(result.get("intent_l2", "")),
                "Likely_Theme_L4": str(result.get("likely_theme_l4", "")),
            },
            "confidence": result.get("confidence", "0.0000"),
            "reason": str(result.get("reason", "")),
            "model": str(result.get("model", "")),
        },
        "csv_saved": bool(result.get("saved")),
    }
    if event.get("demo_bypass"):
        output["demo_bypass"] = True
        output["bypassed_stages"] = list(event.get("bypassed_stages", []))
    return output


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

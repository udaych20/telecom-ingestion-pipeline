"""Publish NORA Cosmos DB changes to Azure Event Hubs.

The Cosmos trigger is backed by the change feed, so inserts and updates are
published automatically.  Source documents are deliberately not copied to the
event hub; only identifiers needed by subscribers are included.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Protocol

import azure.functions as func


LOGGER = logging.getLogger("nora.event_app")
DATABASE_NAME = os.getenv("COSMOS_DATABASE", "NORA")

app = func.FunctionApp()


class Publisher(Protocol):
    def publish(self, event: dict[str, Any], partition_key: str) -> None: ...


class EventHubPublisher:
    """Keyless Event Hubs publisher shared across warm Function invocations."""

    def __init__(self) -> None:
        from azure.eventhub import EventData, EventHubProducerClient
        from azure.identity import DefaultAzureCredential

        self._event_data_type = EventData
        self._credential = DefaultAzureCredential()
        self._client = EventHubProducerClient(
            fully_qualified_namespace=required("EVENT_HUB_NAMESPACE"),
            eventhub_name=required("EVENT_HUB_NAME"),
            credential=self._credential,
        )

    def publish(self, event: dict[str, Any], partition_key: str) -> None:
        batch = self._client.create_batch(partition_key=partition_key)
        batch.add(
            self._event_data_type(
                json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            )
        )
        self._client.send_batch(batch)


_publisher: Publisher | None = None
_publisher_lock = threading.Lock()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def get_publisher() -> Publisher:
    global _publisher
    if _publisher is None:
        with _publisher_lock:
            if _publisher is None:
                _publisher = EventHubPublisher()
    return _publisher


def find_values(value: Any, field: str) -> list[str]:
    """Find unique correlation values at any depth in a Cosmos document."""
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == field and child is not None:
                found.extend(child if isinstance(child, list) else [child])
            found.extend(find_values(child, field))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_values(child, field))
    return list(dict.fromkeys(str(item) for item in found if str(item).strip()))


def build_event(container: str, document: dict[str, Any]) -> dict[str, Any]:
    """Create a stable CloudEvents-style notification for one document version."""
    document_id = str(document.get("id", ""))
    etag = str(document.get("_etag", ""))
    identity = f"{DATABASE_NAME}:{container}:{document_id}:{etag}"
    if not document_id or not etag:
        identity = json.dumps(document, sort_keys=True, default=str)

    timestamp = document.get("_ts")
    event_time = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        if isinstance(timestamp, (int, float))
        else datetime.now(timezone.utc).isoformat()
    )
    return {
        "specversion": "1.0",
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "source": f"/cosmos/{DATABASE_NAME}/{container}",
        "type": "com.nora.cosmos.document.created-or-updated",
        "subject": document_id,
        "time": event_time,
        "datacontenttype": "application/json",
        "data": {
            "database": DATABASE_NAME,
            "container": container,
            "document_id": document_id,
            "etag": etag,
            "cids": find_values(document, "cid"),
            "cid_lists": find_values(document, "cid_list"),
            "run_ids": find_values(document, "run_id"),
        },
    }


def event_partition_key(event: dict[str, Any]) -> str:
    data = event["data"]
    for field in ("cids", "cid_lists", "run_ids"):
        if data[field]:
            return str(data[field][0])
    return str(event["subject"] or event["id"])


def publish_documents(documents: func.DocumentList, container: str) -> None:
    if not documents:
        return
    publisher = get_publisher()
    for document in documents:
        event = build_event(container, dict(document))
        publisher.publish(event, event_partition_key(event))
        LOGGER.info("Published NORA change event %s from %s", event["id"], container)


def decode_event(body: str | bytes) -> dict[str, Any]:
    """Validate an event before subscriber processing."""
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    event = json.loads(body)
    if not isinstance(event, dict):
        raise ValueError("Event body must be a JSON object")
    for field in ("id", "source", "type", "data"):
        if field not in event:
            raise ValueError(f"Event is missing {field}")
    return event


def nora_trigger(container_env: str, default_container: str, lease: str):
    """Declare a consistently configured Cosmos change-feed trigger."""
    return app.cosmos_db_trigger(
        arg_name="documents",
        database_name=DATABASE_NAME,
        container_name=os.getenv(container_env, default_container),
        connection="COSMOS_CONNECTION",
        lease_container_name=os.getenv(f"{container_env}_LEASE", lease),
        create_lease_container_if_not_exists=False,
    )


@nora_trigger("COSMOS_CHAT_CONTAINER", "chat-history-uat", "leases-chat")
def chat_history_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat"))


@nora_trigger("COSMOS_TOOLS_CONTAINER", "context-history-all-tools", "leases-tools")
def tool_history_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, os.getenv("COSMOS_TOOLS_CONTAINER", "context-history-all-tools"))


@nora_trigger("COSMOS_CONTEXT_CONTAINER", "context-history-uat", "leases-context")
def context_history_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, os.getenv("COSMOS_CONTEXT_CONTAINER", "context-history-uat"))


@nora_trigger("COSMOS_FEEDBACK_CONTAINER", "chat-feedback", "leases-feedback")
def feedback_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, os.getenv("COSMOS_FEEDBACK_CONTAINER", "chat-feedback"))


@app.event_hub_message_trigger(
    arg_name="message",
    event_hub_name=os.getenv("EVENT_HUB_NAME", "nora-updates"),
    connection="EVENT_HUB_CONNECTION",
    consumer_group=os.getenv("EVENT_HUB_CONSUMER_GROUP", "nora-update-subscriber"),
)
def nora_update_subscriber(message: func.EventHubEvent) -> None:
    """Subscriber entry point; add downstream processing here."""
    event = decode_event(message.get_body())
    data = event["data"]
    LOGGER.info(
        "Received NORA update id=%s container=%s document_id=%s cids=%s run_ids=%s",
        event["id"],
        data.get("container", ""),
        data.get("document_id", ""),
        data.get("cids", []),
        data.get("run_ids", []),
    )

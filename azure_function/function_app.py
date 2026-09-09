"""Cosmos DB change-feed publisher for Event Hubs and Apache Kafka."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Protocol

import azure.functions as func


LOGGER = logging.getLogger("telecom_ingestion.change_feed")
DATABASE_NAME = os.getenv("COSMOS_DATABASE", "NORA")
CHAT_CONTAINER = os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat")
TOOLS_CONTAINER = os.getenv(
    "COSMOS_TOOLS_CONTAINER", "context-history-all-tools"
)
CONTEXT_CONTAINER = os.getenv("COSMOS_CONTEXT_CONTAINER", "context-history-uat")
FEEDBACK_CONTAINER = os.getenv("COSMOS_FEEDBACK_CONTAINER", "chat-feedback")

app = func.FunctionApp()


class Publisher(Protocol):
    def publish(self, event: dict[str, Any], partition_key: str) -> None: ...


class EventHubsPublisher:
    def __init__(self) -> None:
        from azure.eventhub import EventData, EventHubProducerClient
        from azure.identity import DefaultAzureCredential

        self._event_data_type = EventData
        self._credential = DefaultAzureCredential()
        self._client = EventHubProducerClient(
            fully_qualified_namespace=required("LIVE_STREAM_EVENT_HUB_NAMESPACE"),
            eventhub_name=required("LIVE_STREAM_EVENT_HUB_NAME"),
            credential=self._credential,
        )

    def publish(self, event: dict[str, Any], partition_key: str) -> None:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        batch = self._client.create_batch(partition_key=partition_key)
        batch.add(self._event_data_type(body))
        self._client.send_batch(batch)


class KafkaPublisher:
    def __init__(self) -> None:
        from confluent_kafka import Producer

        configuration: dict[str, Any] = {
            "bootstrap.servers": required("LIVE_STREAM_KAFKA_BOOTSTRAP_SERVERS"),
            "client.id": os.getenv(
                "LIVE_STREAM_KAFKA_CLIENT_ID", "telecom-change-feed-publisher"
            ),
            "security.protocol": os.getenv(
                "LIVE_STREAM_KAFKA_SECURITY_PROTOCOL", "SASL_SSL"
            ),
            "enable.idempotence": True,
            "acks": "all",
        }
        optional_settings = {
            "sasl.mechanism": "LIVE_STREAM_KAFKA_SASL_MECHANISM",
            "sasl.username": "LIVE_STREAM_KAFKA_USERNAME",
            "sasl.password": "LIVE_STREAM_KAFKA_PASSWORD",
            "ssl.ca.location": "LIVE_STREAM_KAFKA_CA_LOCATION",
        }
        for kafka_name, environment_name in optional_settings.items():
            value = os.getenv(environment_name, "").strip()
            if value:
                configuration[kafka_name] = value
        self._topic = required("LIVE_STREAM_KAFKA_TOPIC")
        self._producer = Producer(configuration)

    def publish(self, event: dict[str, Any], partition_key: str) -> None:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        errors: list[Exception] = []

        def delivered(error: Exception | None, _message: Any) -> None:
            if error is not None:
                errors.append(error)

        self._producer.produce(
            self._topic,
            key=partition_key.encode("utf-8"),
            value=body.encode("utf-8"),
            on_delivery=delivered,
        )
        remaining = self._producer.flush(
            float(os.getenv("LIVE_STREAM_KAFKA_FLUSH_TIMEOUT_SECONDS", "10"))
        )
        if errors:
            raise RuntimeError(f"Kafka delivery failed: {errors[0]}")
        if remaining:
            raise TimeoutError(f"Kafka delivery timed out for {remaining} event(s)")


class CompositePublisher:
    def __init__(self, publishers: list[Publisher]) -> None:
        self._publishers = publishers

    def publish(self, event: dict[str, Any], partition_key: str) -> None:
        for publisher in self._publishers:
            publisher.publish(event, partition_key)


_publisher: Publisher | None = None
_publisher_lock = threading.Lock()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def get_publisher() -> Publisher:
    global _publisher
    if _publisher is not None:
        return _publisher
    with _publisher_lock:
        if _publisher is not None:
            return _publisher
        provider = os.getenv("LIVE_STREAM_PROVIDER", "event_hubs").strip().lower()
        if provider == "event_hubs":
            _publisher = EventHubsPublisher()
        elif provider == "kafka":
            _publisher = KafkaPublisher()
        elif provider == "both":
            _publisher = CompositePublisher(
                [EventHubsPublisher(), KafkaPublisher()]
            )
        else:
            raise ValueError(
                "LIVE_STREAM_PROVIDER must be event_hubs, kafka, or both"
            )
        return _publisher


def find_values(data: Any, field: str) -> list[str]:
    values: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == field and value is not None:
                values.extend(value if isinstance(value, list) else [value])
            values.extend(find_values(value, field))
    elif isinstance(data, list):
        for value in data:
            values.extend(find_values(value, field))
    return list(
        dict.fromkeys(
            str(value)
            for value in values
            if value is not None and str(value).strip()
        )
    )


def build_event(container: str, document: dict[str, Any]) -> dict[str, Any]:
    document_id = str(document.get("id", ""))
    etag = str(document.get("_etag", ""))
    identity = f"{DATABASE_NAME}:{container}:{document_id}:{etag}"
    if not document_id or not etag:
        identity = json.dumps(document, sort_keys=True, default=str)
    event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    timestamp = document.get("_ts")
    event_time = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        if isinstance(timestamp, (int, float))
        else datetime.now(timezone.utc).isoformat()
    )
    return {
        "specversion": "1.0",
        "id": event_id,
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


def partition_key(event: dict[str, Any]) -> str:
    data = event["data"]
    for name in ("cids", "cid_lists", "run_ids"):
        if data[name]:
            return str(data[name][0])
    return str(event["subject"] or event["id"])


def publish_documents(documents: func.DocumentList, container: str) -> None:
    if not documents:
        return
    publisher = get_publisher()
    for document in documents:
        event = build_event(container, dict(document))
        publisher.publish(event, partition_key(event))
        LOGGER.info(
            "Published Cosmos change event id=%s container=%s document_id=%s",
            event["id"],
            container,
            event["subject"],
        )


@app.cosmos_db_trigger(
    arg_name="documents",
    database_name=DATABASE_NAME,
    container_name=CHAT_CONTAINER,
    connection="COSMOS_CONNECTION",
    lease_container_name=os.getenv("COSMOS_CHAT_LEASE_CONTAINER", "leases-chat"),
    create_lease_container_if_not_exists=False,
)
def chat_history_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, CHAT_CONTAINER)


@app.cosmos_db_trigger(
    arg_name="documents",
    database_name=DATABASE_NAME,
    container_name=TOOLS_CONTAINER,
    connection="COSMOS_CONNECTION",
    lease_container_name=os.getenv("COSMOS_TOOLS_LEASE_CONTAINER", "leases-tools"),
    create_lease_container_if_not_exists=False,
)
def tool_history_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, TOOLS_CONTAINER)


@app.cosmos_db_trigger(
    arg_name="documents",
    database_name=DATABASE_NAME,
    container_name=CONTEXT_CONTAINER,
    connection="COSMOS_CONNECTION",
    lease_container_name=os.getenv("COSMOS_CONTEXT_LEASE_CONTAINER", "leases-context"),
    create_lease_container_if_not_exists=False,
)
def context_history_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, CONTEXT_CONTAINER)


@app.cosmos_db_trigger(
    arg_name="documents",
    database_name=DATABASE_NAME,
    container_name=FEEDBACK_CONTAINER,
    connection="COSMOS_CONNECTION",
    lease_container_name=os.getenv(
        "COSMOS_FEEDBACK_LEASE_CONTAINER", "leases-feedback"
    ),
    create_lease_container_if_not_exists=False,
)
def feedback_changes(documents: func.DocumentList) -> None:
    publish_documents(documents, FEEDBACK_CONTAINER)

"""Event Hubs and Kafka producers/consumers."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol


class Publisher(Protocol):
    def publish(self, event: dict[str, Any], key: str) -> None: ...


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


class EventHubPublisher:
    def __init__(self) -> None:
        from azure.eventhub import EventData, EventHubProducerClient
        from azure.identity import DefaultAzureCredential

        self.event_data = EventData
        self.client = EventHubProducerClient(
            fully_qualified_namespace=required("EVENT_HUB_NAMESPACE"),
            eventhub_name=required("EVENT_HUB_NAME"),
            credential=DefaultAzureCredential(),
        )

    def publish(self, event: dict[str, Any], key: str) -> None:
        batch = self.client.create_batch(partition_key=key)
        batch.add(self.event_data(json.dumps(event, ensure_ascii=False)))
        self.client.send_batch(batch)


def kafka_options() -> dict[str, Any]:
    protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL").upper()
    servers = [
        server.strip()
        for server in required("KAFKA_BOOTSTRAP_SERVERS").split(",")
        if server.strip()
    ]
    options: dict[str, Any] = {
        "bootstrap_servers": servers,
        "security_protocol": protocol,
        "client_id": os.getenv("KAFKA_CLIENT_ID", "live-intent"),
    }
    if protocol.startswith("SASL"):
        options.update(
            sasl_mechanism=os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
            sasl_plain_username=required("KAFKA_SASL_USERNAME"),
            sasl_plain_password=required("KAFKA_SASL_PASSWORD"),
        )
    if os.getenv("KAFKA_SSL_CAFILE", "").strip():
        options["ssl_cafile"] = os.environ["KAFKA_SSL_CAFILE"]
    return options


class KafkaPublisher:
    def __init__(self) -> None:
        from kafka import KafkaProducer

        self.topic = required("KAFKA_TOPIC")
        self.client = KafkaProducer(acks="all", retries=5, **kafka_options())

    def publish(self, event: dict[str, Any], key: str) -> None:
        result = self.client.send(
            self.topic,
            key=key.encode("utf-8"),
            value=json.dumps(event).encode("utf-8"),
        )
        result.get(timeout=30)


def configured_publisher() -> Publisher:
    kind = os.getenv("STREAM_TRANSPORT", "eventhub").strip().lower()
    if kind == "eventhub":
        return EventHubPublisher()
    if kind == "kafka":
        return KafkaPublisher()
    raise ValueError("STREAM_TRANSPORT must be eventhub or kafka")

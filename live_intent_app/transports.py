"""Kafka producer and consumer helpers."""

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


def kafka_options() -> dict[str, Any]:
    protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").upper()
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
    return KafkaPublisher()

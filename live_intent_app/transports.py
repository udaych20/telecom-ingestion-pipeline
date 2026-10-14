"""Event Hubs and Kafka producers/consumers."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

LOGGER = logging.getLogger("live_intent.transport")


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


class RestPublisher:
    """Post events to the demo/inference API when a broker is unavailable."""

    def __init__(self) -> None:
        self.endpoint = required("REST_FALLBACK_ENDPOINT")
        self.api_key = os.getenv("REST_FALLBACK_API_KEY", "").strip()
        self.timeout = float(os.getenv("REST_FALLBACK_TIMEOUT_SECONDS", "30"))

    def publish(self, event: dict[str, Any], key: str) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-Partition-Key": key,
        }
        if self.api_key:
            header_name = os.getenv(
                "REST_FALLBACK_API_KEY_HEADER",
                "x-functions-key",
            )
            headers[header_name] = self.api_key

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(event).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(
                    f"REST fallback returned HTTP {response.status}"
                )


class PermissionFallbackPublisher:
    """Use REST only when the primary transport rejects authorization."""

    def __init__(self, primary: Publisher, fallback: Publisher) -> None:
        self.primary = primary
        self.fallback = fallback

    def publish(self, event: dict[str, Any], key: str) -> None:
        try:
            self.primary.publish(event, key)
        except Exception as error:
            if not is_permission_error(error):
                raise
            LOGGER.warning(
                "Primary stream transport denied access; sending event %s "
                "through the configured REST fallback",
                event.get("id", "unknown"),
            )
            self.fallback.publish(event, key)


def is_permission_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403}:
        return True

    error_name = type(error).__name__.lower()
    permission_names = (
        "authentication",
        "authorization",
        "permission",
        "sasl",
    )
    return any(name in error_name for name in permission_names)


def configured_publisher() -> Publisher:
    kind = os.getenv("STREAM_TRANSPORT", "eventhub").strip().lower()
    if kind == "rest":
        return RestPublisher()
    if kind not in {"eventhub", "kafka"}:
        raise ValueError("STREAM_TRANSPORT must be eventhub, kafka, or rest")

    fallback_endpoint = os.getenv("REST_FALLBACK_ENDPOINT", "").strip()
    publisher_type = EventHubPublisher if kind == "eventhub" else KafkaPublisher

    try:
        primary: Publisher = publisher_type()
    except Exception as error:
        if not fallback_endpoint or not is_permission_error(error):
            raise
        LOGGER.warning(
            "Primary stream transport could not authenticate; using REST fallback"
        )
        return RestPublisher()

    if fallback_endpoint:
        return PermissionFallbackPublisher(primary, RestPublisher())
    return primary

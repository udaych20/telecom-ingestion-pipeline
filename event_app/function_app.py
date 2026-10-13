"""Publish NORA Cosmos DB changes to the configured live transport.

The Cosmos trigger is backed by the change feed, so inserts and updates are
published automatically. Source documents are not copied to the transport;
only identifiers needed by downstream consumers are included.
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
from pii_masking import tokenize_identifier


LOGGER = logging.getLogger("nora.event_app")
LOGGER.setLevel(os.getenv("NORA_LOG_LEVEL", "INFO").upper())
DATABASE_NAME = os.getenv("COSMOS_DATABASE", "NORA")
STREAM_TRANSPORT = os.getenv("STREAM_TRANSPORT", "eventhub").strip().lower()
if STREAM_TRANSPORT not in {"eventhub", "eventgrid", "kafka"}:
    raise ValueError("STREAM_TRANSPORT must be eventhub, eventgrid, or kafka")

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
        LOGGER.info(
            "Event Hubs publisher initialized namespace=%s hub=%s",
            required("EVENT_HUB_NAMESPACE"),
            required("EVENT_HUB_NAME"),
        )

    def publish(self, event: dict[str, Any], partition_key: str) -> None:
        LOGGER.debug(
            "Publishing event id=%s partition_key=%s", event["id"], partition_key
        )
        batch = self._client.create_batch(partition_key=partition_key)
        batch.add(
            self._event_data_type(
                json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            )
        )
        self._client.send_batch(batch)
        LOGGER.info("Event Hubs accepted event id=%s", event["id"])


class EventGridPublisher:
    """Send CloudEvents to an HTTP topic in an Event Grid namespace."""

    def __init__(self):
        from azure.eventgrid import EventGridPublisherClient
        from azure.identity import DefaultAzureCredential

        self._credential = DefaultAzureCredential()
        self._client = EventGridPublisherClient(
            required("EVENT_GRID_ENDPOINT"), self._credential,
            namespace_topic=required("EVENT_GRID_TOPIC"),
        )

    def publish(self, event, partition_key):
        # Event Grid does not provide Event Hubs partition-key ordering.
        self._client.send(event)
        LOGGER.info("Event Grid accepted event id=%s", event["id"])


class KafkaPublisher:
    """Publish compact change events to an existing Kafka topic."""

    def __init__(self):
        from kafka import KafkaProducer

        protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL").strip().upper()
        if protocol not in {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}:
            raise ValueError("KAFKA_SECURITY_PROTOCOL is not valid")

        options = {
            "bootstrap_servers": split_setting("KAFKA_BOOTSTRAP_SERVERS"),
            "client_id": os.getenv("KAFKA_CLIENT_ID", "nora-intent-finder").strip(),
            "security_protocol": protocol,
            "acks": "all",
            "retries": int(os.getenv("KAFKA_RETRIES", "5")),
            "request_timeout_ms": int(os.getenv("KAFKA_REQUEST_TIMEOUT_MS", "30000")),
        }
        if protocol.startswith("SASL"):
            mechanism = os.getenv("KAFKA_SASL_MECHANISM", "PLAIN").strip().upper()
            if mechanism not in {"PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"}:
                raise ValueError("KAFKA_SASL_MECHANISM is not supported")
            options.update(
                sasl_mechanism=mechanism,
                sasl_plain_username=required("KAFKA_SASL_USERNAME"),
                sasl_plain_password=required("KAFKA_SASL_PASSWORD"),
            )
        ca_file = os.getenv("KAFKA_SSL_CAFILE", "").strip()
        if ca_file:
            options["ssl_cafile"] = ca_file

        self._topic = required("KAFKA_TOPIC")
        self._timeout = int(os.getenv("KAFKA_PUBLISH_TIMEOUT_SECONDS", "30"))
        self._client = KafkaProducer(**options)
        LOGGER.info("Kafka publisher initialized topic=%s", self._topic)

    def publish(self, event, partition_key):
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = self._client.send(
            self._topic,
            key=partition_key.encode("utf-8"),
            value=payload,
            headers=[("content-type", b"application/cloudevents+json")],
        )
        result.get(timeout=self._timeout)
        LOGGER.info("Kafka accepted event id=%s", event["id"])


_publisher: Publisher | None = None
_publisher_lock = threading.Lock()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def split_setting(name: str) -> list[str]:
    values = [value.strip() for value in required(name).split(",") if value.strip()]
    if not values:
        raise ValueError(f"{name} must contain at least one value")
    return values


def get_publisher() -> Publisher:
    global _publisher
    if _publisher is None:
        with _publisher_lock:
            if _publisher is None:
                LOGGER.debug("Creating %s publisher client", STREAM_TRANSPORT)
                publishers = {
                    "eventhub": EventHubPublisher,
                    "eventgrid": EventGridPublisher,
                    "kafka": KafkaPublisher,
                }
                _publisher = publishers[STREAM_TRANSPORT]()
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
    masked_document_id = tokenize_identifier(document_id) if document_id else ""
    etag = str(document.get("_etag", ""))
    cids = [tokenize_identifier(value) for value in find_values(document, "cid")]
    cid_lists = [tokenize_identifier(value) for value in find_values(document, "cid_list")]
    run_ids = [tokenize_identifier(value) for value in find_values(document, "run_id")]
    identity = f"{DATABASE_NAME}:{container}:{document_id}:{etag}"
    if not document_id or not etag:
        identity = json.dumps(document, sort_keys=True, default=str)

    timestamp = document.get("_ts")
    event_time = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        if isinstance(timestamp, (int, float))
        else datetime.now(timezone.utc).isoformat()
    )
    event = {
        "specversion": "1.0",
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "source": f"/cosmos/{DATABASE_NAME}/{container}",
        "type": "com.nora.cosmos.document.created-or-updated",
        "subject": masked_document_id,
        "time": event_time,
        "datacontenttype": "application/json",
        "data": {
            "database": DATABASE_NAME,
            "container": container,
            "document_id": masked_document_id,
            "etag": tokenize_identifier(etag) if etag else "",
            "cids": cids,
            "cid_lists": cid_lists,
            "run_ids": run_ids,
        },
    }
    LOGGER.debug(
        "Built event id=%s container=%s document_id=%s cids=%s run_ids=%s",
        event["id"],
        container,
        masked_document_id,
        event["data"]["cids"],
        event["data"]["run_ids"],
    )
    return event


def event_partition_key(event: dict[str, Any]) -> str:
    data = event["data"]
    for field in ("cids", "cid_lists", "run_ids"):
        if data[field]:
            return str(data[field][0])
    return str(event["subject"] or event["id"])


def publish_documents(documents: func.DocumentList, container: str) -> None:
    if not documents:
        LOGGER.warning("Cosmos trigger returned no documents container=%s", container)
        return
    LOGGER.info(
        "Cosmos change batch received container=%s document_count=%s",
        container,
        len(documents),
    )
    try:
        publisher = get_publisher()
        for document in documents:
            event = build_event(container, dict(document))
            publisher.publish(event, event_partition_key(event))
            LOGGER.info(
                "Published NORA change event id=%s container=%s document_id=%s",
                event["id"],
                container,
                event["subject"],
            )
    except Exception:
        LOGGER.exception(
            "Failed to publish Cosmos change batch container=%s", container
        )
        raise


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
    LOGGER.debug("Validated subscriber event id=%s type=%s", event["id"], event["type"])
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


def nora_update_subscriber(message: func.EventHubEvent) -> None:
    """Subscriber entry point; add downstream processing here."""
    LOGGER.info(
        "Event Hubs trigger received partition=%s sequence_number=%s offset=%s",
        (message.metadata or {}).get("PartitionContext", {}).get(
            "PartitionId", "unknown"
        ),
        message.sequence_number,
        message.offset,
    )
    try:
        event = decode_event(message.get_body())
        process_event(event)
    except Exception:
        LOGGER.exception("Failed to process Event Hubs message")
        raise


def process_event(event):
    """Shared subscriber hook. Currently validates and logs; no model inference."""
    for field in ("id", "source", "type", "data"):
        if field not in event:
            raise ValueError(f"Event is missing {field}")
    data = event["data"]
    if not isinstance(data, dict):
        raise ValueError("NORA event data must be an object")
    LOGGER.info("Received NORA update id=%s container=%s document_id=%s cids=%s run_ids=%s",
                event["id"], data.get("container", ""), data.get("document_id", ""),
                data.get("cids", []), data.get("run_ids", []))


def consume_grid_events(client):
    """Acknowledge only successful processing; release failures for redelivery."""
    batch_size = int(os.getenv("EVENT_GRID_RECEIVE_BATCH_SIZE", "10"))
    if not 1 <= batch_size <= 100:
        raise ValueError("EVENT_GRID_RECEIVE_BATCH_SIZE must be between 1 and 100")
    events = client.receive(max_events=batch_size, max_wait_time=10)
    failures = 0
    for detail in events:
        token = detail.broker_properties.lock_token
        try:
            event = detail.event
            process_event({"id": event.id, "source": event.source, "type": event.type, "data": event.data})
            result = client.acknowledge(lock_tokens=[token])
            if result.failed_lock_tokens or token not in result.succeeded_lock_tokens:
                raise RuntimeError("Event Grid acknowledgement did not succeed")
        except Exception as error:
            failures += 1
            LOGGER.error("Event Grid processing/acknowledgement failed (%s); message will be retried",
                         type(error).__name__)
            try:
                result = client.release(lock_tokens=[token], release_delay=10)
                if result.failed_lock_tokens or token not in result.succeeded_lock_tokens:
                    LOGGER.warning("Event Grid release failed; waiting for message lock expiry")
            except Exception:
                LOGGER.warning("Event Grid release unavailable; waiting for message lock expiry")
    LOGGER.info("Event Grid poll: received=%s successful=%s failed=%s", len(events), len(events) - failures, failures)
    if failures:
        raise RuntimeError(f"Event Grid poll had {failures} processing/acknowledgement failures")


if STREAM_TRANSPORT == "eventhub":
    nora_update_subscriber = app.event_hub_message_trigger(
        arg_name="message", event_hub_name=os.getenv("EVENT_HUB_NAME", "nora-updates"),
        connection="EVENT_HUB_CONNECTION",
        consumer_group=os.getenv("EVENT_HUB_CONSUMER_GROUP", "nora-update-subscriber"),
    )(nora_update_subscriber)
elif STREAM_TRANSPORT == "eventgrid":
    @app.timer_trigger(schedule="*/15 * * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
    def nora_grid_subscriber(timer: func.TimerRequest) -> None:
        from azure.eventgrid import EventGridConsumerClient
        from azure.identity import DefaultAzureCredential

        with DefaultAzureCredential() as credential:
            with EventGridConsumerClient(
                required("EVENT_GRID_ENDPOINT"), credential,
                namespace_topic=required("EVENT_GRID_TOPIC"),
                subscription=required("EVENT_GRID_SUBSCRIPTION"),
            ) as client:
                consume_grid_events(client)

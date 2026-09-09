"""Durable live-event subscribers for Azure Event Hubs and Apache Kafka."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger("telecom_ingestion.live_stream")
SUPPORTED_PROVIDERS = {"event_hubs", "kafka"}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def read_boolean_environment(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off")


def require_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for live streaming")
    return value


@dataclass(frozen=True)
class LiveStreamSettings:
    enabled: bool
    provider: str
    state_database: Path
    max_processing_attempts: int
    retry_initial_seconds: float

    @classmethod
    def from_environment(cls) -> "LiveStreamSettings":
        provider = os.getenv("LIVE_STREAM_PROVIDER", "event_hubs").strip().lower()
        attempts = int(os.getenv("LIVE_STREAM_MAX_PROCESSING_ATTEMPTS", "5"))
        retry_seconds = float(os.getenv("LIVE_STREAM_RETRY_INITIAL_SECONDS", "1"))
        settings = cls(
            enabled=read_boolean_environment("LIVE_STREAM_DATA_ENABLED"),
            provider=provider,
            state_database=Path(
                os.getenv(
                    "LIVE_STREAM_STATE_DATABASE",
                    os.path.join(os.getenv("OUTPUT_DIR", "output"), "live_stream_state.db"),
                )
            ),
            max_processing_attempts=attempts,
            retry_initial_seconds=retry_seconds,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(f"LIVE_STREAM_PROVIDER must be one of: {choices}")
        if self.max_processing_attempts < 1:
            raise ValueError("LIVE_STREAM_MAX_PROCESSING_ATTEMPTS must be at least 1")
        if self.retry_initial_seconds < 0:
            raise ValueError("LIVE_STREAM_RETRY_INITIAL_SECONDS must be non-negative")


class ProcessedEventLedger:
    """Durable event-id ledger used in addition to broker checkpoints/offsets."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                )
                """
            )

    def contains(self, event_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None

    def mark_processed(self, event_id: str, provider: str) -> None:
        processed_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO processed_events(event_id, provider, processed_at)
                VALUES (?, ?, ?)
                """,
                (event_id, provider, processed_at),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def decode_event(payload: str | bytes) -> dict[str, Any]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        event = json.loads(payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Live-stream event is not valid UTF-8 JSON") from error

    if not isinstance(event, dict):
        raise ValueError("Live-stream event must be a JSON object")
    if not str(event.get("id", "")).strip():
        raise ValueError("Live-stream event is missing id")
    if not isinstance(event.get("data"), dict):
        raise ValueError("Live-stream event is missing data")
    return event


def process_with_retry(
    event: dict[str, Any],
    provider: str,
    handler: Callable[[dict[str, Any]], None],
    ledger: ProcessedEventLedger,
    settings: LiveStreamSettings,
) -> bool:
    event_id = str(event["id"])
    if ledger.contains(event_id):
        LOGGER.info("Skipping previously processed event id=%s", event_id)
        return False

    for attempt in range(1, settings.max_processing_attempts + 1):
        try:
            handler(event)
            ledger.mark_processed(event_id, provider)
            LOGGER.info("Processed live event id=%s provider=%s", event_id, provider)
            return True
        except Exception:
            if attempt == settings.max_processing_attempts:
                LOGGER.exception(
                    "Live event failed after %d attempts id=%s provider=%s",
                    attempt,
                    event_id,
                    provider,
                )
                raise
            delay = settings.retry_initial_seconds * (2 ** (attempt - 1))
            LOGGER.warning(
                "Live event attempt %d failed; retrying in %.1fs id=%s",
                attempt,
                delay,
                event_id,
                exc_info=True,
            )
            time.sleep(delay)
    return False


def run_event_hubs(
    settings: LiveStreamSettings,
    handler: Callable[[dict[str, Any]], None],
    credential: Any,
) -> None:
    try:
        from azure.eventhub import EventHubConsumerClient
        from azure.eventhub.extensions.checkpointstoreblob import BlobCheckpointStore
    except ImportError as error:
        raise RuntimeError(
            "Event Hubs live mode requires azure-eventhub and "
            "azure-eventhub-checkpointstoreblob"
        ) from error

    namespace = require_environment("LIVE_STREAM_EVENT_HUB_NAMESPACE")
    event_hub_name = require_environment("LIVE_STREAM_EVENT_HUB_NAME")
    consumer_group = os.getenv("LIVE_STREAM_EVENT_HUB_CONSUMER_GROUP", "$Default")
    storage_url = require_environment("LIVE_STREAM_CHECKPOINT_STORAGE_ACCOUNT_URL")
    blob_container = require_environment("LIVE_STREAM_CHECKPOINT_BLOB_CONTAINER")
    starting_position = os.getenv(
        "LIVE_STREAM_EVENT_HUB_STARTING_POSITION", "@latest"
    ).strip()

    checkpoint_store = BlobCheckpointStore(
        blob_account_url=storage_url,
        container_name=blob_container,
        credential=credential,
    )
    consumer = EventHubConsumerClient(
        fully_qualified_namespace=namespace,
        eventhub_name=event_hub_name,
        consumer_group=consumer_group,
        credential=credential,
        checkpoint_store=checkpoint_store,
    )
    ledger = ProcessedEventLedger(settings.state_database)
    blocked_partitions: set[str] = set()
    blocked_lock = threading.Lock()

    def on_event(partition_context: Any, event_data: Any) -> None:
        partition_id = str(partition_context.partition_id)
        with blocked_lock:
            if partition_id in blocked_partitions:
                return
        try:
            event = decode_event(event_data.body_as_str(encoding="UTF-8"))
            process_with_retry(event, "event_hubs", handler, ledger, settings)
            partition_context.update_checkpoint(event_data)
        except Exception:
            # Do not advance this partition beyond a failed event. A supervised
            # restart resumes from its last durable Blob checkpoint.
            with blocked_lock:
                blocked_partitions.add(partition_id)
            LOGGER.exception(
                "Blocked Event Hubs partition after processing failure partition=%s",
                partition_id,
            )

    def on_error(partition_context: Any, error: Exception) -> None:
        partition_id = (
            str(partition_context.partition_id) if partition_context else "consumer"
        )
        LOGGER.error("Event Hubs receive error partition=%s: %s", partition_id, error)

    LOGGER.info(
        "Starting Event Hubs subscriber namespace=%s hub=%s group=%s",
        namespace,
        event_hub_name,
        consumer_group,
    )
    try:
        with consumer:
            consumer.receive(
                on_event=on_event,
                on_error=on_error,
                starting_position=starting_position,
            )
    finally:
        ledger.close()


def kafka_consumer_configuration() -> dict[str, Any]:
    configuration: dict[str, Any] = {
        "bootstrap.servers": require_environment(
            "LIVE_STREAM_KAFKA_BOOTSTRAP_SERVERS"
        ),
        "group.id": os.getenv(
            "LIVE_STREAM_KAFKA_CONSUMER_GROUP", "telecom-ingestion-live"
        ),
        "client.id": os.getenv(
            "LIVE_STREAM_KAFKA_CLIENT_ID", "telecom-ingestion-pipeline"
        ),
        "auto.offset.reset": os.getenv(
            "LIVE_STREAM_KAFKA_AUTO_OFFSET_RESET", "latest"
        ),
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "security.protocol": os.getenv(
            "LIVE_STREAM_KAFKA_SECURITY_PROTOCOL", "SASL_SSL"
        ),
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
    return configuration


def run_kafka(
    settings: LiveStreamSettings,
    handler: Callable[[dict[str, Any]], None],
) -> None:
    try:
        from confluent_kafka import Consumer, KafkaError, KafkaException
    except ImportError as error:
        raise RuntimeError("Kafka live mode requires confluent-kafka") from error

    topic = require_environment("LIVE_STREAM_KAFKA_TOPIC")
    configuration = kafka_consumer_configuration()
    consumer = Consumer(configuration)
    ledger = ProcessedEventLedger(settings.state_database)
    consumer.subscribe([topic])
    LOGGER.info(
        "Starting Kafka subscriber topic=%s group=%s",
        topic,
        configuration["group.id"],
    )

    try:
        while True:
            message = consumer.poll(timeout=1.0)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(message.error())

            event = decode_event(message.value())
            process_with_retry(event, "kafka", handler, ledger, settings)
            # Commit only after output processing and ledger persistence succeed.
            consumer.commit(message=message, asynchronous=False)
    except KeyboardInterrupt:
        LOGGER.info("Kafka subscriber stopped")
    finally:
        consumer.close()
        ledger.close()


def run_live_stream(
    handler: Callable[[dict[str, Any]], None], credential: Any
) -> None:
    settings = LiveStreamSettings.from_environment()
    if not settings.enabled:
        raise ValueError(
            "LIVE_STREAM_DATA_ENABLED is false; set it to true to run --live"
        )
    if settings.provider == "event_hubs":
        run_event_hubs(settings, handler, credential)
    else:
        run_kafka(settings, handler)

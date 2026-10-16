"""Azure Function: Cosmos change feed to SLM, CSV, and Kafka."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import azure.functions as func

from live_intent_app.settings import load_local_environment

load_local_environment()

from live_intent_app.contracts import attach_intent_result, build_change_event
from live_intent_app.pipeline import LiveIntentPipeline
from live_intent_app.transports import Publisher, configured_publisher

app = func.FunctionApp()
LOGGER = logging.getLogger("live_intent")
_publisher: Publisher | None = None
_pipeline: LiveIntentPipeline | None = None
_lock = threading.Lock()
_audit_lock = threading.Lock()


def publisher() -> Publisher:
    global _publisher
    if _publisher is None:
        with _lock:
            if _publisher is None:
                _publisher = configured_publisher()
    return _publisher


def pipeline() -> LiveIntentPipeline:
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                _pipeline = LiveIntentPipeline()
    return _pipeline


def publish_changes(documents: func.DocumentList, container: str) -> None:
    for item in documents or []:
        document = dict(item)
        event = build_change_event(container, document)
        partition_key = str(
            document.get("cid") or document.get("id") or event["id"]
        )
        write_demo_audit("CHANGE_FEED_RECEIVED", event, partition_key)
        LOGGER.info(
            "CHANGE_FEED_DETECTED container=%s document_id=%s event_id=%s trace_id=%s",
            container,
            document.get("id", ""),
            event["id"],
            document.get("_demo_trace_id", ""),
        )
        result = pipeline().process(event)
        write_demo_audit("SLM_INFERRED", event, partition_key)
        write_demo_audit("CSV_WRITTEN", event, partition_key)
        LOGGER.info(
            "SLM_INFERENCE_SUCCESS event_id=%s L1=%s L2=%s L4=%s confidence=%s model=%s",
            event["id"],
            result["source_intent_l1"],
            result["intent_l2"],
            result["likely_theme_l4"],
            result["confidence"],
            result["model"],
        )
        LOGGER.info(
            "CSV_SAVE_SUCCESS event_id=%s saved=%s path=%s",
            event["id"],
            result["saved"],
            os.getenv("CSV_OUTPUT", "output/live_intent.csv"),
        )
        output_event = attach_intent_result(event, result)
        publisher().publish(output_event, str(event["id"]))
        write_demo_audit("KAFKA_PUBLISHED", event, partition_key)
        LOGGER.info(
            "KAFKA_PUBLISH_SUCCESS event_id=%s topic=%s partition_key=%s ontology=%s",
            output_event["id"],
            os.getenv("KAFKA_TOPIC", "cosmos-changes"),
            output_event["id"],
            json.dumps(output_event["data"]["intent_result"], sort_keys=True),
        )


def write_demo_audit(
    stage: str,
    event: dict,
    partition_key: str,
) -> None:
    """Write local evidence for the demo without changing event processing."""
    if os.getenv("DEMO_AUDIT_ENABLED", "true").lower() != "true":
        return

    path = Path(os.getenv("DEMO_AUDIT_OUTPUT", "output/change_feed_audit.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "stage": stage,
        "event_id": event["id"],
        "document_id": event["data"]["document"].get("id", ""),
        "trace_id": event["data"]["document"].get("_demo_trace_id", ""),
        "partition_key": partition_key,
    }
    with _audit_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cosmos_trigger(
    container_setting: str,
    default: str,
    lease_setting: str,
    lease_default: str,
):
    return app.cosmos_db_trigger(
        arg_name="documents",
        database_name=os.getenv("COSMOS_DATABASE", "NORA"),
        container_name=os.getenv(container_setting, default),
        connection="COSMOS_CONNECTION",
        lease_container_name=os.getenv(lease_setting, lease_default),
        create_lease_container_if_not_exists=False,
    )


@cosmos_trigger(
    "COSMOS_CHAT_CONTAINER",
    "chat-history-uat",
    "COSMOS_CHAT_LEASE",
    "leases-live-chat",
)
def chat_changes(documents: func.DocumentList) -> None:
    container = os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat")
    publish_changes(documents, container)

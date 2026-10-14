"""Azure Functions entry point: Cosmos change feed -> stream -> inference."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import azure.functions as func

from live_intent_app.settings import load_local_environment

load_local_environment()

from live_intent_app.contracts import build_change_event
from live_intent_app.pipeline import LiveIntentPipeline
from live_intent_app.transports import Publisher, configured_publisher

app = func.FunctionApp()
LOGGER = logging.getLogger("live_intent")
_publisher: Publisher | None = None
_pipeline: LiveIntentPipeline | None = None
_lock = threading.Lock()


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
        publisher().publish(event, partition_key)


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
    publish_changes(documents, os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat"))


@cosmos_trigger(
    "COSMOS_CONTEXT_CONTAINER",
    "context-history-uat",
    "COSMOS_CONTEXT_LEASE",
    "leases-live-context",
)
def context_changes(documents: func.DocumentList) -> None:
    publish_changes(documents, os.getenv("COSMOS_CONTEXT_CONTAINER", "context-history-uat"))


@cosmos_trigger(
    "COSMOS_TOOLS_CONTAINER",
    "context-history-all-tools",
    "COSMOS_TOOLS_LEASE",
    "leases-live-tools",
)
def tool_changes(documents: func.DocumentList) -> None:
    publish_changes(documents, os.getenv("COSMOS_TOOLS_CONTAINER", "context-history-all-tools"))


@cosmos_trigger(
    "COSMOS_FEEDBACK_CONTAINER",
    "chat-feedback",
    "COSMOS_FEEDBACK_LEASE",
    "leases-live-feedback",
)
def feedback_changes(documents: func.DocumentList) -> None:
    publish_changes(documents, os.getenv("COSMOS_FEEDBACK_CONTAINER", "chat-feedback"))


def event_hub_consumer(message: func.EventHubEvent) -> None:
    result = pipeline().process(json.loads(message.get_body().decode("utf-8")))
    LOGGER.info(
        "Inferred event=%s intent=%s saved=%s",
        result["event_id"],
        result["intent"],
        result["saved"],
    )


if os.getenv("STREAM_TRANSPORT", "eventhub").strip().lower() == "eventhub":
    event_hub_consumer = app.event_hub_message_trigger(
        arg_name="message",
        event_hub_name=os.getenv("EVENT_HUB_NAME", "live-intent"),
        connection="EVENT_HUB_CONNECTION",
        consumer_group=os.getenv("EVENT_HUB_CONSUMER_GROUP", "live-intent-inference"),
    )(event_hub_consumer)


@app.route(route="demo/infer", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def demo_infer(request: func.HttpRequest) -> func.HttpResponse:
    """Test-only ingress; accepts a document or a complete change event."""
    try:
        body: Any = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object")
        if {"id", "source", "type", "data"}.issubset(body):
            event = body
        else:
            event = build_change_event("demo", body)
        result = pipeline().process(event)
        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
        )
    except (ValueError, json.JSONDecodeError) as error:
        return func.HttpResponse(
            json.dumps({"error": str(error)}),
            status_code=400,
            mimetype="application/json",
        )

"""Infer one Cosmos document, save CSV, then publish it for the demo bypass."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from .contracts import attach_intent_result, build_change_event, mark_demo_bypass
from .pipeline import LiveIntentPipeline
from .settings import load_local_environment
from .transports import KafkaPublisher


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def read_receipt(path: Path) -> tuple[str, dict]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    return str(receipt["container"]), receipt["document"]


def read_cosmos_document(container_name: str, document_id: str) -> dict:
    endpoint = required("COSMOS_CONNECTION__accountEndpoint")
    database_name = required("COSMOS_DATABASE")
    credential = DefaultAzureCredential()
    with CosmosClient(endpoint, credential=credential) as client:
        container = client.get_database_client(database_name).get_container_client(
            container_name
        )
        return container.read_item(item=document_id, partition_key=document_id)


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", default="output/last_cosmos_insert.json")
    args = parser.parse_args()

    container_name, inserted = read_receipt(Path(args.receipt))
    document_id = str(inserted["id"])
    document = read_cosmos_document(container_name, document_id)
    event = mark_demo_bypass(build_change_event(container_name, document))

    result = LiveIntentPipeline().process(event)
    output_event = attach_intent_result(event, result)
    KafkaPublisher().publish(output_event, document_id)
    output = Path("output/last_bypass_event.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(output_event, indent=2), encoding="utf-8")

    print(f"Replayed Cosmos document: {document_id}")
    print(f"SLM intent: {result['intent']}")
    print(f"CSV saved: {result['saved']}")
    print(f"Published Kafka event: {output_event['id']}")
    print("Bypassed: cosmos_change_feed, local_azure_function")


if __name__ == "__main__":
    main()

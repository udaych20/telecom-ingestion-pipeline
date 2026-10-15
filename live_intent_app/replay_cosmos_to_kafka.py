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
from .insert_cosmos_test_document import (
    configured_document_source,
    enabled,
    load_backup_documents,
)
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


def infer_save_and_publish(document: dict, container_name: str) -> tuple[dict, dict]:
    """Run the ordered backup flow: SLM -> durable CSV -> ontology Kafka."""
    document_id = str(document["id"])
    event = mark_demo_bypass(build_change_event(container_name, document))
    result = LiveIntentPipeline().process(event)
    if not result["saved"]:
        raise RuntimeError(
            "Intent row was not newly saved to CSV; Kafka publish was skipped"
        )
    output_event = attach_intent_result(event, result)
    KafkaPublisher().publish(output_event, document_id)
    return result, output_event


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", default="output/last_cosmos_insert.json")
    parser.add_argument(
        "--batch-receipt", default="output/last_cosmos_insert_batch.json"
    )
    parser.add_argument(
        "--document-file",
        help="Cosmos document JSON exported from the Azure portal",
    )
    parser.add_argument(
        "--container",
        default=os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat"),
    )
    args = parser.parse_args()

    run_backup = enabled("DEMO_SLM_BACKUP_ENABLED")
    batch_size = int(os.getenv("DEMO_BATCH_SIZE", "10"))

    if run_backup:
        container_name = args.container
        documents = load_backup_documents(
            configured_document_source(), batch_size
        )
        insertion_evidence = "cosmos_bypassed"
    elif args.document_file:
        document = json.loads(Path(args.document_file).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not document.get("id"):
            raise ValueError("The exported Cosmos document must be a JSON object with id")
        container_name = args.container
        documents = [document]
        insertion_evidence = "manual_browser_export"
    else:
        receipts = json.loads(Path(args.batch_receipt).read_text(encoding="utf-8"))
        container_name = str(receipts[0]["container"])
        documents = [
            read_cosmos_document(container_name, str(item["document"]["id"]))
            for item in receipts
        ]
        insertion_evidence = "cosmos_insert"

    output = Path("output/last_bypass_event.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output_events = []
    for index, document in enumerate(documents, start=1):
        result, output_event = infer_save_and_publish(document, container_name)
        output_events.append(output_event)
        print(
            f"Processed {index}/{len(documents)}: intent={result['intent']} "
            f"kafka_event={output_event['id']}"
        )
    output.write_text(json.dumps(output_events, indent=2), encoding="utf-8")

    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "container": container_name,
        "document": documents[-1],
        "insertion_evidence": insertion_evidence,
    }, indent=2), encoding="utf-8")
    print(f"Completed SLM -> CSV -> Kafka for {len(documents)} events")


if __name__ == "__main__":
    main()

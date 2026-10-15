"""Insert a test conversation into Cosmos DB for change-feed testing."""

from __future__ import annotations

import argparse
import copy
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from .settings import load_local_environment


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def build_document(
    document_id: str,
    conversation_id: str,
    message: str,
) -> dict[str, Any]:
    return {
        "id": document_id,
        "_demo_trace_id": conversation_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "messages": [
            {
                "type": "user",
                "data": {
                    "cid": conversation_id,
                    "content": message,
                },
            }
        ],
        "test_record": True,
    }


def configured_document_source() -> Path:
    directory = os.getenv("DEMO_DOCUMENT_DIR", "").strip()
    if directory:
        return Path(directory)
    return Path(required("DEMO_DOCUMENT_FILE"))


def load_backup_documents(path: Path, batch_size: int) -> list[dict[str, Any]]:
    """Load JSON event templates and give every Cosmos insert a fresh identity."""
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        payload = [json.loads(file.read_text(encoding="utf-8-sig")) for file in files]
    elif path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise ValueError("DEMO_DOCUMENT_FILE must contain a JSON array")
    else:
        raise ValueError(f"Demo document source does not exist: {path}")
    if len(payload) != batch_size:
        raise ValueError(
            f"Demo document source must contain exactly {batch_size} documents; "
            f"found {len(payload)}"
        )

    documents = []
    for index, template in enumerate(payload, start=1):
        if not isinstance(template, dict):
            raise ValueError(f"Backup document {index} must be a JSON object")
        document = copy.deepcopy(template)
        for system_field in ("_etag", "_rid", "_self", "_attachments", "_ts"):
            document.pop(system_field, None)
        suffix = uuid.uuid4().hex[:12]
        base_id = str(document.get("id") or f"backup-{index:03d}")
        document["id"] = f"{base_id}-{suffix}"
        document["_demo_trace_id"] = document["id"]
        document.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
        document["test_record"] = True
        for message in document.get("messages", []):
            if isinstance(message, dict) and isinstance(message.get("data"), dict):
                message["data"]["cid"] = document["id"]
        documents.append(document)
    return documents


def insert_document(
    document: dict[str, Any],
    container_name: str,
) -> dict[str, Any]:
    endpoint = os.getenv("COSMOS_ENDPOINT", "").strip()
    if not endpoint:
        endpoint = required("COSMOS_CONNECTION__accountEndpoint")

    credential = DefaultAzureCredential()
    with CosmosClient(endpoint, credential=credential) as client:
        database = client.get_database_client(required("COSMOS_DATABASE"))
        container = database.get_container_client(container_name)
        return container.create_item(document)


def main() -> None:
    load_local_environment()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--container",
        default=os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat"),
    )
    parser.add_argument(
        "--message",
        default="Please check the status of my mobile service",
    )
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--conversation-id", default=None)
    parser.add_argument(
        "--receipt",
        default="output/last_cosmos_insert.json",
        help="File used by verify_demo.py and the bypass command",
    )
    args = parser.parse_args()

    run_backup = enabled("DEMO_SLM_BACKUP_ENABLED")
    batch_size = int(os.getenv("DEMO_BATCH_SIZE", "10"))
    if batch_size < 1:
        raise ValueError("DEMO_BATCH_SIZE must be at least 1")
    if batch_size > 100:
        raise ValueError("DEMO_BATCH_SIZE must not exceed 100")
    if run_backup:
        print("Backup flow is enabled; Cosmos insertion is intentionally skipped.")
        return

    documents = load_backup_documents(configured_document_source(), batch_size)

    receipts = []
    for index, document in enumerate(documents):
        document_id = str(document["id"])
        inserted = insert_document(document, args.container)
        receipt = {"container": args.container, "document": inserted}
        receipts.append(receipt)

        print(f"Inserted document {index + 1}/{batch_size}: {document_id}")

    receipt = receipts[-1]
    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Container: {args.container}")
    print(f"Receipt: {receipt_path}")
    batch_receipt = receipt_path.with_name("last_cosmos_insert_batch.json")
    batch_receipt.write_text(
        json.dumps(receipts, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Cosmos batch inserted: {batch_size} records")
    print(f"Batch receipt: {batch_receipt}")


if __name__ == "__main__":
    main()

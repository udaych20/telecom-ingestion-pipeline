"""Insert a test conversation into Cosmos DB for change-feed testing."""

from __future__ import annotations

import argparse
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

    suffix = uuid.uuid4().hex[:12]
    document_id = args.document_id or f"live-intent-test-{suffix}"
    conversation_id = args.conversation_id or f"test-cid-{suffix}"
    document = build_document(document_id, conversation_id, args.message)

    inserted = insert_document(document, args.container)
    receipt = {
        "container": args.container,
        "document": inserted,
    }
    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Inserted document: {document_id}")
    print(f"Container: {args.container}")
    print(f"Conversation: {conversation_id}")
    print(f"ETag: {inserted.get('_etag', '')}")
    print(f"Receipt: {receipt_path}")


if __name__ == "__main__":
    main()

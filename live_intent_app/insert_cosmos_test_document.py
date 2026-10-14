"""Insert a test conversation into Cosmos DB for change-feed testing."""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timezone
from typing import Any

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


def insert_document(document: dict[str, Any], container_name: str) -> None:
    endpoint = os.getenv("COSMOS_ENDPOINT", "").strip()
    if not endpoint:
        endpoint = required("COSMOS_CONNECTION__accountEndpoint")

    credential = DefaultAzureCredential()
    with CosmosClient(endpoint, credential=credential) as client:
        database = client.get_database_client(required("COSMOS_DATABASE"))
        container = database.get_container_client(container_name)
        container.create_item(document)


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
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:12]
    document_id = args.document_id or f"live-intent-test-{suffix}"
    conversation_id = args.conversation_id or f"test-cid-{suffix}"
    document = build_document(document_id, conversation_id, args.message)

    insert_document(document, args.container)
    print(f"Inserted document: {document_id}")
    print(f"Container: {args.container}")
    print(f"Conversation: {conversation_id}")


if __name__ == "__main__":
    main()

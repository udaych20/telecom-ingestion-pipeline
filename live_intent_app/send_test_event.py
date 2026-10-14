"""Send a sample Cosmos-style change event to the configured transport."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .contracts import build_change_event
from .settings import load_local_environment
from .transports import configured_publisher


def sample_document() -> dict[str, Any]:
    return {
        "id": "demo-document-1",
        "_etag": "version-1",
        "messages": [
            {
                "type": "user",
                "data": {
                    "cid": "demo-conversation-1",
                    "content": "Please check the status of my mobile service",
                },
            }
        ],
    }


def load_document(path: str | None) -> dict[str, Any]:
    if not path:
        return sample_document()

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("The test document must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document",
        help="Optional path to a Cosmos-style JSON document",
    )
    parser.add_argument(
        "--container",
        default="demo-container",
        help="Container name written into the change event",
    )
    args = parser.parse_args()

    load_local_environment()
    document = load_document(args.document)
    event = build_change_event(args.container, document)
    partition_key = str(document.get("cid") or document.get("id") or event["id"])

    configured_publisher().publish(event, partition_key)
    print(f"Published test event {event['id']}")


if __name__ == "__main__":
    main()

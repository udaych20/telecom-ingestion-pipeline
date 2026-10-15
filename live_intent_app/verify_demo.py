"""Report evidence for every stage in the live-intent demo."""

from __future__ import annotations

import argparse
import csv
import json
import os
import uuid
from pathlib import Path

from .contracts import build_change_event
from .settings import load_local_environment
from .transports import kafka_options, required


def audit_stages(path: Path, event_id: str) -> set[str]:
    if not path.exists():
        return set()
    stages = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry.get("event_id") == event_id:
            stages.add(str(entry.get("stage")))
    return stages


def kafka_contains(event_id: str, timeout_seconds: int) -> bool:
    from kafka import KafkaConsumer

    options = kafka_options()
    options.pop("client_id", None)
    consumer = KafkaConsumer(
        required("KAFKA_TOPIC"),
        group_id=f"demo-verifier-{uuid.uuid4().hex}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=timeout_seconds * 1000,
        **options,
    )
    try:
        for message in consumer:
            event = json.loads(message.value.decode("utf-8"))
            if event.get("id") == event_id:
                return True
    finally:
        consumer.close()
    return False


def csv_contains(path: Path, event_id: str) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return any(row.get("event_id") == event_id for row in csv.DictReader(handle))


def print_status(stage: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "NOT VERIFIED"
    suffix = f" - {detail}" if detail else ""
    print(f"{status:12} {stage}{suffix}")


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", default="output/last_cosmos_insert.json")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--mode", choices=("live", "demo"), default=None)
    args = parser.parse_args()

    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    event = build_change_event(receipt["container"], receipt["document"])
    event_id = event["id"]
    stages = audit_stages(
        Path(os.getenv("DEMO_AUDIT_OUTPUT", "output/change_feed_audit.jsonl")),
        event_id,
    )

    print(f"Event ID: {event_id}")
    csv_found = csv_contains(
        Path(os.getenv("CSV_OUTPUT", "output/live_intent.csv")), event_id
    )
    slm_inferred = "SLM_INFERRED" in stages or csv_found
    results = {
        "Cosmos insert": True,
        "Cosmos change feed": "CHANGE_FEED_RECEIVED" in stages,
        "Local Azure Function": "SLM_INFERRED" in stages,
        "Cloud Foundry SLM": slm_inferred,
        "Intent CSV": csv_found,
        "Kafka result topic": kafka_contains(event_id, args.timeout),
    }
    for stage, passed in results.items():
        detail = str(receipt["document"].get("id", "")) if stage == "Cosmos insert" else ""
        print_status(stage, passed, detail)

    required_stages = {
        "Cosmos insert",
        "Cloud Foundry SLM",
        "Intent CSV",
        "Kafka result topic",
    }
    if args.mode == "live":
        required_stages.update({"Cosmos change feed", "Local Azure Function"})
    if args.mode and not all(results[stage] for stage in required_stages):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

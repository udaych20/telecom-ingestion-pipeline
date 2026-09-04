"""Build Azure Foundry fine-tuning JSONL and a filterable CSV."""

from __future__ import annotations

import argparse
import copy
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("Train_50_intent_rca_query.csv")
ALLOWED_INTENTS = {"rca", "query"}
SYSTEM_PROMPT = (
    "Classify the telecom interaction as rca or query. Return JSON containing "
    "only intent_predicted."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Add classification.intent as data.label to user messages, create an "
            "Azure Foundry fine-tuning JSONL file, and create a flattened CSV."
        )
    )
    parser.add_argument("csv_file", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--jsonl-output", type=Path)
    parser.add_argument("--csv-output", type=Path)
    return parser.parse_args()


def normalized(value: str) -> str:
    return "".join(value.strip().lower().split())


def resolve_column(
    fieldnames: list[str] | None, expected_name: str, fallback_index: int
) -> str:
    if not fieldnames:
        raise ValueError("The input CSV has no header row.")

    expected = normalized(expected_name)
    for fieldname in fieldnames:
        if fieldname is not None and normalized(fieldname) == expected:
            return fieldname

    if len(fieldnames) > fallback_index:
        return fieldnames[fallback_index]

    excel_column = "E" if fallback_index == 4 else "AL"
    raise ValueError(
        f"Could not find {expected_name!r}; the CSV also has no {excel_column} column."
    )


def parse_json_cell(value: str, row_number: int) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"row {row_number}: source.messages is not valid JSON ({exc.msg})"
        ) from exc


def extract_messages(value: Any, row_number: int) -> list[dict[str, Any]]:
    # Accept an array stored directly in source.messages or an object containing it.
    if isinstance(value, dict):
        if isinstance(value.get("messages"), list):
            value = value["messages"]
        elif isinstance(value.get("source"), dict):
            value = value["source"].get("messages")

    if not isinstance(value, list):
        raise ValueError(f"row {row_number}: source.messages must contain a JSON array")
    if not all(isinstance(message, dict) for message in value):
        raise ValueError(f"row {row_number}: every message must be a JSON object")
    return value


def extract_intent(value: str, row_number: int) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"row {row_number}: classification.intent is empty")

    # Most exports contain a plain string. Also support a JSON string/object.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip()
    if isinstance(parsed, dict) and str(parsed.get("intent", "")).strip():
        return str(parsed["intent"]).strip()
    return text


def add_user_labels(messages: list[dict[str, Any]], intent: str) -> int:
    changed = 0
    for message in messages:
        data = message.get("data")
        top_level_type = str(message.get("type", "")).strip().lower()
        data_type = (
            str(data.get("type", "")).strip().lower()
            if isinstance(data, dict)
            else ""
        )
        if top_level_type != "user" and data_type != "user":
            continue

        if not isinstance(data, dict):
            data = {}
            message["data"] = data
        data["label"] = intent
        data["intent_predicted"] = ""
        changed += 1
    return changed


def build_training_record(
    messages: list[dict[str, Any]], intent: str, row_number: int
) -> dict[str, list[dict[str, str]]]:
    """Wrap the annotated source messages in Azure chat-training format."""
    inference_messages = copy.deepcopy(messages)
    user_messages = 0
    for message in inference_messages:
        data = message.get("data")
        top_level_type = str(message.get("type", "")).strip().lower()
        data_type = (
            str(data.get("type", "")).strip().lower()
            if isinstance(data, dict)
            else ""
        )
        if top_level_type != "user" and data_type != "user":
            continue

        if not isinstance(data, dict):
            data = {}
            message["data"] = data
        data["intent_predicted"] = ""
        user_messages += 1

    if user_messages == 0:
        raise ValueError(f"row {row_number}: no type=user message was found")

    input_json = json.dumps(
        inference_messages, ensure_ascii=False, separators=(",", ":")
    )
    expected_json = json.dumps(
        {"intent_predicted": ""}, ensure_ascii=False, separators=(",", ":")
    )

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": input_json},
            {"role": "assistant", "content": expected_json},
        ]
    }


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten(child, child_prefix))
    elif isinstance(value, list):
        # Nested arrays remain JSON in one cell so the CSV stays rectangular.
        result[prefix] = json.dumps(value, ensure_ascii=False)
    else:
        result[prefix] = value
    return result


def output_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    stem = args.csv_file.with_suffix("")
    jsonl_output = args.jsonl_output or stem.with_name(stem.name + "_finetune.jsonl")
    csv_output = args.csv_output or stem.with_name(stem.name + "_flattened.csv")
    return jsonl_output, csv_output


def convert(input_path: Path, jsonl_path: Path, flat_csv_path: Path) -> dict[str, int]:
    fine_tune_records: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    stats = {
        "read": 0,
        "written": 0,
        "filtered": 0,
        "skipped": 0,
        "user_messages": 0,
    }

    with input_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        messages_column = resolve_column(reader.fieldnames, "source.messages", 4)
        intent_column = resolve_column(reader.fieldnames, "classification.intent", 37)

        print(f"Messages column: {messages_column}")
        print(f"Intent column  : {intent_column}")

        for row_number, row in enumerate(reader, start=2):
            stats["read"] += 1
            try:
                intent = extract_intent(row.get(intent_column, "") or "", row_number)
                intent = intent.casefold()
                if intent not in ALLOWED_INTENTS:
                    stats["filtered"] += 1
                    continue

                parsed = parse_json_cell(row.get(messages_column, "") or "", row_number)
                messages = extract_messages(parsed, row_number)
                user_count = add_user_labels(messages, intent)
                if user_count == 0:
                    raise ValueError(f"row {row_number}: no type=user message was found")
                record = build_training_record(messages, intent, row_number)
            except ValueError as exc:
                stats["skipped"] += 1
                print(f"Skipped {exc}")
                continue

            duplicate_key = json.dumps(record, sort_keys=True, ensure_ascii=False)
            if duplicate_key in seen:
                stats["skipped"] += 1
                print(f"Skipped row {row_number}: duplicate training record")
                continue
            seen.add(duplicate_key)
            fine_tune_records.append(record)
            stats["written"] += 1
            stats["user_messages"] += user_count

            for message_index, message in enumerate(messages):
                flattened = {
                    "source_row": row_number,
                    "message_index": message_index,
                    "classification.intent": intent,
                }
                flattened.update(flatten(message, "message"))
                flat_rows.append(flattened)

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    flat_csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Azure Foundry requires UTF-8 with a byte-order mark (BOM).
    with jsonl_path.open("w", encoding="utf-8-sig", newline="\n") as jsonl_file:
        for record in fine_tune_records:
            jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    preferred = ["source_row", "message_index", "classification.intent"]
    other_fields = sorted(
        {key for row in flat_rows for key in row if key not in preferred}
    )
    with flat_csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=preferred + other_fields)
        writer.writeheader()
        writer.writerows(flat_rows)

    return stats


def main() -> None:
    args = parse_args()
    jsonl_path, flat_csv_path = output_paths(args)
    stats = convert(args.csv_file, jsonl_path, flat_csv_path)

    print()
    print(f"Input rows            : {stats['read']}")
    print(f"Fine-tuning records   : {stats['written']}")
    print(f"Labeled user messages : {stats['user_messages']}")
    print(f"Filtered other labels : {stats['filtered']}")
    print(f"Skipped rows          : {stats['skipped']}")
    print(f"JSONL output          : {jsonl_path}")
    print(f"CSV output            : {flat_csv_path}")


if __name__ == "__main__":
    main()

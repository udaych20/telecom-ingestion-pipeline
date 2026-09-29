"""Stream a large interaction CSV into a minimal, event-oriented extract.

The output contains only CID, user input, agent name, function name, and
classification intent. The input is processed one CSV record at a time, so
its size is not limited by available RAM.
"""

import argparse
import csv
import json
from pathlib import Path


FIELDS = ["cid", "user_input", "agent", "function_name", "classification_intent"]
HISTORIES = ("chat_history", "context_history", "tool_history")


def useful(value):
    return value not in (None, "", [], {})


def json_cell(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def first(mapping, *names):
    for name in names:
        value = mapping.get(name)
        if useful(value):
            return value
    return ""


def nested_first(value, names):
    if isinstance(value, dict):
        for name in names:
            if useful(value.get(name)):
                return value[name]
        for item in value.values():
            found = nested_first(item, names)
            if useful(found):
                return found
    elif isinstance(value, list):
        for item in value:
            found = nested_first(item, names)
            if useful(found):
                return found
    return ""


def user_messages(record):
    """Yield user text without copying assistant messages into the extract."""
    messages = record.get("messages") or record.get("history") or []
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except json.JSONDecodeError:
            messages = []
    found_user = False
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        role = str(first(message, "role", "type") or first(data, "role", "type")).lower()
        text = first(message, "content", "text", "message") or first(data, "content", "text", "message")
        if role in {"user", "customer", "human"} and useful(text):
            found_user = True
            yield text if isinstance(text, str) else json_cell(text)
    # Some source documents contain structured input but no messages array.
    if not found_user:
        fallback = first(record, "user_input", "user_inputs", "query", "request", "issue_summary")
        if useful(fallback):
            yield fallback if isinstance(fallback, str) else json_cell(fallback)


def output_rows(row):
    """Convert either an expanded interaction row or a wide enriched row."""
    intent = first(row, "classification_intent", "classification.intent", "intent")
    base_cid = first(row, "cid", "conversation_id", "interaction_id")
    if "source" in row and "data" in row:
        try:
            record = json.loads(row.get("data") or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("invalid JSON in data column") from error
        records = [(row.get("source", ""), record)]
    else:
        records = []
        for source in HISTORIES:
            raw = row.get(f"interaction.{source}") or "[]"
            try:
                items = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON in interaction.{source}") from error
            if not isinstance(items, list):
                raise ValueError(f"interaction.{source} must be a JSON array")
            records.extend((source, item) for item in items)

    for source, record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{source} record must be a JSON object")
        cid = base_cid or nested_first(record, ("cid", "conversation_id"))
        common = {"cid": cid, "user_input": "", "agent": "", "function_name": "",
                  "classification_intent": intent or first(record, "classification_intent", "intent")}
        if source == "chat_history":
            for message in user_messages(record):
                yield {**common, "user_input": message}
        elif source == "context_history":
            agent = first(record, "agent")
            function_name = first(record, "function_name", "name")
            if useful(agent) or useful(function_name):
                yield {**common, "agent": agent, "function_name": function_name}
        elif source == "tool_history":
            function_name = first(record, "function_name", "name")
            if useful(function_name):
                yield {**common, "function_name": function_name}


def extract(source, destination, progress_every=100_000):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Output must differ from input")
    csv.field_size_limit(2**31 - 1)
    with source.open(encoding="utf-8-sig", newline="") as incoming:
        reader = csv.DictReader(incoming)
        columns = set(reader.fieldnames or [])
        expanded = {"source", "data"} <= columns
        wide = any(f"interaction.{name}" in columns for name in HISTORIES)
        if not (expanded or wide):
            raise ValueError("Expected source/data columns or interaction history columns")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8-sig", newline="") as outgoing:
            writer = csv.DictWriter(outgoing, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            written = 0
            for number, row in enumerate(reader, 1):
                try:
                    for result in output_rows(row):
                        writer.writerow(result)
                        written += 1
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Invalid input at CSV record {number + 1}; partial output retained") from error
                if progress_every and number % progress_every == 0:
                    outgoing.flush()
                    print(f"Read {number:,} rows; wrote {written:,} events", flush=True)
    print(f"Saved {written:,} events: {destination.resolve()}", flush=True)
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Large interactions CSV")
    parser.add_argument("--output", type=Path, required=True, help="New compact CSV path")
    parser.add_argument("--progress-every", type=int, default=100_000)
    args = parser.parse_args()
    extract(args.input, args.output, args.progress_every)


if __name__ == "__main__":
    main()

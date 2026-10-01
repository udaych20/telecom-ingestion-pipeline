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
AUDIT_FIELDS = ["cid", "status", "event_count"]
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


def normalize_cid(value):
    return str(value).strip() if value is not None else ""


def load_cids(path, column="cid"):
    """Load a de-duplicated CID allowlist from a small CSV file."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if column not in (reader.fieldnames or []):
            raise ValueError(f"CID CSV has no {column!r} column")
        cids = {normalize_cid(row.get(column)) for row in reader}
    cids.discard("")
    if not cids:
        raise ValueError("CID CSV contains no non-empty CIDs")
    return cids


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


def write_cid_audit(path, selected_cids, event_counts):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        for cid in sorted(selected_cids):
            count = event_counts.get(cid, 0)
            writer.writerow({"cid": cid, "status": "found" if count else "not_found",
                             "event_count": count})


def extract(source, destination, progress_every=100_000, selected_cids=None, cid_audit=None):
    source, destination = Path(source), Path(destination)
    selected_cids = ({normalize_cid(cid) for cid in selected_cids}
                     if selected_cids is not None else None)
    if selected_cids is not None:
        selected_cids.discard("")
        if not selected_cids:
            raise ValueError("CID filter contains no non-empty CIDs")
    if source.resolve() == destination.resolve():
        raise ValueError("Output must differ from input")
    if cid_audit is not None:
        cid_audit = Path(cid_audit)
        if cid_audit.resolve() in {source.resolve(), destination.resolve()}:
            raise ValueError("CID audit must differ from input and output")
        if selected_cids is None:
            raise ValueError("CID audit requires --cid or --cid-csv")
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
            event_counts = {}
            for number, row in enumerate(reader, 1):
                if progress_every and number % progress_every == 0:
                    outgoing.flush()
                    print(f"Read {number:,} rows; wrote {written:,} events", flush=True)
                row_cid = normalize_cid(first(row, "cid", "conversation_id", "interaction_id"))
                # Expanded interaction exports carry the CID outside the large JSON
                # cell, so irrelevant records can be skipped without decoding it.
                if selected_cids is not None and row_cid and row_cid not in selected_cids:
                    continue
                try:
                    for result in output_rows(row):
                        result_cid = normalize_cid(result.get("cid"))
                        if selected_cids is not None and result_cid not in selected_cids:
                            continue
                        result["cid"] = result_cid
                        writer.writerow(result)
                        written += 1
                        if selected_cids is not None:
                            event_counts[result_cid] = event_counts.get(result_cid, 0) + 1
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Invalid input at CSV record {number + 1}; partial output retained") from error
    if selected_cids is not None:
        print(f"Matched {len(event_counts):,} of {len(selected_cids):,} requested CIDs", flush=True)
        if cid_audit is not None:
            write_cid_audit(cid_audit, selected_cids, event_counts)
            print(f"Saved CID audit: {cid_audit.resolve()}", flush=True)
    print(f"Saved {written:,} events: {destination.resolve()}", flush=True)
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Large interactions CSV")
    parser.add_argument("--output", type=Path, required=True, help="New compact CSV path")
    parser.add_argument("--cid", action="append", default=[],
                        help="Include one CID; repeat for multiple CIDs")
    parser.add_argument("--cid-csv", type=Path,
                        help="Small CSV containing CIDs to include")
    parser.add_argument("--cid-column", default="cid",
                        help="CID CSV column name (default: cid)")
    parser.add_argument("--cid-audit", type=Path,
                        help="New CSV listing each requested CID as found or not_found")
    parser.add_argument("--progress-every", type=int, default=100_000)
    args = parser.parse_args()
    selected_cids = set(args.cid)
    if args.cid_csv:
        selected_cids.update(load_cids(args.cid_csv, args.cid_column))
    extract(args.input, args.output, args.progress_every,
            selected_cids=selected_cids if args.cid or args.cid_csv else None,
            cid_audit=args.cid_audit)


if __name__ == "__main__":
    main()

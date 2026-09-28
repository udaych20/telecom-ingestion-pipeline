"""Expand existing interaction exports into a readable, one-record-per-row CSV."""

import argparse
import csv
import json
from pathlib import Path


SOURCES = ("chat_history", "context_history", "tool_history", "feedback")
FIELDS = ["interaction_id", "cid", "source_id", "intent", "run_id", "source",
          "record_id", "agent", "function_name", "input", "output", "error",
          "messages", "feedback", "data"]


def cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def first(record, *names):
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return ""


def report_row(record, source, metadata):
    if not isinstance(record, dict):
        raise ValueError(f"Expected a JSON object for {source}")
    row = dict(metadata)
    row.update({
        "source": source,
        "record_id": record.get("id", row.get("record_id", "")),
        "run_id": record.get("run_id", row.get("run_id", "")),
        "agent": record.get("agent", ""),
        "function_name": record.get("function_name", ""),
        "input": first(record, "arguments", "function_arguments") if source == "tool_history"
                 else first(record, "function_arguments", "arguments"),
        "output": record.get("function_result", ""),
        "error": record.get("error", ""),
        "messages": record.get("messages", "") if source == "chat_history" else "",
        "feedback": record if source == "feedback" else "",
        "data": record,
    })
    return {key: cell(row.get(key, "")) for key in FIELDS}


def expand(row):
    cid = row.get("cid") or row.get("conversation_id", "")
    metadata = {
        "interaction_id": row.get("interaction_id") or cid,
        "cid": cid,
        "source_id": row.get("source_id", ""),
        "intent": row.get("classification.intent", ""),
        "record_id": row.get("record_id", ""),
        "run_id": row.get("run_id", ""),
    }
    if "data" in row and "source" in row:
        yield report_row(json.loads(row["data"]), row["source"], metadata)
        return
    for source in SOURCES:
        records = json.loads(row.get(f"interaction.{source}") or "[]")
        if not isinstance(records, list):
            raise ValueError(f"interaction.{source} must contain a JSON array")
        for record in records:
            yield report_row(record, source, metadata)


def convert(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Report output must differ from the input CSV")
    csv.field_size_limit(2**31 - 1)
    with source.open(encoding="utf-8-sig", newline="") as incoming:
        reader = csv.DictReader(incoming)
        columns = set(reader.fieldnames or [])
        if not ({"data", "source"} <= columns or
                any(f"interaction.{name}" in columns for name in SOURCES)):
            raise ValueError("CSV has no interaction histories; export with INTENT_INCLUDE_INTERACTIONS=true")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects an existing report from accidental replacement.
        with destination.open("x", encoding="utf-8-sig", newline="") as outgoing:
            writer = csv.DictWriter(outgoing, fieldnames=FIELDS)
            writer.writeheader()
            outgoing.flush()
            count = 0
            for number, row in enumerate(reader, start=2):
                try:
                    for item in expand(row):
                        writer.writerow(item)
                        count += 1
                except (ValueError, TypeError) as error:
                    raise ValueError(f"Invalid interaction at CSV record {number}; partial report retained") from error
                outgoing.flush()
                if (number - 1) % 100 == 0:
                    print(f"Read {number - 1} source rows; wrote {count} report rows", flush=True)
    print(f"Saved {count} report rows: {destination.resolve()}", flush=True)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="interactions.csv or an enriched intent CSV")
    parser.add_argument("--output", type=Path, required=True, help="New report CSV path")
    args = parser.parse_args()
    convert(args.input, args.output)


if __name__ == "__main__":
    main()

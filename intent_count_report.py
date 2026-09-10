"""Create intent-wise unique CID counts from classification CSV exports."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_OUTPUT = Path("intent_cid_count_report.csv")
INTENT_COLUMNS = (
    "classification.intent",
    "classification_intent",
    "intent",
    "label",
)
CID_COLUMNS = (
    "conversation_id",
    "cid",
    "source.cid",
    "message.data.cid",
    "message.cid",
)
MESSAGE_COLUMNS = ("source.messages", "messages")


def normalized(value: str) -> str:
    return "".join(value.strip().casefold().split())


def resolve_column(
    fieldnames: list[str] | None,
    candidates: Iterable[str],
) -> str | None:
    if not fieldnames:
        return None
    available = {
        normalized(name): name
        for name in fieldnames
        if name is not None
    }
    for candidate in candidates:
        match = available.get(normalized(candidate))
        if match is not None:
            return match
    return None


def parse_intent(value: str) -> str:
    text = value.strip()
    if not text:
        return "UNCLASSIFIED"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text.casefold()
    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip().casefold()
    if isinstance(parsed, dict):
        intent = str(parsed.get("intent", "")).strip()
        if intent:
            return intent.casefold()
    return text.casefold()


def nested_cids(value: Any) -> set[str]:
    cids: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() == "cid" and child is not None:
                cid = str(child).strip()
                if cid:
                    cids.add(cid)
            else:
                cids.update(nested_cids(child))
    elif isinstance(value, list):
        for child in value:
            cids.update(nested_cids(child))
    return cids


def cell_cids(value: str) -> set[str]:
    text = value.strip()
    if not text:
        return set()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {text}
    if isinstance(parsed, (dict, list)):
        return nested_cids(parsed)
    if parsed is None:
        return set()
    cid = str(parsed).strip()
    return {cid} if cid else set()


def row_cids(
    row: dict[str, str],
    direct_cid_columns: list[str],
    message_column: str | None,
) -> set[str]:
    cids: set[str] = set()
    for column in direct_cid_columns:
        cids.update(cell_cids(row.get(column, "") or ""))
    if cids or message_column is None:
        return cids

    messages = row.get(message_column, "") or ""
    try:
        parsed_messages = json.loads(messages)
    except json.JSONDecodeError:
        return set()
    return nested_cids(parsed_messages)


def summarize_csv(dataset: str, path: Path) -> list[dict[str, Any]]:
    row_counts: Counter[str] = Counter()
    missing_cid_counts: Counter[str] = Counter()
    intent_cids: dict[str, set[str]] = defaultdict(set)
    all_cids: set[str] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        intent_column = resolve_column(reader.fieldnames, INTENT_COLUMNS)
        if intent_column is None:
            choices = ", ".join(INTENT_COLUMNS)
            raise ValueError(
                f"{path}: no classification column found; expected one of {choices}"
            )

        direct_cid_columns = [
            column
            for candidate in CID_COLUMNS
            if (column := resolve_column(reader.fieldnames, (candidate,))) is not None
        ]
        message_column = resolve_column(reader.fieldnames, MESSAGE_COLUMNS)
        if not direct_cid_columns and message_column is None:
            choices = ", ".join((*CID_COLUMNS, *MESSAGE_COLUMNS))
            raise ValueError(f"{path}: no CID source found; expected one of {choices}")

        for row in reader:
            intent = parse_intent(row.get(intent_column, "") or "")
            row_counts[intent] += 1
            cids = row_cids(row, direct_cid_columns, message_column)
            if not cids:
                missing_cid_counts[intent] += 1
                continue
            intent_cids[intent].update(cids)
            all_cids.update(cids)

    rows = [
        {
            "dataset": dataset,
            "classification_intent": intent,
            "input_row_count": row_counts[intent],
            "unique_cid_count": len(intent_cids[intent]),
            "rows_without_cid": missing_cid_counts[intent],
        }
        for intent in sorted(row_counts)
    ]
    rows.append(
        {
            "dataset": dataset,
            "classification_intent": "ALL_INTENTS",
            "input_row_count": sum(row_counts.values()),
            "unique_cid_count": len(all_cids),
            "rows_without_cid": sum(missing_cid_counts.values()),
        }
    )
    return rows


def parse_dataset(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("must use NAME=CSV_PATH")
    name, raw_path = value.split("=", 1)
    name = name.strip()
    path = Path(raw_path.strip())
    if not name or not raw_path.strip():
        raise argparse.ArgumentTypeError("must use a non-empty NAME=CSV_PATH")
    return name, path


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=(
                "dataset",
                "classification_intent",
                "input_row_count",
                "unique_cid_count",
                "rows_without_cid",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare intent and unique-CID counts across CSV exports."
    )
    parser.add_argument(
        "--dataset",
        action="append",
        type=parse_dataset,
        required=True,
        metavar="NAME=CSV_PATH",
        help="named CSV input; repeat once per period",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(arguments)


def main() -> None:
    args = parse_args()
    rows: list[dict[str, Any]] = []
    for dataset, path in args.dataset:
        rows.extend(summarize_csv(dataset, path))
    write_report(args.output, rows)
    print(f"Created {args.output.resolve()}")
    for row in rows:
        print(
            f"{row['dataset']} / {row['classification_intent']}: "
            f"rows={row['input_row_count']:,}, "
            f"unique_cids={row['unique_cid_count']:,}, "
            f"missing_cid_rows={row['rows_without_cid']:,}"
        )


if __name__ == "__main__":
    main()

"""Classify NORA context-history records stored in Azure Cosmos DB.

Authentication uses azure.identity.DefaultAzureCredential. By default this program
is read-only: it exports one JSONL label per source record. Use --write-back only
after reviewing a sample of the exported labels.

Required packages:
    pip install azure-identity azure-cosmos

Example:
    az login
    python intent_app.py

Configuration is read from intent_app_config.env beside this script. Use
--config only when the configuration file is stored elsewhere.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from interaction_reader import get_interaction as read_interaction
from pipeline_logging import LOGGER, configure_logging, progress


TICKET_ID_RE = re.compile(
    r"\b(?:INC|CASE|SR|TKT|TICKET)[\s_-]?\d{3,}\b", re.IGNORECASE
)

TICKET_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:create|raise|open|submit|log|file)\b.{0,30}\b(?:ticket|case|incident|service request)\b",
        r"\b(?:ticket|case|incident|service request)\b.{0,30}\b(?:create|raise|open|submit|update|close|cancel|reopen|escalate)\b",
        r"\b(?:update|close|cancel|reopen|escalate)\b.{0,30}\b(?:ticket|case|incident|service request)\b",
        r"\b(?:ticket|case|incident)\s+(?:status|priority|severity|number|id)\b",
    )
]

MODIFY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:change|modify|update|configure|enable|disable|reset)\b.{0,60}\b(?:configuration|setting|feature|plan|apn|service)\b",
        r"\b(?:enable|disable|reset)\b.{0,40}\b(?:roaming|voicemail|data|feature)\b",
        r"\b(?:set up|setup|activate|install|provision)\b.{0,60}\b(?:device|service|internet|hsi|line|feature)\b",
    )
]

RCA_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:investigate|diagnose|troubleshoot(?:ing)?|root cause|run rca|perform rca)\b",
        r"\bwhy\b.{0,100}\b(?:not working|failed|failing|offline|down|issue|problem)\b",
        r"\b(?:customer|subscriber|device|service|network)\b.{0,80}\b(?:not working|failed|failing|offline|down|issue|problem)\b",
        r"\b(?:router|internet|wifi|wi-fi|signal|call|roaming|device|service)\b.{0,80}\b(?:not working|failed|failing|offline|down|dropping|disconnect|issue|problem|no (?:internet )?connection)\b",
    )
]

QUERY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(?:what|when|where|which|who|how many)\b",
        r"^\s*(?:is|are|does|do|did|has|have|can)\b",
        r"\b(?:show|tell|provide|check|find|get|display)\b.{0,60}\b(?:status|value|details|information|location|site|plan|signal|usage|history)\b",
        r"\b(?:verify|confirm|want(?:s|ed)? to know|would like to know)\b.{0,80}\b(?:status|online|hours|details|information|plan|usage|history|location)\b",
    )
]

GENERAL_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:in general|generally|documentation|policy|procedure|how does|what is)\b",
        r"\b(?:explain|define|meaning of)\b",
    )
]

CUSTOMER_FIELDS = (
    "customer_id",
    "customerId",
    "customer_name",
    "customerName",
    "ban",
    "imei",
    "msisdn",
    "impacted_number",
    "impactedNumber",
    "impacted_device",
    "impactedDevice",
    "account_number",
    "subscriber_id",
)

MESSAGE_FIELDS = (
    "user_message",
    "userMessage",
    "message",
    "question",
    "query",
    "request",
    "prompt",
    "utterance",
    "content",
)

CONVERSATION_ID_FIELDS = (
    "cid",
    "conversationId",
    "conversation_id",
    "sessionId",
    "session_id",
    "threadId",
    "thread_id",
)

TIMESTAMP_FIELDS = ("timestamp", "createdAt", "created_at", "_ts")
ISSUE_FIELDS = (
    "issue_summary",
    "issueSummary",
    "issue",
    "issue_description",
    "description",
    "summary",
)

PII_PATTERNS = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"(?<!\d)\+?\d[\d ()-]{6,}\d(?!\d)"), "[PHONE_OR_ACCOUNT]"),
    (re.compile(r"\b\d{14,16}\b"), "[DEVICE_ID]"),
)


@dataclass(frozen=True)
class Prediction:
    intent: str
    confidence: float
    rule: str
    reason: str
    needs_human_review: bool


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return ""


def nested_dict(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("conversation")
    return value if isinstance(value, dict) else {}


def parse_json_value(value: Any) -> Any:
    """Decode object/array JSON strings while leaving normal strings unchanged."""
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate or candidate[0] not in "[{":
        return value
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return value


def user_inputs(record: dict[str, Any]) -> Any:
    return parse_json_value(record.get("user_inputs"))


def first_value(record: dict[str, Any], fields: Iterable[str]) -> Any:
    conversation = nested_dict(record)
    for source in (record, conversation):
        for field in fields:
            value = source.get(field)
            if value not in (None, "", "N/A"):
                return value
    return None


def nested_first_value(value: Any, fields: Iterable[str]) -> Any:
    """Find the first non-empty named value inside dictionaries and arrays."""
    field_names = set(fields)
    if isinstance(value, dict):
        for key, item in value.items():
            if key in field_names and item not in (None, "", "N/A"):
                return item
        for item in value.values():
            found = nested_first_value(item, field_names)
            if found not in (None, "", "N/A"):
                return found
    elif isinstance(value, list):
        for item in value:
            found = nested_first_value(item, field_names)
            if found not in (None, "", "N/A"):
                return found
    return None


def message_lists(record: dict[str, Any]) -> Iterable[list[Any]]:
    conversation = nested_dict(record)
    for source in (record, conversation):
        for field in ("messages", "history"):
            messages = source.get(field)
            if isinstance(messages, list):
                yield messages


def message_role(message: dict[str, Any]) -> str:
    data = message.get("data") if isinstance(message.get("data"), dict) else {}
    return str(message.get("role") or message.get("type") or data.get("role") or data.get("type") or "").lower()


def message_text(message: dict[str, Any]) -> str:
    data = message.get("data") if isinstance(message.get("data"), dict) else {}
    return compact_text(
        message.get("content")
        or message.get("text")
        or message.get("message")
        or data.get("content")
        or data.get("text")
        or data.get("message")
    )


def extract_user_text_with_source(record: dict[str, Any]) -> tuple[str, str]:
    """Return the selected user utterance and its source path."""
    for messages in message_lists(record):
        user_messages = [
            message_text(message)
            for message in messages
            if isinstance(message, dict)
            and message_role(message) in {"user", "customer", "human"}
        ]
        user_messages = [text for text in user_messages if text]
        if user_messages:
            return user_messages[-1], "messages[].data.content"

    user_input_text = compact_text(
        nested_first_value(user_inputs(record), MESSAGE_FIELDS + ISSUE_FIELDS)
    )
    if user_input_text:
        return user_input_text, "user_inputs"
    direct_text = compact_text(first_value(record, MESSAGE_FIELDS))
    return direct_text, "direct_message_field" if direct_text else "not_found"


def extract_user_text(record: dict[str, Any]) -> str:
    """Return only a user utterance, never an assistant response."""
    return extract_user_text_with_source(record)[0]


def extract_issue(record: dict[str, Any]) -> str:
    direct_issue = compact_text(first_value(record, ISSUE_FIELDS))
    if direct_issue:
        return direct_issue
    return compact_text(nested_first_value(user_inputs(record), ISSUE_FIELDS))


def conversation_id(record: dict[str, Any]) -> str:
    value = first_value(record, CONVERSATION_ID_FIELDS)
    if value is None:
        for messages in message_lists(record):
            value = nested_first_value(messages, ("cid",))
            if value is not None:
                break
    # If no conversation key exists, do not accidentally join unrelated records.
    return str(value if value is not None else record.get("id", "unknown"))


def sort_value(record: dict[str, Any]) -> tuple[int, str]:
    value = first_value(record, TIMESTAMP_FIELDS)
    if isinstance(value, (int, float)):
        return (0, f"{float(value):020.6f}")
    return (1, str(value or ""))


def has_customer_context(record: dict[str, Any], prior_context: bool = False) -> bool:
    if prior_context:
        return True
    return (
        first_value(record, CUSTOMER_FIELDS) is not None
        or nested_first_value(user_inputs(record), CUSTOMER_FIELDS) is not None
    )


def matches(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify(
    record: dict[str, Any],
    *,
    active_ticket: bool = False,
    prior_customer_context: bool = False,
) -> Prediction:
    """Classify one record using the documented first-match rule order."""
    text = extract_user_text(record)
    issue = extract_issue(record)
    customer_context = has_customer_context(record, prior_customer_context)

    # Ticket is sticky across turns. A production workflow should also provide an
    # explicit workflow-complete flag so persistence can end deterministically.
    if active_ticket:
        return Prediction(
            "ticket", 0.98, "ticket.active_workflow",
            "Continuation of an active ticket workflow.", False
        )

    if TICKET_ID_RE.search(text) or matches(text, TICKET_PATTERNS):
        return Prediction(
            "ticket", 0.98, "ticket.explicit_reference_or_action",
            "Explicit ticket/case reference or action.", False
        )

    if matches(text, MODIFY_PATTERNS):
        return Prediction(
            "modify", 0.91, "modify.configuration_change",
            "Customer configuration change was requested.", False
        )

    if matches(text, RCA_PATTERNS):
        return Prediction(
            "rca", 0.90, "rca.issue_diagnosis",
            "Customer issue investigation or diagnosis was requested.", False
        )

    if text and customer_context and matches(text, QUERY_PATTERNS):
        return Prediction(
            "query", 0.90, "query.customer_question",
            "One focused question about a known customer.", False
        )

    if text and not customer_context and matches(text, GENERAL_PATTERNS):
        return Prediction(
            "general", 0.78, "general.non_customer_question",
            "General question without customer context.", True
        )

    if not text and issue:
        return Prediction(
            "clarification_needed",
            0.72,
            "clarification.missing_user_text",
            "Issue summary exists, but no explicit user request was found.",
            True,
        )

    return Prediction(
        "clarification_needed",
        0.55,
        "clarification.no_rule_match",
        "No high-confidence intent rule matched the user request.",
        True,
    )


def flatten_record(
    value: Any,
    *,
    prefix: str = "",
    separator: str = ".",
) -> dict[str, Any]:
    """Flatten nested JSON objects into CSV-friendly key/value columns."""
    flattened: dict[str, Any] = {}

    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}{separator}{key}" if prefix else str(key)
            flattened.update(
                flatten_record(item, prefix=name, separator=separator)
            )
    elif isinstance(value, list):
        # Preserve arrays without creating a variable number of CSV columns.
        flattened[prefix] = json.dumps(value, ensure_ascii=False)
    else:
        flattened[prefix] = value

    return flattened


def flatten_user_inputs(value: Any) -> dict[str, Any]:
    """Flatten user_inputs into stable, review-friendly extracted columns."""
    value = parse_json_value(value)
    prefix = "extracted.user_inputs"
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], dict):
            return flatten_record(value[0], prefix=prefix)
        flattened: dict[str, Any] = {}
        for index, item in enumerate(value):
            flattened.update(flatten_record(item, prefix=f"{prefix}.{index}"))
        return flattened
    if isinstance(value, dict):
        return flatten_record(value, prefix=prefix)
    if value not in (None, ""):
        return {prefix: value}
    return {}


def label_records(
    records: list[dict[str, Any]], *, include_source_fields: bool = False
) -> list[dict[str, Any]]:
    """Group records by CID, classify them in time order, and build CSV rows."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[conversation_id(record)].append(record)

    labels: list[dict[str, Any]] = []
    classified_at = datetime.now(timezone.utc).isoformat()

    for conv_id, conversation_records in grouped.items():
        conversation_records.sort(key=sort_value)
        active_ticket = False
        customer_context = False

        for record in conversation_records:
            user_text, _ = extract_user_text_with_source(record)
            issue = extract_issue(record)
            extracted_user_inputs = flatten_user_inputs(record.get("user_inputs"))
            record_has_customer_context = has_customer_context(
                record, customer_context
            )
            prediction = classify(
                record,
                active_ticket=active_ticket,
                prior_customer_context=customer_context,
            )

            customer_context = has_customer_context(record, customer_context)
            if prediction.intent == "ticket":
                active_ticket = True

            label = {
                "source_id": record.get("id"),
                "conversation_id": conv_id,
                "extracted.user_text[messages[].data.content]": (
                    user_text
                ),
                **extracted_user_inputs,
                "extracted.issue": issue,
                "extracted.has_customer_context": record_has_customer_context,
                "classification.intent": prediction.intent,
                "classification.confidence": prediction.confidence,
                "classification.rule": prediction.rule,
                "classification.reason": prediction.reason,
                "classification.needs_human_review": prediction.needs_human_review,
                "classification.version": "rules-v2",
                "classification.classified_at": classified_at,
            }

            if include_source_fields:
                # Prefix source fields to avoid collisions with classification data.
                source_record = dict(record)
                source_record.pop("user_inputs", None)
                source = {
                    f"source.{key}": value
                    for key, value in flatten_record(source_record).items()
                }
                label = {**source, **label}

            labels.append(label)

    return labels


def load_cids_from_csv(path: Path, column: str = "cid") -> list[str]:
    """Read unique CID strings, preserving leading zeros and CSV order."""
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or column not in reader.fieldnames:
            raise ValueError(f"CID CSV must contain the column {column!r}: {path}")
        cids = list(dict.fromkeys(
            str(row.get(column) or "").strip()
            for row in reader if str(row.get(column) or "").strip()
        ))
    if not cids:
        raise ValueError(f"CID CSV contains no nonempty CIDs: {path}")
    return cids


def iter_records_for_cids(container, cids, start_time, end_time, batch_size):
    """Query requested CIDs using the shapes supported by conversation_id."""
    matches = [f"c.{field} = @cid" for field in CONVERSATION_ID_FIELDS]
    matches += [f"c.conversation.{field} = @cid" for field in CONVERSATION_ID_FIELDS]
    for source in ("c", "c.conversation"):
        for field in ("messages", "history"):
            matches.append(
                f"EXISTS (SELECT VALUE m FROM m IN {source}.{field} "
                "WHERE m.cid = @cid OR m.data.cid = @cid)"
            )
    # conversation_id also uses the document id when no explicit CID exists.
    matches.append("c.id = @cid")
    filters = ["(" + " OR ".join(matches) + ")"]
    bounds = []
    for name, value, operator in (("start", start_time, ">="), ("end", end_time, "<=")):
        if value is not None:
            filters.append(f"c._ts {operator} @{name}")
            bounds.append({"name": f"@{name}", "value": int(value.timestamp())})
    seen = set()
    for index, cid in enumerate(cids, start=1):
        LOGGER.info("CID query %s/%s starting", index, len(cids))
        options = dict(
            query="SELECT * FROM c WHERE " + " AND ".join(filters) + " ORDER BY c._ts DESC",
            parameters=[{"name": "@cid", "value": cid}, *bounds],
            enable_cross_partition_query=True,
        )
        if batch_size is not None:
            options["max_item_count"] = batch_size
        for record in container.query_items(**options):
            # Match the classifier's CID precedence for documents with multiple IDs.
            if conversation_id(record) != cid:
                continue
            key = cosmos_record_key(record)
            if key not in seen:
                seen.add(key)
                yield record
        LOGGER.info("CID query %s/%s completed; %s unique records fetched", index, len(cids), len(seen))


def load_cosmos_records(
    container: Any,
    max_records: int | None,
    workers: int = 1,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    batch_size: int | None = None,
    cids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Read source records from Cosmos."""
    if workers != 1:
        print(
            "INTENT_MAX_WORKERS is ignored by the synchronous Cosmos client; "
            "using one reader"
        )

    records: list[dict[str, Any]] = []
    for item in iter_cosmos_records(container, start_time, end_time, batch_size, cids):
        records.append(item)
        if len(records) == 1 or len(records) % 100 == 0:
            LOGGER.info("Source records fetched: %s", len(records))
        if max_records is not None and len(records) >= max_records:
            break
    return records


def iter_cosmos_records(
    container: Any,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    batch_size: int | None = None,
    cids: list[str] | None = None,
) -> Iterable[dict[str, Any]]:
    """Read all records or query an inclusive Cosmos `_ts` window."""
    if cids is not None:
        return iter_records_for_cids(container, cids, start_time, end_time, batch_size)
    if start_time is None and end_time is None:
        if batch_size is None:
            return container.read_all_items()
        return container.read_all_items(max_item_count=batch_size)

    filters: list[str] = []
    parameters: list[dict[str, Any]] = []
    if start_time is not None:
        filters.append("c._ts >= @start_ts")
        parameters.append(
            {"name": "@start_ts", "value": int(start_time.timestamp())}
        )
    if end_time is not None:
        filters.append("c._ts <= @end_ts")
        parameters.append(
            {"name": "@end_ts", "value": int(end_time.timestamp())}
        )

    query_options: dict[str, Any] = {
        "query": f"SELECT * FROM c WHERE {' AND '.join(filters)} ORDER BY c._ts DESC",
        "parameters": parameters,
        "enable_cross_partition_query": True,
    }
    if batch_size is not None:
        query_options["max_item_count"] = batch_size
    return container.query_items(**query_options)


def cosmos_record_key(record: dict[str, Any]) -> str:
    """Return the Cosmos-generated identity used when comparing two reads."""
    resource_id = record.get("_rid")
    if resource_id:
        return f"rid:{resource_id}"

    # _rid should exist on Cosmos records.  The fallback keeps local fixtures and
    # exported test data useful without pretending that id alone is always unique.
    return "record:" + json.dumps(record, sort_keys=True, default=str)


def find_missing_cosmos_records(
    container: Any,
    exported_records: Iterable[dict[str, Any]],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    cids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Repeat the same Cosmos scope and return records absent from the first read."""
    exported_keys = {cosmos_record_key(record) for record in exported_records}
    missing: list[dict[str, Any]] = []
    inventory_count = 0

    if cids is not None:
        items = iter_cosmos_records(container, start_time, end_time, cids=cids)
    elif start_time is None and end_time is None:
        items = container.query_items(
            query="SELECT * FROM c",
            enable_cross_partition_query=True,
        )
    else:
        items = iter_cosmos_records(container, start_time, end_time)
    for item in items:
        inventory_count += 1
        if cosmos_record_key(item) not in exported_keys:
            missing.append(item)

    return missing, inventory_count


def write_jsonl(path: Path, labels: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for label in labels:
            file.write(json.dumps(label, ensure_ascii=False) + "\n")


def write_csv(
    path: Path,
    labels: list[dict[str, Any]],
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(dict.fromkeys(key for label in labels for key in label))
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(labels)


def write_classification_output(
    path: Path,
    labels: list[dict[str, Any]],
    clarification_path: Path | None = None,
) -> list[Path]:
    """Optionally separate clarification rows while retaining the same CSV columns."""
    if clarification_path is not None:
        if path.suffix.lower() != ".csv" or clarification_path.suffix.lower() != ".csv":
            raise ValueError("Separate clarification output requires two CSV paths")
        if path.resolve() == clarification_path.resolve():
            raise ValueError("Clarification output must differ from INTENT_OUTPUT")
        columns = list(dict.fromkeys(key for label in labels for key in label))
        if not columns:
            columns = ["source_id", "conversation_id", "classification.intent"]
        remaining = []
        clarification = []
        for label in labels:
            if label.get("classification.intent") == "clarification_needed":
                clarification.append(label)
            else:
                remaining.append(label)
        write_csv(path, remaining, columns)
        write_csv(clarification_path, clarification, columns)
        return [path, clarification_path]
    if path.suffix.lower() == ".csv":
        write_csv(path, labels)
    elif path.suffix.lower() in {".jsonl", ".ndjson"}:
        write_jsonl(path, labels)
    else:
        raise ValueError("INTENT_OUTPUT must end in .csv, .jsonl, or .ndjson")
    return [path]


def build_intent_count_rows(
    labels: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build deterministic record and distinct-CID counts for every intent."""
    record_counts: Counter[str] = Counter()
    intent_cids: dict[str, set[str]] = defaultdict(set)
    all_cids: set[str] = set()

    for label in labels:
        intent = str(label.get("classification.intent", "")).strip()
        if not intent:
            continue
        record_counts[intent] += 1

        cid = str(label.get("conversation_id", "")).strip()
        if cid:
            intent_cids[intent].add(cid)
            all_cids.add(cid)

    rows = [
        {
            "classification_intent": intent,
            "classified_record_count": record_counts[intent],
            "unique_cid_count": len(intent_cids[intent]),
        }
        for intent in sorted(record_counts)
    ]
    rows.append(
        {
            "classification_intent": "ALL_INTENTS",
            "classified_record_count": sum(record_counts.values()),
            "unique_cid_count": len(all_cids),
        }
    )
    return rows


def write_intent_count_csv(
    path: Path,
    labels: Iterable[dict[str, Any]],
) -> None:
    rows = build_intent_count_rows(labels)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "classification_intent",
                "classified_record_count",
                "unique_cid_count",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def write_labels_to_container(target_container: Any, labels: Iterable[dict[str, Any]]) -> None:
    for label in labels:
        source_id = label.get("source_id")
        if not source_id:
            continue
        output = dict(label)
        output["id"] = f"intent:{source_id}"
        target_container.upsert_item(output)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE settings without overriding existing environment values."""
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file was not found: {path}. "
            "Create it from intent_app_config.env."
        )

    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                raise ValueError(
                    f"Invalid configuration at {path}:{line_number}; expected KEY=VALUE"
                )
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true or false; received {value!r}")


def env_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required setting {name} is missing")
    return value


def optional_env_time(name: str) -> datetime | None:
    """Parse an optional timezone-aware timestamp from configuration."""
    value = os.environ.get(name, "").strip()
    return parse_iso_time(value) if value else None


def configured_output_path(setting: str, default: str, directory_setting: str) -> Path:
    """Put a bare output filename in its configured artifact directory."""
    path = Path(os.environ.get(setting, default).strip())
    if path.is_absolute() or path.parent != Path("."):
        return path
    directory = Path(os.environ.get(directory_setting, ".").strip() or ".")
    return directory / path


def parse_iso_time(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "must be an ISO 8601 time, for example 2026-09-04T18:00:00+05:30"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "must include a timezone offset, for example +05:30 or Z"
        )
    return parsed.astimezone(timezone.utc)


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Label NORA context-history records with conversation intents."
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("intent_app_config.env")),
        help="Path to the .env configuration file",
    )
    parser.add_argument(
        "--start-time",
        type=parse_iso_time,
        help="inclusive Cosmos document _ts lower bound (ISO 8601 with timezone)",
    )
    parser.add_argument(
        "--end-time",
        type=parse_iso_time,
        help="inclusive Cosmos document _ts upper bound; defaults to process start",
    )
    args = parser.parse_args(arguments)
    if args.end_time is not None and args.start_time is None:
        parser.error("--end-time requires --start-time")
    if args.start_time is not None:
        args.end_time = args.end_time or datetime.now(timezone.utc)
        if args.start_time > args.end_time:
            parser.error("--start-time must be earlier than or equal to --end-time")
    return args


def write_training_placeholder(
    labels: list[dict[str, Any]], csv_path: Path, log_path: Path
) -> None:
    """Write an explicit placeholder manifest; no model is trained yet."""
    counts = Counter(str(label.get("classification.intent", "")) for label in labels)
    rows = [
        {
            "intent": intent,
            "record_count": count,
            "status": "pending_training_implementation",
        }
        for intent, count in sorted(counts.items())
        if intent
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("intent", "record_count", "status"))
        writer.writeheader()
        writer.writerows(rows)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"{datetime.now(timezone.utc).isoformat()} "
        f"training placeholder created for {len(labels)} classified records; "
        "model training was not executed.\n",
        encoding="utf-8",
    )


def redact_training_text(text: str) -> str:
    """Apply conservative PII masking to model-training text."""
    redacted = text
    for pattern, replacement in PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def foundry_split_for_cid(
    cid: str, train_percent: int, validation_percent: int, seed: str
) -> str:
    """Assign a CID deterministically so conversations never cross datasets."""
    bucket = int.from_bytes(
        hashlib.sha256(f"{seed}:{cid}".encode("utf-8")).digest()[:8], "big"
    ) % 100
    if bucket < train_percent:
        return "train"
    if bucket < train_percent + validation_percent:
        return "validation"
    return "test"


def build_foundry_datasets(
    labels: Iterable[dict[str, Any]],
    *,
    train_percent: int = 80,
    validation_percent: int = 10,
    test_percent: int = 10,
    seed: str = "nora-intent-v1",
    redact_pii: bool = True,
    exclude_needs_review: bool = True,
    require_reviewed: bool = False,
    system_prompt: str = (
        "Classify the NORA customer request as ticket, modify, rca, query, "
        "general, or clarification_needed. Return only the intent."
    ),
) -> tuple[dict[str, list[dict[str, Any]]], Counter[str]]:
    """Build deterministic chat-format datasets and rejection statistics."""
    if min(train_percent, validation_percent, test_percent) < 0:
        raise ValueError("Foundry split percentages cannot be negative")
    if train_percent + validation_percent + test_percent != 100:
        raise ValueError("Foundry train, validation, and test percentages must total 100")

    datasets: dict[str, list[dict[str, Any]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    rejected: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    text_field = "extracted.user_text[messages[].data.content]"

    for label in labels:
        cid = str(label.get("conversation_id", "")).strip()
        text = compact_text(label.get(text_field))
        intent = str(label.get("classification.intent", "")).strip()
        if not cid:
            rejected["missing_cid"] += 1
            continue
        if not text:
            rejected["missing_user_text"] += 1
            continue
        if not intent:
            rejected["missing_intent"] += 1
            continue
        if exclude_needs_review and bool(
            label.get("classification.needs_human_review", False)
        ):
            rejected["needs_human_review"] += 1
            continue
        reviewed_flag = label.get("human_reviewed", label.get("source.human_reviewed"))
        review_status = label.get(
            "review.status", label.get("source.review.status", "")
        )
        reviewed = reviewed_flag is True or str(review_status).strip().lower() in {
            "approved",
            "accepted",
        }
        if require_reviewed and not reviewed:
            rejected["not_human_reviewed"] += 1
            continue

        if redact_pii:
            text = redact_training_text(text)
        duplicate_key = (text.casefold(), intent.casefold())
        if duplicate_key in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(duplicate_key)
        split = foundry_split_for_cid(cid, train_percent, validation_percent, seed)
        datasets[split].append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": intent},
                ]
            }
        )
    return datasets, rejected


def write_foundry_jsonl(path: Path, examples: Iterable[dict[str, Any]]) -> None:
    """Write Foundry chat JSONL as UTF-8 with BOM, one example per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        for example in examples:
            file.write(json.dumps(example, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")


def write_feature_report(
    path: Path,
    datasets: dict[str, list[dict[str, Any]]],
    rejected: Counter[str],
) -> None:
    """Write auditable accepted/rejected counts for the feature build."""
    rows = [
        {"category": "accepted", "name": split, "record_count": len(examples)}
        for split, examples in datasets.items()
    ]
    rows.extend(
        {"category": "rejected", "name": reason, "record_count": count}
        for reason, count in sorted(rejected.items())
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("category", "name", "record_count"))
        writer.writeheader()
        writer.writerows(rows)


def create_foundry_artifacts(
    labels: list[dict[str, Any]],
) -> tuple[dict[str, Path], dict[str, int]]:
    """Build and save the configured Foundry training datasets."""
    output_paths = {
        "train": configured_output_path(
            "FOUNDRY_TRAIN_OUTPUT", "foundry_train.jsonl", "FEATURE_OUTPUT_DIR"
        ),
        "validation": configured_output_path(
            "FOUNDRY_VALIDATION_OUTPUT",
            "foundry_validation.jsonl",
            "FEATURE_OUTPUT_DIR",
        ),
        "test": configured_output_path(
            "FOUNDRY_TEST_OUTPUT", "foundry_test.jsonl", "FEATURE_OUTPUT_DIR"
        ),
    }
    report_path = configured_output_path(
        "FEATURE_REPORT_OUTPUT", "foundry_feature_report.csv", "FEATURE_OUTPUT_DIR"
    )
    datasets, rejected = build_foundry_datasets(
        labels,
        train_percent=env_int("TRAIN_PERCENT") or 80,
        validation_percent=env_int("VALIDATION_PERCENT") or 10,
        test_percent=env_int("TEST_PERCENT") or 10,
        seed=os.environ.get("FEATURE_SPLIT_SEED", "nora-intent-v1"),
        redact_pii=env_bool("FEATURE_REDACT_PII", True),
        exclude_needs_review=env_bool("FEATURE_EXCLUDE_NEEDS_REVIEW", True),
        require_reviewed=env_bool("FEATURE_REQUIRE_REVIEWED_LABELS", False),
    )
    for split, path in output_paths.items():
        write_foundry_jsonl(path, datasets[split])
    write_feature_report(report_path, datasets, rejected)

    paths = {**output_paths, "report": report_path}
    counts = {split: len(examples) for split, examples in datasets.items()}
    return paths, counts


def wait_for_foundry_file(
    client: Any, file_id: str, timeout_seconds: int, poll_seconds: int
) -> Any:
    """Wait until Foundry has validated an uploaded fine-tuning file."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        uploaded_file = client.files.retrieve(file_id)
        status = str(getattr(uploaded_file, "status", "")).lower()
        if status == "processed":
            return uploaded_file
        if status in {"error", "failed", "cancelled"}:
            details = getattr(uploaded_file, "status_details", None)
            raise RuntimeError(
                f"Foundry rejected file {file_id}: status={status}, details={details}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for Foundry file {file_id}")
        time.sleep(poll_seconds)


def submit_foundry_training(
    client: Any,
    train_path: Path,
    validation_path: Path,
    *,
    model: str,
    suffix: str,
    seed: int | None,
    training_type: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    """Upload datasets and submit one supervised fine-tuning job."""
    with train_path.open("rb") as train_file:
        train_upload = client.files.create(file=train_file, purpose="fine-tune")
    with validation_path.open("rb") as validation_file:
        validation_upload = client.files.create(
            file=validation_file, purpose="fine-tune"
        )

    wait_for_foundry_file(client, train_upload.id, timeout_seconds, poll_seconds)
    wait_for_foundry_file(
        client, validation_upload.id, timeout_seconds, poll_seconds
    )
    request: dict[str, Any] = {
        "training_file": train_upload.id,
        "validation_file": validation_upload.id,
        "model": model,
    }
    if suffix:
        request["suffix"] = suffix
    if seed is not None:
        request["seed"] = seed
    if training_type:
        request["extra_body"] = {"trainingType": training_type}
    job = client.fine_tuning.jobs.create(**request)
    return {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job.id,
        "status": getattr(job, "status", "submitted"),
        "model": model,
        "training_file_id": train_upload.id,
        "validation_file_id": validation_upload.id,
    }


def start_foundry_training(
    feature_paths: dict[str, Path], feature_counts: dict[str, int]
) -> tuple[Path, dict[str, Any]]:
    """Create a Foundry client, submit training, and persist the job receipt."""
    if feature_counts["train"] < 10:
        raise ValueError("Foundry fine-tuning requires at least 10 training examples")
    if feature_counts["validation"] < 1:
        raise ValueError("Automated Foundry training requires validation examples")
    try:
        from azure.ai.projects import AIProjectClient
    except ImportError as error:
        raise RuntimeError(
            "Foundry training requires azure-ai-projects and openai; "
            "run pip install -r requirements.txt"
        ) from error

    project = AIProjectClient(
        endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
    )
    client = project.get_openai_client()
    receipt = submit_foundry_training(
        client,
        feature_paths["train"],
        feature_paths["validation"],
        model=required_env("FOUNDRY_FINE_TUNE_MODEL"),
        suffix=os.environ.get("FOUNDRY_FINE_TUNE_SUFFIX", "nora-intent").strip(),
        seed=env_int("FOUNDRY_FINE_TUNE_SEED"),
        training_type=os.environ.get(
            "FOUNDRY_FINE_TUNE_TRAINING_TYPE", "GlobalStandard"
        ).strip(),
        timeout_seconds=env_int("FOUNDRY_FILE_TIMEOUT_SECONDS") or 900,
        poll_seconds=env_int("FOUNDRY_POLL_SECONDS") or 10,
    )
    receipt_path = configured_output_path(
        "FOUNDRY_TRAINING_RECEIPT", "foundry_training_job.json", "LOG_OUTPUT_DIR"
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return receipt_path, receipt


def upload_files_to_blob(paths: Iterable[Path], credential: Any) -> list[str]:
    """Upload initial-load artifacts to Blob Storage/ADLS Gen2."""
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as error:
        raise RuntimeError(
            "Blob export requires azure-storage-blob; run pip install -r requirements.txt"
        ) from error

    account_url = required_env("ADLS_ACCOUNT_URL")
    container_name = required_env("ADLS_CONTAINER")
    prefix = os.environ.get("ADLS_BLOB_PREFIX", "intent-training").strip("/")
    service = BlobServiceClient(account_url=account_url, credential=credential)
    container = service.get_container_client(container_name)
    uploaded: list[str] = []
    for path in paths:
        blob_name = f"{prefix}/{path.name}" if prefix else path.name
        with path.open("rb") as data:
            container.upload_blob(name=blob_name, data=data, overwrite=True)
        uploaded.append(blob_name)
    return uploaded


def attach_interactions(database: Any, labels: list[dict[str, Any]]) -> None:
    """Attach each CID's source history once, without changing its intent label."""
    cache: dict[str, dict[str, Any]] = {}
    containers = {
        "chat_container": os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat"),
        "tools_container": os.getenv("COSMOS_TOOLS_CONTAINER", "context-history-all-tools"),
        "context_container": os.getenv("COSMOS_CONTEXT_CONTAINER", "context-history-uat"),
        "feedback_container": os.getenv("COSMOS_FEEDBACK_CONTAINER", "chat-feedback"),
    }
    for label in labels:
        cid = str(label.get("conversation_id") or "").strip()
        if not cid:
            raise ValueError("Interaction export requires a conversation_id")
        if cid not in cache:
            with progress(f"interaction {len(cache) + 1}"):
                history = read_interaction(database, cid, **containers)
            cache[cid] = {
                "interaction.run_ids": history["run_ids"],
                "interaction.chat_history": history["chat_history"],
                "interaction.context_history": history["context_history"],
                "interaction.tool_history": history["tool_history"],
                "interaction.feedback": history["feedback"],
            }
        # Serialize arrays as JSON cells for CSV and retain source field names.
        label.update({key: json.dumps(value, ensure_ascii=False, default=str)
                      for key, value in cache[cid].items()})


def write_interaction_output(database, path, labels, clarification_path=None):
    """Flush completed CID groups before fetching the next interaction."""
    paths = [path] if clarification_path is None else [path, clarification_path]
    if path.suffix.lower() not in {".csv", ".jsonl", ".ndjson"}:
        raise ValueError("INTENT_OUTPUT must end in .csv, .jsonl, or .ndjson")
    if clarification_path is not None:
        if any(item.suffix.lower() != ".csv" for item in paths):
            raise ValueError("Separate clarification output requires two CSV paths")
        if path.resolve() == clarification_path.resolve():
            raise ValueError("Clarification output must differ from INTENT_OUTPUT")
    columns = list(dict.fromkeys(key for label in labels for key in label))
    columns = list(dict.fromkeys([*columns, "interaction.run_ids",
        "interaction.chat_history", "interaction.context_history",
        "interaction.tool_history", "interaction.feedback"]))
    grouped = defaultdict(list)
    for label in labels:
        grouped[label.get("conversation_id")].append(label)
    with ExitStack() as stack:
        files, writers = [], []
        for target in paths:
            target.parent.mkdir(parents=True, exist_ok=True)
            file = stack.enter_context(target.open(
                "w", encoding="utf-8-sig" if target.suffix.lower() == ".csv" else "utf-8",
                newline="",
            ))
            files.append(file)
            writer = csv.DictWriter(file, fieldnames=columns) if target.suffix.lower() == ".csv" else None
            writers.append(writer)
            if writer is not None:
                writer.writeheader()
            file.flush()
            LOGGER.info("Opened incremental output: %s", target.resolve())
        saved = 0
        for index, group in enumerate(grouped.values(), start=1):
            attach_interactions(database, group)
            for label in group:
                destination = int(clarification_path is not None and
                                  label.get("classification.intent") == "clarification_needed")
                if writers[destination] is not None:
                    writers[destination].writerow(label)
                else:
                    files[destination].write(json.dumps(label, ensure_ascii=False) + "\n")
            for file in files:
                file.flush()
            saved += len(group)
            LOGGER.info("Saved %s rows; interactions completed %s/%s", saved, index, len(grouped))
    return paths


def run_initial_load(args: argparse.Namespace) -> None:
    """Run the configured historical extraction and classification once."""

    endpoint = required_env("COSMOS_ENDPOINT")
    database_name = required_env("COSMOS_DATABASE")
    container_name = required_env("COSMOS_CONTAINER")
    output_path = configured_output_path(
        "INTENT_OUTPUT", "intent_labels_all.csv", "CSV_OUTPUT_DIR"
    )
    default_count_path = output_path.with_name(f"{output_path.stem}_counts.csv")
    configured_count_path = os.environ.get("INTENT_COUNT_OUTPUT", "").strip()
    count_output_path = (
        configured_output_path(
            "INTENT_COUNT_OUTPUT", configured_count_path, "CSV_OUTPUT_DIR"
        )
        if configured_count_path
        else default_count_path
    )
    if count_output_path.suffix.lower() != ".csv":
        raise ValueError("INTENT_COUNT_OUTPUT must end in .csv")
    if count_output_path.resolve() == output_path.resolve():
        raise ValueError("INTENT_COUNT_OUTPUT must differ from INTENT_OUTPUT")
    max_records = env_int("INTENT_MAX_RECORDS")
    workers = env_int("INTENT_MAX_WORKERS") or 1
    batch_size = env_int("INITIAL_BATCH_SIZE")
    find_missing = env_bool("INTENT_FIND_MISSING", False)
    missing_output_path = configured_output_path(
        "INTENT_MISSING_OUTPUT", "intent_labels_missing.csv", "CSV_OUTPUT_DIR"
    )
    include_source_fields = env_bool("INTENT_INCLUDE_SOURCE_FIELDS", True)
    write_back = env_bool("INTENT_WRITE_BACK", False)
    target_container_name = os.environ.get(
        "INTENT_TARGET_CONTAINER", "intent-labels"
    ).strip()

    start_time = args.start_time or optional_env_time("INITIAL_START_TIME")
    end_time = args.end_time or optional_env_time("INITIAL_END_TIME")
    if end_time is not None and start_time is None:
        raise ValueError("INITIAL_END_TIME requires INITIAL_START_TIME")
    if start_time is not None:
        end_time = end_time or datetime.now(timezone.utc)
        if start_time > end_time:
            raise ValueError("INITIAL_START_TIME must be earlier than INITIAL_END_TIME")

    cids = None
    if env_bool("INTENT_CIDS_FROM_CSV", False):
        cids = load_cids_from_csv(
            Path(required_env("INTENT_CID_CSV")),
            os.getenv("INTENT_CID_COLUMN", "cid"),
        )
        print(f"Selected {len(cids)} unique CIDs from CSV")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Classification output directory: %s", output_path.parent.resolve())
    with progress("Azure credential initialization"):
        credential = DefaultAzureCredential()
    with progress("Cosmos client initialization and authentication"):
        client = CosmosClient(endpoint, credential=credential)
    database = client.get_database_client(database_name)
    source_container = database.get_container_client(container_name)

    if start_time is not None:
        print(
            "Filtering Cosmos documents by _ts (UTC, inclusive): "
            f"{start_time.isoformat()} through {end_time.isoformat()}"
        )
    with progress("Cosmos source retrieval (including lazy authentication)"):
        records = load_cosmos_records(
            source_container, max_records, workers, start_time, end_time, batch_size, cids,
        )
    with progress(f"intent classification of {len(records)} records"):
        labels = label_records(records, include_source_fields=include_source_fields)
    include_interactions = env_bool("INTENT_INCLUDE_INTERACTIONS", False)
    clarification_path = None
    if env_bool("INTENT_SEPARATE_CLARIFICATION", False):
        clarification_path = configured_output_path(
            "INTENT_CLARIFICATION_OUTPUT", "intent_clarification_needed.csv", "CSV_OUTPUT_DIR"
        )
        if clarification_path.resolve() in {
            count_output_path.resolve(), missing_output_path.resolve()
        }:
            raise ValueError("Clarification output must differ from count and missing outputs")
    with progress("writing classification and count outputs"):
        if include_interactions:
            classification_paths = write_interaction_output(database, output_path, labels, clarification_path)
        else:
            classification_paths = write_classification_output(output_path, labels, clarification_path)
        write_intent_count_csv(count_output_path, labels)

    missing_records: list[dict[str, Any]] = []
    inventory_count: int | None = None
    if find_missing:
        if max_records is not None:
            raise ValueError(
                "INTENT_FIND_MISSING requires INTENT_MAX_RECORDS to be empty"
            )
        with progress("second Cosmos inventory pass"):
            missing_records, inventory_count = find_missing_cosmos_records(
                source_container, records, start_time, end_time, cids,
            )
        combined_labels = label_records(
            [*records, *missing_records],
            # Keep _rid during the comparison even when the main export hides
            # source fields. It is removed below before writing when requested.
            include_source_fields=True,
        )
        missing_keys = {cosmos_record_key(record) for record in missing_records}
        missing_labels = [
            label
            for label in combined_labels
            if (
                f"rid:{label.get('source._rid')}" in missing_keys
                if label.get("source._rid")
                else False
            )
        ]
        if not include_source_fields:
            missing_labels = [
                {
                    key: value
                    for key, value in label.items()
                    if not key.startswith("source.")
                }
                for label in missing_labels
            ]
        if include_interactions:
            write_interaction_output(database, missing_output_path, missing_labels)
        else:
            write_csv(missing_output_path, missing_labels)

    if write_back:
        target = database.get_container_client(target_container_name)
        with progress("Cosmos label write-back"):
            write_labels_to_container(target, labels)

    artifact_paths = [*classification_paths, count_output_path]
    if env_bool("TRAINING_PLACEHOLDER_ENABLED", True):
        training_csv = configured_output_path(
            "TRAINING_MANIFEST_OUTPUT", "training_manifest.csv", "CSV_OUTPUT_DIR"
        )
        training_log = configured_output_path(
            "TRAINING_LOG_OUTPUT", "training.log", "LOG_OUTPUT_DIR"
        )
        write_training_placeholder(labels, training_csv, training_log)
        artifact_paths.extend((training_csv, training_log))

    foundry_counts: dict[str, int] | None = None
    foundry_receipt: dict[str, Any] | None = None
    if env_bool("FEATURE_BUILD_ENABLED", False):
        with progress("Foundry dataset generation"):
            foundry_paths, foundry_counts = create_foundry_artifacts(labels)
        artifact_paths.extend(foundry_paths.values())
        if env_bool("FOUNDRY_TRAINING_ENABLED", False):
            with progress("Foundry file upload and training submission"):
                receipt_path, foundry_receipt = start_foundry_training(
                    foundry_paths, foundry_counts
                )
            artifact_paths.append(receipt_path)
    elif env_bool("FOUNDRY_TRAINING_ENABLED", False):
        raise ValueError("FOUNDRY_TRAINING_ENABLED requires FEATURE_BUILD_ENABLED=true")

    uploaded: list[str] = []
    if env_bool("ADLS_UPLOAD_ENABLED", False):
        with progress("Blob artifact upload"):
            uploaded = upload_files_to_blob(artifact_paths, credential)

    counts = Counter(label["classification.intent"] for label in labels)
    review_count = sum(
        bool(label["classification.needs_human_review"]) for label in labels
    )

    print(f"Read {len(records):,} source records")
    print("Cosmos read workers: 1")
    print(f"Created {len(labels):,} labels")
    for intent, count in sorted(counts.items()):
        print(f"  {intent}: {count:,}")
    print(f"Human review recommended: {review_count:,}")
    print(f"Local output: {output_path.resolve()}")
    if clarification_path is not None:
        print(f"Clarification output: {clarification_path.resolve()}")
    print(f"Intent count output: {count_output_path.resolve()}")
    if find_missing:
        print(f"Fresh Cosmos inventory: {inventory_count:,}")
        print(f"Records missing from first read: {len(missing_records):,}")
        print(f"Missing-record output: {missing_output_path.resolve()}")
    if write_back:
        print(f"Cosmos output container: {target_container_name}")
    if uploaded:
        print(f"Blob/ADLS artifacts: {', '.join(uploaded)}")
    if foundry_counts is not None:
        print(
            "Foundry feature datasets: "
            + ", ".join(f"{name}={count}" for name, count in foundry_counts.items())
        )
    if foundry_receipt is not None:
        print(
            f"Foundry fine-tuning job: {foundry_receipt['job_id']} "
            f"({foundry_receipt['status']})"
        )

    client.close()
    credential.close()


def run_stream_host() -> None:
    """Start the Azure Functions host that owns change-feed/Event Hub triggers."""
    executable = shutil.which("func")
    if executable is None:
        raise RuntimeError(
            "Streaming requires Azure Functions Core Tools ('func') on this machine. "
            "In Azure, deploy event_app and let the Function App host run it."
        )
    event_app_dir = Path(__file__).with_name("event_app")
    print(f"Starting streaming Function host from {event_app_dir.resolve()}")
    subprocess.run(
        [executable, "start", "--script-root", str(event_app_dir)],
        check=True,
        env=os.environ.copy(),
    )


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.config))
    log_path = configured_output_path("INTENT_LOG_OUTPUT", "intent_pipeline.log", "LOG_OUTPUT_DIR")
    configure_logging(log_path, os.getenv("INTENT_LOG_LEVEL", "INFO"))
    LOGGER.info("Configuration: %s", Path(args.config).resolve())
    LOGGER.info("Progress log: %s", log_path.resolve())
    initial_enabled = env_bool("INITIAL_LOAD_ENABLED", True)
    stream_enabled = env_bool("STREAM_LOAD_ENABLED", False)
    if not initial_enabled and not stream_enabled:
        raise ValueError(
            "At least one of INITIAL_LOAD_ENABLED or STREAM_LOAD_ENABLED must be true"
        )
    if initial_enabled:
        run_initial_load(args)
    if stream_enabled:
        run_stream_host()


if __name__ == "__main__":
    main()

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
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential


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


def load_cosmos_records(
    container: Any,
    max_records: int | None,
    workers: int = 1,
) -> list[dict[str, Any]]:
    """Read source records from Cosmos."""
    if workers != 1:
        print(
            "INTENT_MAX_WORKERS is ignored by the synchronous Cosmos client; "
            "using one reader"
        )

    records: list[dict[str, Any]] = []
    for item in container.read_all_items():
        records.append(item)
        if max_records is not None and len(records) >= max_records:
            break
    return records


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
) -> tuple[list[dict[str, Any]], int]:
    """Run a fresh container query and return records absent from the first read."""
    exported_keys = {cosmos_record_key(record) for record in exported_records}
    missing: list[dict[str, Any]] = []
    inventory_count = 0

    items = container.query_items(
        query="SELECT * FROM c",
        enable_cross_partition_query=True,
    )
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


def write_csv(path: Path, labels: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for label in labels for key in label))
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(labels)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Label NORA context-history records with conversation intents."
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("intent_app_config.env")),
        help="Path to the .env configuration file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.config))

    endpoint = required_env("COSMOS_ENDPOINT")
    database_name = required_env("COSMOS_DATABASE")
    container_name = required_env("COSMOS_CONTAINER")
    output_path = Path(os.environ.get("INTENT_OUTPUT", "intent_labels_all.csv"))
    max_records = env_int("INTENT_MAX_RECORDS")
    workers = env_int("INTENT_MAX_WORKERS") or 1
    find_missing = env_bool("INTENT_FIND_MISSING", False)
    missing_output_path = Path(
        os.environ.get("INTENT_MISSING_OUTPUT", "intent_labels_missing.csv")
    )
    include_source_fields = env_bool("INTENT_INCLUDE_SOURCE_FIELDS", True)
    write_back = env_bool("INTENT_WRITE_BACK", False)
    target_container_name = os.environ.get(
        "INTENT_TARGET_CONTAINER", "intent-labels"
    ).strip()

    credential = DefaultAzureCredential()
    client = CosmosClient(endpoint, credential=credential)
    database = client.get_database_client(database_name)
    source_container = database.get_container_client(container_name)

    records = load_cosmos_records(source_container, max_records, workers)
    labels = label_records(records, include_source_fields=include_source_fields)
    if output_path.suffix.lower() == ".csv":
        write_csv(output_path, labels)
    elif output_path.suffix.lower() in {".jsonl", ".ndjson"}:
        write_jsonl(output_path, labels)
    else:
        raise ValueError("--output must end in .csv, .jsonl, or .ndjson")

    missing_records: list[dict[str, Any]] = []
    inventory_count: int | None = None
    if find_missing:
        if max_records is not None:
            raise ValueError(
                "INTENT_FIND_MISSING requires INTENT_MAX_RECORDS to be empty"
            )
        missing_records, inventory_count = find_missing_cosmos_records(
            source_container,
            records,
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
        write_csv(missing_output_path, missing_labels)

    if write_back:
        target = database.get_container_client(target_container_name)
        write_labels_to_container(target, labels)

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
    if find_missing:
        print(f"Fresh Cosmos inventory: {inventory_count:,}")
        print(f"Records missing from first read: {len(missing_records):,}")
        print(f"Missing-record output: {missing_output_path.resolve()}")
    if write_back:
        print(f"Cosmos output container: {target_container_name}")


if __name__ == "__main__":
    main()

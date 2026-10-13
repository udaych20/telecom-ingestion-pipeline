"""PII masking used by the batch and change-feed paths."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any


SENSITIVE_FIELDS = {
    "account_number", "accountnumber", "address", "ban", "birth", "cell_id",
    "cellid", "customer_id", "customer_name", "customerid", "customername",
    "dob", "email", "email_address", "emailaddress", "enodeb_id", "first_name",
    "firstname", "full_name", "fullname", "gnodeb_id", "iccid", "imei", "imsi",
    "impacted_device", "impacted_number", "impacteddevice", "impactednumber",
    "last_name", "lastname", "mac_address", "macaddress", "mdn", "meid", "mobile",
    "mobile_number", "mobilenumber", "msisdn", "phone", "phone_number",
    "phonenumber", "postal", "postcode", "sim", "sim_serial", "simserial", "sin",
    "site_id", "siteid", "ssn", "subscriber_id", "subscriberid",
}

TOKENIZED_FIELDS = {
    "cid", "cid_list", "conversation_id", "conversationid", "document_id",
    "interaction_id", "run_id", "runid", "session_id", "sessionid", "thread_id", "threadid",
}

PERSON_CONTAINERS = {"contact", "customer", "person", "profile", "subscriber", "user"}
TOKEN_PATTERN = re.compile(r"tok_[0-9a-f]{24}")

_TEXT_PATTERNS = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{14,16}\b"), "[DEVICE_ID]"),
    (re.compile(r"(?<!\d)\+?\d[\d ()-]{6,}\d(?!\d)"), "[PHONE_OR_ACCOUNT]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP_ADDRESS]"),
)


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _configured_sensitive_fields() -> set[str]:
    return {
        _normalized_key(item)
        for item in os.getenv("PII_ADDITIONAL_SENSITIVE_FIELDS", "").split(",")
        if item.strip()
    }


def _is_sensitive_field(key: Any, parent_key: Any = None) -> bool:
    field = _normalized_key(key)
    sensitive_fields = SENSITIVE_FIELDS | _configured_sensitive_fields()
    if field in sensitive_fields:
        return True
    if any(field.endswith(f"_{suffix}") for suffix in sensitive_fields):
        return True
    return field == "name" and _normalized_key(parent_key) in PERSON_CONTAINERS


def tokenize_identifier(value: Any) -> str:
    """Create a stable, non-plaintext token for an operational identifier."""
    if isinstance(value, str) and TOKEN_PATTERN.fullmatch(value):
        return value
    raw = str(value).encode("utf-8")
    secret = os.getenv("PII_TOKENIZATION_KEY", "").encode("utf-8")
    if secret:
        digest = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    else:
        digest = hashlib.sha256(raw).hexdigest()
    return f"tok_{digest[:24]}"


def mask_text(value: str) -> str:
    """Mask recognizable PII in unstructured text, including JSON strings."""
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            return json.dumps(mask_pii(decoded), ensure_ascii=False)

    masked = value
    for pattern, replacement in _TEXT_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def mask_pii(value: Any, *, parent_key: Any = None) -> Any:
    """Return a recursively masked copy of a Cosmos value."""
    if isinstance(value, dict):
        return _mask_dict(value, parent_key)
    if isinstance(value, list):
        return [mask_pii(item, parent_key=parent_key) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_pii(item, parent_key=parent_key) for item in value)
    if isinstance(value, str):
        return mask_text(value)
    return value


def _mask_dict(value: dict[Any, Any], parent_key: Any) -> dict[Any, Any]:
    masked = {}
    for key, item in value.items():
        field = _normalized_key(key)
        if item is None:
            masked[key] = None
        elif field in TOKENIZED_FIELDS:
            masked[key] = _mask_identifier_value(item)
        elif _is_sensitive_field(key, parent_key):
            masked[key] = "[REDACTED]"
        else:
            masked[key] = mask_pii(item, parent_key=key)
    return masked


def _mask_identifier_value(value: Any) -> Any:
    if isinstance(value, list):
        return [tokenize_identifier(item) for item in value]
    return tokenize_identifier(value)

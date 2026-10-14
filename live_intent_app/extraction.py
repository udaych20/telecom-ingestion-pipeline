"""Small, deployment-safe subset of the extraction behavior in intent_app.py."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

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
ISSUE_FIELDS = (
    "issue_summary",
    "issueSummary",
    "issue",
    "issue_description",
    "description",
    "summary",
)
CID_FIELDS = (
    "cid",
    "conversationId",
    "conversation_id",
    "sessionId",
    "session_id",
    "threadId",
    "thread_id",
)


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def parse_json(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip().startswith(("{", "[")):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def find_first(value: Any, fields: Iterable[str]) -> Any:
    names = set(fields)
    if isinstance(value, dict):
        for key, child in value.items():
            if key in names and child not in (None, "", "N/A"):
                return child
        for child in value.values():
            result = find_first(child, names)
            if result not in (None, "", "N/A"):
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_first(child, names)
            if result not in (None, "", "N/A"):
                return result
    return None


def extract_user_text(document: dict[str, Any]) -> str:
    """Return the most recent user message from a Cosmos document."""
    for owner in (document, document.get("conversation", {})):
        if not isinstance(owner, dict):
            continue
        for key in ("messages", "history"):
            messages = owner.get(key)
            if not isinstance(messages, list):
                continue
            user_messages = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                data = message.get("data")
                if not isinstance(data, dict):
                    data = {}

                role = str(
                    message.get("role")
                    or message.get("type")
                    or data.get("role")
                    or data.get("type")
                    or ""
                ).lower()
                if role in {"user", "customer", "human"}:
                    text = compact(
                        message.get("content")
                        or message.get("text")
                        or data.get("content")
                        or data.get("text")
                    )
                    if text:
                        user_messages.append(text)

            if user_messages:
                return user_messages[-1]

    user_input = parse_json(document.get("user_inputs"))
    fallback = find_first(user_input, MESSAGE_FIELDS + ISSUE_FIELDS)
    if fallback is None:
        fallback = find_first(document, MESSAGE_FIELDS)
    return compact(fallback)


def conversation_id(document: dict[str, Any]) -> str:
    return str(find_first(document, CID_FIELDS) or document.get("id") or "unknown")

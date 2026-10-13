"""Build the chat-format candidate shared by batch and live processing."""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = (
    "Classify the NORA customer request as ticket, modify, rca, query, "
    "general, or clarification_needed. Return only the intent."
)

USER_TEXT_FIELD = "extracted.user_text[messages[].data.content]"


def build_training_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    """Classify one masked record and return a reviewable chat example."""
    # Imported here because intent_app uses SYSTEM_PROMPT for the batch builder.
    from intent_app import label_records

    label = None
    for item in reversed(label_records([record])):
        if str(item.get(USER_TEXT_FIELD, "")).strip():
            label = item
            break

    if not label:
        return None

    user_text = str(label[USER_TEXT_FIELD]).strip()
    intent = label["classification.intent"]
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": intent},
        ],
        "classification": {
            "confidence": label["classification.confidence"],
            "rule": label["classification.rule"],
            "needs_human_review": label["classification.needs_human_review"],
            "version": label["classification.version"],
        },
        "status": "training_candidate",
    }

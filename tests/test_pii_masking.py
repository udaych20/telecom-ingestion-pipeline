import json
import unittest
from unittest.mock import Mock, patch

from interaction_reader import get_interaction
from pii_masking import mask_pii, tokenize_identifier


class PiiMaskingTests(unittest.TestCase):
    def test_masks_nested_fields_and_free_text_without_mutating_source(self):
        source = {
            "id": "document-1",
            "_rid": "cosmos-rid",
            "customer_name": "Jane Doe",
            "profile": {"emailAddress": "jane@example.com", "note": "Call +1 416 555 0199"},
            "payload": json.dumps({"account_number": "123456789", "message": "jane@example.com"}),
        }

        masked = mask_pii(source)

        self.assertEqual(masked["id"], "document-1")
        self.assertEqual(masked["_rid"], "cosmos-rid")
        self.assertEqual(masked["customer_name"], "[REDACTED]")
        self.assertEqual(masked["profile"]["emailAddress"], "[REDACTED]")
        self.assertEqual(masked["profile"]["note"], "Call [PHONE_OR_ACCOUNT]")
        self.assertEqual(json.loads(masked["payload"])["account_number"], "[REDACTED]")
        self.assertEqual(source["customer_name"], "Jane Doe")

    def test_masks_contextual_names_and_configured_telecom_codes(self):
        with patch.dict("os.environ", {"PII_ADDITIONAL_SENSITIVE_FIELDS": "switch_code"}):
            masked = mask_pii({"profile": {"name": "Jane Doe"}, "switch_code": "ABC-42"})
        self.assertEqual(masked["profile"]["name"], "[REDACTED]")
        self.assertEqual(masked["switch_code"], "[REDACTED]")

    def test_interaction_reader_masks_only_after_join_values_are_collected(self):
        records = {
            "chat": [{"id": "chat-1", "messages": [{"data": {"email": "jane@example.com"}}]}],
            "context": [{"id": "context-1", "run_id": "cid-1", "account_number": "123456789"}],
            "tools": [{"id": "tool-1", "run_id": "cid-1", "result": "Call 416-555-0199"}],
            "feedback": [],
        }
        containers = {name: Mock() for name in records}
        for name, container in containers.items():
            container.query_items.return_value = records[name]
        database = Mock()
        database.get_container_client.side_effect = containers.__getitem__

        result = get_interaction(
            database, "cid-1", chat_container="chat", context_container="context",
            tools_container="tools", feedback_container="feedback",
        )

        self.assertEqual(result["run_ids"], [tokenize_identifier("cid-1")])
        self.assertEqual(result["context_history"][0]["account_number"], "[REDACTED]")
        self.assertEqual(result["tool_history"][0]["result"], "Call [PHONE_OR_ACCOUNT]")
        self.assertEqual(result["chat_history"][0]["messages"][0]["data"]["email"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()

import unittest

from intent_app import (
    classify,
    conversation_id,
    extract_issue,
    extract_user_text,
    label_records,
    load_cosmos_records,
)


def screenshot_style_record(text, *, cid="cid-1", record_id="record-1"):
    return {
        "id": record_id,
        "messages": [
            {
                "type": "user",
                "data": {"content": text, "type": "user", "cid": cid},
            },
            {
                "type": "assistant",
                "data": {"content": "Assistant response", "type": "assistant", "cid": cid},
            },
        ],
        "user_inputs": {
            "issue_summary": text,
            "device": {
                "impactedDeviceType": "MSISDN",
                "impactedDevice": "15551234567",
            },
        },
    }


class IntentExtractionTests(unittest.TestCase):
    def test_parallel_cosmos_read_uses_configured_worker_count(self):
        class Container:
            def __init__(self):
                self.query_arguments = None

            def query_items(self, **kwargs):
                self.query_arguments = kwargs
                return [{"id": "one"}, {"id": "two"}]

        container = Container()

        records = load_cosmos_records(container, max_records=None, workers=10)

        self.assertEqual(len(records), 2)
        self.assertEqual(container.query_arguments["query"], "SELECT * FROM c")
        self.assertTrue(container.query_arguments["enable_cross_partition_query"])
        self.assertEqual(container.query_arguments["max_concurrency"], 10)

    def test_extracts_nested_user_message_and_cid(self):
        record = screenshot_style_record("router is showing no internet connection")

        self.assertEqual(extract_user_text(record), "router is showing no internet connection")
        self.assertEqual(extract_issue(record), "router is showing no internet connection")
        self.assertEqual(conversation_id(record), "cid-1")
        self.assertEqual(classify(record).intent, "rca")

    def test_uses_structured_user_inputs_when_messages_are_absent(self):
        record = {
            "id": "record-2",
            "user_inputs": {
                "issue_summary": "signal keeps dropping",
                "device": {"impactedDevice": "15551234567"},
            },
        }

        self.assertEqual(extract_user_text(record), "signal keeps dropping")
        self.assertEqual(classify(record).intent, "rca")

    def test_exports_extracted_fields_and_rules_v2(self):
        labels = label_records([
            screenshot_style_record("Customer is troubleshooting Apple watch")
        ])

        self.assertEqual(labels[0]["conversation_id"], "cid-1")
        self.assertEqual(
            labels[0]["extracted.user_text[messages[].data.content]"],
            "Customer is troubleshooting Apple watch",
        )
        self.assertNotIn("extracted.user_text", labels[0])
        self.assertNotIn("extracted.user_text.source", labels[0])
        self.assertEqual(
            labels[0]["extracted.user_inputs.device.impactedDeviceType"],
            "MSISDN",
        )
        self.assertEqual(
            labels[0]["extracted.user_inputs.device.impactedDevice"],
            "15551234567",
        )
        self.assertTrue(labels[0]["extracted.has_customer_context"])
        self.assertEqual(labels[0]["classification.intent"], "rca")
        self.assertEqual(labels[0]["classification.rule"], "rca.issue_diagnosis")
        self.assertEqual(labels[0]["classification.version"], "rules-v2")

    def test_flattens_single_item_user_inputs_list_without_index(self):
        record = screenshot_style_record("Check account status")
        record["user_inputs"] = [{
            "issue_summary": "Check account status",
            "device": {"impactedDevice": "15551234567"},
        }]

        label = label_records([record])[0]

        self.assertEqual(
            label["extracted.user_inputs.issue_summary"],
            "Check account status",
        )
        self.assertEqual(
            label["extracted.user_inputs.device.impactedDevice"],
            "15551234567",
        )

    def test_parses_and_flattens_json_string_user_inputs(self):
        record = screenshot_style_record("Check device status")
        record["user_inputs"] = (
            '{"device":{"impactedDeviceType":"MSISDN",'
            '"impactedDevice":"15180157401"},'
            '"output_type":"technical","category":"Other",'
            '"location":{"latitude":null,"longitude":null}}'
        )

        label = label_records([record], include_source_fields=True)[0]

        self.assertEqual(
            label["extracted.user_inputs.device.impactedDeviceType"],
            "MSISDN",
        )
        self.assertEqual(
            label["extracted.user_inputs.device.impactedDevice"],
            "15180157401",
        )
        self.assertEqual(label["extracted.user_inputs.output_type"], "technical")
        self.assertEqual(label["extracted.user_inputs.category"], "Other")
        self.assertIsNone(label["extracted.user_inputs.location.latitude"])
        self.assertNotIn("source.user_inputs", label)

    def test_ticket_intent_remains_sticky_within_nested_cid(self):
        labels = label_records([
            screenshot_style_record("Create a support ticket", record_id="first"),
            screenshot_style_record("Add this detail", record_id="second"),
        ])

        self.assertEqual([label["classification.intent"] for label in labels], ["ticket", "ticket"])


if __name__ == "__main__":
    unittest.main()

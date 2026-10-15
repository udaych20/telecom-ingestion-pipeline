import csv
import tempfile
import unittest
from pathlib import Path

from live_intent_app.contracts import (
    attach_intent_result,
    build_change_event,
    mark_demo_bypass,
)
from live_intent_app.inference import Inference
from live_intent_app.insert_cosmos_test_document import build_document
from live_intent_app.pipeline import LiveIntentPipeline, LocalCsvSink


class TestModel:
    def infer(self, text):
        return Inference("query", 0.91, "Test prediction", "test-model")


class LivePipelineTests(unittest.TestCase):
    def test_document_is_inferred_and_written_once(self):
        document = {
            "id": "doc-1",
            "_etag": "v1",
            "messages": [
                {
                    "type": "user",
                    "data": {
                        "content": "Check status for +1 555 123 4567",
                        "cid": "c-1",
                    },
                }
            ],
        }
        event = build_change_event("chat", document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.csv"
            pipeline = LiveIntentPipeline(TestModel(), LocalCsvSink(path))
            first = pipeline.process(event)
            second = pipeline.process(event)
            self.assertEqual(first["intent"], "query")
            self.assertTrue(first["saved"])
            self.assertFalse(second["saved"])
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertIn("[PHONE_OR_ACCOUNT]", rows[0]["user_text"])
            self.assertNotEqual(rows[0]["source_id"], "doc-1")

    def test_change_version_has_a_different_event_id(self):
        one = build_change_event("chat", {"id": "1", "_etag": "a"})
        two = build_change_event("chat", {"id": "1", "_etag": "b"})
        self.assertNotEqual(one["id"], two["id"])

    def test_cosmos_test_document_matches_expected_shape(self):
        document = build_document("doc-1", "cid-1", "Check service status")

        self.assertEqual(document["id"], "doc-1")
        self.assertEqual(document["messages"][0]["data"]["cid"], "cid-1")
        self.assertEqual(
            document["messages"][0]["data"]["content"],
            "Check service status",
        )
        self.assertTrue(document["test_record"])

    def test_demo_bypass_is_explicit(self):
        event = mark_demo_bypass(build_change_event("chat", {"id": "1", "_etag": "a"}))

        self.assertTrue(event["demo_bypass"])
        self.assertEqual(
            event["bypassed_stages"],
            ["cosmos_change_feed", "local_azure_function"],
        )

    def test_intent_result_is_attached_before_kafka_publish(self):
        event = build_change_event("chat", {"id": "1", "_etag": "a"})
        result = {
            "event_id": event["id"],
            "intent": "query",
            "confidence": "0.9100",
            "reason": "Test prediction",
            "model": "test-model",
            "saved": True,
        }

        output = attach_intent_result(event, result)

        self.assertEqual(output["type"], "com.telecom.cosmos.intent.inferred")
        self.assertEqual(output["data"]["intent_result"]["intent"], "query")
        self.assertTrue(output["data"]["csv_saved"])
        self.assertNotIn("saved", output["data"]["intent_result"])


if __name__ == "__main__":
    unittest.main()

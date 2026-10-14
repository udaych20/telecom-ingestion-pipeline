import csv
import tempfile
import unittest
from pathlib import Path

from live_intent_app.contracts import build_change_event
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


if __name__ == "__main__":
    unittest.main()

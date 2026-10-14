import csv
import tempfile
import unittest
from pathlib import Path

from live_intent_app.contracts import build_change_event
from live_intent_app.inference import DemoRuleModel
from live_intent_app.pipeline import LiveIntentPipeline, LocalCsvSink
from live_intent_app.transports import (
    PermissionFallbackPublisher,
    is_permission_error,
)


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
            pipeline = LiveIntentPipeline(DemoRuleModel(), LocalCsvSink(path))
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

    def test_permission_failure_uses_rest_fallback(self):
        class AuthorizationError(Exception):
            pass

        class DeniedPublisher:
            def publish(self, event, key):
                raise AuthorizationError("denied")

        class RecordingPublisher:
            def __init__(self):
                self.events = []

            def publish(self, event, key):
                self.events.append((event, key))

        fallback = RecordingPublisher()
        publisher = PermissionFallbackPublisher(DeniedPublisher(), fallback)
        event = {"id": "event-1"}

        publisher.publish(event, "cid-1")

        self.assertEqual(fallback.events, [(event, "cid-1")])
        self.assertTrue(is_permission_error(AuthorizationError("denied")))


if __name__ == "__main__":
    unittest.main()

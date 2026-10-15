import csv
import json
import os
import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

from live_intent_app.contracts import (
    attach_intent_result,
    build_change_event,
    mark_demo_bypass,
)
from live_intent_app.inference import AzureOpenAIModel, Inference, configured_model
from live_intent_app.insert_cosmos_test_document import build_document
from live_intent_app.insert_cosmos_test_document import main as insert_demo_main
from live_intent_app.pipeline import LiveIntentPipeline, LocalCsvSink
from live_intent_app.replay_cosmos_to_kafka import infer_save_and_publish


class TestModel:
    def infer(self, text):
        return Inference("query", 0.91, "Test prediction", "test-model")


class LivePipelineTests(unittest.TestCase):
    @patch("live_intent_app.insert_cosmos_test_document.insert_document")
    def test_backup_flag_skips_cosmos_insertion(self, insert):
        with patch.dict(os.environ, {
            "DEMO_SLM_BACKUP_ENABLED": "true",
            "DEMO_BATCH_SIZE": "10",
        }), patch.object(sys, "argv", ["insert"]):
            insert_demo_main()

        insert.assert_not_called()

    @patch("live_intent_app.insert_cosmos_test_document.insert_document")
    def test_disabled_backup_flag_inserts_ten_json_events(self, insert):
        insert.side_effect = lambda document, container: {**document, "_etag": document["id"]}
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "demo-events"
            events.mkdir()
            for index in range(10):
                (events / f"event-{index + 1:02d}.json").write_text(json.dumps({
                    "id": f"source-{index}",
                    "_etag": "cosmos-managed",
                    "messages": [{"type": "user", "data": {
                        "cid": "old-cid", "content": f"JSON event {index}"
                    }}],
                }))
            receipt = str(Path(directory) / "last_cosmos_insert.json")
            with patch.dict(os.environ, {
                "DEMO_SLM_BACKUP_ENABLED": "false",
                "DEMO_BATCH_SIZE": "10",
                "DEMO_DOCUMENT_DIR": str(events),
            }), patch.object(sys, "argv", ["insert", "--receipt", receipt]):
                insert_demo_main()

            batch = json.loads(
                (Path(directory) / "last_cosmos_insert_batch.json").read_text()
            )

        self.assertEqual(insert.call_count, 10)
        self.assertEqual(len(batch), 10)
        self.assertEqual(len({item["document"]["id"] for item in batch}), 10)
        self.assertEqual(
            batch[0]["document"]["messages"][0]["data"]["content"],
            "JSON event 0",
        )
        self.assertEqual(
            batch[0]["document"]["messages"][0]["data"]["cid"],
            batch[0]["document"]["id"],
        )
        self.assertNotEqual(batch[0]["document"]["_etag"], "cosmos-managed")

    @patch("openai.AzureOpenAI")
    def test_azure_openai_slm_uses_sdk_client_and_deployment(self, client_type):
        response = client_type.return_value.chat.completions.create.return_value
        response.choices[0].message.content = json.dumps({
            "intent": "query", "confidence": 0.93, "reason": "status"
        })
        with patch.dict(os.environ, {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_DEPLOYMENT": "demo slm",
            "AZURE_OPENAI_API_KEY": "secret-for-test",
            "AZURE_OPENAI_API_VERSION": "2024-10-21",
        }, clear=False):
            model = configured_model()
            prediction = model.infer("check status")

        self.assertIsInstance(model, AzureOpenAIModel)
        client_type.assert_called_once_with(
            azure_endpoint="https://example.openai.azure.com",
            api_key="secret-for-test",
            api_version="2024-10-21",
            max_retries=0,
        )
        call = client_type.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(call["model"], "demo slm")
        self.assertEqual(prediction.intent, "query")

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

    @patch("live_intent_app.replay_cosmos_to_kafka.KafkaPublisher")
    @patch("live_intent_app.replay_cosmos_to_kafka.LiveIntentPipeline")
    def test_demo_saves_csv_before_publishing_intent(self, pipeline_type, publisher_type):
        calls = []
        pipeline_type.return_value.process.side_effect = lambda event: (
            calls.append("csv")
            or {
                "event_id": event["id"], "intent": "query", "confidence": "0.9100",
                "reason": "status request", "model": "slm", "saved": True,
            }
        )
        publisher_type.return_value.publish.side_effect = lambda event, key: calls.append("kafka")

        result, event = infer_save_and_publish(
            {"id": "doc-1", "_etag": "v1", "messages": [{"type": "user", "data": {"content": "status"}}]},
            "chat-history-uat",
        )

        self.assertEqual(calls, ["csv", "kafka"])
        self.assertEqual(event["data"]["intent_result"]["intent"], "query")
        self.assertTrue(event["data"]["csv_saved"])

    @patch("live_intent_app.replay_cosmos_to_kafka.KafkaPublisher")
    @patch("live_intent_app.replay_cosmos_to_kafka.LiveIntentPipeline")
    def test_demo_does_not_publish_when_csv_is_not_saved(self, pipeline_type, publisher_type):
        pipeline_type.return_value.process.return_value = {"saved": False}

        with self.assertRaisesRegex(RuntimeError, "Kafka publish was skipped"):
            infer_save_and_publish({"id": "doc-1", "_etag": "v1"}, "chat-history-uat")

        publisher_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()

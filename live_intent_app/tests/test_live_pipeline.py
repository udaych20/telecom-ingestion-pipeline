import csv
import json
import os
import tempfile
import unittest
import sys
import types
from unittest.mock import Mock, patch
from pathlib import Path

from live_intent_app.contracts import (
    attach_intent_result,
    build_change_event,
    mark_demo_bypass,
)
from live_intent_app.inference import (
    AzureOpenAIModel,
    Inference,
    configured_model,
    normalize_confidence,
)
from live_intent_app.extraction import extract_user_text
from live_intent_app.insert_cosmos_test_document import build_document
from live_intent_app.insert_cosmos_test_document import main as insert_demo_main
from live_intent_app.pipeline import LiveIntentPipeline, LocalCsvSink
from live_intent_app.replay_cosmos_to_kafka import infer_save_and_publish
from live_intent_app.transports import KafkaPublisher


class TestModel:
    def infer(self, text):
        return Inference(
            "ticket",
            "ACTIVATION",
            "data working / data / working",
            0.91,
            "Test prediction",
            "test-model",
        )


class LivePipelineTests(unittest.TestCase):
    def test_kafka_publishes_plain_user_content_only(self):
        producer_type = Mock()
        producer_type.return_value.send.return_value.get.return_value = None
        kafka_module = types.SimpleNamespace(KafkaProducer=producer_type)
        with patch.dict(sys.modules, {"kafka": kafka_module}), patch.dict(os.environ, {
            "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
            "KAFKA_TOPIC": "live-intent",
            "KAFKA_SECURITY_PROTOCOL": "PLAINTEXT",
        }):
            publisher = KafkaPublisher()
            publisher.publish("only this user content", "event-key")

        producer_type.return_value.send.assert_called_once_with(
            "live-intent",
            key=b"event-key",
            value=b"only this user content",
        )

    def test_inference_text_uses_only_user_data_content(self):
        document = {
            "content": "ignore root content",
            "messages": [
                {"type": "user", "data": {"content": "process this intent"}},
                {"type": "assistant", "data": {"content": "ignore assistant"}},
            ],
        }

        self.assertEqual(extract_user_text(document), "process this intent")

    def test_confidence_accepts_numeric_percent_and_labels(self):
        self.assertEqual(normalize_confidence(0.82), 0.82)
        self.assertEqual(normalize_confidence("82%"), 0.82)
        self.assertEqual(normalize_confidence("high"), 0.9)
        self.assertEqual(normalize_confidence("medium"), 0.6)
        self.assertEqual(normalize_confidence("low"), 0.3)

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
            "Source_Intent-L1": "ticket",
            "Intent_L2": "ACTIVATION",
            "Likely_Theme_L4": "data working / data / working",
            "confidence": 0.93,
            "reason": "status",
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
        self.assertEqual(prediction.source_intent_l1, "ticket")
        self.assertEqual(prediction.intent_l2, "ACTIVATION")
        self.assertEqual(
            prediction.likely_theme_l4,
            "data working / data / working",
        )

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
            self.assertEqual(first["source_intent_l1"], "ticket")
            self.assertEqual(first["intent_l2"], "ACTIVATION")
            self.assertEqual(
                first["likely_theme_l4"],
                "data working / data / working",
            )
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

    def test_legacy_csv_is_migrated_to_hierarchical_columns(self):
        document = {
            "id": "doc-2",
            "_etag": "v1",
            "messages": [{"type": "user", "data": {"content": "activate data"}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.csv"
            path.write_text(
                "event_id,user_text,intent,confidence\nold,status,query,0.8\n",
                encoding="utf-8",
            )
            LiveIntentPipeline(TestModel(), LocalCsvSink(path)).process(
                build_change_event("chat", document)
            )
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(rows[0]["source_intent_l1"], "query")
        self.assertEqual(rows[1]["source_intent_l1"], "ticket")
        self.assertEqual(rows[1]["intent_l2"], "ACTIVATION")
        self.assertEqual(
            rows[1]["likely_theme_l4"],
            "data working / data / working",
        )

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
            "user_text": "Check my service status",
            "source_intent_l1": "ticket",
            "intent_l2": "ACTIVATION",
            "likely_theme_l4": "data working / data / working",
            "confidence": "0.9100",
            "reason": "Test prediction",
            "model": "test-model",
            "saved": True,
        }

        output = attach_intent_result(event, result)

        self.assertEqual(output["type"], "com.telecom.user.intent.inferred")
        self.assertEqual(
            output["data"]["intent_result"]["ontology"],
            {
                "Source_Intent-L1": "ticket",
                "Intent_L2": "ACTIVATION",
                "Likely_Theme_L4": "data working / data / working",
            },
        )
        self.assertEqual(
            output["data"]["message"],
            {"type": "user", "data": {"content": "Check my service status"}},
        )
        self.assertTrue(output["data"]["csv_saved"])
        self.assertNotIn("saved", output["data"]["intent_result"])
        self.assertNotIn("document", output["data"])
        self.assertNotIn("container", output["data"])

    @patch("live_intent_app.replay_cosmos_to_kafka.KafkaPublisher")
    @patch("live_intent_app.replay_cosmos_to_kafka.LiveIntentPipeline")
    def test_demo_saves_csv_before_publishing_intent(self, pipeline_type, publisher_type):
        calls = []
        pipeline_type.return_value.process.side_effect = lambda event: (
            calls.append("csv")
            or {
                "event_id": event["id"],
                "source_intent_l1": "ticket",
                "intent_l2": "ACTIVATION",
                "likely_theme_l4": "data working / data / working",
                "confidence": "0.9100",
                "reason": "status request", "model": "slm", "saved": True,
                "user_text": "status",
            }
        )
        publisher_type.return_value.publish.side_effect = lambda event, key: calls.append("kafka")

        result, event = infer_save_and_publish(
            {"id": "doc-1", "_etag": "v1", "messages": [{"type": "user", "data": {"content": "status"}}]},
            "chat-history-uat",
        )

        self.assertEqual(calls, ["csv", "kafka"])
        publisher_type.return_value.publish.assert_called_once_with(
            event, event["id"]
        )
        self.assertEqual(
            event["data"]["intent_result"]["ontology"]["Source_Intent-L1"],
            "ticket",
        )
        self.assertEqual(event["data"]["message"]["data"]["content"], "status")
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

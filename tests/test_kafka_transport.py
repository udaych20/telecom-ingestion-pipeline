import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load_kafka_app():
    path = Path(__file__).resolve().parents[1] / "event_app" / "function_app.py"
    spec = importlib.util.spec_from_file_location("test_event_app_kafka", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"STREAM_TRANSPORT": "kafka"}):
        spec.loader.exec_module(module)
    return module


class KafkaTransportTests(unittest.TestCase):
    def setUp(self):
        self.module = load_kafka_app()

    def test_kafka_registers_only_cosmos_triggers(self):
        functions = self.module.app.get_functions()
        binding_types = [
            binding.type
            for function in functions
            for binding in function.get_bindings()
        ]
        self.assertEqual(binding_types.count("cosmosDBTrigger"), 4)
        self.assertNotIn("eventHubTrigger", binding_types)
        self.assertNotIn("timerTrigger", binding_types)

    def test_publisher_sends_cloud_event_with_interaction_key(self):
        producer = Mock()
        send_result = Mock()
        producer.send.return_value = send_result
        kafka_module = types.SimpleNamespace(KafkaProducer=Mock(return_value=producer))
        settings = {
            "KAFKA_BOOTSTRAP_SERVERS": "broker-1:9093, broker-2:9093",
            "KAFKA_TOPIC": "nora-updates",
            "KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
            "KAFKA_SASL_MECHANISM": "SCRAM-SHA-512",
            "KAFKA_SASL_USERNAME": "nora",
            "KAFKA_SASL_PASSWORD": "secret",
        }
        with patch.dict(sys.modules, {"kafka": kafka_module}), patch.dict(os.environ, settings):
            publisher = self.module.KafkaPublisher()
            event = self.module.build_event(
                "chat", {"id": "doc-1", "_etag": "v1", "cid": "customer-1"}
            )
            key = self.module.event_partition_key(event)
            publisher.publish(event, key)

        options = kafka_module.KafkaProducer.call_args.kwargs
        self.assertEqual(options["bootstrap_servers"], ["broker-1:9093", "broker-2:9093"])
        self.assertEqual(options["security_protocol"], "SASL_SSL")
        self.assertEqual(options["sasl_mechanism"], "SCRAM-SHA-512")
        args, kwargs = producer.send.call_args
        self.assertEqual(args, ("nora-updates",))
        self.assertEqual(kwargs["key"], key.encode("utf-8"))
        self.assertEqual(json.loads(kwargs["value"]), event)
        send_result.get.assert_called_once_with(timeout=30)

    def test_sasl_credentials_are_required(self):
        kafka_module = types.SimpleNamespace(KafkaProducer=Mock())
        settings = {
            "KAFKA_BOOTSTRAP_SERVERS": "broker:9093",
            "KAFKA_TOPIC": "nora-updates",
            "KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
            "KAFKA_SASL_USERNAME": "",
            "KAFKA_SASL_PASSWORD": "",
        }
        with patch.dict(sys.modules, {"kafka": kafka_module}), patch.dict(
            os.environ, settings, clear=False
        ):
            with self.assertRaisesRegex(ValueError, "KAFKA_SASL_USERNAME"):
                self.module.KafkaPublisher()


if __name__ == "__main__":
    unittest.main()

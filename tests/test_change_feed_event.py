import importlib
import json
import sys
import types
import unittest
from unittest.mock import patch


class FakeFunctionApp:
    def cosmos_db_trigger(self, **_settings):
        return lambda function: function


class ChangeFeedEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_functions = types.ModuleType("azure.functions")
        fake_functions.FunctionApp = FakeFunctionApp
        fake_functions.DocumentList = list
        with patch.dict(sys.modules, {"azure.functions": fake_functions}):
            cls.module = importlib.import_module("azure_function.function_app")

    def test_event_is_stable_and_excludes_customer_content(self):
        document = {
            "id": "document-1",
            "_etag": "etag-1",
            "_ts": 1_700_000_000,
            "messages": [
                {
                    "data": {
                        "cid": "cid-1",
                        "content": "customer content must not enter the broker",
                    }
                }
            ],
        }

        first = self.module.build_event("chat-history-uat", document)
        second = self.module.build_event("chat-history-uat", document)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(["cid-1"], first["data"]["cids"])
        serialized = json.dumps(first)
        self.assertNotIn("customer content", serialized)
        self.assertNotIn("document", first["data"])

    def test_new_etag_creates_a_new_event_id(self):
        first = self.module.build_event(
            "chat-history-uat", {"id": "document-1", "_etag": "etag-1"}
        )
        second = self.module.build_event(
            "chat-history-uat", {"id": "document-1", "_etag": "etag-2"}
        )
        self.assertNotEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()

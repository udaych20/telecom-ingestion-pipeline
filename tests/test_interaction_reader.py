import json
import csv
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from interaction_reader import get_interaction
from intent_app import attach_interactions, write_interaction_output


class InteractionTests(unittest.TestCase):
    @patch("intent_app.attach_interactions")
    def test_csv_is_flushed_before_next_cid_and_survives_failure(self, attach):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "main.csv"
            clarification = Path(directory) / "clarification.csv"
            labels = [
                {"conversation_id": "one", "classification.intent": "query"},
                {"conversation_id": "one", "classification.intent": "clarification_needed"},
                {"conversation_id": "two", "classification.intent": "query"},
            ]
            def read(path):
                with path.open(encoding="utf-8-sig", newline="") as file:
                    return list(csv.DictReader(file))
            def fetch(database, group):
                if group[0]["conversation_id"] == "two":
                    self.assertEqual(read(output)[0]["conversation_id"], "one")
                    self.assertEqual(read(clarification)[0]["conversation_id"], "one")
                    raise RuntimeError("next CID failed")
                for label in group:
                    label["interaction.tool_history"] = '[{"tool":"lookup"}]'
            attach.side_effect = fetch
            with self.assertRaisesRegex(RuntimeError, "next CID failed"):
                write_interaction_output(Mock(), output, labels, clarification)
            self.assertEqual(len(read(output)), 1)
            self.assertEqual(len(read(clarification)), 1)
            self.assertEqual(json.loads(read(output)[0]["interaction.tool_history"]),
                             [{"tool": "lookup"}])

    def test_reader_follows_app_cid_run_id_join(self):
        records = {
            "chat": [{"id": "chat-1", "messages": []}],
            "context": [{"id": "context-1", "run_id": "cid-1", "agent": "diagnostic"}],
            "tools": [{"id": "tool-1", "run_id": "cid-1", "tool_calls": [{"name": "lookup"}]}],
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
        self.assertEqual(result["tool_history"], records["tools"])
        self.assertEqual(result["context_history"], records["context"])
        for name in ("context", "tools"):
            args, kwargs = containers[name].query_items.call_args
            self.assertIn("c.run_id = @value", args[0])
            self.assertEqual(kwargs["parameters"], [{"name": "@value", "value": "cid-1"}])
        self.assertEqual(result["feedback"], [])

    def test_missing_chat_fails_instead_of_returning_unlinked_history(self):
        database = Mock()
        database.get_container_client.return_value.query_items.return_value = []
        with self.assertRaisesRegex(ValueError, "Chat not found"):
            get_interaction(database, "absent")

    @patch("intent_app.read_interaction")
    def test_enrichment_reuses_cid_and_preserves_payload_and_intents(self, reader):
        reader.return_value = {
            "run_ids": ["cid-1"], "chat_history": [], "feedback": [],
            "context_history": [{"agent": "diagnostic"}],
            "tool_history": [{"tool_calls": [{"name": "lookup", "args": {"x": 1}}]}],
        }
        labels = [
            {"conversation_id": "cid-1", "classification.intent": "rca"},
            {"conversation_id": "cid-1", "classification.intent": "clarification_needed"},
        ]
        attach_interactions(Mock(), labels)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(labels[0]["classification.intent"], "rca")
        self.assertEqual(json.loads(labels[1]["interaction.tool_history"]),
                         reader.return_value["tool_history"])

    @patch("intent_app.read_interaction", side_effect=RuntimeError("Cosmos unavailable"))
    def test_query_failure_is_not_hidden(self, reader):
        with self.assertRaisesRegex(RuntimeError, "Cosmos unavailable"):
            attach_interactions(Mock(), [{"conversation_id": "cid-1"}])


if __name__ == "__main__":
    unittest.main()

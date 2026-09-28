import csv
import json
from pathlib import Path
import tempfile
import unittest

from interaction_report import convert, expand


class InteractionReportTests(unittest.TestCase):
    def test_agent_fields_and_false_result(self):
        record = {"agent": "Network", "function_name": "check", "function_arguments":
                  {"text": "a,b\n日本語"}, "function_result": False, "run_id": "r1"}
        row = next(expand({"cid": "c1", "source": "context_history", "data": json.dumps(record)}))
        self.assertEqual(row["agent"], "Network")
        self.assertEqual(row["run_id"], "r1")
        self.assertIs(row["output"], False)
        self.assertEqual(json.loads(row["input"]), record["function_arguments"])

    def test_intent_export_preserves_all_sources(self):
        row = {"conversation_id": "c1", "source_id": "s1", "classification.intent": "query",
               "interaction.tool_history": json.dumps([{"function_name": "check", "arguments": {"x": 1}, "function_result": 0}]),
               "interaction.context_history": '[{"agent":"Network"}]',
               "interaction.chat_history": '[{"messages":[]}]',
               "interaction.feedback": '[{"rating":1}]'}
        result = list(expand(row))
        self.assertEqual(len(result), 4)
        self.assertTrue(all(r["intent"] == "query" and r["source_id"] == "s1" for r in result))
        tool = next(r for r in result if r["source"] == "tool_history")
        self.assertEqual(tool["agent"], "")
        self.assertEqual(tool["output"], 0)

    def test_conversion_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "in.csv", Path(folder) / "out.csv"
            with source.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["cid", "source", "data"])
                writer.writeheader()
                writer.writerow({"cid": "c1", "source": "tool_history", "data": json.dumps({"function_result": "a,b\nnext"})})
            self.assertEqual(convert(source, output), 1)
            with output.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(next(csv.DictReader(file))["output"], "a,b\nnext")
            with self.assertRaises(FileExistsError):
                convert(source, output)
            with self.assertRaises(ValueError):
                convert(source, source)

    def test_invalid_json_is_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            list(expand({"source": "tool_history", "data": "bad json"}))


if __name__ == "__main__":
    unittest.main()

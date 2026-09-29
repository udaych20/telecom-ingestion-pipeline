import csv
import json
from pathlib import Path
import tempfile
import unittest

from extract_interactions import extract, output_rows


class ExtractInteractionsTests(unittest.TestCase):
    def test_extracts_only_user_agent_and_tool_events(self):
        chat = {"messages": [
            {"type": "user", "data": {"content": "Why no signal?"}},
            {"type": "assistant", "data": {"content": "Checking"}},
        ]}
        row = {"interaction_id": "c1", "run_id": "r1", "intent": "rca",
               "source": "chat_history", "data": json.dumps(chat)}
        self.assertEqual(list(output_rows(row))[0], {
            "cid": "c1", "user_input": "Why no signal?", "agent": "",
            "function_name": "", "classification_intent": "rca"})

        row.update(source="context_history", data=json.dumps({
            "agent": "Network", "function_name": "diagnose", "function_arguments": {"ban": "1"}}))
        agent = list(output_rows(row))[0]
        self.assertEqual(agent["agent"], "Network")
        self.assertEqual(agent["function_name"], "diagnose")

        row.update(source="tool_history", data=json.dumps({
            "run_id": "r2", "function_name": "lookup", "arguments": {"x": 1}, "function_result": False}))
        tool = list(output_rows(row))[0]
        self.assertEqual(tool["function_name"], "lookup")
        self.assertNotIn("run_id", tool)
        self.assertNotIn("arguments", tool)

    def test_streaming_file_and_wide_format(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "in.csv", Path(folder) / "out.csv"
            fields = ["conversation_id", "classification.intent", "interaction.chat_history",
                      "interaction.context_history", "interaction.tool_history"]
            with source.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerow({"conversation_id": "c2", "classification.intent": "query",
                                 "interaction.chat_history": json.dumps([{"user_input": "status?"}]),
                                 "interaction.context_history": "[]", "interaction.tool_history": "[]"})
            self.assertEqual(extract(source, target, progress_every=0), 1)
            with target.open(encoding="utf-8-sig", newline="") as file:
                result = next(csv.DictReader(file))
            self.assertEqual(list(result), ["cid", "user_input", "agent", "function_name",
                                            "classification_intent"])
            self.assertEqual(result["user_input"], "status?")


if __name__ == "__main__":
    unittest.main()

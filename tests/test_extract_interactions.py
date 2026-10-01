import csv
import json
from pathlib import Path
import tempfile
import unittest

from extract_interactions import extract, load_cids, output_rows


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

    def test_filters_large_export_from_cid_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "interactions.csv"
            cid_file = Path(folder) / "cids.csv"
            target = Path(folder) / "compact.csv"
            audit = Path(folder) / "audit.csv"
            with source.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["interaction_id", "source", "data", "intent"])
                writer.writeheader()
                for cid in ("c1", "c2", "c3"):
                    writer.writerow({"interaction_id": cid, "source": "tool_history",
                                     "data": json.dumps({"function_name": f"tool-{cid}"}),
                                     "intent": "query"})
            with cid_file.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["conversation_id"])
                writer.writeheader()
                writer.writerow({"conversation_id": " c2 "})
                writer.writerow({"conversation_id": "c2"})
                writer.writerow({"conversation_id": "c4"})

            selected = load_cids(cid_file, "conversation_id")
            self.assertEqual(selected, {"c2", "c4"})
            self.assertEqual(extract(source, target, progress_every=0, selected_cids=selected,
                                     cid_audit=audit), 1)
            with target.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["cid"], "c2")
            self.assertEqual(rows[0]["function_name"], "tool-c2")
            with audit.open(encoding="utf-8-sig", newline="") as file:
                audit_rows = {row["cid"]: row for row in csv.DictReader(file)}
            self.assertEqual(audit_rows["c2"], {"cid": "c2", "status": "found", "event_count": "1"})
            self.assertEqual(audit_rows["c4"], {"cid": "c4", "status": "not_found", "event_count": "0"})

    def test_rejects_missing_cid_column(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cids.csv"
            path.write_text("wrong\nc1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no 'cid' column"):
                load_cids(path)


if __name__ == "__main__":
    unittest.main()

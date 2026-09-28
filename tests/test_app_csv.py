import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class AppCsvTests(unittest.TestCase):
    def test_sampler_unique_cids_and_multiple_intents(self):
        sampler = app.InteractionSampler(1)
        interaction = {"interaction_id": "one"}
        with patch.object(app, "classify_interaction", return_value=("context_history", {}, ["query", "rca"])):
            selected = sampler.eligible(interaction)
            self.assertEqual(selected, ["query", "rca"])
            self.assertEqual(sampler.counts["query"], 0)
            sampler.record_saved("one", selected)
            sampler.record_saved("one", selected)
            self.assertEqual(sampler.eligible(interaction), [])
            self.assertEqual(sampler.eligible({"interaction_id": "two"}), [])
            self.assertEqual(sampler.counts["query"], 1)
            self.assertFalse(sampler.full())
        for intent in ("ticket", "modify", "general", "clarification_needed"):
            sampler.record_saved(intent, [intent])
        self.assertTrue(sampler.full())

    def test_sampler_target_validation_and_shortfall_report(self):
        for invalid in (0, -1):
            with self.assertRaises(ValueError):
                app.InteractionSampler(invalid)
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "OUTPUT_DIR", folder):
            sampler = app.InteractionSampler(100)
            sampler.record_saved("one", ["ticket"])
            sampler.report()
            with (Path(folder) / "interaction_sample_counts.csv").open(newline="") as file:
                rows = {r["intent"]: r for r in csv.DictReader(file)}
            self.assertEqual(rows["ticket"]["shortfall"], "99")
            self.assertEqual(rows["query"]["shortfall"], "100")

    def test_all_intents_and_readable_fields(self):
        intents = ["ticket", "modify", "rca", "query", "general", "clarification_needed"]
        interaction = {
            "interaction_id": "cid1", "cids": ["cid1"],
            "chat_history": [], "feedback": [],
            "context_history": [{"id": str(i), "agent": "Agent", "function_name": "check",
                                 "function_arguments": {"x": i}, "function_result": "done"}
                                for i in range(6)],
            "tool_history": [{"id": "tool", "run_id": "run1", "function_name": "check",
                              "arguments": {"x": 0}, "function_result": False}],
        }
        labels = [{"source_id": str(i), "classification.intent": intent}
                  for i, intent in enumerate(intents)]
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "OUTPUT_DIR", folder), \
                patch.object(app, "label_records", return_value=labels):
            app.save_interaction_csv(interaction)
            app.save_interaction_csv(interaction)
            with (Path(folder) / "interactions.csv").open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 14)
            contexts = [r for r in rows if r["source"] == "context_history"]
            self.assertEqual({r["intent"] for r in contexts}, set(intents))
            self.assertEqual(contexts[0]["agent"], "Agent")
            self.assertEqual(contexts[0]["function_name"], "check")
            self.assertEqual(json.loads(contexts[0]["input"]), {"x": 0})
            tool = rows[0]
            self.assertEqual(tool["intent_scope"], "conversation")
            self.assertEqual(tool["agent"], "")
            self.assertEqual(tool["output"], "False")
            self.assertEqual(json.loads(tool["data"])["id"], "tool")

    def test_old_header_is_preserved(self):
        interaction = {"interaction_id": "c", "cids": ["c"], "context_history": [],
                       "chat_history": [], "tool_history": [], "feedback": []}
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "OUTPUT_DIR", folder):
            path = Path(folder) / "interactions.csv"
            path.write_text("interaction_id,cid\nc,c\n", encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "older/different header"):
                app.save_interaction_csv(interaction)
            self.assertEqual(path.read_bytes(), before)

    def test_chat_fallback_uses_real_classifier(self):
        interaction = {"interaction_id": "c", "cids": ["c"], "context_history": [],
                       "chat_history": [{"id": "chat", "messages": [{"type": "human",
                                         "data": {"content": "create a ticket", "cid": "c"}}]}],
                       "tool_history": [], "feedback": []}
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "OUTPUT_DIR", folder):
            app.save_interaction_csv(interaction)
            with (Path(folder) / "interactions.csv").open(encoding="utf-8", newline="") as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row["intent"], "ticket")
            self.assertEqual(row["intent_scope"], "record")

import csv
import json
import tempfile
import unittest
from pathlib import Path

from intent_count_report import summarize_csv


class IntentCountReportTests(unittest.TestCase):
    def test_summarizes_original_intent_app_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "intent.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "conversation_id",
                        "classification.intent",
                    ),
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"conversation_id": "c1", "classification.intent": "rca"},
                        {"conversation_id": "c1", "classification.intent": "rca"},
                        {"conversation_id": "c2", "classification.intent": "query"},
                    ]
                )

            rows = summarize_csv("old_data", path)

        self.assertEqual(rows[0]["classification_intent"], "query")
        self.assertEqual(rows[0]["unique_cid_count"], 1)
        self.assertEqual(rows[1]["classification_intent"], "rca")
        self.assertEqual(rows[1]["input_row_count"], 2)
        self.assertEqual(rows[1]["unique_cid_count"], 1)
        self.assertEqual(rows[2]["classification_intent"], "ALL_INTENTS")
        self.assertEqual(rows[2]["unique_cid_count"], 2)

    def test_summarizes_test_py_flattened_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flattened.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=("message.data.cid", "classification.intent"),
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"message.data.cid": "c1", "classification.intent": "rca"},
                        {"message.data.cid": "c1", "classification.intent": "rca"},
                        {"message.data.cid": "c2", "classification.intent": "query"},
                    ]
                )

            rows = summarize_csv("friday_to_yesterday", path)

        self.assertEqual(rows[-1]["input_row_count"], 3)
        self.assertEqual(rows[-1]["unique_cid_count"], 2)

    def test_extracts_cid_from_source_messages_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=("source.messages", "classification.intent"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source.messages": json.dumps(
                            [
                                {"type": "user", "data": {"cid": "c1"}},
                                {"type": "assistant", "data": {"cid": "c1"}},
                            ]
                        ),
                        "classification.intent": "rca",
                    }
                )

            rows = summarize_csv("old_data", path)

        self.assertEqual(rows[0]["unique_cid_count"], 1)
        self.assertEqual(rows[0]["rows_without_cid"], 0)


if __name__ == "__main__":
    unittest.main()

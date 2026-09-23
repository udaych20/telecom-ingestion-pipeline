import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from intent_app import IncrementalOutput, export_selected_cids


class IncrementalSourceTests(unittest.TestCase):
    @patch("intent_app.load_cosmos_records")
    def test_first_cid_saved_before_later_source_query_and_survives_failure(self, load):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.csv"
            output = IncrementalOutput(path)
            self.assertTrue(path.exists())
            def fetch(*args, **kwargs):
                if kwargs["cids"] == ["second"]:
                    with path.open(encoding="utf-8-sig", newline="") as file:
                        rows = list(csv.DictReader(file))
                    self.assertEqual(rows[0]["conversation_id"], "first")
                    raise RuntimeError("connection failed")
                return [{"id": "first", "cid": "first", "user_message": "Create ticket"}]
            load.side_effect = fetch
            with self.assertRaisesRegex(RuntimeError, "saved rows remain"):
                export_selected_cids(Mock(), Mock(), ["first", "second"], output,
                                     batch_size=1, workers=1)
            with path.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(len(list(csv.DictReader(file))), 1)

    def test_new_source_columns_preserve_earlier_rows_and_split(self):
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / "main.csv"
            other = Path(directory) / "other.csv"
            output = IncrementalOutput(main, other)
            output.append([{"source_id": "a", "classification.intent": "query", "source.first": "one"}])
            output.append([{"source_id": "b", "classification.intent": "clarification_needed", "source.new": "two"}])
            with main.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["source.first"], "one")
            self.assertEqual(rows[0]["source.new"], "")
            with other.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(list(csv.DictReader(file))[0]["source.new"], "two")

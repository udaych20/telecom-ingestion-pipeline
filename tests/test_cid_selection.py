import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from intent_app import load_cids_from_csv, load_cosmos_records, find_missing_cosmos_records


class CidSelectionTests(unittest.TestCase):
    def test_csv_preserves_ids_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cids.csv"
            path.write_text("conversation_id\n001\n\n 002 \n001\n", encoding="utf-8-sig")
            self.assertEqual(load_cids_from_csv(path, "conversation_id"), ["001", "002"])
            with self.assertRaises(ValueError):
                load_cids_from_csv(path)
            path.write_text("cid\n\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cids_from_csv(path)

    def test_selected_read_and_audit_do_not_expand_scope(self):
        container = Mock()
        selected = {"id": "one", "cid": "001"}
        unrelated = {"id": "two", "cid": "002"}
        container.query_items.return_value = [selected, unrelated, selected]
        self.assertEqual(load_cosmos_records(container, None, cids=["001"]), [selected])
        container.read_all_items.assert_not_called()
        self.assertEqual(container.query_items.call_args.kwargs["parameters"],
                         [{"name": "@cid", "value": "001"}])
        missing, count = find_missing_cosmos_records(container, [], cids=["001"])
        self.assertEqual((missing, count), ([selected], 1))

    def test_empty_selection_never_becomes_full_read(self):
        container = Mock()
        self.assertEqual(load_cosmos_records(container, None, cids=[]), [])
        container.query_items.assert_not_called()
        container.read_all_items.assert_not_called()

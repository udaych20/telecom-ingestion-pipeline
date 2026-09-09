import argparse
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import app


class TimeframeExportTests(unittest.TestCase):
    def test_parse_iso_time_requires_timezone_and_normalizes_to_utc(self):
        parsed = app.parse_iso_time("2026-09-04T18:00:00+05:30")

        self.assertEqual(
            datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc), parsed
        )
        with self.assertRaises(argparse.ArgumentTypeError):
            app.parse_iso_time("2026-09-04T18:00:00")

    def test_timeframe_query_uses_inclusive_epoch_bounds(self):
        database = MagicMock()
        container = MagicMock()
        database.get_container_client.return_value = container
        container.query_items.return_value = [{"cid": "c1"}]
        start = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
        end = datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)

        self.assertEqual(
            [["c1"]], list(app.get_chat_id_batches(database, start, end))
        )
        query_call = container.query_items.call_args
        self.assertIn("c._ts >= @start_ts", query_call.args[0])
        self.assertIn("c._ts <= @end_ts", query_call.args[0])
        self.assertEqual(
            [
                {"name": "@start_ts", "value": int(start.timestamp())},
                {"name": "@end_ts", "value": int(end.timestamp())},
            ],
            query_call.kwargs["parameters"],
        )

    def test_csv_output_does_not_create_interactions_jsonl(self):
        interaction = {
            "interaction_id": "c1",
            "cids": ["c1"],
            "run_ids": [],
            "chat_history": [{"id": "chat", "cid": "c1"}],
            "tool_history": [],
            "context_history": [],
            "feedback": [],
        }
        with tempfile.TemporaryDirectory() as output_dir, patch.object(
            app, "OUTPUT_DIR", output_dir
        ), patch.object(app, "INGESTION_MODE", "none"):
            app.save_interaction(interaction, "csv")

            self.assertTrue(
                os.path.exists(os.path.join(output_dir, "interactions.csv"))
            )
            self.assertFalse(
                os.path.exists(os.path.join(output_dir, "interactions.jsonl"))
            )


if __name__ == "__main__":
    unittest.main()

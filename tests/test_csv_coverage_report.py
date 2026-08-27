import csv
import json
import os
import tempfile
import unittest

from csv_coverage_report import generate_coverage_report


class CsvCoverageReportTests(unittest.TestCase):
    def test_generates_metrics_and_deduplicates_repeated_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "interactions.csv")
            output = os.path.join(directory, "report")
            rows = [
                ("cid-1", "chat_history", "chat-1", "{}"),
                ("cid-1", "context_history", "context-1", "{}"),
                ("cid-1", "tool_history", "tool-1", "{}"),
                ("cid-1", "feedback", "feedback-1", "{}"),
                ("cid-1", "feedback", "feedback-1", "{}"),
                ("cid-2", "chat_history", "chat-2", "{}"),
            ]
            with open(source, "w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["interaction_id", "source", "record_id", "data"])
                writer.writerows(rows)

            summary, paths = generate_coverage_report(source, output, progress_every=1)

            self.assertEqual(summary["source_csv_rows_scanned"], 6)
            self.assertEqual(summary["distinct_chat_cids_analyzed"], 2)
            self.assertEqual(summary["complete_all_four_containers"], 1)
            self.assertEqual(summary["sources"]["feedback"]["total_records"], 1)
            self.assertEqual(summary["cids_by_referenced_container_count"], {"0": 1, "1": 0, "2": 0, "3": 1})
            self.assertTrue(all(os.path.exists(path) for path in paths))
            self.assertFalse(os.path.exists(os.path.join(output, ".csv_coverage_index.sqlite3")))

            with open(os.path.join(output, "coverage_summary.json"), encoding="utf-8") as file:
                written = json.load(file)
            self.assertEqual(written["incomplete_interactions"], 1)


if __name__ == "__main__":
    unittest.main()

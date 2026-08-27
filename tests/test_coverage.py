import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app


def interaction(cid, chat=1, context=0, tools=0, feedback=0):
    records = lambda count: [{"id": str(index)} for index in range(count)]
    return {
        "interaction_id": cid,
        "chat_history": records(chat),
        "context_history": records(context),
        "tool_history": records(tools),
        "feedback": records(feedback),
    }


class CoverageReportTests(unittest.TestCase):
    def test_interaction_coverage_counts_matches(self):
        row = app.interaction_coverage(interaction("cid-1", 2, 1, 3, 0))

        self.assertEqual(row["chat_history_records"], 2)
        self.assertEqual(row["context_history_records"], 1)
        self.assertEqual(row["tool_history_records"], 3)
        self.assertEqual(row["feedback_records"], 0)
        self.assertEqual(row["referenced_container_count"], 2)
        self.assertEqual(row["complete_all_four_containers"], 0)

    def test_summary_reports_coverage_and_failures(self):
        rows = [
            app.interaction_coverage(interaction("complete", 1, 2, 3, 1)),
            app.interaction_coverage(interaction("chat-only")),
        ]

        summary = app.build_coverage_summary(rows, failed=1)

        self.assertEqual(summary["distinct_chat_cids_attempted"], 3)
        self.assertEqual(summary["distinct_chat_cids_analyzed"], 2)
        self.assertEqual(summary["complete_all_four_containers"], 1)
        self.assertEqual(summary["complete_coverage_percent"], 50.0)
        self.assertEqual(summary["sources"]["tool_history"]["cids_with_match"], 1)
        self.assertEqual(summary["sources"]["tool_history"]["total_records"], 3)
        self.assertEqual(summary["cids_by_referenced_container_count"], {
            "0": 1, "1": 0, "2": 0, "3": 1,
        })

    def test_report_files_are_replaced_with_current_run(self):
        rows = [app.interaction_coverage(interaction("cid-1", 1, 1, 0, 0))]
        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(app, "OUTPUT_DIR", output_dir):
                app.write_coverage_reports(rows, failed=0)
                app.write_coverage_reports(rows, failed=0)

            detail = os.path.join(output_dir, "interaction_coverage.csv")
            with open(detail, encoding="utf-8") as file:
                self.assertEqual(len(file.readlines()), 2)

            summary_path = os.path.join(output_dir, "coverage_summary.json")
            with open(summary_path, encoding="utf-8") as file:
                summary = json.load(file)
            self.assertEqual(summary["distinct_chat_cids_analyzed"], 1)

            summary_csv = os.path.join(output_dir, "coverage_summary.csv")
            with open(summary_csv, encoding="utf-8") as file:
                contents = file.read()
            self.assertIn("complete_coverage_percent", contents)
            self.assertIn("cids_matching_1_of_3_referenced_containers", contents)

    def test_checkpoint_round_trip_and_removal(self):
        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(app, "OUTPUT_DIR", output_dir):
                app.save_checkpoint("--all-report", {"cid-2", "cid-1"})
                completed, resumed = app.load_checkpoint("--all-report")
                self.assertTrue(resumed)
                self.assertEqual(completed, {"cid-1", "cid-2"})
                app.remove_checkpoint("--all-report")
                self.assertEqual(app.load_checkpoint("--all-report"), (set(), False))

    def test_batch_resume_skips_completed_ids_within_limit(self):
        class Container:
            def query_items(self, *_args, **_kwargs):
                return [{"cid": "cid-1"}, {"cid": "cid-2"}, {"cid": "cid-3"}]

        class Database:
            def get_container_client(self, _name):
                return Container()

        with patch.object(app, "BATCH_LIMIT", 2), patch.object(app, "BATCH_SIZE", 100):
            batches = list(app.get_chat_id_batches(Database(), {"cid-1"}))

        self.assertEqual(batches, [["cid-2"]])


if __name__ == "__main__":
    unittest.main()

import argparse
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from intent_app import (
    build_foundry_datasets,
    build_intent_count_rows,
    classify,
    conversation_id,
    extract_issue,
    extract_user_text,
    find_missing_cosmos_records,
    label_records,
    load_cosmos_records,
    parse_iso_time,
    submit_foundry_training,
    write_foundry_jsonl,
    write_training_placeholder,
)


def screenshot_style_record(text, *, cid="cid-1", record_id="record-1"):
    return {
        "id": record_id,
        "messages": [
            {
                "type": "user",
                "data": {"content": text, "type": "user", "cid": cid},
            },
            {
                "type": "assistant",
                "data": {"content": "Assistant response", "type": "assistant", "cid": cid},
            },
        ],
        "user_inputs": {
            "issue_summary": text,
            "device": {
                "impactedDeviceType": "MSISDN",
                "impactedDevice": "15551234567",
            },
        },
    }


class IntentExtractionTests(unittest.TestCase):
    def test_submits_foundry_training_after_files_are_processed(self):
        class Result:
            def __init__(self, result_id, status="processed"):
                self.id = result_id
                self.status = status

        class Files:
            def __init__(self):
                self.uploaded = []

            def create(self, *, file, purpose):
                self.uploaded.append((Path(file.name).name, purpose))
                return Result(f"file-{len(self.uploaded)}")

            def retrieve(self, file_id):
                return Result(file_id)

        class Jobs:
            def __init__(self):
                self.request = None

            def create(self, **request):
                self.request = request
                return Result("job-1", "queued")

        class Client:
            def __init__(self):
                self.files = Files()
                self.fine_tuning = type("FineTuning", (), {"jobs": Jobs()})()

        with tempfile.TemporaryDirectory() as directory:
            train_path = Path(directory) / "train.jsonl"
            validation_path = Path(directory) / "validation.jsonl"
            train_path.write_text("{}\n", encoding="utf-8")
            validation_path.write_text("{}\n", encoding="utf-8")
            client = Client()

            receipt = submit_foundry_training(
                client,
                train_path,
                validation_path,
                model="model-version",
                suffix="nora",
                seed=105,
                training_type="GlobalStandard",
                timeout_seconds=1,
                poll_seconds=1,
            )

            self.assertEqual(receipt["job_id"], "job-1")
            self.assertEqual(
                client.files.uploaded,
                [("train.jsonl", "fine-tune"), ("validation.jsonl", "fine-tune")],
            )
            self.assertEqual(
                client.fine_tuning.jobs.request["training_file"], "file-1"
            )
            self.assertEqual(
                client.fine_tuning.jobs.request["validation_file"], "file-2"
            )

    def test_foundry_feature_build_keeps_cid_in_one_split_and_redacts_pii(self):
        labels = []
        for index in range(2):
            labels.append(
                {
                    "conversation_id": "same-cid",
                    "extracted.user_text[messages[].data.content]": (
                        f"Request {index} for 15551234567"
                    ),
                    "classification.intent": "query",
                    "classification.needs_human_review": False,
                }
            )

        datasets, rejected = build_foundry_datasets(labels)

        populated = [name for name, examples in datasets.items() if examples]
        self.assertEqual(len(populated), 1)
        self.assertEqual(len(datasets[populated[0]]), 2)
        self.assertIn(
            "[PHONE_OR_ACCOUNT]",
            datasets[populated[0]][0]["messages"][1]["content"],
        )
        self.assertEqual(rejected, {})

    def test_foundry_feature_build_filters_review_rows_and_duplicates(self):
        labels = [
            {
                "conversation_id": "cid-1",
                "extracted.user_text[messages[].data.content]": "Show account status",
                "classification.intent": "query",
                "classification.needs_human_review": False,
            },
            {
                "conversation_id": "cid-2",
                "extracted.user_text[messages[].data.content]": "Show account status",
                "classification.intent": "query",
                "classification.needs_human_review": False,
            },
            {
                "conversation_id": "cid-3",
                "extracted.user_text[messages[].data.content]": "Unknown request",
                "classification.intent": "clarification_needed",
                "classification.needs_human_review": True,
            },
        ]

        datasets, rejected = build_foundry_datasets(labels)

        self.assertEqual(sum(map(len, datasets.values())), 1)
        self.assertEqual(rejected["duplicate"], 1)
        self.assertEqual(rejected["needs_human_review"], 1)

    def test_foundry_jsonl_has_bom_and_chat_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.jsonl"
            example = {
                "messages": [
                    {"role": "user", "content": "Status?"},
                    {"role": "assistant", "content": "query"},
                ]
            }
            write_foundry_jsonl(path, [example])

            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig") as file:
                self.assertEqual(__import__("json").loads(file.readline()), example)

    def test_foundry_feature_build_can_require_source_human_approval(self):
        base = {
            "conversation_id": "cid-reviewed",
            "extracted.user_text[messages[].data.content]": "Show plan status",
            "classification.intent": "query",
            "classification.needs_human_review": False,
        }
        datasets, rejected = build_foundry_datasets(
            [{**base, "source.review.status": "approved"}],
            require_reviewed=True,
        )
        self.assertEqual(sum(map(len, datasets.values())), 1)
        self.assertEqual(rejected, {})

    def test_initial_batch_size_is_passed_to_cosmos_reader(self):
        class Container:
            def read_all_items(self, **kwargs):
                self.arguments = kwargs
                return []

        container = Container()
        load_cosmos_records(container, None, batch_size=250)
        self.assertEqual(container.arguments["max_item_count"], 250)

    def test_writes_explicit_dummy_training_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "training.csv"
            log_path = Path(directory) / "training.log"
            write_training_placeholder(
                [
                    {"classification.intent": "query"},
                    {"classification.intent": "query"},
                    {"classification.intent": "ticket"},
                ],
                csv_path,
                log_path,
            )
            with csv_path.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["intent"], "query")
            self.assertEqual(rows[0]["record_count"], "2")
            self.assertIn("model training was not executed", log_path.read_text())

    def test_worker_setting_does_not_reach_unsupported_cosmos_query(self):
        class Container:
            def __init__(self):
                self.read_called = False

            def read_all_items(self):
                self.read_called = True
                return [{"id": "one"}, {"id": "two"}]

        container = Container()

        records = load_cosmos_records(container, max_records=None, workers=10)

        self.assertEqual(len(records), 2)
        self.assertTrue(container.read_called)

    def test_finds_records_missing_from_the_first_cosmos_read(self):
        class Container:
            def query_items(self, **kwargs):
                self.arguments = kwargs
                return [
                    {"id": "one", "_rid": "rid-one"},
                    {"id": "two", "_rid": "rid-two"},
                ]

        missing, inventory_count = find_missing_cosmos_records(
            Container(),
            [{"id": "one", "_rid": "rid-one"}],
        )

        self.assertEqual(inventory_count, 2)
        self.assertEqual(missing, [{"id": "two", "_rid": "rid-two"}])

    def test_timeframe_query_uses_inclusive_epoch_bounds(self):
        class Container:
            def query_items(self, **kwargs):
                self.arguments = kwargs
                return [{"id": "one", "_ts": 1788525000}]

        container = Container()
        start = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
        end = datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)

        records = load_cosmos_records(
            container,
            max_records=None,
            start_time=start,
            end_time=end,
        )

        self.assertEqual(records, [{"id": "one", "_ts": 1788525000}])
        self.assertIn("c._ts >= @start_ts", container.arguments["query"])
        self.assertIn("c._ts <= @end_ts", container.arguments["query"])
        self.assertEqual(
            container.arguments["parameters"],
            [
                {"name": "@start_ts", "value": 1788525000},
                {"name": "@end_ts", "value": 1788957000},
            ],
        )

    def test_missing_record_audit_uses_the_same_timeframe(self):
        class Container:
            def query_items(self, **kwargs):
                self.arguments = kwargs
                return [{"id": "two", "_rid": "rid-two"}]

        container = Container()
        start = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
        end = datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)

        missing, inventory_count = find_missing_cosmos_records(
            container,
            [],
            start,
            end,
        )

        self.assertEqual(inventory_count, 1)
        self.assertEqual(missing, [{"id": "two", "_rid": "rid-two"}])
        self.assertEqual(
            container.arguments["parameters"],
            [
                {"name": "@start_ts", "value": 1788525000},
                {"name": "@end_ts", "value": 1788957000},
            ],
        )

    def test_parse_iso_time_requires_timezone_and_normalizes_to_utc(self):
        parsed = parse_iso_time("2026-09-04T18:00:00+05:30")

        self.assertEqual(
            parsed,
            datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
        )
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_iso_time("2026-09-04T18:00:00")

    def test_builds_record_and_unique_cid_counts_by_intent(self):
        rows = build_intent_count_rows(
            [
                {"conversation_id": "c1", "classification.intent": "rca"},
                {"conversation_id": "c1", "classification.intent": "rca"},
                {"conversation_id": "c1", "classification.intent": "query"},
                {"conversation_id": "c2", "classification.intent": "query"},
            ]
        )

        self.assertEqual(
            rows,
            [
                {
                    "classification_intent": "query",
                    "classified_record_count": 2,
                    "unique_cid_count": 2,
                },
                {
                    "classification_intent": "rca",
                    "classified_record_count": 2,
                    "unique_cid_count": 1,
                },
                {
                    "classification_intent": "ALL_INTENTS",
                    "classified_record_count": 4,
                    "unique_cid_count": 2,
                },
            ],
        )

    def test_extracts_nested_user_message_and_cid(self):
        record = screenshot_style_record("router is showing no internet connection")

        self.assertEqual(extract_user_text(record), "router is showing no internet connection")
        self.assertEqual(extract_issue(record), "router is showing no internet connection")
        self.assertEqual(conversation_id(record), "cid-1")
        self.assertEqual(classify(record).intent, "rca")

    def test_uses_structured_user_inputs_when_messages_are_absent(self):
        record = {
            "id": "record-2",
            "user_inputs": {
                "issue_summary": "signal keeps dropping",
                "device": {"impactedDevice": "15551234567"},
            },
        }

        self.assertEqual(extract_user_text(record), "signal keeps dropping")
        self.assertEqual(classify(record).intent, "rca")

    def test_exports_extracted_fields_and_rules_v2(self):
        labels = label_records([
            screenshot_style_record("Customer is troubleshooting Apple watch")
        ])

        self.assertEqual(labels[0]["conversation_id"], "cid-1")
        self.assertEqual(
            labels[0]["extracted.user_text[messages[].data.content]"],
            "Customer is troubleshooting Apple watch",
        )
        self.assertNotIn("extracted.user_text", labels[0])
        self.assertNotIn("extracted.user_text.source", labels[0])
        self.assertEqual(
            labels[0]["extracted.user_inputs.device.impactedDeviceType"],
            "MSISDN",
        )
        self.assertEqual(
            labels[0]["extracted.user_inputs.device.impactedDevice"],
            "15551234567",
        )
        self.assertTrue(labels[0]["extracted.has_customer_context"])
        self.assertEqual(labels[0]["classification.intent"], "rca")
        self.assertEqual(labels[0]["classification.rule"], "rca.issue_diagnosis")
        self.assertEqual(labels[0]["classification.version"], "rules-v2")

    def test_flattens_single_item_user_inputs_list_without_index(self):
        record = screenshot_style_record("Check account status")
        record["user_inputs"] = [{
            "issue_summary": "Check account status",
            "device": {"impactedDevice": "15551234567"},
        }]

        label = label_records([record])[0]

        self.assertEqual(
            label["extracted.user_inputs.issue_summary"],
            "Check account status",
        )
        self.assertEqual(
            label["extracted.user_inputs.device.impactedDevice"],
            "15551234567",
        )

    def test_parses_and_flattens_json_string_user_inputs(self):
        record = screenshot_style_record("Check device status")
        record["user_inputs"] = (
            '{"device":{"impactedDeviceType":"MSISDN",'
            '"impactedDevice":"15180157401"},'
            '"output_type":"technical","category":"Other",'
            '"location":{"latitude":null,"longitude":null}}'
        )

        label = label_records([record], include_source_fields=True)[0]

        self.assertEqual(
            label["extracted.user_inputs.device.impactedDeviceType"],
            "MSISDN",
        )
        self.assertEqual(
            label["extracted.user_inputs.device.impactedDevice"],
            "15180157401",
        )
        self.assertEqual(label["extracted.user_inputs.output_type"], "technical")
        self.assertEqual(label["extracted.user_inputs.category"], "Other")
        self.assertIsNone(label["extracted.user_inputs.location.latitude"])
        self.assertNotIn("source.user_inputs", label)

    def test_ticket_intent_remains_sticky_within_nested_cid(self):
        labels = label_records([
            screenshot_style_record("Create a support ticket", record_id="first"),
            screenshot_style_record("Add this detail", record_id="second"),
        ])

        self.assertEqual([label["classification.intent"] for label in labels], ["ticket", "ticket"])


if __name__ == "__main__":
    unittest.main()

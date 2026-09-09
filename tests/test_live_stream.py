import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live_stream import (
    LiveStreamSettings,
    ProcessedEventLedger,
    decode_event,
    process_with_retry,
    read_boolean_environment,
)


class LiveStreamSettingsTests(unittest.TestCase):
    def test_boolean_environment_is_strict(self):
        with patch.dict(os.environ, {"FEATURE_FLAG": "yes"}):
            self.assertTrue(read_boolean_environment("FEATURE_FLAG"))
        with patch.dict(os.environ, {"FEATURE_FLAG": "invalid"}):
            with self.assertRaisesRegex(ValueError, "FEATURE_FLAG"):
                read_boolean_environment("FEATURE_FLAG")

    def test_settings_reject_unknown_provider(self):
        with patch.dict(
            os.environ,
            {
                "LIVE_STREAM_DATA_ENABLED": "true",
                "LIVE_STREAM_PROVIDER": "unknown",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "LIVE_STREAM_PROVIDER"):
                LiveStreamSettings.from_environment()


class EventProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.ledger = ProcessedEventLedger(
            Path(self.temporary_directory.name) / "state.db"
        )
        self.settings = LiveStreamSettings(
            enabled=True,
            provider="kafka",
            state_database=Path(self.temporary_directory.name) / "state.db",
            max_processing_attempts=3,
            retry_initial_seconds=0,
        )

    def tearDown(self):
        self.ledger.close()
        self.temporary_directory.cleanup()

    def test_invalid_envelope_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing id"):
            decode_event('{"data": {}}')

    def test_successful_replay_is_suppressed(self):
        event = {"id": "event-1", "data": {}}
        calls = []

        first = process_with_retry(
            event, "kafka", calls.append, self.ledger, self.settings
        )
        second = process_with_retry(
            event, "kafka", calls.append, self.ledger, self.settings
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual([event], calls)

    def test_processing_retries_before_marking_success(self):
        event = {"id": "event-2", "data": {}}
        attempts = []

        def handler(_event):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient")

        process_with_retry(event, "kafka", handler, self.ledger, self.settings)

        self.assertEqual(3, len(attempts))
        self.assertTrue(self.ledger.contains("event-2"))


if __name__ == "__main__":
    unittest.main()

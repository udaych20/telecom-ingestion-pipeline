import tempfile
import unittest
from pathlib import Path
from pipeline_logging import LOGGER, configure_logging, progress


class ProgressTests(unittest.TestCase):
    def test_log_file_is_created_and_records_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "run.log"
            try:
                configure_logging(path)
                self.assertTrue(path.exists())
                with progress("read"):
                    pass
                with self.assertRaises(RuntimeError):
                    with progress("write"):
                        raise RuntimeError("private payload")
                contents = path.read_text(encoding="utf-8")
                self.assertIn("Starting: read", contents)
                self.assertIn("Completed: read", contents)
                self.assertIn("Failed: write", contents)
                self.assertNotIn("private payload", contents)
            finally:
                for handler in LOGGER.handlers[:]:
                    LOGGER.removeHandler(handler)
                    handler.close()

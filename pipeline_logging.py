"""Progress reporting for long-running Cosmos operations."""

import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

LOGGER = logging.getLogger("nora.pipeline")


def configure_logging(path: Path, level: str = "INFO") -> None:
    level = level.upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("INTENT_LOG_LEVEL must be DEBUG, INFO, WARNING, or ERROR")
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False


@contextmanager
def progress(stage: str):
    """Report waiting every 15 seconds, including blocked SDK calls."""
    started = time.monotonic()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(15):
            LOGGER.info("Still waiting: %s (%.1fs elapsed)", stage, time.monotonic() - started)

    LOGGER.info("Starting: %s", stage)
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    except BaseException as error:
        # Do not log SDK response bodies or document content.
        LOGGER.error("Failed: %s after %.1fs (%s)", stage,
                     time.monotonic() - started, type(error).__name__)
        raise
    else:
        LOGGER.info("Completed: %s (%.1fs)", stage, time.monotonic() - started)
    finally:
        stopped.set()
        worker.join()

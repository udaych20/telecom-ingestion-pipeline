"""Check external dependencies before running a live or bypass demonstration."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

from .settings import load_local_environment


PLACEHOLDERS = ("YOUR-", "PASTE-", "example.com", "00000000-")


class Checks:
    def __init__(self) -> None:
        self.failed = False

    def report(self, passed: bool, name: str, detail: str = "") -> None:
        status = "PASS" if passed else "FAIL"
        suffix = f" - {detail}" if detail else ""
        print(f"{status:5} {name}{suffix}")
        self.failed = self.failed or not passed


def configured(name: str) -> tuple[bool, str]:
    value = os.getenv(name, "").strip()
    valid = bool(value) and not any(marker.lower() in value.lower() for marker in PLACEHOLDERS)
    return valid, "configured" if valid else "missing or still a placeholder"


def check_imports(checks: Checks) -> None:
    modules = ("azure.cosmos", "azure.identity", "kafka", "dotenv")
    missing = []
    for module in modules:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    checks.report(not missing, "Python dependencies", ", ".join(missing) or "installed")


def check_cosmos(checks: Checks) -> None:
    endpoint_ok, endpoint_detail = configured("COSMOS_CONNECTION__accountEndpoint")
    database_ok, database_detail = configured("COSMOS_DATABASE")
    container_ok, container_detail = configured("COSMOS_CHAT_CONTAINER")
    checks.report(endpoint_ok, "Cosmos endpoint", endpoint_detail)
    checks.report(database_ok, "Cosmos database", database_detail)
    checks.report(container_ok, "Cosmos chat container", container_detail)
    if not all((endpoint_ok, database_ok, container_ok)):
        return

    try:
        from azure.cosmos import CosmosClient
        from azure.identity import DefaultAzureCredential

        with CosmosClient(
            os.environ["COSMOS_CONNECTION__accountEndpoint"],
            credential=DefaultAzureCredential(),
        ) as client:
            container = client.get_database_client(
                os.environ["COSMOS_DATABASE"]
            ).get_container_client(os.environ["COSMOS_CHAT_CONTAINER"])
            properties = container.read()
        checks.report(True, "Cosmos access", str(properties.get("id", "reachable")))
    except Exception as error:
        checks.report(False, "Cosmos access", str(error))


def check_kafka(checks: Checks) -> None:
    bootstrap_ok, bootstrap_detail = configured("KAFKA_BOOTSTRAP_SERVERS")
    topic_ok, topic_detail = configured("KAFKA_TOPIC")
    checks.report(bootstrap_ok, "Kafka bootstrap servers", bootstrap_detail)
    checks.report(topic_ok, "Kafka topic", topic_detail)
    if not bootstrap_ok or not topic_ok:
        return

    try:
        from kafka import KafkaAdminClient

        from .transports import kafka_options

        options = kafka_options()
        options.pop("client_id", None)
        admin = KafkaAdminClient(request_timeout_ms=5000, **options)
        try:
            topics = admin.list_topics()
        finally:
            admin.close()
        topic = os.environ["KAFKA_TOPIC"]
        checks.report(topic in topics, "Kafka connectivity", f"topic={topic}")
    except Exception as error:
        checks.report(False, "Kafka connectivity", str(error))


def check_model(checks: Checks) -> None:
    for name in ("MODEL_ENDPOINT", "MODEL_DEPLOYMENT", "MODEL_API_KEY"):
        passed, detail = configured(name)
        checks.report(passed, name, detail)


def check_functions_host(checks: Checks) -> None:
    executable = shutil.which("func")
    checks.report(bool(executable), "Azure Functions Core Tools", executable or "func not found")
    if not executable:
        return

    root = Path(executable).resolve().parent
    worker_root = root / "workers" / "python"
    configs = list(worker_root.rglob("worker.config.json")) if worker_root.exists() else []
    checks.report(
        bool(configs),
        "Azure Functions Python worker",
        str(configs[0]) if configs else f"not found below {worker_root}",
    )

    settings_path = Path("local.settings.json")
    if not settings_path.exists():
        checks.report(False, "local.settings.json", "file not found")
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))["Values"]
        storage = str(settings.get("AzureWebJobsStorage", "")).strip()
        valid = bool(storage) and "PASTE-" not in storage.upper()
        checks.report(valid, "AzureWebJobsStorage", "configured" if valid else "missing or placeholder")
    except (KeyError, json.JSONDecodeError) as error:
        checks.report(False, "local.settings.json", str(error))


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "demo"), required=True)
    args = parser.parse_args()

    checks = Checks()
    checks.report(sys.version_info >= (3, 11), "Python version", sys.version.split()[0])
    check_imports(checks)
    check_cosmos(checks)
    check_kafka(checks)
    check_model(checks)
    if args.mode == "live":
        check_functions_host(checks)

    if checks.failed:
        print("\nReadiness check failed. Fix the FAIL items before running the demo.")
        raise SystemExit(1)
    print(f"\n{args.mode.capitalize()} mode is ready.")


if __name__ == "__main__":
    main()

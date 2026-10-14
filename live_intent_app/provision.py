"""Create the Azure resources required by the live intent pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass

from .settings import load_local_environment

load_local_environment()

COSMOS_DATA_CONTRIBUTOR_ROLE = (
    "00000000-0000-0000-0000-000000000002"
)


class ProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True)
class AzureNames:
    subscription: str
    resource_group: str
    location: str
    cosmos_account: str
    database: str
    event_hubs_namespace: str
    event_hub: str
    consumer_group: str


class AzureCli:
    def __init__(self, subscription: str, dry_run: bool = False) -> None:
        self.subscription = subscription
        self.dry_run = dry_run

    def run(self, *arguments: str, allow_failure: bool = False) -> str:
        command = [
            "az",
            *arguments,
            "--subscription",
            self.subscription,
            "--only-show-errors",
        ]
        if self.dry_run:
            print("DRY RUN:", subprocess.list2cmdline(command))
            return ""

        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode and not allow_failure:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ProvisioningError(detail)
        return completed.stdout.strip()

    def exists(self, *show_arguments: str) -> bool:
        if self.dry_run:
            return False
        output = self.run(*show_arguments, "--output", "json", allow_failure=True)
        return bool(output)


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def configured_names() -> AzureNames:
    return AzureNames(
        subscription=required("AZURE_SUBSCRIPTION_ID"),
        resource_group=required("AZURE_RESOURCE_GROUP"),
        location=required("AZURE_LOCATION"),
        cosmos_account=required("AZURE_COSMOS_ACCOUNT"),
        database=os.getenv("COSMOS_DATABASE", "NORA"),
        event_hubs_namespace=required("EVENT_HUB_NAMESPACE_NAME"),
        event_hub=os.getenv("EVENT_HUB_NAME", "live-intent"),
        consumer_group=os.getenv(
            "EVENT_HUB_CONSUMER_GROUP",
            "live-intent-inference",
        ),
    )


def ensure_resource_group(cli: AzureCli, names: AzureNames) -> None:
    if cli.exists("group", "show", "--name", names.resource_group):
        print(f"Resource group exists: {names.resource_group}")
        return
    cli.run(
        "group",
        "create",
        "--name",
        names.resource_group,
        "--location",
        names.location,
    )
    print(f"Created resource group: {names.resource_group}")


def ensure_cosmos_account(cli: AzureCli, names: AzureNames) -> None:
    common = ("--resource-group", names.resource_group, "--name", names.cosmos_account)
    if cli.exists("cosmosdb", "show", *common):
        print(f"Cosmos account exists: {names.cosmos_account}")
        return
    cli.run(
        "cosmosdb",
        "create",
        *common,
        "--locations",
        f"regionName={names.location}",
        "failoverPriority=0",
        "isZoneRedundant=False",
        "--capabilities",
        "EnableServerless",
        "--default-consistency-level",
        "Session",
    )
    print(f"Created serverless Cosmos account: {names.cosmos_account}")


def ensure_cosmos_database(cli: AzureCli, names: AzureNames) -> None:
    common = (
        "--resource-group",
        names.resource_group,
        "--account-name",
        names.cosmos_account,
        "--name",
        names.database,
    )
    if cli.exists("cosmosdb", "sql", "database", "show", *common):
        print(f"Cosmos database exists: {names.database}")
        return
    cli.run("cosmosdb", "sql", "database", "create", *common)
    print(f"Created Cosmos database: {names.database}")


def container_names() -> list[str]:
    settings = (
        ("COSMOS_CHAT_CONTAINER", "chat-history-uat"),
        ("COSMOS_CONTEXT_CONTAINER", "context-history-uat"),
        ("COSMOS_TOOLS_CONTAINER", "context-history-all-tools"),
        ("COSMOS_FEEDBACK_CONTAINER", "chat-feedback"),
        ("COSMOS_CHAT_LEASE", "leases-live-chat"),
        ("COSMOS_CONTEXT_LEASE", "leases-live-context"),
        ("COSMOS_TOOLS_LEASE", "leases-live-tools"),
        ("COSMOS_FEEDBACK_LEASE", "leases-live-feedback"),
    )
    return list(dict.fromkeys(os.getenv(name, default) for name, default in settings))


def ensure_cosmos_container(
    cli: AzureCli,
    names: AzureNames,
    container: str,
) -> None:
    common = (
        "--resource-group",
        names.resource_group,
        "--account-name",
        names.cosmos_account,
        "--database-name",
        names.database,
        "--name",
        container,
    )
    if cli.exists("cosmosdb", "sql", "container", "show", *common):
        print(f"Cosmos container exists: {container}")
        return
    cli.run(
        "cosmosdb",
        "sql",
        "container",
        "create",
        *common,
        "--partition-key-path",
        os.getenv("COSMOS_PARTITION_KEY_PATH", "/id"),
    )
    print(f"Created Cosmos container: {container}")


def ensure_event_hubs(cli: AzureCli, names: AzureNames) -> None:
    namespace_args = (
        "--resource-group",
        names.resource_group,
        "--name",
        names.event_hubs_namespace,
    )
    if not cli.exists("eventhubs", "namespace", "show", *namespace_args):
        cli.run(
            "eventhubs",
            "namespace",
            "create",
            *namespace_args,
            "--location",
            names.location,
            "--sku",
            "Standard",
        )
        print(f"Created Event Hubs namespace: {names.event_hubs_namespace}")
    else:
        print(f"Event Hubs namespace exists: {names.event_hubs_namespace}")

    hub_args = (
        "--resource-group",
        names.resource_group,
        "--namespace-name",
        names.event_hubs_namespace,
        "--name",
        names.event_hub,
    )
    if not cli.exists("eventhubs", "eventhub", "show", *hub_args):
        cli.run("eventhubs", "eventhub", "create", *hub_args)
        print(f"Created Event Hub: {names.event_hub}")
    else:
        print(f"Event Hub exists: {names.event_hub}")

    consumer_args = (
        "--resource-group",
        names.resource_group,
        "--namespace-name",
        names.event_hubs_namespace,
        "--eventhub-name",
        names.event_hub,
        "--name",
        names.consumer_group,
    )
    if not cli.exists("eventhubs", "eventhub", "consumer-group", "show", *consumer_args):
        cli.run("eventhubs", "eventhub", "consumer-group", "create", *consumer_args)
        print(f"Created consumer group: {names.consumer_group}")
    else:
        print(f"Consumer group exists: {names.consumer_group}")


def assign_function_roles(cli: AzureCli, names: AzureNames) -> None:
    principal_id = os.getenv("FUNCTION_PRINCIPAL_ID", "").strip()
    if not principal_id:
        print("FUNCTION_PRINCIPAL_ID is not set; skipped managed-identity roles")
        return

    account_id = cli.run(
        "cosmosdb",
        "show",
        "--resource-group",
        names.resource_group,
        "--name",
        names.cosmos_account,
        "--query",
        "id",
        "--output",
        "tsv",
    )
    role_definition = f"{account_id}/sqlRoleDefinitions/{COSMOS_DATA_CONTRIBUTOR_ROLE}"
    cli.run(
        "cosmosdb",
        "sql",
        "role",
        "assignment",
        "create",
        "--resource-group",
        names.resource_group,
        "--account-name",
        names.cosmos_account,
        "--scope",
        "/",
        "--principal-id",
        principal_id,
        "--role-definition-id",
        role_definition,
    )

    namespace_id = cli.run(
        "eventhubs",
        "namespace",
        "show",
        "--resource-group",
        names.resource_group,
        "--name",
        names.event_hubs_namespace,
        "--query",
        "id",
        "--output",
        "tsv",
    )
    for role in ("Azure Event Hubs Data Sender", "Azure Event Hubs Data Receiver"):
        cli.run(
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            principal_id,
            "--assignee-principal-type",
            "ServicePrincipal",
            "--role",
            role,
            "--scope",
            namespace_id,
        )
    print("Assigned Cosmos and Event Hubs roles to the Function identity")


def print_runtime_settings(names: AzureNames) -> None:
    endpoint = f"https://{names.cosmos_account}.documents.azure.com:443/"
    namespace = f"{names.event_hubs_namespace}.servicebus.windows.net"
    print("\nRuntime settings:")
    print(f"COSMOS_CONNECTION__accountEndpoint={endpoint}")
    print(f"COSMOS_DATABASE={names.database}")
    print(f"EVENT_HUB_NAMESPACE={namespace}")
    print(f"EVENT_HUB_CONNECTION__fullyQualifiedNamespace={namespace}")
    print(f"EVENT_HUB_NAME={names.event_hub}")
    print(f"EVENT_HUB_CONSUMER_GROUP={names.consumer_group}")


def print_rest_fallback() -> None:
    print("\nAzure provisioning or role assignment was denied.")
    print("The no-cloud demo still works with:")
    print("  MODEL_PROVIDER=demo")
    print("  CSV_SINK=local")
    print("  python -m live_intent_app.demo_service")
    print("For broker permission failures, configure:")
    print("  REST_FALLBACK_ENDPOINT=http://HOST:8080/demo/infer")
    print("Or bypass the broker with STREAM_TRANSPORT=rest.")


def provision(dry_run: bool = False) -> None:
    names = configured_names()
    cli = AzureCli(names.subscription, dry_run=dry_run)
    ensure_resource_group(cli, names)
    ensure_cosmos_account(cli, names)
    ensure_cosmos_database(cli, names)
    for container in container_names():
        ensure_cosmos_container(cli, names, container)
    ensure_event_hubs(cli, names)
    assign_function_roles(cli, names)
    print_runtime_settings(names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Azure CLI commands without creating resources",
    )
    args = parser.parse_args()

    try:
        provision(dry_run=args.dry_run)
    except (ProvisioningError, ValueError, FileNotFoundError) as error:
        print(f"Provisioning failed: {error}")
        print_rest_fallback()
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
